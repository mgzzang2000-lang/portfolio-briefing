# 로컬 실시간 포지션 감시 — GitHub Actions(5분 간격)의 반응 지연을 보완
# 포지션 보유 중엔 5초 간격으로 가격을 확인해 손절/익절/상한가를 즉시 처리한다.
# 종목 스캔·매수는 계속 GitHub Actions가 담당하고, 이 스크립트는 "판다" 역할만 한다.
import os, sys, time, subprocess
from datetime import datetime

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(REPO_DIR)


def load_env():
    env_path = os.path.join(REPO_DIR, '.env')
    if not os.path.exists(env_path):
        raise SystemExit(
            ".env 파일이 없습니다. .env.example을 복사해 .env로 만들고 값을 채워주세요.")
    with open(env_path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            os.environ.setdefault(k.strip(), v.strip())


load_env()
import auto_trading as bot  # noqa: E402  (환경변수 세팅 후 import 필요)

KST = bot.KST
NO_POSITION_INTERVAL = 20   # 포지션 없을 때 대기(초)
POSITION_CHECK_INTERVAL = 5  # 포지션 보유 중 가격 확인 간격(초)
GIT_PULL_EVERY = 12         # 포지션 보유 중 git pull 주기 (POSITION_CHECK_INTERVAL 배수)
KAKAO_TOKEN_REFRESH_SEC = 1800


def run_git(*args, timeout=15):
    try:
        return subprocess.run(['git', *args], cwd=REPO_DIR,
                               capture_output=True, text=True, timeout=timeout)
    except Exception as e:
        print(f"[git {' '.join(args)} 실패] {e}")
        return None


def git_pull():
    run_git('pull', '--rebase')


def git_push_dashboard():
    run_git('add', 'dashboard_data.json')
    commit = run_git('commit', '-m', '[로컬감시] 대시보드 업데이트 [skip ci]')
    if commit and 'nothing to commit' in (commit.stdout + commit.stderr):
        return
    for _ in range(5):
        push = run_git('push')
        if push and push.returncode == 0:
            print("[git push] 완료")
            return
        run_git('fetch', 'origin', 'main')
        run_git('rebase', 'origin/main')
        time.sleep(2)
    print("[git push] 재시도 끝까지 실패 — 다음 GitHub Actions 실행이 대신 반영할 것")


def wait_seconds(sec):
    time.sleep(max(1, sec))


def main():
    print(f"[로컬 감시 시작] {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')}")
    kakao_token = None
    kakao_fetched_at = 0
    loop_count = 0

    while True:
        now = datetime.now(KST)

        if now.weekday() >= 5:
            print(f"[{now.strftime('%H:%M:%S')}] 주말 — 10분 대기")
            wait_seconds(600)
            continue

        market_open   = now.replace(hour=9,  minute=0,  second=0, microsecond=0)
        market_close  = now.replace(hour=15, minute=30, second=0, microsecond=0)
        force_sell_at = now.replace(hour=15, minute=20, second=0, microsecond=0)

        if now < market_open:
            wait_seconds(min((market_open - now).total_seconds(), 60))
            continue
        if now > market_close:
            print(f"[{now.strftime('%H:%M:%S')}] 장 마감 — 30분 대기")
            wait_seconds(1800)
            continue

        try:
            kis_token = bot.get_kis_token()
            if kakao_token is None or (time.time() - kakao_fetched_at) > KAKAO_TOKEN_REFRESH_SEC:
                kakao_token = bot.get_kakao_token()
                kakao_fetched_at = time.time()
        except Exception as e:
            print(f"[토큰 오류] {e}")
            wait_seconds(10)
            continue

        dash = bot.load_dashboard()
        guard = bot.check_daily_guard(dash, now)
        dash_position = dash.get('position')

        if not dash_position:
            print(f"[{now.strftime('%H:%M:%S')}] 포지션 없음 — 대기")
            git_pull()
            wait_seconds(NO_POSITION_INTERVAL)
            continue

        # 포지션 보유 중: 매 iteration마다 git pull 하지 않고 주기적으로만
        if loop_count % GIT_PULL_EVERY == 0:
            git_pull()
            dash = bot.load_dashboard()
            guard = bot.check_daily_guard(dash, now)
            dash_position = dash.get('position')
            if not dash_position:
                loop_count += 1
                continue
        loop_count += 1

        try:
            holdings, cash = bot.get_balance(kis_token)
        except Exception as e:
            print(f"[잔고조회 오류] {e}")
            wait_seconds(5)
            continue

        bot_code = dash_position['code']
        matched = [h for h in holdings
                   if h.get('pdno') == bot_code and int(h.get('hldg_qty', 0)) > 0]
        if not matched:
            print(f"[{now.strftime('%H:%M:%S')}] 대시보드엔 포지션 있는데 실제 계좌엔 없음 "
                  f"(GitHub Actions가 이미 처리했을 수 있음) — 다음 git pull에서 갱신 확인")
            wait_seconds(POSITION_CHECK_INTERVAL)
            continue

        qty_before = int(matched[0].get('hldg_qty', 0))
        try:
            bot.manage_position(kis_token, kakao_token, dash, guard, now, matched[0], force_sell_at)
        except Exception as e:
            print(f"[포지션 관리 오류] {e}")
            try:
                bot.send_kakao(kakao_token, f"⚠️ 로컬 감시 오류\n{str(e)[:150]}")
            except Exception:
                pass
            wait_seconds(POSITION_CHECK_INTERVAL)
            continue

        # 실제 계좌 보유수량을 다시 조회해서 (전량/부분)청산이 실제로 일어났는지 확인.
        # dash['position']의 qty 필드는 2시간 부분청산 때 갱신되지 않아 이걸로는 판단 불가.
        try:
            holdings_after, _ = bot.get_balance(kis_token)
            matched_after = [h for h in holdings_after
                             if h.get('pdno') == bot_code and int(h.get('hldg_qty', 0)) > 0]
            qty_after = int(matched_after[0]['hldg_qty']) if matched_after else 0
        except Exception:
            qty_after = qty_before  # 조회 실패 시 안전하게 push 스킵
        if qty_after != qty_before:
            # 전량/부분 청산으로 실제 보유수량이 바뀐 경우에만 push
            # (매 5초 체크마다 push하면 GitHub 쪽과 과도하게 충돌하므로 의미있는 변화만 반영)
            git_push_dashboard()

        wait_seconds(POSITION_CHECK_INTERVAL)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n[로컬 감시 종료]")
        sys.exit(0)

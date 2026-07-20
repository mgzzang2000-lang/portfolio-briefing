# 상시 실시간 감시 — GitHub Actions(5분 간격)의 반응 지연을 보완.
# 포지션 보유 중엔 5초 간격으로 가격을 확인해 손절/익절/상한가를 즉시 처리하고,
# 포지션이 없을 때도 20초 간격으로 신규 매수 스캔을 직접 돈다("사고 판다" 둘 다 담당).
# [2026-07-18] 원래는 "판다" 역할만 하고 매수는 GitHub Actions(5분 cron)에 맡겼는데,
# 매도(5초 반응)와 매수(최대 5~6분 지연) 사이 비대칭이 심하다는 지적으로 매수 스캔도
# 이쪽(오라클 클라우드 상시 인스턴스에서 구동)으로 옮김. GitHub Actions는 이 프로세스가
# 살아있으면(하트비트 90초 이내) 매수 스캔도 양보하고, 죽었을 때만 백업으로 개입한다
# (auto_trading.main()의 watcher_is_alive() 가드 참고).
import os, sys, time, json, subprocess
from datetime import datetime

# Windows 콘솔(cp949)에서 이모지 등 유니코드 출력 시 크래시 방지
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
sys.stderr.reconfigure(encoding='utf-8', errors='replace')

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
import market_calendar  # noqa: E402

KST = bot.KST
NO_POSITION_INTERVAL = 20   # 포지션 없을 때 대기(초)
POSITION_CHECK_INTERVAL = 5  # 포지션 보유 중 가격 확인 간격(초)
GIT_PULL_EVERY = 12         # 포지션 보유 중 git pull 주기 (POSITION_CHECK_INTERVAL 배수)
HEARTBEAT_EVERY = 6         # 포지션 보유 중 하트비트 푸시 주기 (30초) — GitHub Actions가
                            # 이걸 보고 "로컬 watcher가 살아있으니 포지션 관리를 양보"할지 판단
HEARTBEAT_FILE = os.path.join(REPO_DIR, 'watcher_heartbeat.json')
KAKAO_TOKEN_REFRESH_SEC = 1800


def run_git(*args, timeout=15):
    try:
        return subprocess.run(['git', *args], cwd=REPO_DIR,
                               capture_output=True, text=True, timeout=timeout)
    except Exception as e:
        print(f"[git {' '.join(args)} 실패] {e}")
        return None


def git_pull():
    # [2026-07-14] rebase는 충돌 시 ours/theirs 방향이 뒤집혀 dashboard_merge.py
    # 병합 규칙과 안 맞을 수 있어 merge로 통일 (충돌나도 dashboard_merge.py가
    # 항상 자동으로 풀어주므로 사람 개입 없이 진행됨).
    # [2026-07-16] current_price만 바뀌어 커밋 안 된 로컬 수정이 남아있으면(qty 변화가
    # 없어 git_push_dashboard가 실행 안 된 경우) git이 "local changes would be
    # overwritten by merge"로 pull을 거부하는데, 이 함수는 결과를 확인하지 않아
    # 이후 몇 시간 동안 조용히 계속 실패할 수 있었음(오늘 09:29 이후 6시간 이상
    # 로컬 git이 멈춰 있었던 사고의 직접 원인으로 추정). pull 전에 미커밋 변경을
    # 먼저 커밋해서 항상 pull이 성공하도록 한다.
    run_git('add', 'dashboard_data.json')
    commit = run_git('commit', '-m', '[로컬감시] 대시보드 업데이트(pull 전 정리) [skip ci]')
    result = run_git('pull', '--no-rebase')
    if result is None or result.returncode != 0:
        print(f"[git pull 실패] {(result.stderr if result else '알 수 없는 오류')[:200]}")


def git_push_dashboard():
    run_git('add', 'dashboard_data.json')
    commit = run_git('commit', '-m', '[로컬감시] 대시보드 업데이트 [skip ci]')
    if commit and 'nothing to commit' in (commit.stdout + commit.stderr):
        return
    for _ in range(10):
        push = run_git('push')
        if push and push.returncode == 0:
            print("[git push] 완료")
            return
        run_git('fetch', 'origin', 'main')
        merge = run_git('merge', 'origin/main', '-m', 'merge: 대시보드 동기화 [skip ci]')
        if merge and merge.returncode != 0:
            # dashboard_merge.py가 항상 성공 처리하므로 이 분기는 거의 발생 안 하지만,
            # 혹시 대비해 add까지는 해둔다 (merge driver가 conflict 마커 없이 파일을
            # 이미 덮어썼을 것이므로 add만 하면 커밋 가능한 상태가 됨).
            run_git('add', 'dashboard_data.json')
            run_git('commit', '--no-edit')
        time.sleep(2)
    print("[git push] 10회 재시도 끝까지 실패 — 다음 GitHub Actions 실행이 대신 반영할 것")


def push_heartbeat():
    """[2026-07-10 추가] 로컬 watcher가 살아있다는 신호를 가볍게 남긴다.
    GitHub Actions가 이 신호(90초 이내)를 보면 같은 포지션을 동시에 건드리지 않고
    양보한다 — 흥구석유·금호전기·흥아해운에서 두 감시자가 거의 동시에 +2% 부분익절
    조건을 각자 판단해 중복 매도했던 레이스 컨디션 방지용. 실패해도 다음 주기에
    다시 시도하면 되므로 조용히 넘어간다."""
    try:
        with open(HEARTBEAT_FILE, 'w', encoding='utf-8') as f:
            json.dump({'alive_at': datetime.now(KST).isoformat()}, f)
    except Exception as e:
        print(f"[하트비트 기록 실패] {e}")
        return
    run_git('add', 'watcher_heartbeat.json')
    commit = run_git('commit', '-m', '[로컬감시] heartbeat [skip ci]')
    if commit and 'nothing to commit' in (commit.stdout + commit.stderr):
        return
    for _ in range(3):
        push = run_git('push')
        if push and push.returncode == 0:
            return
        run_git('fetch', 'origin', 'main')
        run_git('merge', 'origin/main', '-m', 'merge: heartbeat 동기화 [skip ci]')
        time.sleep(1)
    print("[하트비트 푸시] 재시도 끝까지 실패 — 다음 주기에 재시도")


def wait_seconds(sec):
    time.sleep(max(1, sec))


def main():
    print(f"[로컬 감시 시작] {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')}")
    kakao_token = None
    kakao_fetched_at = 0
    loop_count = 0
    holiday_cache = {'date': None, 'is_holiday': False}

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
        except Exception as e:
            print(f"[KIS 토큰 오류] {e}")
            wait_seconds(10)
            continue

        # [2026-07-20] 카카오 알림 토큰 발급 실패가 이 try에 같이 묶여 있으면(예: 리프레시
        # 토큰 만료, 카카오 서버 순간 오류) 알림 하나 때문에 kis_token까지 못 쓰게 되고
        # 이후 모든 로직(휴장일 체크·매수 스캔·포지션 관리)을 건너뛰며 10초마다 재시도만
        # 반복 — 실제로 09:14~09:49(오늘) 사이 289회 연속 실패하며 감시가 통째로 멈췄던
        # 사고의 원인. 카카오는 "알림"일 뿐 매매 감시의 필수 조건이 아니므로 실패해도
        # kakao_token=None으로 두고 계속 진행한다(send_kakao는 실패해도 각 호출부에서
        # try/except로 이미 감싸져 있어 None 토큰이어도 감시 자체는 안전).
        if kakao_token is None or (time.time() - kakao_fetched_at) > KAKAO_TOKEN_REFRESH_SEC:
            try:
                kakao_token = bot.get_kakao_token()
            except Exception as e:
                print(f"[카카오 토큰 오류] {e} — 알림 없이 매매 감시는 계속 진행")
                kakao_token = None
            kakao_fetched_at = time.time()

        # [2026-07-17] 요일(주말)만 체크해서는 법정공휴일 등 평일 휴장일을 못 걸러냄 —
        # 보유 포지션이 휴장일까지 넘어오면 이 루프가 마지막 실제 거래일의 고정된
        # (그러나 신선하지 않은) 가격으로 손절/익절을 오판할 위험이 있음. 하루 1회만
        # 조회해서 캐시(같은 날 반복 호출로 API 낭비 방지).
        today_str = now.strftime('%Y%m%d')
        if holiday_cache['date'] != today_str:
            trading_today = market_calendar.is_trading_day(kis_token, today_str)
            holiday_cache['date'] = today_str
            holiday_cache['is_holiday'] = (trading_today is False)
        if holiday_cache['is_holiday']:
            print(f"[{now.strftime('%H:%M:%S')}] 휴장일 — 30분 대기")
            wait_seconds(1800)
            continue

        dash = bot.load_dashboard()
        guard = bot.check_daily_guard(dash, now)
        dash_position = dash.get('position')

        if not dash_position:
            # [2026-07-18] 예전엔 여기서 그냥 대기만 하고 매수는 GitHub Actions(5분 cron)에
            # 맡겼는데, 매도(5초 반응)와 비교해 매수만 최대 5~6분 지연되는 비대칭이 있어
            # 이 루프에서 직접 매수 스캔까지 돌도록 확장(20초 간격 — GH Actions 내부
            # 60초 루프보다 빠름). 로직은 auto_trading.attempt_entry_scan()을 그대로
            # 재사용해 GitHub Actions와 완전히 동일한 조건으로 판단한다.
            try:
                holdings, cash = bot.get_balance(kis_token)
            except Exception as e:
                print(f"[잔고조회 오류] {e}")
                wait_seconds(5)
                continue
            if cash is None:
                # [2026-07-09] get_balance()는 API 응답이 불완전하면 "0원"이 아니라
                # None을 반환해 "값을 모른다"를 표시함(auto_trading.main()과 동일 원칙) —
                # 이 상태로 매수 스캔을 진행하면 잘못된 수량 계산으로 이어질 수 있어 스킵.
                print(f"[{now.strftime('%H:%M:%S')}] 잔고 조회 실패(None) — 이번 스캔 건너뜀")
                wait_seconds(5)
                continue
            print(f"[{now.strftime('%H:%M:%S')}] 포지션 없음 — 매수 스캔")
            try:
                bot.attempt_entry_scan(kis_token, kakao_token, dash, guard, holdings, cash)
            except Exception as e:
                print(f"[매수 스캔 오류] {e}")
                try:
                    bot.send_kakao(kakao_token, f"⚠️ 상시감시 매수스캔 오류\n{str(e)[:150]}")
                except Exception:
                    pass
            if dash.get('position'):
                # 이번 iteration에서 실제로 매수가 체결됨 — 다른 감시자(GH Actions 등)가
                # 곧바로 알 수 있도록 대기하지 말고 즉시 push.
                print(f"[{now.strftime('%H:%M:%S')}] 매수 체결 — 대시보드 즉시 반영")
                git_push_dashboard()
            # [2026-07-18] 하트비트를 포지션 보유 중에만 갱신했었는데, 포지션이 없는
            # 스캔 구간에도 이 프로세스가 살아있다는 신호를 보내야 GitHub Actions가
            # 매수 스캔을 양보할 수 있다 — 매 iteration(20초)마다 갱신해 90초 임계값
            # 대비 넉넉한 여유를 둔다.
            push_heartbeat()
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

        if loop_count % HEARTBEAT_EVERY == 0:
            push_heartbeat()

        wait_seconds(POSITION_CHECK_INTERVAL)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n[로컬 감시 종료]")
        sys.exit(0)

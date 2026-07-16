# git merge driver — dashboard_data.json은 watcher.py(내 PC)와 GitHub Actions가
# 동시에 커밋할 수 있어 텍스트 기반 git merge/rebase가 자주 충돌났음
# (2026-07-14: 이 충돌 때문에 005860 매수 기록이 통째로 유실되고, 손절 로직이
# 그 포지션의 존재 자체를 몰라 손절이 안 나간 사고 발생).
# git이 파일을 줄 단위로 합치는 대신, 이 스크립트가 두 버전을 "내용"으로 이해해서
# 항상 자동으로 하나의 결과를 만든다 — 충돌 마커가 생기는 경우 자체가 없어짐.
#
# git 설정: .gitattributes의 "dashboard_data.json merge=dashboard-merge" +
# `git config merge.dashboard-merge.driver "python dashboard_merge.py %O %A %B"`
# 인자: %O=공통 조상, %A=ours(우리 쪽, 이 경로에 결과를 써야 함), %B=theirs(합칠 대상)
import sys, json


def load(path):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def trade_key(t):
    # [2026-07-16] 'stock' 필드가 종목코드/실제종목명 등 기록자마다 달라질 수 있어
    # (003280 부분매도 뒤 포지션 오판 사고 때 같은 실제 매도 1건이 stock 값만 다르게
    # 두 번 기록됨 — 중복 판정 실패로 거래건수·손익 통계가 부풀려짐), 실제 체결을
    # 특정하기 충분한 action/date(분단위)/qty/price만으로 동일 거래를 식별한다.
    return (t.get('action'), t.get('date'), t.get('qty'), t.get('price'))


def merge_trades(a_trades, b_trades):
    seen = {}
    for t in (a_trades or []) + (b_trades or []):
        seen[trade_key(t)] = t
    return sorted(seen.values(), key=lambda t: t.get('date', ''))


def merge_balance_history(a_hist, b_hist, fresher):
    by_date = {}
    for h in (a_hist or []):
        by_date[h['date']] = h
    for h in (b_hist or []):
        # 같은 날짜가 양쪽에 다 있으면 last_updated가 더 최신인 쪽 값을 신뢰
        if h['date'] not in by_date or fresher == 'b':
            by_date[h['date']] = h
    return [by_date[d] for d in sorted(by_date.keys())][-60:]


def merge_dashboards(a, b):
    """a, b: 두 버전의 dashboard dict. last_updated가 더 늦은 쪽을 '최신 상태'로 신뢰하되,
    trades(거래내역)는 절대 유실되지 않도록 항상 두 쪽을 합집합으로 합친다."""
    a_time = a.get('last_updated', '') or ''
    b_time = b.get('last_updated', '') or ''
    fresher, other = (a, b) if a_time >= b_time else (b, a)
    fresher_tag = 'a' if a_time >= b_time else 'b'

    result = dict(fresher)  # position / current_balance / daily_loss_guard / last_updated: 최신 쪽 신뢰
    result['initial_balance'] = a.get('initial_balance', b.get('initial_balance', 500000))
    result['trades'] = merge_trades(a.get('trades'), b.get('trades'))
    result['balance_history'] = merge_balance_history(
        a.get('balance_history'), b.get('balance_history'), fresher_tag)

    # daily_loss_guard: 같은 날짜면 연속손절 카운트는 더 큰(더 안전한) 쪽을 채택
    ag = a.get('daily_loss_guard') or {}
    bg = b.get('daily_loss_guard') or {}
    if ag.get('date') == bg.get('date') and ag.get('date'):
        result['daily_loss_guard'] = {
            'date': ag.get('date'),
            'consecutive_losses': max(ag.get('consecutive_losses', 0), bg.get('consecutive_losses', 0)),
            'notified': ag.get('notified', False) or bg.get('notified', False),
        }
    return result


def main():
    _base_path, ours_path, theirs_path = sys.argv[1], sys.argv[2], sys.argv[3]
    try:
        ours = load(ours_path)
        theirs = load(theirs_path)
        if ours is None and theirs is None:
            sys.exit(1)  # 정말 둘 다 파싱 불가하면 사람이 봐야 함 (사실상 불가능한 케이스)
        if ours is None:
            merged = theirs
        elif theirs is None:
            merged = ours
        else:
            merged = merge_dashboards(ours, theirs)
        with open(ours_path, 'w', encoding='utf-8') as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
        sys.exit(0)  # 항상 성공 처리 — 충돌 마커가 생기지 않도록
    except Exception as e:
        # 병합 로직 자체가 실패해도 최소한 파싱되는 쪽(ours)을 그대로 남겨 git이
        # 충돌 마커를 만들지 않게 한다 — 데이터 유실보다 "약간 낡은 상태"가 안전함.
        print(f"[dashboard_merge 오류] {e} — ours 버전 유지", file=sys.stderr)
        sys.exit(0)


if __name__ == '__main__':
    main()

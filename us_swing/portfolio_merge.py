# git merge driver — portfolio_state.json은 trade_execution.py(하루 1회)와
# position_monitor.py(미장중 매시간)가 동시에 커밋할 수 있어 텍스트 기반 git
# merge/rebase가 충돌났음(2026-07-18: 이 충돌로 DOC 22주 매수 기록 + 손절/익절가가
# 통째로 유실돼, 실계좌엔 있는데 봇은 그 존재조차 모르는 상태로 며칠간 방치됨 —
# 손절 보호가 전혀 안 되는 위험한 사고였음).
# 국내봇의 dashboard_merge.py와 동일 원리 — git이 파일을 줄 단위로 합치는 대신,
# 이 스크립트가 두 버전을 "내용"으로 이해해서 항상 자동으로 하나의 결과를 만든다.
#
# git 설정: .gitattributes의 "us_swing/portfolio_state.json merge=portfolio-merge" +
# `git config merge.portfolio-merge.driver "python us_swing/portfolio_merge.py %O %A %B"`
# 인자: %O=공통 조상, %A=ours(우리 쪽, 이 경로에 결과를 써야 함), %B=theirs(합칠 대상)
import sys, json


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def trade_key(t):
    # 실제 체결을 특정하기 충분한 필드만으로 동일 거래를 식별(dashboard_merge.py와
    # 같은 원칙 — action/date(분단위)/symbol/qty/price면 충분).
    return (t.get("action"), t.get("date"), t.get("symbol"), t.get("qty"), t.get("price"))


def merge_trade_history(a_trades, b_trades):
    seen = {}
    for t in (a_trades or []) + (b_trades or []):
        seen[trade_key(t)] = t
    return sorted(seen.values(), key=lambda t: t.get("date", ""))


def merge_history_list(a_hist, b_hist, fresher):
    by_date = {}
    for h in (a_hist or []):
        by_date[h["date"]] = h
    for h in (b_hist or []):
        if h["date"] not in by_date or fresher == "b":
            by_date[h["date"]] = h
    return [by_date[d] for d in sorted(by_date.keys())][-60:]


def merge_positions(a_pos, b_pos, fresher):
    # symbol -> position dict. 합집합이 핵심(한쪽에만 있는 종목이 사라지면 안 됨 —
    # DOC 사고가 정확히 이 케이스: 한쪽 커밋에만 있던 신규 포지션이 통째로 증발함).
    # 같은 종목이 양쪽에 다르게 기록돼 있으면(동시에 부분매도 처리 등) 더 최신 쪽 채택.
    result = dict(a_pos or {})
    for sym, pos in (b_pos or {}).items():
        if sym not in result or fresher == "b":
            result[sym] = pos
    return result


def merge_states(a, b):
    """a, b: 두 버전의 portfolio_state dict. last_updated가 더 늦은 쪽을 '최신 상태'로
    신뢰하되, positions(보유종목)와 trade_history(거래내역)는 절대 유실되지 않도록
    항상 두 쪽을 합집합으로 합친다."""
    a_time = a.get("last_updated", "") or ""
    b_time = b.get("last_updated", "") or ""
    fresher, other = (a, b) if a_time >= b_time else (b, a)
    fresher_tag = "a" if a_time >= b_time else "b"

    result = dict(fresher)  # current_balance/total_assets/last_updated: 최신 쪽 신뢰
    result["initial_balance"] = a.get("initial_balance", b.get("initial_balance"))
    result["positions"] = merge_positions(a.get("positions"), b.get("positions"), fresher_tag)
    result["trade_history"] = merge_trade_history(a.get("trade_history"), b.get("trade_history"))
    result["balance_history"] = merge_history_list(
        a.get("balance_history"), b.get("balance_history"), fresher_tag)
    result["total_assets_history"] = merge_history_list(
        a.get("total_assets_history"), b.get("total_assets_history"), fresher_tag)
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
            merged = merge_states(ours, theirs)
        with open(ours_path, "w", encoding="utf-8") as f:
            json.dump(merged, f, ensure_ascii=False, indent=2)
        sys.exit(0)  # 항상 성공 처리 — 충돌 마커가 생기지 않도록
    except Exception as e:
        # 병합 로직 자체가 실패해도 최소한 파싱되는 쪽(ours)을 그대로 남겨 git이
        # 충돌 마커를 만들지 않게 한다 — 데이터 유실보다 "약간 낡은 상태"가 안전함.
        print(f"[portfolio_merge 오류] {e} — ours 버전 유지", file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    main()

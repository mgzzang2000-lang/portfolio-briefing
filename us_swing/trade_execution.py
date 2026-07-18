#!/usr/bin/env python3
"""
미국주식 스윙 — 실제 매수/매도 실행 (23:30 KST, 미장 정규개장 이후).

scan_signals.py(22:00 KST)가 저장한 당일 후보/청산대상을 읽어서:
  1. 추세 이탈(30주선 하향 이탈) 표시된 보유종목 매도
  2. 빈 슬롯만큼 상대강도 상위 후보 매수 (최대 MAX_POSITIONS종목,
     종목당 min(PER_POSITION_KRW, MAX_POSITION_USD) 캡)

보유 현황은 항상 KIS 잔고조회(get_holdings)로 실시간 확인 — 로컬 파일에
의존하지 않음(계좌가 진실의 원천). portfolio_state.json은 KIS가 모르는
전략 고유 정보(손절가)만 보관.

주문은 지정가만 가능(KIS 정책) — 매수는 현재가+0.5%, 매도는 현재가-0.5%로
넣어서 체결 가능성을 높임(살짝 불리한 가격에 체결될 수 있음, 슬리피지 감수).

[2026-07-07] 신설.
"""
import json, os, sys, time
from datetime import datetime, timezone, timedelta

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")  # Windows(cp949) 콘솔 출력 크래시 방지

from kis_common import (
    KST, get_kis_token, get_holdings, get_quote, place_order,
    get_usd_krw_rate, QUOTE_TO_TRADE_EXCD, record_balance, sync_live_balance,
    sync_total_assets,
)
from get_universe import get_universe

ACCOUNT_NO = os.environ["KIS_ACCOUNT_NO"]
ACCOUNT_PROD = "01"

DATA_DIR = os.path.dirname(__file__)
STATE_FILE = os.path.join(DATA_DIR, "portfolio_state.json")

MAX_POSITIONS = 3
PER_POSITION_KRW = 1_000_000
MAX_POSITION_USD = 500  # [2026-07-09] 사용자 확정 — 종목당 매수금액 상한. 현재 환율 기준
                         # PER_POSITION_KRW(100만원)보다 낮아서 이 값이 실질적 캡으로 작동.
                         # [2026-07-18] 리스크 기준 사이징 도입 이후로도 "한 종목에 계좌
                         # 절반 이상 몰빵하지 않는다"는 상한선 역할로 그대로 유지.
RISK_PCT_PER_TRADE = 0.025  # [2026-07-18 신규, 2026-07-18 재조정] 계좌 총자산의 이
# 비율만큼만 이 트레이드 하나에서 잃도록 수량을 정한다(Minervini 권장범위 1.25~2.5%의
# 상한값 — 계좌가 작아서(~$1,580) 1.5%로는 손절 7~10%인 종목이 $237~339로 지나치게
# 작아지는 문제가 있어 상한값으로 조정). 기존엔 손절폭이 종목마다 달라도(ATR 기반)
# 다 똑같은 정액($500)을 사서, 변동성 큰(손절폭 넓은) 종목이 오히려 계좌 대비 더 큰
# %를 거는 비일관성이 있었음 — 수량 = 리스크예산 / 손절폭(1주당)으로 바꿔서 트레이드
# 마다 실제 손실 위험을 항상 같은 비율로 맞춘다.
STOP_LOSS_FLOOR_PCT = 0.07  # [2026-07-09 최초 0.10, 2026-07-18 재조정] Minervini 권장
# 손절 상한(7~8%)에 맞춰 기존 10%보다 타이트하게 — 리스크 기준 사이징과 결합하면
# 같은 리스크예산으로도 포지션 금액이 커지는 효과(예: 리스크 2.5%+손절7% 조합 시
# 최악의 경우 포지션 ≈ $500 캡 부근, 예전 정액 사이징과 자본배치 규모가 비슷해짐).
ATR_MULT = 2.0
TAKE_PROFIT_PCT = 0.30      # 최종 목표가(잔여 물량) — 분할익절 이후에도 적용
PARTIAL_TP_PCT = 0.15       # 이 수익률 도달 시 보유량의 PARTIAL_FRACTION만큼 먼저 매도
PARTIAL_FRACTION = 0.5      # 분할익절 시 매도할 비율 — position_monitor.py가 이 값을 import해서 씀
RATCHET_PCT = 0.0           # 분할익절 후 잔여물량 손절가를 본절(entry_price)로 올림
                             # — position_monitor.py가 이 값을 import해서 씀(코드리뷰에서 두 파일에
                             # 값을 따로 두면 한쪽만 바꿔도 조용히 어긋난다는 지적을 받아 단일 소스로 통합)
# [2026-07-09] 백테스트 비교(익절30%/손절10% 단일 vs +15%분할50%+본절래칫 vs +5%래칫) 결과,
# 분할익절+본절래칫 쪽이 승률(53.2% vs 41.5%)과 낙폭(22.3% vs 26.4%) 모두 더 나아서
# "한 방 대박보다 꾸준함" 성향에 맞다고 사용자가 최종 확정. 총수익은 단일 30%/10%보다
# 낮지만(누적 245% vs 408%, 5.3년 기준) 그 트레이드오프를 감수하기로 함.


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    # [2026-07-18] portfolio_merge.py가 병합 시 "더 최신 쪽"을 판단하는 기준 —
    # 이 필드 없이는 trade_execution.py/position_monitor.py 중 어느 쪽 저장분이
    # 더 최신인지 알 수 없어 병합 규칙을 못 정함(DOC 매수기록이 git push 충돌로
    # 통째로 유실됐던 사고의 재발방지 조치, [[project-us-swing]] 참고).
    if isinstance(data, dict):
        data["last_updated"] = datetime.now(KST).isoformat()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def log_trade(state, action, symbol, qty, price, reason):
    state.setdefault("trade_history", []).append({
        "action": action, "date": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
        "symbol": symbol, "qty": qty, "price": price, "reason": reason,
    })


def main():
    today_str = datetime.now(KST).strftime("%Y%m%d")
    candidates_path = os.path.join(DATA_DIR, f"candidates_{today_str}.json")
    scan_result = load_json(candidates_path, None)
    if not scan_result:
        print(f"[오류] 오늘({today_str}) 스캔 결과가 없음 — scan_signals.py가 먼저 돌아야 함")
        return

    token = get_kis_token()
    state = load_json(STATE_FILE, {
        "initial_balance": 200, "current_balance": 200,
        "positions": {}, "trade_history": [], "balance_history": [],
    })
    sync_live_balance(token, ACCOUNT_NO, ACCOUNT_PROD, state)
    holdings = get_holdings(token, ACCOUNT_NO, ACCOUNT_PROD)
    held_symbols = {h["symbol"] for h in holdings}

    # ── 1. 추세 이탈 종목 매도 ──
    for symbol in scan_result.get("trend_exit_symbols", []):
        if symbol not in held_symbols:
            continue
        h = next(x for x in holdings if x["symbol"] == symbol)
        excd_cache = load_json(os.path.join(DATA_DIR, "excd_cache.json"), {})
        quote_excd = excd_cache.get(symbol, "NAS")
        trade_excd = QUOTE_TO_TRADE_EXCD.get(quote_excd, "NASD")
        current_price = get_quote(token, symbol, quote_excd) or h["current_price"]
        limit_price = round(current_price * 0.995, 2)
        ok, resp = place_order(token, ACCOUNT_NO, ACCOUNT_PROD, trade_excd,
                                symbol, h["qty"], limit_price, "sell")
        if ok:
            print(f"[매도] {symbol} {h['qty']}주 @ {limit_price} (추세이탈)")
            log_trade(state, "sell", symbol, h["qty"], limit_price, "30주선 하향이탈")
            record_balance(state, h["qty"] * limit_price)
            state["positions"].pop(symbol, None)
            held_symbols.discard(symbol)
        else:
            print(f"[매도 실패] {symbol}: {resp.get('msg1', resp)}")
        time.sleep(0.3)

    # ── 2. 빈 슬롯만큼 신규 매수 ──
    # [2026-07-18 신규] 시장 전체 건강도(SPY Stage2) 필터 — SPY 자체가 하락/조정
    # 국면이면 섹터RS/개별RS가 아무리 좋아도 신규매수를 이번 사이클 통째로 보류.
    # 매도(추세이탈/하드손절)는 이 필터와 무관하게 항상 실행되므로 이 분기보다 위에서
    # 이미 처리됨. 리서치 근거: "지수 자체를 먼저 걸러내는 게 어떤 손절 규칙보다도
    # 하락장에서 자본을 더 많이 지켰다"(Weinstein 방법론 위계 구조).
    if not scan_result.get("spy_market_healthy", True):
        print("[시장필터] SPY가 Stage2(상승국면) 아님 — 이번 사이클 신규매수 보류")
        sync_total_assets(state, [h for h in holdings if h["symbol"] in held_symbols])
        save_json(STATE_FILE, state)
        return

    free_slots = MAX_POSITIONS - len(held_symbols)
    if free_slots <= 0:
        print(f"빈 슬롯 없음 (보유 {len(held_symbols)}/{MAX_POSITIONS})")
        sync_total_assets(state, [h for h in holdings if h["symbol"] in held_symbols])
        save_json(STATE_FILE, state)
        return

    usd_krw = get_usd_krw_rate()
    budget_usd = min(PER_POSITION_KRW / usd_krw, MAX_POSITION_USD)
    excd_cache = load_json(os.path.join(DATA_DIR, "excd_cache.json"), {})

    # [2026-07-18] 리스크 기준 사이징의 기준 자산 — 방금 갱신한 현금(current_balance,
    # sync_live_balance) + 보유종목 평가금액(holdings, 이번 사이클 시작 시 조회)을
    # 합쳐서 계산. sync_total_assets()가 이 값을 state["total_assets"]에 저장하는 건
    # 매수 루프 이후라, 그걸 기다리지 않고 여기서 직접 같은 방식으로 구한다.
    equity_usd = state.get("current_balance", 0) + sum(h["qty"] * h["current_price"] for h in holdings)
    risk_budget_usd = equity_usd * RISK_PCT_PER_TRADE

    # [2026-07-09] 섹터 쏠림 방지 — 최대 3종목만 보유하는데 상위 후보 3개가
    # 같은 섹터에 몰려도 그대로 다 사던 문제. 섹터당 최대 1종목으로 제한해서
    # 실제로 분산되게 함. 이미 보유 중인 종목의 섹터도 포함해서 계산.
    # [코드리뷰 수정] 매수 시점에 기록해둔 pos["sector"]를 우선 사용 — 이후 S&P500
    # 편출/재편입으로 get_universe()가 그 종목을 더 이상 반환하지 않게 되더라도
    # 섹터 캡이 조용히 무력화되지 않도록 함. 로컬에 기록 없는(수동매수 등) 보유종목만
    # get_universe() 조회로 보완.
    held_sectors = set()
    unresolved_held = []
    for s in held_symbols:
        tracked_sector = state.get("positions", {}).get(s, {}).get("sector")
        if tracked_sector:
            held_sectors.add(tracked_sector)
        else:
            unresolved_held.append(s)
    if unresolved_held:
        symbol_to_sector = {u["symbol"]: u["sector"] for u in get_universe()}
        held_sectors |= {symbol_to_sector[s] for s in unresolved_held if s in symbol_to_sector}

    bought = 0
    for c in scan_result.get("candidates", []):
        if bought >= free_slots:
            break
        symbol = c["symbol"]
        if symbol in held_symbols:
            continue
        sector = c.get("sector")
        if sector in held_sectors:
            print(f"  [건너뜀] {symbol}: 섹터 쏠림 방지 ({sector} 이미 보유 중)")
            continue
        quote_excd = excd_cache.get(symbol, "NAS")
        trade_excd = QUOTE_TO_TRADE_EXCD.get(quote_excd, "NASD")
        current_price = get_quote(token, symbol, quote_excd)
        if not current_price:
            print(f"  [건너뜀] {symbol}: 현재가 조회 실패")
            continue

        # [2026-07-07] -8%는 "최대 손실 한도"(cap)임 — ATR이 그보다 더 넓은
        # 손절폭을 제시해도 8%보다 더 잃게 두면 안 됨. 대신 ATR상 변동성이
        # 작아서 더 타이트하게 잡아도 되는 종목은 그렇게 둠(불필요한 리스크
        # 방지). 그래서 "8% 캡보다 타이트한 쪽"을 선택 = max(둘 다 하락폭이므로
        # 가격에 더 가까운/숫자가 더 큰 쪽).
        floor_price = current_price * (1 - STOP_LOSS_FLOOR_PCT)
        if atr := c.get("atr"):
            dynamic_stop = current_price - atr * ATR_MULT
            stop_price = max(dynamic_stop, floor_price)
        else:
            stop_price = floor_price

        # [2026-07-18] 리스크 기준 사이징 — "손절폭(1주당 손실) × 수량 = 리스크예산"이
        # 되도록 수량을 정하고, 기존 정액 예산(budget_usd)은 상한선으로만 유지(변동성
        # 낮은/손절폭 좁은 종목이 계좌 대비 과도하게 큰 포지션이 되는 것 방지).
        risk_per_share = current_price - stop_price
        if risk_per_share <= 0:
            print(f"  [건너뜀] {symbol}: 손절가가 현재가 이상 — 계산 오류로 추정")
            continue
        qty_by_risk = int(risk_budget_usd // risk_per_share)
        qty_by_budget = int(budget_usd // current_price)
        qty = min(qty_by_risk, qty_by_budget)
        if qty < 1:
            print(f"  [건너뜀] {symbol}: 수량 부족(리스크기준 {qty_by_risk}주, 예산기준 {qty_by_budget}주)")
            continue
        limit_price = round(current_price * 1.005, 2)

        ok, resp = place_order(token, ACCOUNT_NO, ACCOUNT_PROD, trade_excd,
                                symbol, qty, limit_price, "buy")
        if ok:
            tp_price = round(limit_price * (1 + TAKE_PROFIT_PCT), 2)
            partial_tp_price = round(limit_price * (1 + PARTIAL_TP_PCT), 2)
            print(f"[매수] {symbol} {qty}주 @ {limit_price} "
                  f"(손절가 {stop_price:.2f}, 분할익절가 {partial_tp_price}, 최종익절가 {tp_price})")
            log_trade(state, "buy", symbol, qty, limit_price,
                       f"RS12w={c.get('rel_strength_12w', 0):.1%}")
            record_balance(state, -qty * limit_price)
            state["positions"][symbol] = {
                "stop_price": round(stop_price, 2),
                "tp_price": tp_price,
                "partial_tp_price": partial_tp_price,
                "partial_taken": False,
                "entry_price": limit_price,
                "entry_date": today_str,
                "qty": qty,
                "sector": sector,  # [코드리뷰 수정] 섹터캡용 — get_universe() 재조회 없이 매수시점 값 그대로 사용
            }
            held_symbols.add(symbol)
            held_sectors.add(sector)
            bought += 1
        else:
            print(f"[매수 실패] {symbol}: {resp.get('msg1', resp)}")
        time.sleep(0.3)

    # 이번 실행에서 새로 산 종목은 holdings(실행 시작 시점 조회)에 없으므로
    # 방금 체결된 entry_price를 현재가 대용으로 사용(매수 직후라 오차 미미) —
    # 다음 position_monitor.py(매시간) 사이클에서 실제 현재가로 갱신됨.
    total_holdings = [h for h in holdings if h["symbol"] in held_symbols]
    priced_symbols = {h["symbol"] for h in total_holdings}
    for symbol in held_symbols - priced_symbols:
        pos = state["positions"].get(symbol)
        if pos:
            total_holdings.append({"symbol": symbol, "qty": pos["qty"], "current_price": pos["entry_price"]})
    sync_total_assets(state, total_holdings)
    save_json(STATE_FILE, state)
    print(f"완료 — 신규매수 {bought}건, 현재 보유 {len(held_symbols)}/{MAX_POSITIONS}")


if __name__ == "__main__":
    main()

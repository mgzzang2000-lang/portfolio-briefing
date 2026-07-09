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
STOP_LOSS_FLOOR_PCT = 0.10  # [2026-07-09] 백테스트로 확인한 익절30%/손절10% 조합 채택
ATR_MULT = 2.0
TAKE_PROFIT_PCT = 0.30      # 최종 목표가(잔여 물량) — 분할익절 이후에도 적용
PARTIAL_TP_PCT = 0.15       # 이 수익률 도달 시 보유량의 절반을 먼저 매도
RATCHET_PCT = 0.0           # 분할익절 후 잔여물량 손절가를 본절(entry_price)로 올림
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
    free_slots = MAX_POSITIONS - len(held_symbols)
    if free_slots <= 0:
        print(f"빈 슬롯 없음 (보유 {len(held_symbols)}/{MAX_POSITIONS})")
        save_json(STATE_FILE, state)
        return

    usd_krw = get_usd_krw_rate()
    budget_usd = min(PER_POSITION_KRW / usd_krw, MAX_POSITION_USD)
    excd_cache = load_json(os.path.join(DATA_DIR, "excd_cache.json"), {})

    # [2026-07-09] 섹터 쏠림 방지 — 최대 3종목만 보유하는데 상위 후보 3개가
    # 같은 섹터에 몰려도 그대로 다 사던 문제. 섹터당 최대 1종목으로 제한해서
    # 실제로 분산되게 함. 이미 보유 중인 종목의 섹터도 포함해서 계산.
    symbol_to_sector = {u["symbol"]: u["sector"] for u in get_universe()}
    held_sectors = {symbol_to_sector[s] for s in held_symbols if s in symbol_to_sector}

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
        qty = int(budget_usd // current_price)
        if qty < 1:
            print(f"  [건너뜀] {symbol}: 예산 부족(가격 {current_price}, 예산 ${budget_usd:.2f})")
            continue
        limit_price = round(current_price * 1.005, 2)

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
            }
            held_symbols.add(symbol)
            held_sectors.add(sector)
            bought += 1
        else:
            print(f"[매수 실패] {symbol}: {resp.get('msg1', resp)}")
        time.sleep(0.3)

    save_json(STATE_FILE, state)
    print(f"완료 — 신규매수 {bought}건, 현재 보유 {len(held_symbols)}/{MAX_POSITIONS}")


if __name__ == "__main__":
    main()

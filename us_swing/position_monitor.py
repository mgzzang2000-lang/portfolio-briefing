#!/usr/bin/env python3
"""
미국주식 스윙 — 시간마다 보유 포지션 하드 손절 확인 (미장 정규시간대에만 실행).

30주선 이탈(추세꺾임) 청산은 주봉 종가가 필요해서 scan_signals.py가 밤에
한 번만 확인하지만, 이 스크립트는 그 사이 급락에 대비한 실시간 하드
손절(-8% 등, trade_execution.py가 매수 시 계산해 portfolio_state.json에
저장한 stop_price)만 확인. 보유 현황은 항상 KIS 잔고조회로 실시간 확인.

[2026-07-07] 신설.
"""
import json, os, sys, time
from datetime import datetime, timezone, timedelta

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")  # Windows(cp949) 콘솔 출력 크래시 방지

from kis_common import (
    KST, get_kis_token, get_holdings, place_order, QUOTE_TO_TRADE_EXCD,
)

ACCOUNT_NO = os.environ["KIS_ACCOUNT_NO"]
ACCOUNT_PROD = "01"

DATA_DIR = os.path.dirname(__file__)
STATE_FILE = os.path.join(DATA_DIR, "portfolio_state.json")
EXCD_CACHE_FILE = os.path.join(DATA_DIR, "excd_cache.json")


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def main():
    token = get_kis_token()
    state = load_json(STATE_FILE, {"positions": {}, "trade_history": []})
    excd_cache = load_json(EXCD_CACHE_FILE, {})
    holdings = get_holdings(token, ACCOUNT_NO, ACCOUNT_PROD)

    if not holdings:
        print("보유 포지션 없음 — 확인할 것 없음")
        return

    for h in holdings:
        symbol, current_price, qty = h["symbol"], h["current_price"], h["qty"]
        pos = state.get("positions", {}).get(symbol)
        if not pos:
            print(f"  [경고] {symbol} 보유 중이나 손절가 기록 없음 — 수동 매수됐거나 상태 유실, 건너뜀")
            continue
        stop_price = pos["stop_price"]
        print(f"  {symbol}: 현재 {current_price} vs 손절가 {stop_price} ({h['pnl_pct']:+.2f}%)")
        if current_price > stop_price:
            continue

        quote_excd = excd_cache.get(symbol, "NAS")
        trade_excd = QUOTE_TO_TRADE_EXCD.get(quote_excd, "NASD")
        limit_price = round(current_price * 0.995, 2)
        ok, resp = place_order(token, ACCOUNT_NO, ACCOUNT_PROD, trade_excd,
                                symbol, qty, limit_price, "sell")
        if ok:
            print(f"[손절매도] {symbol} {qty}주 @ {limit_price}")
            state.setdefault("trade_history", []).append({
                "action": "sell", "date": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
                "symbol": symbol, "qty": qty, "price": limit_price,
                "reason": f"하드손절 (기준 {stop_price})",
            })
            state["positions"].pop(symbol, None)
        else:
            print(f"[손절매도 실패] {symbol}: {resp.get('msg1', resp)}")
        time.sleep(0.3)

    save_json(STATE_FILE, state)


if __name__ == "__main__":
    main()

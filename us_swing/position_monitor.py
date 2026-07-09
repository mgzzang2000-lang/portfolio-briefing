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
    record_balance, sync_live_balance,
)

ACCOUNT_NO = os.environ["KIS_ACCOUNT_NO"]
ACCOUNT_PROD = "01"

DATA_DIR = os.path.dirname(__file__)
STATE_FILE = os.path.join(DATA_DIR, "portfolio_state.json")
EXCD_CACHE_FILE = os.path.join(DATA_DIR, "excd_cache.json")

RATCHET_PCT = 0.0  # [2026-07-09] 분할익절 체결 후 잔여물량 손절가를 본절로 올림
                    # (trade_execution.py의 같은 값과 반드시 일치시킬 것)


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
    state = load_json(STATE_FILE, {
        "initial_balance": 200, "current_balance": 200,
        "positions": {}, "trade_history": [], "balance_history": [],
    })
    excd_cache = load_json(EXCD_CACHE_FILE, {})
    sync_live_balance(token, ACCOUNT_NO, ACCOUNT_PROD, state)
    holdings = get_holdings(token, ACCOUNT_NO, ACCOUNT_PROD)

    if not holdings:
        # [2026-07-09] 잔고 갱신은 이미 위에서 끝났으니, 보유 포지션이 없어도
        # 그 값이 저장되도록 여기서도 save — 종전엔 이 분기에서 그냥 return해서
        # 매 사이클 잔고 새로고침이 무의미해지고 있었음.
        save_json(STATE_FILE, state)
        print("보유 포지션 없음 — 확인할 것 없음")
        return

    for h in holdings:
        symbol, current_price, qty = h["symbol"], h["current_price"], h["qty"]
        pos = state.get("positions", {}).get(symbol)
        if not pos:
            print(f"  [경고] {symbol} 보유 중이나 손절가 기록 없음 — 수동 매수됐거나 상태 유실, 건너뜀")
            continue
        stop_price = pos["stop_price"]
        tp_price = pos.get("tp_price")  # [2026-07-09] 이 필드 도입 전에 매수된 포지션엔 없을 수 있음
        partial_tp_price = pos.get("partial_tp_price")
        partial_taken = pos.get("partial_taken", False)
        label = f" / 최종익절가 {tp_price}" if tp_price else ""
        if partial_tp_price and not partial_taken:
            label += f" / 분할익절가 {partial_tp_price}"
        print(f"  {symbol}: 현재 {current_price} vs 손절가 {stop_price}{label} ({h['pnl_pct']:+.2f}%)")

        quote_excd = excd_cache.get(symbol, "NAS")
        trade_excd = QUOTE_TO_TRADE_EXCD.get(quote_excd, "NASD")
        limit_price = round(current_price * 0.995, 2)

        # 우선순위: 손절(리스크관리) > 최종익절(잔여물량 목표 도달) > 분할익절.
        # 가격이 한 번에 최종익절가까지 뛰어넘은 경우엔 굳이 분할을 거칠 필요 없이
        # 바로 전량 최종익절로 처리 — 시간 단위 폴링이라 그 사이 갭업 가능.
        if current_price <= stop_price:
            reason = f"손절 (기준 {stop_price})" + (" — 본절래칫 이후" if partial_taken else "")
            sell_qty = qty
        elif tp_price and current_price >= tp_price:
            reason = f"최종익절 (기준 {tp_price})"
            sell_qty = qty
        elif partial_tp_price and not partial_taken and current_price >= partial_tp_price:
            sell_qty = qty // 2
            if sell_qty < 1:
                print(f"    분할익절가 도달했지만 보유수량({qty}주)이 적어 분할 불가 — 최종익절/손절까지 홀드")
                continue
            reason = f"분할익절 (기준 {partial_tp_price}, 보유 {qty}주 중 {sell_qty}주)"
        else:
            continue

        ok, resp = place_order(token, ACCOUNT_NO, ACCOUNT_PROD, trade_excd,
                                symbol, sell_qty, limit_price, "sell")
        if ok:
            print(f"[매도] {symbol} {sell_qty}주 @ {limit_price} ({reason})")
            state.setdefault("trade_history", []).append({
                "action": "sell", "date": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
                "symbol": symbol, "qty": sell_qty, "price": limit_price,
                "reason": reason,
            })
            record_balance(state, sell_qty * limit_price)
            if sell_qty == qty:
                state["positions"].pop(symbol, None)
            else:
                # 분할익절 — 포지션은 유지, 잔여물량 손절가를 본절로 래칫업
                pos["partial_taken"] = True
                pos["stop_price"] = round(max(stop_price, pos["entry_price"] * (1 + RATCHET_PCT)), 2)
        else:
            print(f"[매도 실패] {symbol}: {resp.get('msg1', resp)}")
        time.sleep(0.3)

    save_json(STATE_FILE, state)


if __name__ == "__main__":
    main()

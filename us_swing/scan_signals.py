#!/usr/bin/env python3
"""
미국주식 스윙/장기 추세추종 — 신호 스캔 (신설, 아직 매수/매도 실행 없음).

지금 단계는 "신호만 기록"까지만 함 — 실제 자금 배분(몇 종목 동시보유,
종목당 비중)을 정한 뒤에 주문 실행 로직을 얹을 예정.

전략 요약 (하루 1회, 주봉 기준 — 국내주식 봇과 달리 스윙/장기라 초 단위
타이밍이 필요 없음):
  ① 섹터 상대강도: GICS 11개 섹터별로 "최근 12주 수익률 - SPY 12주 수익률"
     평균을 구해서 순위 매김 — 상위 절반 섹터에 속한 종목만 후보로 남김
     (이미 다 유행이 지난 업종, 반대로 아직 소외된 업종을 걸러내기 위함)
  ② 개별종목 상대강도: 자기 자신의 이평선 정배열(느리게 확정되는 지표)
     대신, SPY 대비 12주 초과수익률이 양(+)인 종목 위주로 봄 —
     이평선이 아직 완전히 정배열되기 전에도 시장보다 강한 종목을 포착
  ③ Stage2 진입(Stan Weinstein 방식): 주가가 30주 이평선 위에 있고,
     30주 이평선 자체가 상승 중인 구간만 인정 — 바닥 다지기(Stage1)를
     막 벗어나는 시점을 잡으려는 목적, 완전한 정배열 확정까지 기다리지 않음
  ※ 뉴스/재료 확인은 의도적으로 뺌 — 교집합이 너무 좁아져 매매빈도가
     급격히 줄고, 뉴스는 이미 반영된 뒤인 경우가 많아 타이밍 개선 효과도
     적음. 대신 손절을 타이트하게 가져가는 쪽으로 리스크를 관리(다음 단계).

[2026-07-07] 신설.
"""
import json, os, time
from datetime import datetime, timezone, timedelta

from kis_common import KST, get_kis_token, get_weekly_ohlcv, calc_ma
from get_universe import get_universe

DATA_DIR = os.path.dirname(__file__)
EXCD_CACHE_FILE = os.path.join(DATA_DIR, "excd_cache.json")
LOOKBACK_WEEKS = 12  # 상대강도 계산에 쓸 기간
MA_PERIOD = 30        # Stage2 판정용 이평선(주)


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def rel_strength(closes, spy_closes, weeks=LOOKBACK_WEEKS):
    if len(closes) <= weeks or len(spy_closes) <= weeks:
        return None
    stock_ret = closes[0] / closes[weeks] - 1
    spy_ret = spy_closes[0] / spy_closes[weeks] - 1
    return stock_ret - spy_ret


def is_stage2(closes):
    ma_now = calc_ma(closes, MA_PERIOD)
    ma_prev = calc_ma(closes[4:], MA_PERIOD)  # 4주 전 시점의 30주 이평
    if ma_now is None or ma_prev is None:
        return False
    price = closes[0]
    return price > ma_now and ma_now > ma_prev


def main():
    token = get_kis_token()
    excd_cache = load_json(EXCD_CACHE_FILE, {})

    spy_closes, _, spy_excd = get_weekly_ohlcv(token, "SPY", excd_cache.get("SPY"))
    if not spy_closes:
        print("[오류] SPY 벤치마크 데이터를 못 가져옴 — 중단")
        return
    excd_cache["SPY"] = spy_excd

    universe = get_universe()
    print(f"S&P500 {len(universe)}종목 스캔 시작")

    results = []
    for i, stock in enumerate(universe):
        symbol, sector = stock["symbol"], stock["sector"]
        closes, volumes, excd = get_weekly_ohlcv(token, symbol, excd_cache.get(symbol))
        if not closes:
            continue
        excd_cache[symbol] = excd
        rs = rel_strength(closes, spy_closes)
        stage2 = is_stage2(closes)
        results.append({
            "symbol": symbol, "sector": sector, "price": closes[0],
            "rel_strength_12w": rs, "stage2": stage2,
        })
        if (i + 1) % 50 == 0:
            print(f"  진행: {i+1}/{len(universe)}")
            save_json(EXCD_CACHE_FILE, excd_cache)  # 중간 실패해도 지금까지 캐시는 보존
        time.sleep(0.1)

    save_json(EXCD_CACHE_FILE, excd_cache)

    # ── 섹터별 상대강도 순위 ──
    sector_rs = {}
    for sector in set(r["sector"] for r in results):
        vals = [r["rel_strength_12w"] for r in results if r["sector"] == sector and r["rel_strength_12w"] is not None]
        if vals:
            sector_rs[sector] = sum(vals) / len(vals)
    ranked_sectors = sorted(sector_rs, key=lambda s: -sector_rs[s])
    top_sectors = set(ranked_sectors[:max(1, len(ranked_sectors) // 2)])
    print(f"섹터 순위(상위 절반만 후보 인정): {[(s, round(sector_rs[s]*100, 1)) for s in ranked_sectors]}")

    # ── 최종 후보: 상위 섹터 + 상대강도 양수 + Stage2 ──
    candidates = [
        r for r in results
        if r["sector"] in top_sectors and r["rel_strength_12w"] and r["rel_strength_12w"] > 0 and r["stage2"]
    ]
    candidates.sort(key=lambda r: -r["rel_strength_12w"])

    today_str = datetime.now(KST).strftime("%Y%m%d")
    out_path = os.path.join(DATA_DIR, f"candidates_{today_str}.json")
    save_json(out_path, {
        "date": today_str,
        "sector_ranking": [{"sector": s, "avg_rel_strength_12w": round(sector_rs[s], 4)} for s in ranked_sectors],
        "candidates": candidates,
    })
    print(f"최종 후보 {len(candidates)}종목: {[c['symbol'] for c in candidates]}")


if __name__ == "__main__":
    main()

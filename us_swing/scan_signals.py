#!/usr/bin/env python3
"""
미국주식 스윙/장기 추세추종 — 신호 스캔 (22:00 KST, 매수/매도 실행 전 마지막 스캔).

여기서는 후보 산출 + 보유종목 추세이탈 판정까지만 하고, 실제 주문은
trade_execution.py(23:30 KST, 정규개장 이후)가 이 결과를 읽어서 실행함.

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
import json, os, sys, time
from datetime import datetime, timezone, timedelta

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")  # Windows(cp949) 콘솔 출력 크래시 방지

from kis_common import (
    KST, get_kis_token, get_weekly_ohlcv, calc_ma, calc_atr,
    get_holdings,
)
from get_universe import get_universe

ACCOUNT_NO = os.environ.get("KIS_ACCOUNT_NO", "")
ACCOUNT_PROD = "01"

DATA_DIR = os.path.dirname(__file__)
EXCD_CACHE_FILE = os.path.join(DATA_DIR, "excd_cache.json")
LOOKBACK_WEEKS = 12  # 상대강도 계산에 쓸 기간
MA_PERIOD = 30        # Stage2 판정용 이평선(주)

# [2026-07-09] 신뢰도 강화 4종 세트 — 사용자 확정
RS_TOP_PERCENTILE = 0.30    # 통과 섹터 내에서도 상대강도 상위 30%만 최종 후보 인정
ANOMALY_RS_ABS_THRESHOLD = 1.0  # 12주 상대강도가 ±100%p 넘으면 데이터 이상치로 간주해 제외
                                 # (BNY +1372% 이상치 발견 계기 — 분할 미반영 등 데이터 오류 추정,
                                 #  이 값을 그대로 두면 섹터 평균까지 왜곡되므로 sector_rs 계산 전에 걸러냄)
MIN_AVG_WEEKLY_VOLUME = 1_000_000  # 최근 12주 평균 거래량(주) — 일평균 20만주 미만급 저유동성 종목 제외


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

    spy_ohlcv, spy_excd = get_weekly_ohlcv(token, "SPY", excd_cache.get("SPY"))
    if not spy_ohlcv:
        print("[오류] SPY 벤치마크 데이터를 못 가져옴 — 중단")
        return
    spy_closes = spy_ohlcv["closes"]
    excd_cache["SPY"] = spy_excd

    universe = get_universe()
    print(f"S&P500 {len(universe)}종목 스캔 시작")

    results = []
    ohlcv_by_symbol = {}
    for i, stock in enumerate(universe):
        symbol, sector = stock["symbol"], stock["sector"]
        ohlcv, excd = get_weekly_ohlcv(token, symbol, excd_cache.get(symbol))
        if not ohlcv:
            continue
        excd_cache[symbol] = excd
        ohlcv_by_symbol[symbol] = (ohlcv, excd)
        closes = ohlcv["closes"]
        rs = rel_strength(closes, spy_closes)
        if rs is not None and abs(rs) > ANOMALY_RS_ABS_THRESHOLD:
            print(f"  [이상치 제외] {symbol}: 12주 상대강도 {rs:+.1%} — 데이터 오류로 추정, 후보/섹터평균 계산에서 제외")
            rs = None
        stage2 = is_stage2(closes)
        atr = calc_atr(ohlcv["highs"], ohlcv["lows"], closes)
        vols = ohlcv["volumes"][:LOOKBACK_WEEKS]
        avg_volume = sum(vols) / len(vols) if vols else 0
        results.append({
            "symbol": symbol, "sector": sector, "price": closes[0],
            "rel_strength_12w": rs, "stage2": stage2, "atr": atr,
            "avg_volume": avg_volume,
        })
        if (i + 1) % 50 == 0:
            print(f"  진행: {i+1}/{len(universe)}")
            save_json(EXCD_CACHE_FILE, excd_cache)  # 중간 실패해도 지금까지 캐시는 보존
        time.sleep(0.1)

    save_json(EXCD_CACHE_FILE, excd_cache)

    # ── 보유 중인 포지션의 추세(30주선) 이탈 여부 확인 ──
    # 진입 조건(가격>30주선 AND 30주선 상승)과 대칭 — 주봉 종가가 30주선
    # 아래로 내려가면 Stage2가 깨진 것으로 보고 청산 대상으로 표시.
    # (실제 매도 실행은 trade_execution.py에서, -8% 하드손절은 position_monitor.py에서 별도 처리)
    trend_exit_symbols = []
    if ACCOUNT_NO:
        holdings = get_holdings(token, ACCOUNT_NO, ACCOUNT_PROD)
        for h in holdings:
            symbol = h["symbol"]
            ohlcv = ohlcv_by_symbol.get(symbol)
            if not ohlcv:
                # 유니버스에 없는(S&P500 편출됐거나 스캔 실패한) 보유종목은
                # 별도로 다시 조회해서 확인
                ohlcv_result, _ = get_weekly_ohlcv(token, symbol, excd_cache.get(symbol))
                ohlcv = (ohlcv_result, None)
            data = ohlcv[0]
            if not data:
                print(f"  [경고] 보유종목 {symbol} 주봉 데이터 확보 실패 — 추세 확인 불가")
                continue
            ma30 = calc_ma(data["closes"], MA_PERIOD)
            if ma30 is not None and data["closes"][0] < ma30:
                trend_exit_symbols.append(symbol)
        print(f"추세 이탈 청산 대상: {trend_exit_symbols}")

    # ── 섹터별 상대강도 순위 ──
    sector_rs = {}
    for sector in set(r["sector"] for r in results):
        vals = [r["rel_strength_12w"] for r in results if r["sector"] == sector and r["rel_strength_12w"] is not None]
        if vals:
            sector_rs[sector] = sum(vals) / len(vals)
    ranked_sectors = sorted(sector_rs, key=lambda s: -sector_rs[s])
    top_sectors = set(ranked_sectors[:max(1, len(ranked_sectors) // 2)])
    print(f"섹터 순위(상위 절반만 후보 인정): {[(s, round(sector_rs[s]*100, 1)) for s in ranked_sectors]}")

    # ── RS 상위 30% 문턱 산출 ──
    # 통과 섹터 안에서도 "SPY보다 조금이라도 나으면 통과"(rs>0)는 너무 헐거워서
    # (거의 절반이 통과) 그 안에서도 상대강도 상위 30%만 최종 후보로 인정.
    sector_pool_rs = sorted(
        (r["rel_strength_12w"] for r in results
         if r["sector"] in top_sectors and r["rel_strength_12w"] is not None),
        reverse=True,
    )
    if sector_pool_rs:
        cutoff_count = max(1, round(len(sector_pool_rs) * RS_TOP_PERCENTILE))
        rs_threshold = sector_pool_rs[cutoff_count - 1]
    else:
        rs_threshold = 0
    print(f"RS 상위 {RS_TOP_PERCENTILE:.0%} 문턱: {rs_threshold:+.1%} (통과섹터 내 {len(sector_pool_rs)}종목 중)")

    # ── 최종 후보: 상위 섹터 + RS 상위 30% + Stage2 + 최소 유동성 ──
    candidates = [
        r for r in results
        if r["sector"] in top_sectors
        and r["rel_strength_12w"] is not None and r["rel_strength_12w"] > 0
        and r["rel_strength_12w"] >= rs_threshold
        and r["stage2"]
        and r["avg_volume"] >= MIN_AVG_WEEKLY_VOLUME
    ]
    candidates.sort(key=lambda r: -r["rel_strength_12w"])

    today_str = datetime.now(KST).strftime("%Y%m%d")
    out_path = os.path.join(DATA_DIR, f"candidates_{today_str}.json")
    save_json(out_path, {
        "date": today_str,
        "sector_ranking": [{"sector": s, "avg_rel_strength_12w": round(sector_rs[s], 4)} for s in ranked_sectors],
        "candidates": candidates,
        "trend_exit_symbols": trend_exit_symbols,
    })
    print(f"최종 후보 {len(candidates)}종목: {[c['symbol'] for c in candidates]}")


if __name__ == "__main__":
    main()

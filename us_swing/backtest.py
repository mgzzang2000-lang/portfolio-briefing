#!/usr/bin/env python3
"""
미국주식 스윙 전략 백테스트 — 실거래 파이프라인(cron으로 도는 3개 스크립트)과는
완전히 분리된 리서치 전용 스크립트. 실거래 코드에 영향 안 줌, 수동 실행만.

신호 판정 로직(상대강도/Stage2)은 scan_signals.py / kis_common.py에서 그대로
import해서 재사용 — 백테스트 따로, 실거래 따로 로직이 갈라지는 걸 방지하기 위함.
다만 데이터 소스는 KIS API 대신 yfinance를 씀 — KIS 주봉 API는 최대 100주
(~2년)만 제공해서 장기 백테스트에 부족하기 때문(kis_common.get_weekly_ohlcv
docstring 참고). 대신 KIS로 실제 매매되는 계산과 100% 동일한 함수를 쓰므로
로직 자체는 실거래와 같음.

한계(결과 해석 시 참고):
  - 생존편향: 현재 S&P500 구성종목 리스트만 쓰므로, 과거에 지수에서 편출된
    (즉 성과가 나빠서 빠진) 종목은 백테스트에 아예 없음 → 실제보다 성과가
    좋게 나올 가능성이 있음.
  - 하드손절(-8%)을 주봉 저가로만 확인 — 실거래는 매시간 체크하므로 주중
    급락 시 실제보다 더 유리한 가격에 손절된 것처럼 보일 수 있음.
  - 슬리피지/지정가 미체결 가능성 반영 안 함 — 주봉 종가로 체결 가정.

[2026-07-09] 신설.
"""
import json, os, sys
from datetime import datetime

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

# kis_common은 모듈 import 시점에 KIS_APP_KEY/SECRET 환경변수를 요구함(네트워크
# 호출은 안 하지만 top-level에서 os.environ[...] 읽음) — 백테스트는 API 호출을
# 안 하므로 더미 값으로 충분.
os.environ.setdefault("KIS_APP_KEY", "backtest-dummy")
os.environ.setdefault("KIS_APP_SECRET", "backtest-dummy")

from kis_common import calc_ma, calc_atr
from scan_signals import (
    rel_strength, is_stage2, LOOKBACK_WEEKS, MA_PERIOD,
    RS_TOP_PERCENTILE, ANOMALY_RS_ABS_THRESHOLD, MIN_AVG_WEEKLY_VOLUME,
)
from get_universe import get_universe

# [2026-07-09] 실거래 로직(scan_signals.py/trade_execution.py)에 반영된 신뢰도
# 강화 4종(RS상위30%/이상치제외/거래량필터/섹터당1종목)을 백테스트도 그대로
# 반영 — 그래야 여기서 나온 승률/손익 숫자가 실제 돌아가는 로직과 일치함.
MAX_POSITIONS = 3
STOP_LOSS_FLOOR_PCT = 0.10  # [코드리뷰 수정] trade_execution.py의 현재 값(0.10)과 동기화.
                             # main()의 그리드서치는 항상 run_backtest(stop_loss_floor_pct=...)로
                             # 명시 override하므로 그리드 결과엔 영향 없었지만, run_backtest()를
                             # 파라미터 없이 직접 호출하면(REPL 등) 이 기본값이 조용히 쓰이므로 동기화.
ATR_MULT = 2.0
WARMUP_WEEKS = 40  # MA_PERIOD(30)+여유 — 신호 계산에 필요한 최소 과거 주수
YEARS = "6y"


def yf_symbol(symbol):
    # yfinance는 우선주/클래스주 표기가 KIS와 다름 (예: BRK.B -> BRK-B)
    return symbol.replace(".", "-")


def fetch_all_weekly(symbols):
    import yfinance as yf
    tickers = [yf_symbol(s) for s in symbols]
    print(f"yfinance 다운로드 시작: {len(tickers)}종목, 기간 {YEARS}, 주봉")
    df = yf.download(tickers, period=YEARS, interval="1wk",
                      group_by="ticker", auto_adjust=True,
                      progress=False, threads=True)
    return df


def extract_series(df, symbol):
    ysym = yf_symbol(symbol)
    try:
        sub = df[ysym]
    except KeyError:
        return None
    closes = sub["Close"]
    highs = sub["High"]
    lows = sub["Low"]
    volumes = sub["Volume"]
    if closes.dropna().shape[0] < WARMUP_WEEKS + 10:
        return None
    return {"close": closes, "high": highs, "low": lows, "volume": volumes}


def window_desc(series_dict, key, t, span):
    """t시점까지(포함) 최근 span개를 최신순(index0=latest)으로 반환. NaN 있으면 None."""
    vals = series_dict[key].iloc[max(0, t - span + 1): t + 1].tolist()
    if any(v != v for v in vals):  # NaN 체크
        return None
    return list(reversed(vals))


def compute_signal(series_by_symbol, symbol, t, need):
    s = series_by_symbol.get(symbol)
    if s is None or t >= len(s["close"]):
        return None
    closes = window_desc(s, "close", t, need)
    highs = window_desc(s, "high", t, need)
    lows = window_desc(s, "low", t, need)
    if closes is None or highs is None or lows is None:
        return None
    return closes, highs, lows


def run_backtest(universe, series_by_symbol, spy_series, take_profit_pct=None,
                  stop_loss_floor_pct=STOP_LOSS_FLOOR_PCT,
                  partial_tp_pct=None, partial_fraction=0.5, ratchet_pct=None):
    """partial_tp_pct: 이 수익률 도달 시 partial_fraction만큼 부분 익절.
    ratchet_pct: 부분 익절 이후 남은 물량의 손절가를 entry_price*(1+ratchet_pct)로
    올림(0.0=본절, 0.05=+5% 등). None이면 손절가 유지(래칫 없음)."""
    n_weeks = len(spy_series["close"])
    need = MA_PERIOD + 10  # is_stage2가 필요로 하는 최소 과거 길이(여유 포함)

    symbols_by_sector = {}
    symbol_to_sector = {}
    for u in universe:
        symbols_by_sector.setdefault(u["sector"], []).append(u["symbol"])
        symbol_to_sector[u["symbol"]] = u["sector"]

    positions = {}  # symbol -> dict
    trades = []

    for t in range(WARMUP_WEEKS, n_weeks):
        spy_closes = window_desc(spy_series, "close", t, LOOKBACK_WEEKS + 1)
        if spy_closes is None:
            continue

        # ── 1. 보유 포지션 청산 체크 (하드손절 > 익절 > 추세이탈 우선순위) ──
        for symbol in list(positions.keys()):
            s = series_by_symbol.get(symbol)
            if s is None or t >= len(s["close"]):
                continue
            close_t = s["close"].iloc[t]
            low_t = s["low"].iloc[t]
            high_t = s["high"].iloc[t]
            if close_t != close_t:  # NaN(상장폐지/데이터 없음) — 보유 중 소실, 마지막 유효가로 청산 처리
                continue
            pos = positions[symbol]

            # [코드리뷰 수정] 우선순위를 손절 > 최종익절 > 분할익절 > 추세이탈 순으로
            # 정렬 — 원래는 분할익절 체크가 손절 체크보다 먼저 실행돼서, 같은 주에
            # 저가가 손절가를 뚫고 고가가 분할익절가도 뚫은(변동성 큰) 주를 "손절"이
            # 아니라 "부분익절 성공"으로 잘못 집계하고 있었음. position_monitor.py의
            # 실거래 우선순위(손절 > 최종익절 > 분할익절)와 반드시 일치시켜야 함.
            exit_price, reason = None, None
            if low_t <= pos["stop_price"]:
                exit_price, reason = pos["stop_price"], "hard_stop"
            elif take_profit_pct and high_t >= pos["tp_price"]:
                exit_price, reason = pos["tp_price"], "take_profit"
            elif (partial_tp_pct and not pos.get("partial_taken")
                    and high_t >= pos["entry_price"] * (1 + partial_tp_pct)):
                # 분할 익절 트리거 — 이번 주에 처음 도달했으면 부분청산만 기록하고
                # 나머지 청산조건(손절/익절/추세이탈) 판정은 다음 주부터 (같은 주 이중처리 방지)
                pos["partial_taken"] = True
                pos["partial_fraction"] = partial_fraction
                pos["partial_return"] = partial_tp_pct
                if ratchet_pct is not None:
                    pos["stop_price"] = max(pos["stop_price"], pos["entry_price"] * (1 + ratchet_pct))
                continue
            else:
                closes_desc = window_desc(s, "close", t, need)
                if closes_desc is not None:
                    ma30 = calc_ma(closes_desc, MA_PERIOD)
                    if ma30 is not None and close_t < ma30:
                        exit_price, reason = close_t, "trend_exit"
            if exit_price is not None:
                final_leg_return = exit_price / pos["entry_price"] - 1
                if pos.get("partial_taken"):
                    frac = pos["partial_fraction"]
                    ret_pct = frac * pos["partial_return"] + (1 - frac) * final_leg_return
                    reason = f"partial+{reason}"
                else:
                    ret_pct = final_leg_return
                trades.append({
                    "symbol": symbol, "entry_week": pos["entry_week"], "exit_week": t,
                    "entry_price": pos["entry_price"], "exit_price": exit_price,
                    "return_pct": ret_pct, "reason": reason,
                    "hold_weeks": t - pos["entry_week"],
                })
                positions.pop(symbol)

        free_slots = MAX_POSITIONS - len(positions)
        if free_slots <= 0:
            continue

        # ── 2. 섹터 상대강도 순위 (이상치는 sector_rs 계산 전에 제외) ──
        sector_rs = {}
        stock_rs_cache = {}
        for sector, syms in symbols_by_sector.items():
            vals = []
            for symbol in syms:
                sig = compute_signal(series_by_symbol, symbol, t, need)
                if sig is None:
                    continue
                closes, highs, lows = sig
                rs = rel_strength(closes, spy_closes)
                if rs is not None and abs(rs) > ANOMALY_RS_ABS_THRESHOLD:
                    rs = None  # 데이터 이상치로 간주 — 후보/섹터평균 둘 다 제외
                stock_rs_cache[symbol] = (rs, closes, highs, lows)
                if rs is not None:
                    vals.append(rs)
            if vals:
                sector_rs[sector] = sum(vals) / len(vals)
        if not sector_rs:
            continue
        ranked_sectors = sorted(sector_rs, key=lambda s_: -sector_rs[s_])
        top_sectors = set(ranked_sectors[:max(1, len(ranked_sectors) // 2)])

        # ── 3. RS 상위 30% 문턱 ──
        sector_pool_rs = sorted(
            (v[0] for sym, v in stock_rs_cache.items()
             if v[0] is not None and symbol_to_sector.get(sym) in top_sectors),
            reverse=True,
        )
        if sector_pool_rs:
            cutoff_count = max(1, round(len(sector_pool_rs) * RS_TOP_PERCENTILE))
            rs_threshold = sector_pool_rs[cutoff_count - 1]
        else:
            rs_threshold = 0

        # ── 4. 최종 후보: 상위 섹터 + RS 상위30% + Stage2 + 최소 유동성 ──
        candidates = []
        for u in universe:
            symbol, sector = u["symbol"], u["sector"]
            if symbol in positions or sector not in top_sectors:
                continue
            cached = stock_rs_cache.get(symbol)
            if not cached:
                continue
            rs, closes, highs, lows = cached
            if rs is None or rs <= 0 or rs < rs_threshold:
                continue
            if not is_stage2(closes):
                continue
            s = series_by_symbol[symbol]
            vols = s["volume"].iloc[max(0, t - LOOKBACK_WEEKS + 1): t + 1]
            avg_volume = vols.mean() if len(vols) else 0
            if avg_volume != avg_volume or avg_volume < MIN_AVG_WEEKLY_VOLUME:  # NaN 또는 유동성 부족
                continue
            atr = calc_atr(highs, lows, closes)
            candidates.append({"symbol": symbol, "sector": sector, "rs": rs, "atr": atr, "price": closes[0]})
        candidates.sort(key=lambda c: -c["rs"])

        # ── 5. 신규 진입 (섹터당 최대 1종목) ──
        held_sectors = {symbol_to_sector.get(sym) for sym in positions}
        bought_this_round = 0
        for c in candidates:
            if bought_this_round >= free_slots:
                break
            if c["sector"] in held_sectors:
                continue
            entry_price = c["price"]
            floor_price = entry_price * (1 - stop_loss_floor_pct)
            if c["atr"]:
                stop_price = max(entry_price - c["atr"] * ATR_MULT, floor_price)
            else:
                stop_price = floor_price
            pos = {"entry_week": t, "entry_price": entry_price, "stop_price": stop_price}
            if take_profit_pct:
                pos["tp_price"] = entry_price * (1 + take_profit_pct)
            positions[c["symbol"]] = pos
            held_sectors.add(c["sector"])
            bought_this_round += 1

    still_open = len(positions)
    return trades, still_open


def summarize(trades, still_open, label):
    print(f"\n{'='*60}\n[{label}]\n{'='*60}")
    if not trades:
        print("완결된 거래 없음")
        return
    n = len(trades)
    wins = [t for t in trades if t["return_pct"] > 0]
    losses = [t for t in trades if t["return_pct"] <= 0]
    win_rate = len(wins) / n
    avg_win = sum(t["return_pct"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["return_pct"] for t in losses) / len(losses) if losses else 0
    expectancy = sum(t["return_pct"] for t in trades) / n
    gross_win = sum(t["return_pct"] for t in wins)
    gross_loss = -sum(t["return_pct"] for t in losses)
    profit_factor = gross_win / gross_loss if gross_loss > 0 else float("inf")
    avg_hold = sum(t["hold_weeks"] for t in trades) / n

    # 고정 사이즈 가정 누적수익률로 근사 최대낙폭(체결완료 거래만, 동시보유 미반영 근사치)
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for t in sorted(trades, key=lambda x: x["exit_week"]):
        equity *= (1 + t["return_pct"] / MAX_POSITIONS)  # 슬롯당 1/3 비중 근사
        peak = max(peak, equity)
        max_dd = max(max_dd, (peak - equity) / peak)

    reason_counts = {}
    for t in trades:
        reason_counts[t["reason"]] = reason_counts.get(t["reason"], 0) + 1

    # 상위 3개 거래가 전체 '승리 수익 합계'에서 차지하는 비중 — 이게 높을수록
    # 몇 개의 대박 거래에 결과 전체가 좌우된다는 뜻(꾸준함과는 반대 성격).
    top3_concentration = None
    if wins:
        top3_sum = sum(t["return_pct"] for t in sorted(wins, key=lambda t: -t["return_pct"])[:3])
        total_win_sum = sum(t["return_pct"] for t in wins)
        top3_concentration = top3_sum / total_win_sum if total_win_sum > 0 else None

    print(f"완결 거래 수: {n}건 (백테스트 종료 시점 보유중 {still_open}건 제외)")
    print(f"승률: {win_rate:.1%}  (승 {len(wins)} / 패 {len(losses)})")
    print(f"평균 수익(승): {avg_win:+.1%}   평균 손실(패): {avg_loss:+.1%}")
    print(f"기대값(거래당 평균 수익률): {expectancy:+.2%}")
    print(f"손익비(Profit Factor): {profit_factor:.2f}")
    print(f"평균 보유기간: {avg_hold:.1f}주")
    print(f"근사 최대낙폭(3종목 균등비중 가정): {max_dd:.1%}")
    print(f"근사 누적수익률(3종목 균등비중, 복리 가정): {(equity-1):+.1%}")
    if top3_concentration is not None:
        print(f"상위3개 거래의 '승리수익 합계' 내 비중: {top3_concentration:.1%}  (높을수록 소수 대박거래 의존도 높음)")
    print(f"청산 사유 분포: {reason_counts}")
    return {"label": label, "n": n, "win_rate": win_rate, "expectancy": expectancy,
            "profit_factor": profit_factor, "max_dd": max_dd, "total_return": equity - 1,
            "top3_concentration": top3_concentration}


def main():
    universe = get_universe()
    symbols = [u["symbol"] for u in universe]
    print(f"유니버스: {len(symbols)}종목")

    df = fetch_all_weekly(["SPY"] + symbols)

    spy_series = extract_series(df, "SPY")
    if spy_series is None:
        print("[오류] SPY 데이터 확보 실패 — 중단")
        return

    series_by_symbol = {}
    skipped = 0
    for u in universe:
        s = extract_series(df, u["symbol"])
        if s is not None:
            series_by_symbol[u["symbol"]] = s
        else:
            skipped += 1
    print(f"데이터 확보: {len(series_by_symbol)}종목 (스킵 {skipped}종목 — 상장기간 짧거나 다운로드 실패)")

    print(f"백테스트 기간: 약 {len(spy_series['close']) - WARMUP_WEEKS}주 (워밍업 {WARMUP_WEEKS}주 제외)")

    # ── 익절×손절 3x3 그리드 비교 (사용자 확정: 익절 16/20/30%, 손절 10/8/4%) ──
    tp_grid = [0.16, 0.20, 0.30]
    sl_grid = [0.10, 0.08, 0.04]
    variants = [
        (tp, sl, f"익절+{tp:.0%} / 손절-{sl:.0%}")
        for sl in sl_grid for tp in tp_grid
    ]
    results = {}
    summary_rows = []
    for tp, sl, label in variants:
        trades, still_open = run_backtest(universe, series_by_symbol, spy_series,
                                           take_profit_pct=tp, stop_loss_floor_pct=sl)
        row = summarize(trades, still_open, label)
        results[label] = trades
        if row:
            row["tp"], row["sl"] = tp, sl
            summary_rows.append(row)

    summary_rows.sort(key=lambda r: -r["total_return"])
    print(f"\n{'='*100}\n[전체 비교 — 누적수익률 높은 순]\n{'='*100}")
    print(f"{'변형':22s} {'거래수':>6s} {'승률':>7s} {'기대값':>8s} {'손익비':>7s} "
          f"{'낙폭':>7s} {'누적수익률':>10s} {'상위3집중도':>9s}")
    for r in summary_rows:
        conc = f"{r['top3_concentration']:.1%}" if r["top3_concentration"] is not None else "-"
        print(f"{r['label']:22s} {r['n']:>6d} {r['win_rate']:>6.1%} {r['expectancy']:>+7.2%} "
              f"{r['profit_factor']:>7.2f} {r['max_dd']:>6.1%} {r['total_return']:>+9.1%} {conc:>9s}")

    out_path = os.path.join(os.path.dirname(__file__), "backtest_results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({
            label: [{k: (round(v, 4) if isinstance(v, float) else v) for k, v in t.items()} for t in trades]
            for label, trades in results.items()
        }, f, ensure_ascii=False, indent=2)
    print(f"\n상세 거래내역 저장: {out_path}")


if __name__ == "__main__":
    main()

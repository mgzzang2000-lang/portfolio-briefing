#!/usr/bin/env python3
"""
1분봉 기반 백테스트 - 시간대별 진입/청산 규칙 검증
=====================================================
[중요] KIS 분봉 API(inquire-time-itemchartprice)가 "당일"이 아닌 과거
특정 날짜의 1분봉을 실제로 내려주는지는 이 스크립트만으로 보장할 수
없다. FID_INPUT_DATE_1에 과거 날짜를 넣어 요청하도록 구현은 해뒀지만,
실행 시 특정 종목/날짜에서 output2가 비어 있다면 그 날짜의 분봉을
KIS가 지원하지 않는다는 뜻이다. 그 경우 콘솔에 경고가 출력되며 해당
거래일은 스킵된다 (뒤에 CAVEATS 참고).

검증 대상 규칙 (2026-07-02 합의 사항)
-----------------------------------------------------
[진입 - 3단계 시간대]
  09:00~09:10 : 갭 < 3%           (공통 필터 통과 종목 대상)
  09:10~11:00 : 갭 < 5%  AND (매집패턴 OR 눌림목후재돌파)
  11:00~14:00 : 신규 진입 없음
  14:00~14:30 : 갭 조건 대신 "당일 고가 98% 이상 근접" 조건으로 대체
  14:30~15:30 : 신규 진입 없음

  공통 필터(일봉 기준, 기존 유지): MA20/MA200 상단, MACD 골든크로스
  5일 이내, BB 스퀴즈 또는 BB상단 근접, 거래량 20일평균 대비 2배 이상

  매집패턴 : 당일 등락률 -1%~+3% 구간 + 거래량 페이스가 20일 평균 대비
             2배 이상(경과 시간 대비 누적거래량으로 추정)
  눌림목후재돌파 : 최근 20분 롤링 고점 대비 -1% 이상 눌림 후, 그 롤링
             고점을 다시 돌파

[청산]
  기본  : ATR(14, 1분봉) x 1.5 손절, 최소 손절폭 하한 -1.5%
  익절  : 진입가 대비 +4%
  트레일링 : 미실현 수익 +2% 도달 시 손절선 -> 진입가+0.4%(본전+α)로 상향
             (그 이전엔 ATR 손절 유지, 이후엔 max(ATR손절, 본전+α) 사용)
  시간규칙 : 진입 후 정확히 2시간 경과 시점 수익률이 0%~+1% 구간이면
             50% 부분청산. 잔량 50%는 손절선을 즉시 본전+0.4%로 고정.
             (-1%~0% 구간이면 이 규칙은 발동하지 않음 -> 이 규칙으로
              인한 손실 실현은 절대 없음)
  강제청산 : 15:20 시장가 전량 청산

CAVEATS
-----------------------------------------------------
1. KIS 분봉 API의 과거 날짜 지원 여부가 검증되지 않음 (위 설명 참고).
2. 매집패턴의 "거래량 페이스"는 (누적거래량/경과분) vs (20일평균거래량/390분)
   비율로 근사한 것이며, 실제 세력 매집과 100% 일치하지 않을 수 있음.
3. 단일 포지션 구조를 유지 (기존과 동일) - 후보가 여러 개여도 그날 최초
   1건만 진입.
4. 체결 슬리피지/수수료는 반영하지 않음 (기존 backtest.py와 동일 가정).
=====================================================
"""
import os, json, time, requests
from datetime import datetime, timezone, timedelta, date, time as dtime

KST      = timezone(timedelta(hours=9))
BASE_URL = "https://openapi.koreainvestment.com:9443"
MAX_BET  = 500_000
KIS_APP_KEY    = os.environ['KIS_APP_KEY']
KIS_APP_SECRET = os.environ['KIS_APP_SECRET']
TOKEN_FILE     = "kis_token.json"

BACKTEST_DAYS = [
    date(2026, 6, 23),
    date(2026, 6, 24),
    date(2026, 6, 25),
    date(2026, 6, 26),
    date(2026, 6, 27),
]
DATA_START   = "20260101"
BACKTEST_END = "20260627"

TRADING_MINUTES_PER_DAY = 390  # 09:00~15:30

# -- 시간대 경계 --
T_0900 = dtime(9, 0)
T_0910 = dtime(9, 10)
T_1100 = dtime(11, 0)
T_1400 = dtime(14, 0)
T_1430 = dtime(14, 30)
T_1520 = dtime(15, 20)
T_1530 = dtime(15, 30)

# ── KIS 인증 / 공통 GET (backtest.py와 동일) ─────────────────────
def get_kis_token():
    try:
        with open(TOKEN_FILE, 'r') as f:
            cached = json.load(f)
        issued_at = datetime.fromisoformat(cached['issued_at'])
        age = (datetime.now(KST) - issued_at).total_seconds()
        if age < 23 * 3600 and cached.get('access_token'):
            return cached['access_token']
    except Exception:
        pass
    r = requests.post(f"{BASE_URL}/oauth2/tokenP", json={
        "grant_type": "client_credentials",
        "appkey": KIS_APP_KEY, "appsecret": KIS_APP_SECRET
    }, timeout=10)
    data = r.json()
    if 'access_token' not in data:
        raise Exception(f"KIS 토큰 오류: {data}")
    with open(TOKEN_FILE, 'w') as f:
        json.dump({'access_token': data['access_token'],
                   'issued_at': datetime.now(KST).isoformat()}, f)
    return data['access_token']

def kis_get(token, path, params, tr_id, retries=3):
    headers = {
        "authorization": f"Bearer {token}",
        "appkey": KIS_APP_KEY, "appsecret": KIS_APP_SECRET,
        "tr_id": tr_id, "custtype": "P"
    }
    for attempt in range(retries):
        try:
            r = requests.get(f"{BASE_URL}{path}", headers=headers,
                             params=params, timeout=10)
            if not r.text.strip():
                return {}
            return r.json()
        except Exception:
            if attempt < retries - 1:
                time.sleep(2)
    return {}

# ── 유니버스 / 일봉 데이터 ────────────────────────────────────────
def get_volume_rank(token, market="J"):
    scr_code = "20172" if market == "Q" else "20171"
    data = kis_get(token, "/uapi/domestic-stock/v1/quotations/volume-rank", {
        "FID_COND_MRKT_DIV_CODE": market,
        "FID_COND_SCR_DIV_CODE": scr_code,
        "FID_INPUT_ISCD": "0000",
        "FID_DIV_CLS_CODE": "0", "FID_BLNG_CLS_CODE": "0",
        "FID_TRGT_CLS_CODE": "111111111",
        "FID_TRGT_EXLS_CLS_CODE": "000000",
        "FID_INPUT_PRICE_1": "", "FID_INPUT_PRICE_2": "",
        "FID_VOL_CNT": "", "FID_INPUT_DATE_1": ""
    }, "FHPST01710000")
    return [x['mksc_shrn_iscd'] for x in data.get('output', [])[:100]]

def get_daily_raw(token, code, market="J"):
    data = kis_get(token, "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice", {
        "FID_COND_MRKT_DIV_CODE": market,
        "FID_INPUT_ISCD": code,
        "FID_INPUT_DATE_1": DATA_START,
        "FID_INPUT_DATE_2": BACKTEST_END,
        "FID_PERIOD_DIV_CODE": "D",
        "FID_ORG_ADJ_PRC": "0",
    }, "FHKST03010100")
    rows = data.get('output2', [])
    if not rows and market == "J":
        return get_daily_raw(token, code, market="Q")
    return rows

def get_minute_bars_for_date(token, code, ymd, market="J"):
    """
    특정 과거 날짜(ymd, 'YYYYMMDD')의 1분봉 전체를 반환 (시간 오름차순).
    KIS 분봉 API가 과거 날짜를 지원하지 않으면 빈 리스트를 반환한다.
    한 번의 호출로 최근 30건 정도만 오는 경우가 많아, 09:00부터
    30분 단위로 FID_INPUT_HOUR_1을 옮겨가며 여러 번 호출해 하루 전체를
    이어붙인다.
    """
    all_rows = {}
    # 09:00 ~ 15:30 을 30분 간격으로 순회하며 각 시점 기준 최근 봉을 수집
    checkpoints = []
    t = datetime.combine(datetime.strptime(ymd, "%Y%m%d"), T_0900)
    end = datetime.combine(datetime.strptime(ymd, "%Y%m%d"), T_1530)
    while t <= end:
        checkpoints.append(t.strftime('%H%M%S'))
        t += timedelta(minutes=30)
    for hhmmss in checkpoints:
        data = kis_get(token, "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice", {
            "FID_COND_MRKT_DIV_CODE": market,
            "FID_INPUT_ISCD": code,
            "FID_INPUT_HOUR_1": hhmmss,
            "FID_PW_DATA_INCU_YN": "Y",
            "FID_ETC_CLS_CODE": "0",
            "FID_INPUT_DATE_1": ymd,
        }, "FHKST03010200")
        for row in data.get('output2', []):
            hms = row.get('stck_cntg_hour', '')
            if hms:
                all_rows[hms] = row
        time.sleep(0.05)
    if not all_rows:
        return []
    ordered = [all_rows[k] for k in sorted(all_rows.keys())]
    return ordered

# ── 지표 계산 (backtest.py와 공통) ────────────────────────────────
def calc_ma(closes, period):
    if len(closes) < period:
        return None
    return sum(closes[:period]) / period

def calc_macd(closes, fast=12, slow=26, signal=9):
    if len(closes) < slow + signal + 6:
        return None, None, None
    asc = list(reversed(closes))
    n = len(asc)
    kf, ks, ksig = 2/(fast+1), 2/(slow+1), 2/(signal+1)
    ef = sum(asc[:fast]) / fast
    es = sum(asc[:slow]) / slow
    for i in range(fast, slow):
        ef = asc[i]*kf + ef*(1-kf)
    macd_s = []
    for i in range(slow, n):
        ef = asc[i]*kf + ef*(1-kf)
        es = asc[i]*ks + es*(1-ks)
        macd_s.append(ef - es)
    if len(macd_s) < signal:
        return None, None, None
    sig = sum(macd_s[:signal]) / signal
    sig_s = [sig]
    for v in macd_s[signal:]:
        sig = v*ksig + sig*(1-ksig)
        sig_s.append(sig)
    golden = None
    check = min(6, len(macd_s)-1, len(sig_s)-1)
    for i in range(1, check+1):
        if macd_s[-(i+1)] < sig_s[-(i+1)] and macd_s[-i] >= sig_s[-i]:
            golden = i
            break
    return macd_s[-1], sig_s[-1], golden

def calc_bb(closes, period=20, mult=2.0):
    if len(closes) < period:
        return None, None, None, None
    d = closes[:period]
    mid = sum(d) / period
    std = (sum((x-mid)**2 for x in d) / period) ** 0.5
    upper = mid + mult*std
    lower = mid - mult*std
    bw = (upper-lower)/mid if mid else 0
    return upper, mid, lower, bw

def is_bb_squeeze(closes, period=20, lookback=20):
    if len(closes) < period + lookback:
        return False
    _, _, _, bw_now = calc_bb(closes, period)
    if bw_now is None:
        return False
    hist = []
    for i in range(1, lookback+1):
        _, _, _, bw = calc_bb(closes[i:], period)
        if bw is not None:
            hist.append(bw)
    if not hist:
        return False
    threshold = sorted(hist)[int(len(hist)*0.3)]
    return bw_now <= threshold

def calc_atr_from_minutes(minute_bars_desc, period=14):
    """1분봉(최신이 index0) 기준 ATR"""
    if len(minute_bars_desc) < period + 1:
        return None
    highs  = [float(b['stck_hgpr']) for b in minute_bars_desc]
    lows   = [float(b['stck_lwpr']) for b in minute_bars_desc]
    closes = [float(b['stck_prpr']) for b in minute_bars_desc]
    trs = [max(highs[i]-lows[i],
               abs(highs[i]-closes[i+1]),
               abs(lows[i]-closes[i+1])) for i in range(period)]
    return sum(trs) / period

# ── [진입 보조] 매집패턴 / 눌림목 후 재돌파 ───────────────────────
def check_accumulation_pattern(cum_vol, elapsed_minutes, vol_avg20, day_pct_change):
    """매집패턴: 등락률 -1%~+3% + 거래량 페이스가 20일 평균 대비 2배 이상"""
    if elapsed_minutes <= 0 or not vol_avg20:
        return False
    if not (-1.0 <= day_pct_change <= 3.0):
        return False
    pace_today = cum_vol / elapsed_minutes
    pace_avg   = vol_avg20 / TRADING_MINUTES_PER_DAY
    if pace_avg <= 0:
        return False
    return (pace_today / pace_avg) >= 2.0

def check_pullback_rebreak(bars_asc, lookback=20, pullback_pct=1.0):
    """눌림목 후 재돌파: 최근 lookback분 롤링고점 대비 -pullback_pct% 눌림 후 재돌파"""
    if len(bars_asc) < lookback + 3:
        return False
    closes = [float(b['stck_prpr']) for b in bars_asc]
    window = closes[-(lookback+1):-1]
    if not window:
        return False
    roll_high = max(window)
    cur = closes[-1]
    min_in_window = min(window)
    pulled_back = (roll_high - min_in_window) / roll_high * 100 >= pullback_pct
    rebroke = cur >= roll_high
    return pulled_back and rebroke

# ── 진입 시간대 판정 ──────────────────────────────────────────────
def entry_window(t):
    """t: datetime.time. 반환: 'tier1'|'tier2'|'tier3'|None"""
    if T_0900 <= t < T_0910:
        return 'tier1'
    if T_0910 <= t < T_1100:
        return 'tier2'
    if T_1400 <= t < T_1430:
        return 'tier3'
    return None

def passes_entry_filter(tier, today_open, prev_close, cur_price, day_high_so_far,
                         cum_vol, elapsed_minutes, vol_avg20, bars_asc):
    gap = (today_open - prev_close) / prev_close * 100
    day_pct_change = (cur_price - prev_close) / prev_close * 100
    if tier == 'tier1':
        return gap < 3.0
    if tier == 'tier2':
        if gap >= 5.0:
            return False
        accum = check_accumulation_pattern(cum_vol, elapsed_minutes, vol_avg20, day_pct_change)
        pullback = check_pullback_rebreak(bars_asc)
        return accum or pullback
    if tier == 'tier3':
        if day_high_so_far <= 0:
            return False
        return cur_price >= day_high_so_far * 0.98
    return False

# ── 청산 규칙 ─────────────────────────────────────────────────────
def apply_exit_rules(position, cur_price, cur_high, cur_low, now_dt, elapsed_since_entry):
    """
    position: dict(entry, qty, stop, target, entry_dt, breakeven_applied, half_exited)
    반환: (action, detail)
      action in {None, 'STOP', 'TARGET', 'TIME_PARTIAL', 'FORCED'}
    """
    entry = position['entry']
    pnl_pct = (cur_price - entry) / entry * 100

    # 1) 트레일링: +2% 도달 시 손절선을 본전+0.4%로 상향 (한 번만 적용)
    if not position.get('breakeven_applied') and pnl_pct >= 2.0:
        breakeven_stop = entry * 1.004
        position['stop'] = max(position['stop'], breakeven_stop)
        position['breakeven_applied'] = True

    # 2) 2시간 경과 판정 (하루에 한 번만 체크)
    if not position.get('time_rule_checked') and elapsed_since_entry >= timedelta(hours=2):
        position['time_rule_checked'] = True
        if 0.0 <= pnl_pct <= 1.0:
            position['stop'] = max(position['stop'], entry * 1.004)
            position['breakeven_applied'] = True
            return 'TIME_PARTIAL', pnl_pct
        # -1%~0% 구간이면 아무 것도 하지 않음 (손실 실현 규칙 없음)

    # 3) 강제청산 15:20
    if now_dt.time() >= T_1520:
        return 'FORCED', pnl_pct

    # 4) 익절 / 손절 (해당 분봉의 고가/저가로 체결 가정)
    if cur_high >= position['target']:
        return 'TARGET', pnl_pct
    if cur_low <= position['stop']:
        return 'STOP', pnl_pct

    return None, pnl_pct

# ── 메인 시뮬레이션 ───────────────────────────────────────────────
def run_intraday_backtest():
    print("=" * 60)
    print("  1분봉 백테스트: 시간대별 진입 + 트레일링/시간청산 규칙")
    print("=" * 60)
    token = get_kis_token()

    print("\n[1] 스캔 대상 수집 중...")
    kospi  = get_volume_rank(token, "J")
    time.sleep(1)
    kosdaq = get_volume_rank(token, "Q")
    kospi_set = set(kospi)
    universe = list(dict.fromkeys(kospi + kosdaq))
    scan_list = universe[:60]  # 분봉 호출 비용이 크므로 상위 60종목으로 축소
    print(f"    스캔 대상 {len(scan_list)}종목")

    print("\n[2] 일봉 데이터 로딩 중 (신호 계산용)...")
    daily = {}
    for i, code in enumerate(scan_list):
        market = "J" if code in kospi_set else "Q"
        rows = get_daily_raw(token, code, market)
        if len(rows) >= 42:
            daily[code] = {'rows': rows, 'market': market}
        time.sleep(0.08)
    print(f"    유효 종목: {len(daily)}개")
    date_index = {c: {r['stck_bsop_date']: i for i, r in enumerate(v['rows'])}
                  for c, v in daily.items()}

    portfolio = {'cash': MAX_BET, 'position': None}
    trade_log = []
    skipped_days = []

    for bday in BACKTEST_DAYS:
        day_str = bday.strftime('%Y%m%d')
        day_disp = bday.strftime('%m/%d(%a)')
        print(f"\n--- {day_disp} ---")

        # 이 날짜 신호 통과 후보 산출 (일봉 기준, tier 무관 공통 필터)
        eligible = {}
        for code, v in daily.items():
            rows = v['rows']
            if day_str not in date_index[code]:
                continue
            idx = date_index[code][day_str]
            today = rows[idx]
            past = rows[idx+1:]
            if len(past) < 30:
                continue
            prev_close = float(past[0].get('stck_clpr', 0))
            today_open = float(today.get('stck_oprc', 0))
            if prev_close == 0 or today_open == 0:
                continue
            closes = [float(r['stck_clpr']) for r in past]
            volumes = [int(r.get('acml_vol', 0)) for r in past]
            ma20 = calc_ma(closes, 20)
            ma200 = calc_ma(closes, 200)
            macd, sig_line, golden = calc_macd(closes)
            bb_upper, _, _, _ = calc_bb(closes, 20)
            squeeze = is_bb_squeeze(closes)
            if ma20 is None or macd is None or bb_upper is None:
                continue
            today_close = float(today.get('stck_clpr', 0))
            if today_close <= ma20:
                continue
            if ma200 and today_close <= ma200:
                continue
            if golden is None or macd <= sig_line:
                continue
            if not (squeeze or today_close >= bb_upper * 0.98):
                continue
            vol_avg20 = sum(volumes[:20]) / 20 if len(volumes) >= 20 else None
            eligible[code] = {'prev_close': prev_close, 'today_open': today_open,
                               'vol_avg20': vol_avg20, 'market': v['market']}

        print(f"    공통 필터 통과: {len(eligible)}종목 (분봉 조회 대상)")

        if not eligible:
            skipped_days.append((day_disp, "공통필터 통과 종목 없음"))
            continue

        # 후보군 분봉 로딩 (비용이 크므로 상위 15종목만)
        minute_data = {}
        for code, info in list(eligible.items())[:15]:
            bars = get_minute_bars_for_date(token, code, day_str, info['market'])
            if bars:
                minute_data[code] = bars
            time.sleep(0.1)

        if not minute_data:
            skipped_days.append((day_disp, "분봉 데이터 없음 - KIS가 과거 날짜 분봉을 미지원할 가능성"))
            print("    [경고] 분봉 데이터 없음 - 이 날짜는 스킵")
            continue

        # 시간 축을 만들어 분 단위로 전체 종목을 동시에 순회
        all_times = sorted({b['stck_cntg_hour'] for bars in minute_data.values() for b in bars})
        day_high_so_far = {code: 0.0 for code in minute_data}
        cum_vol_so_far = {code: 0 for code in minute_data}

        for hms in all_times:
            now_dt = datetime.combine(bday, dtime(int(hms[0:2]), int(hms[2:4]), int(hms[4:6])))

            # 포지션 관리 먼저
            if portfolio['position']:
                pos = portfolio['position']
                code = pos['code']
                bar = next((b for b in minute_data.get(code, []) if b['stck_cntg_hour'] == hms), None)
                if bar:
                    cur_price = float(bar['stck_prpr'])
                    cur_high = float(bar['stck_hgpr'])
                    cur_low = float(bar['stck_lwpr'])
                    elapsed = now_dt - pos['entry_dt']
                    action, pnl_pct = apply_exit_rules(pos, cur_price, cur_high, cur_low, now_dt, elapsed)
                    if action == 'TIME_PARTIAL':
                        half_qty = pos['qty'] // 2
                        if half_qty >= 1:
                            _record_sell(portfolio, trade_log, pos, day_disp, cur_price, half_qty, "2시간횡보 50%부분익절")
                            pos['qty'] -= half_qty
                    elif action in ('TARGET', 'STOP', 'FORCED'):
                        exit_price = {'TARGET': pos['target'], 'STOP': pos['stop'], 'FORCED': cur_price}[action]
                        reason = {'TARGET': '익절 +4%', 'STOP': '손절(트레일링/ATR)', 'FORCED': '15:20 강제청산'}[action]
                        _record_sell(portfolio, trade_log, pos, day_disp, exit_price, pos['qty'], reason)
                        portfolio['position'] = None

            # 신규 진입 (포지션 없을 때만)
            if portfolio['position'] is None:
                t = now_dt.time()
                tier = entry_window(t)
                if tier:
                    best = None
                    for code, bars in minute_data.items():
                        bar = next((b for b in bars if b['stck_cntg_hour'] == hms), None)
                        if not bar:
                            continue
                        cur_price = float(bar['stck_prpr'])
                        cur_high = float(bar['stck_hgpr'])
                        day_high_so_far[code] = max(day_high_so_far[code], cur_high)
                        cum_vol_so_far[code] = int(bar.get('acml_vol', cum_vol_so_far[code]))
                        info = eligible[code]
                        bars_asc = [b for b in bars if b['stck_cntg_hour'] <= hms]
                        elapsed_minutes = max(1, (now_dt.hour*60+now_dt.minute) - 9*60)
                        ok = passes_entry_filter(
                            tier, info['today_open'], info['prev_close'], cur_price,
                            day_high_so_far[code], cum_vol_so_far[code], elapsed_minutes,
                            info['vol_avg20'], bars_asc,
                        )
                        if ok:
                            best = (code, cur_price, bars_asc)
                            break
                    if best:
                        code, entry_price, bars_asc = best
                        atr = calc_atr_from_minutes(list(reversed(bars_asc))[-15:] if len(bars_asc) >= 15 else [])
                        stop = entry_price - (atr * 1.5 if atr else entry_price * 0.02)
                        stop = min(stop, entry_price * 0.985)  # 최소 손절폭 하한 -1.5%
                        qty = int(min(portfolio['cash'], MAX_BET) / entry_price)
                        if qty >= 1:
                            portfolio['cash'] -= entry_price * qty
                            portfolio['position'] = {
                                'code': code, 'entry': entry_price, 'qty': qty,
                                'stop': stop, 'target': entry_price * 1.04,
                                'entry_dt': now_dt, 'tier': tier,
                                'breakeven_applied': False, 'time_rule_checked': False,
                            }
                            trade_log.append({
                                'action': 'BUY', 'date': day_disp, 'time': hms,
                                'code': code, 'price': entry_price, 'qty': qty,
                                'tier': tier, 'stop': stop, 'target': entry_price*1.04,
                            })
                            print(f"    [{hms}] 매수 {code} @ {entry_price:,.0f} (tier={tier})")

        # 그날 장 마감 시점까지 포지션이 남아있으면 마지막 봉 가격으로 강제청산
        if portfolio['position']:
            pos = portfolio['position']
            code = pos['code']
            last_bar = minute_data.get(code, [None])[-1] if minute_data.get(code) else None
            close_price = float(last_bar['stck_prpr']) if last_bar else pos['entry']
            _record_sell(portfolio, trade_log, pos, day_disp, close_price, pos['qty'], "15:30 마감청산")
            portfolio['position'] = None

    # ── 결과 집계 ──
    sells = [t for t in trade_log if t['action'] == 'SELL']
    total_pnl = sum(t['pnl'] for t in sells)
    wins = [t for t in sells if t['pnl'] > 0]
    print("\n" + "=" * 60)
    print("  결과 요약")
    print("=" * 60)
    print(f"  초기 자금  : {MAX_BET:>10,.0f}원")
    print(f"  총 손익    : {total_pnl:>+10,.0f}원 ({total_pnl/MAX_BET*100:+.1f}%)")
    print(f"  총 매도 수 : {len(sells)}회 (부분청산 포함)")
    if sells:
        print(f"  승률       : {len(wins)}/{len(sells)} ({len(wins)/len(sells)*100:.0f}%)")
    print("\n  거래 내역")
    for t in trade_log:
        if t['action'] == 'BUY':
            print(f"  {t['date']} {t.get('time','')} 매수 {t['code']} {t['qty']}주 @ {t['price']:,.0f} [tier={t.get('tier')}]")
        else:
            sign = "OK" if t['pnl'] > 0 else "NG"
            print(f"  {t['date']} 매도 {t['code']} {t['qty']}주 {t['pnl_pct']:+.1f}% ({t['pnl']:>+,.0f}원) {t['reason']} {sign}")
    if skipped_days:
        print("\n  [스킵된 날짜]")
        for d, reason in skipped_days:
            print(f"    {d}: {reason}")
    print("=" * 60)

def _record_sell(portfolio, trade_log, pos, day_disp, price, qty, reason):
    pnl = (price - pos['entry']) * qty
    pnl_pct = (price - pos['entry']) / pos['entry'] * 100
    portfolio['cash'] += price * qty
    trade_log.append({
        'action': 'SELL', 'date': day_disp, 'code': pos['code'],
        'price': price, 'qty': qty, 'pnl': pnl, 'pnl_pct': pnl_pct,
        'reason': reason,
    })

if __name__ == '__main__':
    run_intraday_backtest()

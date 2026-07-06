#!/usr/bin/env python3
"""
1분봉 백테스트 — collect_intraday_data.py가 minute_data/ 에 실측 저장한
1분봉을 사용해서 auto_trading.py(라이브) 전략을 검증한다.

[2026-07-06] 전면 재작성.
기존 버전은 minute_data/를 전혀 읽지 않고 KIS API에 과거 날짜 분봉을
직접 요청했었다(비신뢰 — KIS가 과거 날짜 분봉을 안정적으로 지원하지
않는다는 게 바로 collect_intraday_data.py를 만든 이유였는데, 정작 이
스크립트는 그 결과물을 쓰지 않고 있었음). 또한 진입 필터가 라이브와
달리 MA200/MACD를 여전히 요구하고 있었음(라이브는 2026-07-02 제거).
이번 재작성으로 두 문제 모두 해소:
  - minute_data/watchlist_{YYYYMMDD}.json, {code}_{YYYYMMDD}.json 사용
  - 일봉 공통 필터/우선순위/1분봉 타이밍 로직을 auto_trading.py와 동일하게 맞춤

일봉 공통 필터 (auto_trading.py의 scan_signals와 동일):
  가격 > MA20, (BB(20,2) 스퀴즈 OR BB상단 98% 근접),
  거래량 20일평균 대비 경과시간비례 2배 이상
진입 시간대: 09:00~11:00(갭<5%) / 14:00~14:30(당일고가 98% 근접, 갭조건 없음)
우선순위: 당일신규돌파 > 스퀴즈 > 거래량비율 — 상위 3종목만 1분봉 타이밍 확인
1분봉 타이밍(check_1min_entry와 동일): 스토캐스틱RSI(14,5,3,3) K>D and K>20,
  현재가 >= 1분봉 BB(20) 중심선
청산: ATR(14,1분봉)x1.5 손절(최소 -1.5%), +4% 익절,
  +2% 트레일링 -> 본전+0.4%, 2시간 시점 0~1%면 50%부분청산, 15:20 강제청산

[한계]
1. minute_data의 워치리스트는 collect_intraday_data.py가 하루 최초 실행
   시점에 딱 한 번만 고정한다 — 장중에 새로 조건을 충족한 종목은 그날
   못 잡는다 (라이브는 매 사이클 새로 스캔).
2. 체결 슬리피지/수수료는 반영하지 않는다.
=====================================================
"""
import os, json, time, glob, re, requests
from datetime import datetime, timezone, timedelta, time as dtime

KST      = timezone(timedelta(hours=9))
BASE_URL = "https://openapi.koreainvestment.com:9443"
MAX_BET  = 500_000
KIS_APP_KEY    = os.environ['KIS_APP_KEY']
KIS_APP_SECRET = os.environ['KIS_APP_SECRET']
TOKEN_FILE     = "kis_token.json"
DATA_DIR       = "minute_data"

TRADING_MINUTES_PER_DAY = 390  # 09:00~15:30

T_0900 = dtime(9, 0)
T_1100 = dtime(11, 0)
T_1400 = dtime(14, 0)
T_1430 = dtime(14, 30)
T_1520 = dtime(15, 20)

# ── KIS 인증 / 공통 GET (일봉은 과거 날짜 조회가 신뢰 가능해서 그대로 사용) ──
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

def get_daily_raw(token, code, market, end_date):
    """end_date('YYYYMMDD') 기준 과거 400일치 일봉 원본(최신이 index 0)."""
    start = (datetime.strptime(end_date, '%Y%m%d') - timedelta(days=400)).strftime('%Y%m%d')
    data = kis_get(token, "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice", {
        "FID_COND_MRKT_DIV_CODE": market,
        "FID_INPUT_ISCD": code,
        "FID_INPUT_DATE_1": start,
        "FID_INPUT_DATE_2": end_date,
        "FID_PERIOD_DIV_CODE": "D",
        "FID_ORG_ADJ_PRC": "0",
    }, "FHKST03010100")
    rows = data.get('output2', [])
    if not rows and market == "J":
        return get_daily_raw(token, code, "Q", end_date)
    return rows

# ── 수집된 데이터 로딩 ────────────────────────────────────────────
def find_collected_days():
    files = glob.glob(os.path.join(DATA_DIR, "watchlist_*.json"))
    days = []
    for f in files:
        m = re.search(r'watchlist_(\d{8})\.json$', f)
        if m:
            days.append(m.group(1))
    return sorted(days)

def load_json(path, default):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default

# ── 지표 (auto_trading.py와 동일) ──────────────────────────────────
def calc_ma(closes, period):
    if len(closes) < period:
        return None
    return sum(closes[:period]) / period

def calc_bb(closes, period=20, mult=2.0):
    if len(closes) < period:
        return None, None, None, None
    d = closes[:period]
    mid = sum(d) / period
    std = (sum((x - mid) ** 2 for x in d) / period) ** 0.5
    upper = mid + mult * std
    lower = mid - mult * std
    bw = (upper - lower) / mid if mid else 0
    return upper, mid, lower, bw

def is_bb_squeeze(closes, period=20, lookback=20):
    if len(closes) < period + lookback:
        return False
    _, _, _, bw_now = calc_bb(closes, period)
    if bw_now is None:
        return False
    hist = []
    for i in range(1, lookback + 1):
        _, _, _, bw = calc_bb(closes[i:], period)
        if bw is not None:
            hist.append(bw)
    if not hist:
        return False
    threshold = sorted(hist)[int(len(hist) * 0.3)]
    return bw_now <= threshold

def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    diffs = [closes[i] - closes[i + 1] for i in range(period)]
    gains = sum(d for d in diffs if d > 0) / period
    losses = sum(abs(d) for d in diffs if d < 0) / period
    if losses == 0:
        return 100.0
    return 100 - (100 / (1 + gains / losses))

def calc_stoch_rsi(closes, rsi_period=14, stoch_period=5, smooth_k=3, smooth_d=3):
    """closes: 최신이 index 0. returns (K, D)"""
    needed = rsi_period + stoch_period + smooth_k + smooth_d + 2
    if len(closes) < needed:
        return None, None
    asc = list(reversed(closes))
    rsi_vals = []
    for i in range(rsi_period, len(asc)):
        window = list(reversed(asc[i - rsi_period: i + 1]))
        r = calc_rsi(window, rsi_period)
        if r is not None:
            rsi_vals.append(r)
    if len(rsi_vals) < stoch_period:
        return None, None
    raw_k = []
    for i in range(stoch_period - 1, len(rsi_vals)):
        win = rsi_vals[i - stoch_period + 1: i + 1]
        hi, lo = max(win), min(win)
        raw_k.append(50.0 if hi == lo else (rsi_vals[i] - lo) / (hi - lo) * 100)
    if len(raw_k) < smooth_k:
        return None, None
    k_vals = [sum(raw_k[i - smooth_k + 1: i + 1]) / smooth_k
              for i in range(smooth_k - 1, len(raw_k))]
    if len(k_vals) < smooth_d:
        return None, None
    d_vals = [sum(k_vals[i - smooth_d + 1: i + 1]) / smooth_d
              for i in range(smooth_d - 1, len(k_vals))]
    return k_vals[-1], d_vals[-1]

def check_pullback_reclaim(closes, highs, lows, volumes, lookback=15):
    """auto_trading.py의 check_pullback_reclaim과 동일 로직(2026-07-06 눌림목+불플래그 진입 필터)"""
    if len(closes) < lookback + 3 or len(volumes) < lookback + 3:
        return False, "데이터 부족"
    asc_c = list(reversed(closes[:lookback]))
    asc_h = list(reversed(highs[:lookback]))
    asc_l = list(reversed(lows[:lookback]))
    asc_v = list(reversed(volumes[:lookback]))
    search_end = lookback - 3
    if search_end < 3:
        return False, "탐색 구간 부족"
    peak_idx = max(range(search_end), key=lambda i: asc_h[i])
    peak_price = asc_h[peak_idx]
    flag_start = max(0, peak_idx - 3)
    flagpole_vol = sum(asc_v[flag_start:peak_idx + 1]) / max(1, peak_idx + 1 - flag_start)
    pullback_range = range(peak_idx + 1, lookback - 1)
    if len(list(pullback_range)) < 2:
        return False, "눌림 구간 부족"
    trough_price = min(asc_l[i] for i in pullback_range)
    pullback_vol = sum(asc_v[i] for i in pullback_range) / len(list(pullback_range))
    pullback_pct = (peak_price - trough_price) / peak_price * 100 if peak_price else 0
    cur_close = asc_c[-1]
    cur_vol = asc_v[-1]
    healthy_pullback = 0.2 <= pullback_pct <= 4.0
    volume_dried_up = pullback_vol < flagpole_vol * 0.8
    reclaim = cur_close >= peak_price * 0.997
    volume_surge = cur_vol >= pullback_vol * 1.5
    ok = healthy_pullback and volume_dried_up and reclaim and volume_surge
    return ok, f"눌림{pullback_pct:.1f}% 거래량감소{volume_dried_up} 재돌파{reclaim} 거래량급증{volume_surge}"

def calc_atr_from_minutes(minute_bars_desc, period=14):
    """1분봉 원본 dict 리스트(최신이 index0) 기준 ATR"""
    if len(minute_bars_desc) < period + 1:
        return None
    highs  = [float(b['stck_hgpr']) for b in minute_bars_desc]
    lows   = [float(b['stck_lwpr']) for b in minute_bars_desc]
    closes = [float(b['stck_prpr']) for b in minute_bars_desc]
    trs = [max(highs[i] - lows[i],
               abs(highs[i] - closes[i + 1]),
               abs(lows[i] - closes[i + 1])) for i in range(period)]
    return sum(trs) / period

# ── 일봉 공통 필터 (auto_trading.py의 scan_signals와 동일 로직) ──────
def build_daily_eligibility(daily_rows, ymd):
    """
    daily_rows: 해당 종목의 일봉 원본(최신이 index 0).
    ymd 당일 행(today)은 제외하고, 그 이전 행들만으로 MA20/BB/거래량평균을
    계산한다(라이브가 "어제까지"의 20일선/밴드를 기준으로 오늘을 판정하는 것과 동일).
    반환: None(대상 아님) 또는 dict(prev_close, today_open, vol_avg20,
          in_squeeze, is_new_breakout)
    """
    idx = next((i for i, r in enumerate(daily_rows) if r['stck_bsop_date'] == ymd), None)
    if idx is None:
        return None
    today = daily_rows[idx]
    past = daily_rows[idx + 1:]
    if len(past) < 42:
        return None
    closes  = [float(r['stck_clpr']) for r in past]
    volumes = [int(r.get('acml_vol', 0)) for r in past]
    prev_close = closes[0]
    today_open = float(today.get('stck_oprc', 0))
    if prev_close == 0 or today_open == 0:
        return None
    ma20 = calc_ma(closes, 20)
    bb_upper, _, _, _ = calc_bb(closes, 20)
    in_squeeze = is_bb_squeeze(closes)
    if ma20 is None or bb_upper is None:
        return None
    today_close = float(today.get('stck_clpr', 0))
    if today_close <= ma20:
        return None
    if not (in_squeeze or today_close >= bb_upper * 0.98):
        return None
    ma20_prev = calc_ma(closes[1:], 20)
    is_new_breakout = ma20_prev is not None and len(closes) > 1 and closes[1] <= ma20_prev
    vol_avg20 = sum(volumes[:20]) / 20 if len(volumes) >= 20 else None
    return {
        'prev_close': prev_close, 'today_open': today_open,
        'vol_avg20': vol_avg20, 'in_squeeze': in_squeeze,
        'is_new_breakout': is_new_breakout,
    }

def entry_window(t):
    if T_0900 <= t < T_1100:
        return 'morning'
    if T_1400 <= t < T_1430:
        return 'afternoon'
    return None

def passes_gap_or_high_filter(window, info, cur_price, day_high_so_far):
    gap = (info['today_open'] - info['prev_close']) / info['prev_close'] * 100
    if window == 'morning':
        return gap < 5.0
    if window == 'afternoon':
        if day_high_so_far <= 0:
            return False
        return cur_price >= day_high_so_far * 0.98
    return False

def passes_volume_pace(info, cum_vol, elapsed_minutes):
    if not info['vol_avg20']:
        return True
    pace_needed = info['vol_avg20'] * (elapsed_minutes / TRADING_MINUTES_PER_DAY) * 2
    return cum_vol >= pace_needed

# ── 청산 규칙 (auto_trading.py의 manage_position과 동일 로직, 2026-07-06 갱신) ──
def apply_exit_rules(position, cur_price, cur_high, cur_low, now_dt, elapsed_since_entry):
    """
    [2026-07-06] +2% 도달 시 50% 부분익절(신규)로 갱신.
    position: dict(entry, qty, stop, target, entry_dt, breakeven_applied, time_rule_checked, partial_2pct_done)
    반환: (action, pnl_pct) — action in {None, 'STOP', 'TARGET', 'PARTIAL_2PCT', 'TIME_PARTIAL', 'FORCED'}
    """
    entry = position['entry']
    pnl_pct = (cur_price - entry) / entry * 100

    if not position.get('partial_2pct_done') and cur_high >= entry * 1.02:
        position['stop'] = max(position['stop'], entry * 1.004)
        position['breakeven_applied'] = True
        position['partial_2pct_done'] = True
        return 'PARTIAL_2PCT', pnl_pct

    if not position.get('time_rule_checked') and elapsed_since_entry >= timedelta(hours=2):
        position['time_rule_checked'] = True
        if 0.0 <= pnl_pct <= 1.0:
            position['stop'] = max(position['stop'], entry * 1.004)
            position['breakeven_applied'] = True
            return 'TIME_PARTIAL', pnl_pct

    if now_dt.time() >= T_1520:
        return 'FORCED', pnl_pct
    if cur_high >= position['target']:
        return 'TARGET', pnl_pct
    if cur_low <= position['stop']:
        return 'STOP', pnl_pct
    return None, pnl_pct

def apply_exit_rules_old(position, cur_price, cur_high, cur_low, now_dt, elapsed_since_entry):
    """
    [비교용] 2026-07-06 이전 방식 — +2%에서 손절선만 올리고 팔지는 않음(전량 유지).
    실제 매매엔 쓰이지 않고, "부분익절 안 했으면 어땠을까" 비교 통계용으로만 사용.
    """
    entry = position['entry']
    pnl_pct = (cur_price - entry) / entry * 100
    if not position.get('breakeven_applied') and pnl_pct >= 2.0:
        position['stop'] = max(position['stop'], entry * 1.004)
        position['breakeven_applied'] = True
    if not position.get('time_rule_checked') and elapsed_since_entry >= timedelta(hours=2):
        position['time_rule_checked'] = True
        if 0.0 <= pnl_pct <= 1.0:
            position['stop'] = max(position['stop'], entry * 1.004)
            position['breakeven_applied'] = True
            return 'TIME_PARTIAL', pnl_pct
    if now_dt.time() >= T_1520:
        return 'FORCED', pnl_pct
    if cur_high >= position['target']:
        return 'TARGET', pnl_pct
    if cur_low <= position['stop']:
        return 'STOP', pnl_pct
    return None, pnl_pct

def simulate_shadow_old(entry_price, target, initial_stop, entry_dt, bars_from_entry):
    """
    [2026-07-06 추가] 특정 트레이드의 진입~청산 구간 1분봉을 그대로 재생해서,
    "+2% 부분익절 없이 옛날 방식(트레일링만)이었다면" 결과가 어땠을지 계산.
    bars_from_entry: 진입 시점 이후의 1분봉(시간 오름차순), 마지막 봉까지 청산 안 되면 마지막 종가로 강제청산 처리.
    반환: (old_pnl_pct, case) — case는 'A_LOSS'(손절) / 'B_REVERSAL'(+2%후 반락) / 'C_FULL'(+4%도달) / 'FORCED'
    """
    pos = {'entry': entry_price, 'target': target, 'stop': initial_stop,
           'breakeven_applied': False, 'time_rule_checked': False}
    reached_2pct = False
    for b in bars_from_entry:
        now_dt = datetime.combine(entry_dt.date(),
                                   dtime(int(b['stck_cntg_hour'][0:2]), int(b['stck_cntg_hour'][2:4]), int(b['stck_cntg_hour'][4:6])))
        cur_price = float(b['stck_prpr']); cur_high = float(b['stck_hgpr']); cur_low = float(b['stck_lwpr'])
        if cur_high >= entry_price * 1.02:
            reached_2pct = True
        elapsed = now_dt - entry_dt
        action, pnl_pct = apply_exit_rules_old(pos, cur_price, cur_high, cur_low, now_dt, elapsed)
        if action == 'TARGET':
            return (target - entry_price) / entry_price * 100, 'C_FULL'
        if action == 'STOP':
            stop_pnl = (pos['stop'] - entry_price) / entry_price * 100
            return stop_pnl, ('B_REVERSAL' if reached_2pct else 'A_LOSS')
        if action in ('FORCED', 'TIME_PARTIAL'):
            return pnl_pct, 'FORCED'
    # 데이터 소진 — 마지막 봉 종가로 마감
    last_pnl = (float(bars_from_entry[-1]['stck_prpr']) - entry_price) / entry_price * 100 if bars_from_entry else 0.0
    return last_pnl, ('B_REVERSAL' if reached_2pct else 'A_LOSS')

def _record_sell(portfolio, trade_log, pos, day_disp, price, qty, reason):
    pnl = (price - pos['entry']) * qty
    pnl_pct = (price - pos['entry']) / pos['entry'] * 100
    portfolio['cash'] += price * qty
    trade_log.append({
        'action': 'SELL', 'date': day_disp, 'code': pos['code'],
        'price': price, 'qty': qty, 'pnl': pnl, 'pnl_pct': pnl_pct,
        'reason': reason,
    })

# ── 메인 시뮬레이션 ───────────────────────────────────────────────
def run_intraday_backtest():
    print("=" * 60)
    print("  1분봉 백테스트 (minute_data/ 실측 데이터 기반)")
    print("=" * 60)

    days = find_collected_days()
    if not days:
        print("\n[중단] minute_data/watchlist_*.json 이 없습니다 — 아직 수집된 날짜가 없어요.")
        print("        collect-intraday 워크플로우가 며칠 돌고 난 뒤 다시 실행하세요.")
        return
    print(f"\n수집된 날짜: {days}")

    token = get_kis_token()

    daily_cache = {}
    def get_daily_rows(code, market):
        if code not in daily_cache:
            daily_cache[code] = get_daily_raw(token, code, market, days[-1])
            time.sleep(0.08)
        return daily_cache[code]

    portfolio = {'cash': MAX_BET, 'position': None}
    trade_log = []
    skipped_days = []
    shadow_log = []  # [2026-07-06] +2% 부분익절 신규 vs 구(舊)방식 비교용

    for ymd in days:
        bday = datetime.strptime(ymd, '%Y%m%d').date()
        day_disp = bday.strftime('%m/%d(%a)')
        print(f"\n--- {day_disp} ---")

        watchlist = load_json(f"{DATA_DIR}/watchlist_{ymd}.json", [])
        if not watchlist:
            skipped_days.append((day_disp, "워치리스트 없음"))
            continue

        eligible = {}
        minute_data = {}
        for w in watchlist:
            code, market = w['code'], w['market']
            rows = get_daily_rows(code, market)
            info = build_daily_eligibility(rows, ymd)
            if info is None:
                continue
            bars = load_json(f"{DATA_DIR}/{code}_{ymd}.json", [])
            if not bars:
                continue
            eligible[code] = info
            minute_data[code] = bars  # collector가 시간 오름차순으로 저장해둠

        print(f"    일봉 필터 통과 + 1분봉 보유: {len(eligible)}종목")
        if not eligible:
            skipped_days.append((day_disp, "일봉 필터 통과 + 1분봉 보유 종목 없음"))
            continue

        all_times = sorted({b['stck_cntg_hour'] for bars in minute_data.values() for b in bars})
        day_high_so_far = {code: 0.0 for code in minute_data}

        for hms in all_times:
            now_dt = datetime.combine(bday, dtime(int(hms[0:2]), int(hms[2:4]), int(hms[4:6])))

            bars_by_code = {}
            for code, bars in minute_data.items():
                bars_asc = [b for b in bars if b['stck_cntg_hour'] <= hms]
                if bars_asc:
                    bars_by_code[code] = bars_asc
                    day_high_so_far[code] = max(day_high_so_far[code], float(bars_asc[-1]['stck_hgpr']))

            # ① 포지션 관리
            if portfolio['position']:
                pos = portfolio['position']
                code = pos['code']
                bar = next((b for b in minute_data.get(code, []) if b['stck_cntg_hour'] == hms), None)
                if bar:
                    cur_price = float(bar['stck_prpr'])
                    cur_high  = float(bar['stck_hgpr'])
                    cur_low   = float(bar['stck_lwpr'])
                    elapsed = now_dt - pos['entry_dt']
                    action, pnl_pct = apply_exit_rules(pos, cur_price, cur_high, cur_low, now_dt, elapsed)
                    if action == 'PARTIAL_2PCT':
                        half_qty = pos['qty'] // 2
                        if half_qty >= 1:
                            _record_sell(portfolio, trade_log, pos, day_disp, cur_price, half_qty, "+2% 50%부분익절")
                            pos['realized_amt'] = pos.get('realized_amt', 0.0) + (cur_price - pos['entry']) * half_qty
                            pos['qty'] -= half_qty
                    elif action == 'TIME_PARTIAL':
                        half_qty = pos['qty'] // 2
                        if half_qty >= 1:
                            _record_sell(portfolio, trade_log, pos, day_disp, cur_price, half_qty, "2시간횡보 50%부분익절")
                            pos['realized_amt'] = pos.get('realized_amt', 0.0) + (cur_price - pos['entry']) * half_qty
                            pos['qty'] -= half_qty
                    elif action in ('TARGET', 'STOP', 'FORCED'):
                        exit_price = {'TARGET': pos['target'], 'STOP': pos['stop'], 'FORCED': cur_price}[action]
                        reason = {'TARGET': '익절 +4%', 'STOP': '손절(트레일링/ATR)', 'FORCED': '15:20 강제청산'}[action]
                        _record_sell(portfolio, trade_log, pos, day_disp, exit_price, pos['qty'], reason)
                        # [2026-07-06] +2% 부분익절 신규 방식(new) vs 없었다면(old) 비교 계산
                        total_realized = pos.get('realized_amt', 0.0) + (exit_price - pos['entry']) * pos['qty']
                        new_pnl_pct = total_realized / (pos['entry'] * pos['orig_qty']) * 100
                        entry_bars = [b for b in minute_data.get(code, [])
                                      if pos['entry_hms'] <= b['stck_cntg_hour'] <= hms]
                        old_pnl_pct, case = simulate_shadow_old(
                            pos['entry'], pos['entry'] * 1.04, pos['initial_stop'], pos['entry_dt'], entry_bars)
                        shadow_log.append({
                            'date': day_disp, 'code': code, 'new_reason': reason,
                            'new_pnl_pct': new_pnl_pct, 'old_pnl_pct': old_pnl_pct, 'case': case,
                        })
                        portfolio['position'] = None

            # ② 신규 진입 (상위 3후보만 1분봉 타이밍 확인 — auto_trading.py와 동일)
            if portfolio['position'] is None:
                window = entry_window(now_dt.time())
                if window:
                    elapsed_minutes = max(1, (now_dt.hour * 60 + now_dt.minute) - 9 * 60)
                    candidates = []
                    for code, info in eligible.items():
                        bars_asc = bars_by_code.get(code)
                        if not bars_asc:
                            continue
                        cur_bar = bars_asc[-1]
                        cur_price = float(cur_bar['stck_prpr'])
                        cum_vol   = int(cur_bar.get('acml_vol', 0))
                        if not passes_gap_or_high_filter(window, info, cur_price, day_high_so_far[code]):
                            continue
                        if not passes_volume_pace(info, cum_vol, elapsed_minutes):
                            continue
                        vol_ratio = (cum_vol / info['vol_avg20']) if info['vol_avg20'] else 0
                        candidates.append((code, info, bars_asc, vol_ratio))
                    candidates.sort(key=lambda c: (-int(c[1]['is_new_breakout']),
                                                    -int(c[1]['in_squeeze']), -c[3]))
                    for code, info, bars_asc, _ in candidates[:3]:
                        bars_desc = list(reversed(bars_asc))
                        closes_desc  = [float(b['stck_prpr']) for b in bars_desc]
                        highs_desc   = [float(b['stck_hgpr']) for b in bars_desc]
                        lows_desc    = [float(b['stck_lwpr']) for b in bars_desc]
                        volumes_desc = [int(b.get('cntg_vol', 0)) for b in bars_desc]
                        stoch_k, stoch_d = calc_stoch_rsi(closes_desc)
                        if stoch_k is None or not (stoch_k > stoch_d and stoch_k > 20):
                            continue
                        # [2026-07-06] 눌림목+불플래그 재돌파 확인 (기존 "BB중심선 위" 단순조건 대체)
                        pullback_ok, _ = check_pullback_reclaim(closes_desc, highs_desc, lows_desc, volumes_desc)
                        if not pullback_ok:
                            continue
                        entry_price = closes_desc[0]
                        atr = calc_atr_from_minutes(bars_desc)
                        stop = entry_price - (atr * 1.5 if atr else entry_price * 0.02)
                        stop = min(stop, entry_price * 0.985)
                        qty = int(min(portfolio['cash'], MAX_BET) / entry_price)
                        if qty >= 1:
                            portfolio['cash'] -= entry_price * qty
                            portfolio['position'] = {
                                'code': code, 'entry': entry_price, 'qty': qty, 'orig_qty': qty,
                                'stop': stop, 'target': entry_price * 1.04, 'initial_stop': stop,
                                'entry_dt': now_dt, 'entry_hms': hms, 'window': window,
                                'breakeven_applied': False, 'time_rule_checked': False,
                                'partial_2pct_done': False,
                            }
                            trade_log.append({
                                'action': 'BUY', 'date': day_disp, 'time': hms,
                                'code': code, 'price': entry_price, 'qty': qty,
                                'window': window, 'stop': stop, 'target': entry_price * 1.04,
                            })
                            print(f"    [{hms}] 매수 {code} @ {entry_price:,.0f} (window={window})")
                        break  # 상위 3 중 1분봉 타이밍 통과한 첫 종목에서 종료 (매수 실패해도 다음 후보로 안 넘어감 — 라이브와 동일)

        # 그날 마감까지 포지션이 남아있으면 마지막 봉 가격으로 강제청산
        if portfolio['position']:
            pos = portfolio['position']
            code = pos['code']
            last_bar = minute_data.get(code, [None])[-1] if minute_data.get(code) else None
            close_price = float(last_bar['stck_prpr']) if last_bar else pos['entry']
            _record_sell(portfolio, trade_log, pos, day_disp, close_price, pos['qty'], "15:30 마감청산")
            total_realized = pos.get('realized_amt', 0.0) + (close_price - pos['entry']) * pos['qty']
            new_pnl_pct = total_realized / (pos['entry'] * pos['orig_qty']) * 100
            entry_bars = [b for b in minute_data.get(code, []) if b['stck_cntg_hour'] >= pos['entry_hms']]
            old_pnl_pct, case = simulate_shadow_old(
                pos['entry'], pos['entry'] * 1.04, pos['initial_stop'], pos['entry_dt'], entry_bars)
            shadow_log.append({
                'date': day_disp, 'code': code, 'new_reason': '15:30 마감청산',
                'new_pnl_pct': new_pnl_pct, 'old_pnl_pct': old_pnl_pct, 'case': case,
            })
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

    # [2026-07-06] +2% 부분익절 신규 방식 vs 옛날 방식(부분익절 없음) 비교
    if shadow_log:
        print("\n" + "=" * 60)
        print("  +2% 부분익절 신규(new) vs 기존(old) 비교")
        print("=" * 60)
        new_avg = sum(s['new_pnl_pct'] for s in shadow_log) / len(shadow_log)
        old_avg = sum(s['old_pnl_pct'] for s in shadow_log) / len(shadow_log)
        print(f"  완결 트레이드 수 : {len(shadow_log)}건")
        print(f"  신규(부분익절)   평균손익 : {new_avg:+.2f}%")
        print(f"  기존(전량유지)   평균손익 : {old_avg:+.2f}%")
        print(f"  차이 (신규-기존) : {new_avg - old_avg:+.2f}%p  {'-> 부분익절이 유리' if new_avg > old_avg else '-> 부분익절 없는 쪽이 유리'}")
        case_counts = {}
        for s in shadow_log:
            case_counts[s['case']] = case_counts.get(s['case'], 0) + 1
        print(f"  케이스 분포 : A(손절)={case_counts.get('A_LOSS',0)} "
              f"B(+2%후반락)={case_counts.get('B_REVERSAL',0)} "
              f"C(+4%완주)={case_counts.get('C_FULL',0)} "
              f"기타(강제청산)={case_counts.get('FORCED',0)}")
        b_count = case_counts.get('B_REVERSAL', 0)
        c_count = case_counts.get('C_FULL', 0)
        if c_count > 0:
            print(f"  B/C 비율 : {b_count}/{c_count} = {b_count/c_count:.2f} (1.25 이상이면 이론상 부분익절이 유리)")

    print("\n  거래 내역")
    for t in trade_log:
        if t['action'] == 'BUY':
            print(f"  {t['date']} {t.get('time','')} 매수 {t['code']} {t['qty']}주 @ {t['price']:,.0f} [window={t.get('window')}]")
        else:
            sign = "OK" if t['pnl'] > 0 else "NG"
            print(f"  {t['date']} 매도 {t['code']} {t['qty']}주 {t['pnl_pct']:+.1f}% ({t['pnl']:>+,.0f}원) {t['reason']} {sign}")
    if skipped_days:
        print("\n  [스킵된 날짜]")
        for d, reason in skipped_days:
            print(f"    {d}: {reason}")
    print("=" * 60)

if __name__ == '__main__':
    run_intraday_backtest()

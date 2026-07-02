# 자동매매 — 2단계 추세 추종 단타 전략
# ═══════════════════════════════════════════════════════════
# [1단계: 일봉] 종목 선정
#   MA20/MA200 상단, MACD 골든크로스(5일내),
#   볼린저밴드 스퀴즈 or BB 상단 근접,
#   거래량 2배, 갭 +3% 미만
# -------------------------------------------------------
# [2단계: 1분봉] 진입 타이밍 확인 (상위 3 후보에만 적용)
#   스토캐스틱RSI: K > D AND K > 20 (과매도 탈출 후 상승)
#   현재가 ≥ 1분봉 볼린저밴드 중심선
# -------------------------------------------------------
# 손절: 1분봉 ATR(14) × 1.5 동적 손절 (position에 저장)
# 익절: +4% | 강제청산: 15:20
# ═══════════════════════════════════════════════════════════

import os, json, time, requests
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
BASE_URL = "https://openapi.koreainvestment.com:9443"
MAX_BET = 500_000
ACCOUNT_NO   = os.environ['KIS_ACCOUNT_NO']
ACCOUNT_PROD = "01"

KIS_APP_KEY    = os.environ['KIS_APP_KEY']
KIS_APP_SECRET = os.environ['KIS_APP_SECRET']
KAKAO_CLIENT_ID     = os.environ['KAKAO_CLIENT_ID']
KAKAO_CLIENT_SECRET = os.environ['KAKAO_CLIENT_SECRET']
KAKAO_REFRESH_TOKEN = os.environ['KAKAO_REFRESH_TOKEN']

DASHBOARD_FILE = "dashboard_data.json"
TOKEN_FILE     = "kis_token.json"

# ── KIS 인증 (토큰 캐싱 — 1일 1회 발급) ─────────────────────
def get_kis_token():
    try:
        with open(TOKEN_FILE, 'r') as f:
            cached = json.load(f)
        issued_at = datetime.fromisoformat(cached['issued_at'])
        age = (datetime.now(KST) - issued_at).total_seconds()
        if age < 23 * 3600 and cached.get('access_token'):
            print(f"[토큰] 캐시 재사용 (발급 후 {age/3600:.1f}시간)")
            return cached['access_token']
    except Exception:
        pass

    print("[토큰] 새로 발급 중...")
    r = requests.post(f"{BASE_URL}/oauth2/tokenP", json={
        "grant_type": "client_credentials",
        "appkey": KIS_APP_KEY,
        "appsecret": KIS_APP_SECRET
    }, timeout=10)
    data = r.json()
    if 'access_token' not in data:
        raise Exception(f"KIS 토큰 오류: {data}")

    with open(TOKEN_FILE, 'w') as f:
        json.dump({'access_token': data['access_token'],
                   'issued_at': datetime.now(KST).isoformat()}, f)
    print("[토큰] 발급 및 저장 완료")
    return data['access_token']

def kis_get(token, path, params, tr_id, retries=3):
    headers = {
        "authorization": f"Bearer {token}",
        "appkey": KIS_APP_KEY,
        "appsecret": KIS_APP_SECRET,
        "tr_id": tr_id,
        "custtype": "P"
    }
    for attempt in range(retries):
        try:
            r = requests.get(f"{BASE_URL}{path}", headers=headers, params=params, timeout=10)
            print(f"[API] {tr_id} status={r.status_code} len={len(r.text)}")
            if not r.text.strip():
                print(f"[경고] 빈 응답: {tr_id}")
                return {}
            try:
                return r.json()
            except Exception as e:
                print(f"[오류] JSON 파싱 실패 {tr_id}: {e} | body={r.text[:200]}")
                return {}
        except Exception as e:
            print(f"[재시도 {attempt+1}/{retries}] {tr_id}: {e}")
            if attempt < retries - 1:
                time.sleep(2)
    return {}

def kis_post(token, path, body, tr_id):
    headers = {
        "authorization": f"Bearer {token}",
        "appkey": KIS_APP_KEY,
        "appsecret": KIS_APP_SECRET,
        "tr_id": tr_id,
        "custtype": "P",
        "content-type": "application/json"
    }
    r = requests.post(f"{BASE_URL}{path}", headers=headers, json=body, timeout=10)
    print(f"[API] {tr_id} status={r.status_code}")
    if not r.text.strip():
        return {}
    try:
        return r.json()
    except Exception as e:
        print(f"[오류] JSON 파싱 실패 {tr_id}: {e}")
        return {}

# ── 카카오톡 ──────────────────────────────────────────────────
def get_kakao_token():
    r = requests.post('https://kauth.kakao.com/oauth/token', data={
        'grant_type': 'refresh_token',
        'client_id': KAKAO_CLIENT_ID,
        'client_secret': KAKAO_CLIENT_SECRET,
        'refresh_token': KAKAO_REFRESH_TOKEN,
    }, timeout=10)
    return r.json()['access_token']

def send_kakao(kakao_token, msg):
    obj = json.dumps({
        'object_type': 'text',
        'text': msg[:200],
        'link': {'web_url': 'https://mgzzang2000-lang.github.io/portfolio-briefing/',
                 'mobile_web_url': 'https://mgzzang2000-lang.github.io/portfolio-briefing/'}
    })
    requests.post('https://kapi.kakao.com/v2/api/talk/memo/default/send',
                  headers={'Authorization': f'Bearer {kakao_token}'},
                  data={'template_object': obj}, timeout=10)
    print(f"[카톡] {msg[:60]}")

# ── 대시보드 데이터 ───────────────────────────────────────────
def load_dashboard():
    try:
        with open(DASHBOARD_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {"initial_balance": 500000, "current_balance": 500000,
                "trades": [], "position": None, "last_updated": ""}

def save_dashboard(data):
    data['last_updated'] = datetime.now(KST).isoformat()
    history = data.setdefault('balance_history', [])
    today = datetime.now(KST).strftime('%m/%d')
    entry = {'date': today, 'balance': data.get('current_balance', 500000)}
    if history and history[-1]['date'] == today:
        history[-1] = entry
    else:
        history.append(entry)
    data['balance_history'] = history[-60:]
    with open(DASHBOARD_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("[대시보드] 저장 완료")

def log_buy(dash, code, name, qty, price, cash_after, stop_price, target_price):
    """stop_price: ATR×1.5 동적 손절가, target_price: +4% 익절가"""
    entry_time = datetime.now(KST)
    dash['trades'].append({
        'action': 'buy',
        'date': entry_time.strftime('%m/%d %H:%M'),
        'code': code, 'stock': name, 'qty': qty,
        'price': int(price), 'amount': int(price * qty)
    })
    dash['position'] = {
        'code': code, 'name': name, 'qty': qty,
        'avg_price': int(price), 'current_price': int(price),
        'stop_price':   int(stop_price),    # ATR×1.5 손절가
        'target_price': int(target_price),  # +4% 익절가
        'entry_time': entry_time.isoformat(),  # 2시간 부분청산 용도
        'trailing_activated': False,  # 트레일링 스탑 활성화 여부
    }
    dash['current_balance'] = int(cash_after)

def log_sell(dash, name, qty, avg_price, sell_price, pnl_pct, pnl_amt, reason, new_cash):
    dash['trades'].append({
        'action': 'sell',
        'date': datetime.now(KST).strftime('%m/%d %H:%M'),
        'stock': name, 'qty': qty, 'price': int(sell_price),
        'avg_price': int(avg_price), 'pnl_pct': round(pnl_pct, 2),
        'pnl_amt': int(pnl_amt), 'reason': reason
    })
    dash['position'] = None
    dash['current_balance'] = int(new_cash)

def update_position_price(dash, current_price):
    if dash.get('position'):
        dash['position']['current_price'] = int(current_price)

# ── 시장 데이터 ───────────────────────────────────────────────
def get_volume_rank(token, market="J"):
    scr_code = "20172" if market == "Q" else "20171"
    data = kis_get(token, "/uapi/domestic-stock/v1/quotations/volume-rank", {
        "FID_COND_MRKT_DIV_CODE": market,
        "FID_COND_SCR_DIV_CODE": scr_code,
        "FID_INPUT_ISCD": "0000",
        "FID_DIV_CLS_CODE": "0",
        "FID_BLNG_CLS_CODE": "0",
        "FID_TRGT_CLS_CODE": "111111111",
        "FID_TRGT_EXLS_CLS_CODE": "000000",
        "FID_INPUT_PRICE_1": "",
        "FID_INPUT_PRICE_2": "",
        "FID_VOL_CNT": "",
        "FID_INPUT_DATE_1": ""
    }, "FHPST01710000")
    output = data.get('output', [])
    if not output:
        print(f"[경고] 거래량 순위 빈 결과 (market={market}): {data}")
    return [item['mksc_shrn_iscd'] for item in output[:100]]

def get_daily_ohlcv(token, code, market="J"):
    """일봉 OHLCV — FHKST03010100 날짜범위 API (충분한 데이터 확보)"""
    today = datetime.now(KST).strftime('%Y%m%d')
    start = (datetime.now(KST) - timedelta(days=500)).strftime('%Y%m%d')
    data = kis_get(token, "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice", {
        "FID_COND_MRKT_DIV_CODE": market,
        "FID_INPUT_ISCD": code,
        "FID_INPUT_DATE_1": start,
        "FID_INPUT_DATE_2": today,
        "FID_PERIOD_DIV_CODE": "D",
        "FID_ORG_ADJ_PRC": "0",
    }, "FHKST03010100")
    output = data.get('output2', [])
    # KOSPI로 조회했는데 빈 결과면 KOSDAQ으로 재시도
    if not output and market == "J":
        return get_daily_ohlcv(token, code, market="Q")
    if len(output) < 42:
        return None
    closes  = [float(x['stck_clpr']) for x in output]
    highs   = [float(x.get('stck_hgpr', x['stck_clpr'])) for x in output]
    lows    = [float(x.get('stck_lwpr', x['stck_clpr'])) for x in output]
    volumes = [int(x.get('acml_vol', 0)) for x in output]
    return {'closes': closes, 'highs': highs, 'lows': lows, 'volumes': volumes}

def get_minute_ohlcv(token, code, market="J", n=30):
    """
    1분봉 OHLCV (최신이 index 0)
    스토캐스틱RSI·BB·ATR 계산용
    """
    now = datetime.now(KST)
    data = kis_get(token, "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice", {
        "FID_COND_MRKT_DIV_CODE": market,
        "FID_INPUT_ISCD": code,
        "FID_INPUT_HOUR_1": now.strftime('%H%M%S'),
        "FID_PW_DATA_INCU_YN": "Y",
        "FID_ETC_CLS_CODE": "0",
        "FID_INPUT_DATE_1": now.strftime('%Y%m%d'),
    }, "FHKST03010100")
    output = data.get('output2', [])
    if not output or len(output) < 10:
        print(f"  [1분봉] {code} 데이터 부족 ({len(output)}봉)")
        return None
    result = output[:n]
    closes = [float(x.get('stck_prpr', 0)) for x in result]
    highs  = [float(x.get('stck_hgpr', 0)) for x in result]
    lows   = [float(x.get('stck_lwpr', 0)) for x in result]
    if any(c == 0 for c in closes[:3]):
        return None
    return {'closes': closes, 'highs': highs, 'lows': lows}

def get_current_price(token, code, market="J"):
    data = kis_get(token, "/uapi/domestic-stock/v1/quotations/inquire-price", {
        "FID_COND_MRKT_DIV_CODE": market,
        "FID_INPUT_ISCD": code
    }, "FHKST01010100")
    o = data.get('output', {})
    return {
        'price':      float(o.get('stck_prpr', 0)),
        'open':       float(o.get('stck_oprc', 0)),
        'prev_close': float(o.get('stck_sdpr', 0)),
        'name':       o.get('hts_kor_isnm', code),
        'volume':     int(o.get('acml_vol', 0)),
    }

# ── 지표 계산 ─────────────────────────────────────────────────
def calc_ma(closes, period):
    if len(closes) < period:
        return None
    return sum(closes[:period]) / period

def calc_ema(closes_desc, period):
    if len(closes_desc) < period:
        return None
    asc = list(reversed(closes_desc))
    k = 2 / (period + 1)
    ema = sum(asc[:period]) / period
    for v in asc[period:]:
        ema = v * k + ema * (1 - k)
    return ema

def calc_rsi(closes, period=14):
    """closes: 최신이 index 0"""
    if len(closes) < period + 1:
        return None
    diffs  = [closes[i] - closes[i+1] for i in range(period)]
    gains  = sum(d for d in diffs if d > 0) / period
    losses = sum(abs(d) for d in diffs if d < 0) / period
    if losses == 0:
        return 100.0
    return 100 - (100 / (1 + gains / losses))

def calc_macd(closes_desc, fast=12, slow=26, signal=9):
    """returns: (macd, signal_line, golden_days_ago)"""
    if len(closes_desc) < slow + signal + 6:
        return None, None, None
    asc = list(reversed(closes_desc))
    n = len(asc)
    k_f   = 2 / (fast + 1)
    k_s   = 2 / (slow + 1)
    k_sig = 2 / (signal + 1)
    ema_f = sum(asc[:fast]) / fast
    ema_s = sum(asc[:slow]) / slow
    for i in range(fast, slow):
        ema_f = asc[i] * k_f + ema_f * (1 - k_f)
    macd_series = []
    for i in range(slow, n):
        ema_f = asc[i] * k_f + ema_f * (1 - k_f)
        ema_s = asc[i] * k_s + ema_s * (1 - k_s)
        macd_series.append(ema_f - ema_s)
    if len(macd_series) < signal:
        return None, None, None
    sig = sum(macd_series[:signal]) / signal
    sig_series = [sig]
    for v in macd_series[signal:]:
        sig = v * k_sig + sig * (1 - k_sig)
        sig_series.append(sig)
    golden_days = None
    check = min(6, len(macd_series) - 1, len(sig_series) - 1)
    for i in range(1, check + 1):
        if (macd_series[-(i+1)] < sig_series[-(i+1)] and
                macd_series[-i] >= sig_series[-i]):
            golden_days = i
            break
    return macd_series[-1], sig_series[-1], golden_days

def calc_atr(highs, lows, closes, period=14):
    """ATR. 모두 최신이 index 0. TR = max(H-L, |H-prevC|, |L-prevC|)"""
    if len(closes) < period + 1:
        return None
    trs = []
    for i in range(period):
        tr = max(highs[i] - lows[i],
                 abs(highs[i] - closes[i + 1]),
                 abs(lows[i]  - closes[i + 1]))
        trs.append(tr)
    return sum(trs) / period

def calc_bb(closes, period=20, mult=2.0):
    """볼린저밴드. closes: 최신이 index 0. returns: (upper, mid, lower, bandwidth)"""
    if len(closes) < period:
        return None, None, None, None
    data = closes[:period]
    mid  = sum(data) / period
    var  = sum((x - mid) ** 2 for x in data) / period
    std  = var ** 0.5
    upper = mid + mult * std
    lower = mid - mult * std
    bw    = (upper - lower) / mid if mid != 0 else 0
    return upper, mid, lower, bw

def is_daily_bb_squeeze(closes, period=20, lookback=20):
    """
    일봉 BB 스퀴즈 감지.
    현재 밴드폭이 과거 lookback봉 중 하위 30%이면 True.
    """
    if len(closes) < period + lookback:
        return False
    _, _, _, bw_now = calc_bb(closes, period)
    if bw_now is None:
        return False
    bw_hist = []
    for i in range(1, lookback + 1):
        _, _, _, bw = calc_bb(closes[i:], period)
        if bw is not None:
            bw_hist.append(bw)
    if not bw_hist:
        return False
    threshold = sorted(bw_hist)[int(len(bw_hist) * 0.3)]
    return bw_now <= threshold

def calc_stoch_rsi(closes, rsi_period=14, stoch_period=5, smooth_k=3, smooth_d=3):
    """
    스토캐스틱 RSI (1분봉 최적화: stoch_period=5, 약 25봉 필요)
    closes: 최신이 index 0
    returns: (K, D) — 매수 신호: K > D AND K > 20
    """
    needed = rsi_period + stoch_period + smooth_k + smooth_d + 2
    if len(closes) < needed:
        return None, None

    asc = list(reversed(closes))

    # RSI 시계열
    rsi_vals = []
    for i in range(rsi_period, len(asc)):
        window = list(reversed(asc[i - rsi_period: i + 1]))
        r = calc_rsi(window, rsi_period)
        if r is not None:
            rsi_vals.append(r)
    if len(rsi_vals) < stoch_period:
        return None, None

    # Raw Stochastic K
    raw_k = []
    for i in range(stoch_period - 1, len(rsi_vals)):
        win = rsi_vals[i - stoch_period + 1: i + 1]
        hi, lo = max(win), min(win)
        raw_k.append(50.0 if hi == lo
                     else (rsi_vals[i] - lo) / (hi - lo) * 100)

    # Smooth K (SMA)
    if len(raw_k) < smooth_k:
        return None, None
    k_vals = [sum(raw_k[i - smooth_k + 1: i + 1]) / smooth_k
               for i in range(smooth_k - 1, len(raw_k))]

    # Smooth D (SMA of K)
    if len(k_vals) < smooth_d:
        return None, None
    d_vals = [sum(k_vals[i - smooth_d + 1: i + 1]) / smooth_d
               for i in range(smooth_d - 1, len(k_vals))]

    return k_vals[-1], d_vals[-1]

# ── 계좌 조회 ─────────────────────────────────────────────────
def get_balance(token):
    data = kis_get(token, "/uapi/domestic-stock/v1/trading/inquire-balance", {
        "CANO": ACCOUNT_NO, "ACNT_PRDT_CD": ACCOUNT_PROD,
        "AFHR_FLPR_YN": "N", "OFL_YN": "", "INQR_DVSN": "02",
        "UNPR_DVSN": "01", "FUND_STTL_ICLD_YN": "N",
        "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "00",
        "CTX_AREA_FK100": "", "CTX_AREA_NK100": ""
    }, "TTTC8434R")
    holdings = data.get('output1', [])
    summary  = data.get('output2', [{}])[0]
    cash = float(summary.get('dnca_tot_amt', 0))
    return holdings, cash

# ── 주문 ──────────────────────────────────────────────────────
def place_order(token, code, qty, side="buy"):
    tr_id = "TTTC0802U" if side == "buy" else "TTTC0801U"
    body = {
        "CANO": ACCOUNT_NO, "ACNT_PRDT_CD": ACCOUNT_PROD,
        "PDNO": code, "ORD_DVSN": "01",
        "ORD_QTY": str(qty), "ORD_UNPR": "0"
    }
    result = kis_post(token, "/uapi/domestic-stock/v1/trading/order-cash", body, tr_id)
    print(f"[주문] {side} {code} {qty}주 → {result}")
    return result

# ── [1단계] 일봉 신호 스캔 ────────────────────────────────────
def scan_signals(token):
    """
    일봉 종목 선정 조건:
      ① MA20 상단
      ② MA200 상단 (데이터 충분할 때)
      ③ MACD 골든크로스 5일 이내 + 현재 MACD > Signal
      ④ 볼린저밴드 스퀴즈 OR BB 상단 근접(98%) [기존 RSI 단독 조건 제거]
      ⑤ 거래량 20일 평균 2배 이상
      ⑥ 시간대별 갭 필터: 09:00~09:10(3%), 09:10~11:00(5%), 14:00~14:30(고가98%)
    정렬: 스퀴즈 종목 우선 + 거래량 비율 순
    """
    # [2026-07-02 추가] 시간대별 진입 가능 여부 판정
    now = datetime.now(KST)
    now_hour = now.hour
    now_minute = now.minute
    
    # 진입 시간대 판정
    if 9 <= now_hour < 11:  # 09:00~11:00 (두 단계)
        can_entry = True
    elif 14 <= now_hour < 15 and now_minute < 30:  # 14:00~14:30
        can_entry = True
    else:  # 11:00~14:00, 14:30~15:30, 기타 (신규 진입 없음)
        can_entry = False
    
    if not can_entry:
        print(f"[{now.strftime('%H:%M')}] 신규 진입 시간 아님 — 스캔 스킵")
        return []
    
    # 갭 필터 임계값 설정
    if 9 <= now_hour < 9 or (now_hour == 9 and now_minute < 10):
        gap_threshold = 3.0  # 09:00~09:10: 엄격
    elif 9 <= now_hour < 11:
        gap_threshold = 5.0  # 09:10~11:00: 보통
    elif 14 <= now_hour < 15 and now_minute < 30:
        gap_threshold = float('inf')  # 14:00~14:30: 갭 조건 제거 (대신 고가근접 사용)
    else:
        gap_threshold = 3.0  # 기본값 (도달 불가)
    
    candidates = []
    kospi  = get_volume_rank(token, "J")
    time.sleep(1)
    kosdaq = get_volume_rank(token, "Q")
    kospi_set = set(kospi)
    stocks = list(dict.fromkeys(kospi + kosdaq))
    print(f"스캔 대상: 코스피 {len(kospi)} + 코스닥 {len(kosdaq)} = {len(stocks)}종목")
    if not stocks:
        print("[경고] 스캔 대상 없음")
        return []

    for code in stocks:
        try:
            market = "J" if code in kospi_set else "Q"
            ohlcv = get_daily_ohlcv(token, code, market)
            if not ohlcv:
                continue

            closes  = ohlcv['closes']
            highs   = ohlcv['highs']
            lows    = ohlcv['lows']
            volumes = ohlcv['volumes']

            ma20  = calc_ma(closes, 20)
            ma200 = calc_ma(closes, 200)
            macd, signal_line, golden_days = calc_macd(closes)
            bb_upper, bb_mid, _, bw = calc_bb(closes, 20)
            in_squeeze = is_daily_bb_squeeze(closes)

            if ma20 is None or macd is None or bb_upper is None:
                continue

            vol_avg20 = sum(volumes[1:21]) / 20 if len(volumes) >= 21 else None
            cur = get_current_price(token, code, market)
            price = cur['price']
            if price == 0 or cur['open'] == 0 or cur['prev_close'] == 0:
                continue

            gap = (cur['open'] - cur['prev_close']) / cur['prev_close'] * 100

            # ── 필터 ──────────────────────────────────────────
            if price <= ma20:
                continue
            if ma200 is not None and price <= ma200:
                continue
            if golden_days is None or macd <= signal_line:
                continue
            # BB: 스퀴즈 상태 OR BB 상단 근접 (기존 RSI 조건 대체)
            bb_near_upper = (price >= bb_upper * 0.98)
            if not (in_squeeze or bb_near_upper):
                continue
            if vol_avg20 and cur['volume'] < vol_avg20 * 2:
                continue
            # [2026-07-02] 시간대별 갭 필터 적용
            if gap >= gap_threshold:
                continue
            # 14:00~14:30 구간: 갭 조건 대신 당일 고가 98% 이상 근접 확인
            if now_hour == 14 and now_minute < 30 and gap_threshold == float('inf'):
                today_high = highs[0]
                if today_high < cur['open'] * 0.98:
                    continue  # 고가 98% 이상 미달 → 진입 안 함

            vol_ratio = cur['volume'] / vol_avg20 if vol_avg20 else 0
            ma200_str  = f"{ma200:,.0f}" if ma200 else "N/A"
            bb_tag     = "스퀴즈" if in_squeeze else "BB상단근접"
            print(f"  [일봉] {cur['name']} MACD={golden_days}일전 "
                  f"갭={gap:+.1f}% 거래량={vol_ratio:.1f}배 "
                  f"BB={bb_tag} MA200={ma200_str}")

            candidates.append({
                'code': code, 'name': cur['name'], 'market': market,
                'price': price, 'ma20': ma20, 'ma200': ma200,
                'macd': macd, 'golden_days': golden_days,
                'gap': gap, 'vol_ratio': vol_ratio,
                'in_squeeze': in_squeeze, 'bw': bw,
            })
            time.sleep(0.06)

        except Exception as e:
            print(f"  오류 {code}: {e}")
            continue

    # 스퀴즈 우선, 동순위 내 거래량 비율 순
    candidates.sort(key=lambda x: (-int(x['in_squeeze']), -x['vol_ratio']))
    return candidates

# ── [2단계] 1분봉 진입 타이밍 확인 ──────────────────────────────
def check_1min_entry(token, code, name, market="J"):
    """
    1분봉 기준 진입 최종 확인
      ① 스토캐스틱RSI: K > D AND K > 20
      ② 현재가 >= 1분봉 볼린저밴드 중심선(MA20)
    ATR(14) × 1.5 = 동적 손절가 계산
    returns: (ok, stop_price, target_price, info_str)
    """
    minute = get_minute_ohlcv(token, code, market)
    if not minute:
        return False, 0, 0, "1분봉 데이터 없음"

    closes = minute['closes']
    highs  = minute['highs']
    lows   = minute['lows']
    price  = closes[0]

    # ① 스토캐스틱 RSI
    stoch_k, stoch_d = calc_stoch_rsi(closes)
    if stoch_k is None:
        return False, 0, 0, "스토캐스틱RSI 계산 불가"
    stoch_ok = (stoch_k > stoch_d and stoch_k > 20)

    # ② 1분봉 BB 중심선
    _, bb_mid_1m, _, _ = calc_bb(closes, period=20)
    bb_ok = (bb_mid_1m is not None and price >= bb_mid_1m)

    # ③ ATR×1.5 손절가
    atr_1m = calc_atr(highs, lows, closes, period=14)
    if atr_1m:
        stop_price = price - atr_1m * 1.5
        stop_price = min(stop_price, price * 0.995)  # 최소 -0.5% 보호
    else:
        stop_price = price * 0.98  # ATR 계산 불가 시 폴백

    target_price = price * 1.04

    atr_str = f"{atr_1m:.0f}" if atr_1m else "N/A"
    mid_str = f"{bb_mid_1m:.0f}" if bb_mid_1m else "N/A"
    s_tag = "✓" if stoch_ok else "✗"
    b_tag = "✓" if bb_ok else "✗"
    info = (f"StochRSI K={stoch_k:.1f}/D={stoch_d:.1f}({s_tag}) "
            f"BB중심={mid_str}({b_tag}) "
            f"ATR={atr_str} 손절={stop_price:,.0f}")

    print(f"  [1분봉] {name}: {info}")

    if stoch_ok and bb_ok:
        return True, stop_price, target_price, info
    return False, 0, 0, info

# ── 메인 ──────────────────────────────────────────────────────
def main():
    now = datetime.now(KST)
    print(f"\n=== 자동매매 {now.strftime('%m/%d %H:%M:%S')} ===")

    market_open   = now.replace(hour=9,  minute=0,  second=0, microsecond=0)
    force_sell_at = now.replace(hour=15, minute=20, second=0, microsecond=0)
    market_close  = now.replace(hour=15, minute=30, second=0, microsecond=0)
    entry_cutoff  = now.replace(hour=11, minute=0,  second=0, microsecond=0)

    if now < market_open or now > market_close:
        print("장 시간 외 — 종료")
        return

    kis_token   = get_kis_token()
    kakao_token = get_kakao_token()
    dash = load_dashboard()

    try:
        holdings, cash = get_balance(kis_token)
        # ① 보유 포지션 관리 ──────────────────────────────────
        active = [h for h in holdings if int(h.get('hldg_qty', 0)) > 0]
        if active:
            h = active[0]
            code      = h['pdno']
            name      = h['prdt_name']
            qty       = int(h['hldg_qty'])
            avg_price = float(h['pchs_avg_pric'])
            cur_price = float(h['prpr'])
            pnl       = (cur_price - avg_price) / avg_price * 100

            # position 데이터에서 손절가·익절가 복원 (없으면 폴백)
            pos          = dash.get('position') or {}
            stop_price   = pos.get('stop_price',   avg_price * 0.98)
            target_price = pos.get('target_price', avg_price * 1.04)

            print(f"보유: {name} {qty}주 | 평균가:{avg_price:,.0f} 현재:{cur_price:,.0f} ({pnl:+.2f}%)")
            print(f"  손절:{stop_price:,.0f} | 익절:{target_price:,.0f}")
            update_position_price(dash, cur_price)

            sell, reason = False, ""
            
            # [2026-07-02 추가] 트레일링 스탑 (+2% 도달시)
            if not pos.get('trailing_activated') and pnl >= 2.0:
                trailing_stop = avg_price * 1.004  # 본전+0.4%
                pos['stop_price'] = max(stop_price, trailing_stop)
                pos['trailing_activated'] = True
                stop_price = pos['stop_price']
                print(f"  [트레일링] +2% 도달 → 손절선 상향: {stop_price:,.0f}")
            
            # [2026-07-02 추가] 2시간 부분청산 (0~+1% 구간)
            if pos.get('entry_time'):
                entry_dt = datetime.fromisoformat(pos['entry_time'])
                elapsed_sec = (now - entry_dt).total_seconds()
                two_hours = 2 * 3600
                if abs(elapsed_sec - two_hours) <= 120:  # ±2분
                    if 0 <= pnl <= 1.0:
                        sell_qty = qty // 2
                        if sell_qty >= 1:
                            place_order(kis_token, code, sell_qty, "sell")
                            time.sleep(1)
                            _, new_cash = get_balance(kis_token)
                            pos['stop_price'] = avg_price * 1.004
                            dash['current_balance'] = int(new_cash)
                            print(f"  [2시간] 50% 부분청산 ({sell_qty}주) → 나머지 손절: {pos['stop_price']:,.0f}")
                            save_dashboard(dash)
                            return
            
            if cur_price >= target_price:
                sell, reason = True, f"익절 ({pnl:+.2f}%)"
            elif cur_price <= stop_price:
                sell, reason = True, f"손절 ({pnl:+.2f}%)"
            elif now >= force_sell_at:
                sell, reason = True, "강제청산 (15:20)"

            if sell:
                place_order(kis_token, code, qty, "sell")
                time.sleep(1)
                _, new_cash = get_balance(kis_token)
                pnl_amt = int((cur_price - avg_price) * qty)
                log_sell(dash, name, qty, avg_price, cur_price,
                         pnl, pnl_amt, reason, new_cash)
                save_dashboard(dash)
                msg = (f"📤 매도\n{name} {qty}주\n"
                       f"사유: {reason}\n"
                       f"손익: {pnl:+.2f}% ({pnl_amt:+,}원)\n"
                       f"💰 잔고: {new_cash:,.0f}원")
                send_kakao(kakao_token, msg)
            else:
                save_dashboard(dash)
            return

        # ② 신규 진입 스캔 (11시 이전만) ─────────────────────
        if now >= entry_cutoff:
            print("11시 이후 — 신규 진입 없음")
            return

        print("포지션 없음 → [1단계] 일봉 신호 스캔")
        candidates = scan_signals(kis_token)
        if not candidates:
            print("일봉 조건 충족 종목 없음")
            return

        # 상위 3 후보 → 1분봉 타이밍 확인
        top_n = min(3, len(candidates))
        print(f"\n→ [2단계] 1분봉 진입 타이밍 확인 (상위 {top_n}종목)")
        chosen = None
        for c in candidates[:top_n]:
            time.sleep(0.1)
            ok, stop_px, target_px, info = check_1min_entry(
                kis_token, c['code'], c['name'], c.get('market', 'J'))
            if ok:
                chosen = {**c, 'stop_price': stop_px, 'target_price': target_px}
                print(f"  → 진입 결정: {c['name']}")
                break
            print(f"  → 패스: {c['name']}")

        if not chosen:
            print("1분봉 타이밍 조건 미충족 — 다음 스캔 대기")
            return

        # ③ 매수 ──────────────────────────────────────────────
        price = chosen['price']
        qty   = int(min(cash, MAX_BET) / price)
        if qty < 1:
            print(f"매수 수량 부족 (가격:{price:,}원)")
            return

        place_order(kis_token, chosen['code'], qty, "buy")
        used = int(price * qty)
        log_buy(dash, chosen['code'], chosen['name'], qty, price,
                cash - used, chosen['stop_price'], chosen['target_price'])
        save_dashboard(dash)

        ma200_str   = f"{chosen['ma200']:,.0f}" if chosen['ma200'] else "N/A"
        squeeze_tag = "🔥스퀴즈" if chosen['in_squeeze'] else "📈BB상단"
        msg = (f"📥 매수 {squeeze_tag}\n{chosen['name']} {qty}주\n"
               f"가격: {price:,.0f}원\n"
               f"MACD크로스: {chosen['golden_days']}일전 | 거래량: {chosen['vol_ratio']:.1f}배\n"
               f"갭: {chosen['gap']:+.1f}% | MA200: {ma200_str}\n"
               f"익절: {chosen['target_price']:,.0f} | ATR손절: {chosen['stop_price']:,.0f}\n"
               f"💰 투입: {used:,.0f}원")
        send_kakao(kakao_token, msg)

    except Exception as e:
        err_msg = f"\u26a0\ufe0f \uc790\ub3d9\ub9e4\ub9e4 \uc624\ub958\\n{str(e)[:150]}"
        print(err_msg)
        try:
            send_kakao(kakao_token, err_msg)
        except Exception:
            pass

if __name__ == '__main__':
    main()

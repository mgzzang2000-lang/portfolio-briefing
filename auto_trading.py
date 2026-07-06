# 자동매매 — 2단계 추세 추종 단타 전략
# ═══════════════════════════════════════════════════════════
# [1단계: 일봉] 종목 선정
#   MA20 상단, 볼린저밴드 스퀴즈 or BB 상단 근접,
#   거래량 2배(시간 경과 비례 보정), 갭 필터
#   [2026-07-02] MA200 필터 제거, MACD 관련 조건/계산 전체 삭제
#   [2026-07-02] MA20 "당일 신규 돌파" 여부는 제외 조건이 아니라
#                정렬 우선순위로 사용 (1순위: 당일 신규 돌파, 2순위: 기존 상단 유지)
#   [2026-07-02] 09:00~09:10 갭 기준 3% -> 5%로 완화 (09:10~11:00과 동일)
#   [2026-07-05] 주문(rt_cd) 성공여부 미확인 버그 수정 — 매수/매도/2시간부분청산 모두
#                주문 실패 시 카톡 알림만 보내고 상태를 변경하지 않도록 수정
#   [2026-07-05] 보유 포지션 식별을 holdings[0] 대신 dash['position']['code']와
#                일치하는 종목으로 한정 — 계좌에 봇이 사지 않은 종목이 있어도 오작동 방지
#   [2026-07-06] 하루 연속 손절 2회 도달 시 당일 신규 진입 중단 (daily_loss_guard)
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
MAX_BET = 300_000  # [2026-07-06] 눌림목+불플래그 진입 로직 검증 전까지 축소 운용 (20만→30만 조정)
DERIVATIVE_ETF_KEYWORDS = ["레버리지", "인버스", "ETN", "선물"]  # [2026-07-06] 파생상품 ETF/ETN 매수 제외
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
DAILY_LOSS_LIMIT = 2  # [2026-07-06] 하루 연속 손절 한도 — 도달하면 당일 신규 진입 중단
def check_daily_guard(dash, now):
    """오늘 날짜 기준 연속 손절 횟수 관리. 날짜가 바뀌면 초기화."""
    today_str = now.strftime('%m/%d')
    guard = dash.setdefault('daily_loss_guard', {})
    if guard.get('date') != today_str:
        guard['date'] = today_str
        guard['consecutive_losses'] = 0
        guard['notified'] = False
    return guard
def log_buy(dash, code, name, qty, price, cash_after, stop_price, target_price, market="J"):
    """stop_price: ATR×1.5 동적 손절가, target_price: +4% 익절가"""
    entry_time = datetime.now(KST)
    dash['trades'].append({
        'action': 'buy',
        'date': entry_time.strftime('%m/%d %H:%M'),
        'code': code, 'stock': name, 'qty': qty,
        'price': int(price), 'amount': int(price * qty)
    })
    dash['position'] = {
        'code': code, 'name': name, 'qty': qty, 'market': market,
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
    }, "FHKST03010200")  # [2026-07-03] 버그 수정: 일봉 TR ID(FHKST03010100)가 잘못 들어가 있어 1분봉 조회가 항상 실패했음
    output = data.get('output2', [])
    if not output or len(output) < 10:
        print(f"  [1분봉] {code} 데이터 부족 ({len(output)}봉)")
        return None
    result = output[:n]
    closes  = [float(x.get('stck_prpr', 0)) for x in result]
    highs   = [float(x.get('stck_hgpr', 0)) for x in result]
    lows    = [float(x.get('stck_lwpr', 0)) for x in result]
    volumes = [int(x.get('cntg_vol', 0)) for x in result]  # 해당 1분봉 체결거래량
    if any(c == 0 for c in closes[:3]):
        return None
    return {'closes': closes, 'highs': highs, 'lows': lows, 'volumes': volumes}
def get_current_price(token, code, market="J"):
    data = kis_get(token, "/uapi/domestic-stock/v1/quotations/inquire-price", {
        "FID_COND_MRKT_DIV_CODE": market,
        "FID_INPUT_ISCD": code
    }, "FHKST01010100")
    o = data.get('output', {})
    return {
        'price':       float(o.get('stck_prpr', 0)),
        'open':        float(o.get('stck_oprc', 0)),
        'prev_close':  float(o.get('stck_sdpr', 0)),
        'name':        o.get('hts_kor_isnm', code),
        'volume':      int(o.get('acml_vol', 0)),
        'upper_limit': float(o.get('stck_mxpr', 0)),  # 당일 상한가
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
    """returns: (ok, result) — ok=True는 rt_cd == '0'(정상접수)일 때만"""
    tr_id = "TTTC0802U" if side == "buy" else "TTTC0801U"
    body = {
        "CANO": ACCOUNT_NO, "ACNT_PRDT_CD": ACCOUNT_PROD,
        "PDNO": code, "ORD_DVSN": "01",
        "ORD_QTY": str(qty), "ORD_UNPR": "0"
    }
    result = kis_post(token, "/uapi/domestic-stock/v1/trading/order-cash", body, tr_id)
    ok = result.get('rt_cd') == '0'
    print(f"[주문] {side} {code} {qty}주 → {'성공' if ok else '실패'} {result}")
    return ok, result
# ── [1단계] 일봉 신호 스캔 ────────────────────────────────────
def scan_signals(token):
    """
    일봉 종목 선정 조건:
      ① MA20 상단 (당일 신규 돌파 여부는 제외 조건이 아니라 정렬 우선순위로만 사용)
      ② [2026-07-02 제거] MA200 필터 — 장기추세 조건 없음
      ③ [2026-07-02 제거] MACD 관련 조건 전체 삭제
      ④ 볼린저밴드 스퀴즈 OR BB 상단 근접(98%)
      ⑤ 거래량 20일 평균 2배 이상 (시간 경과 비례 보정)
      ⑥ 시간대별 갭 필터: 09:00~09:10(5%), 09:10~11:00(5%), 14:00~14:30(고가98%)
    정렬: 1순위 당일 신규 돌파 > 2순위 스퀴즈 종목 > 3순위 거래량 비율 순
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
    # [2026-07-02] 09:00~09:10 구간도 3% -> 5%로 완화 (09:10~11:00과 동일)
    if now_hour == 9 and now_minute < 10:
        gap_threshold = 5.0  # 09:00~09:10
    elif 9 <= now_hour < 11:
        gap_threshold = 5.0  # 09:10~11:00
    elif 14 <= now_hour < 15 and now_minute < 30:
        gap_threshold = float('inf')  # 14:00~14:30: 갭 조건 제거 (대신 고가근접 사용)
    else:
        gap_threshold = 5.0  # 기본값 (도달 불가)

    # [2026-07-02] 시간 경과 비례 거래량 기대치
    # 오늘 누적거래량(장중 계속 증가)을 과거 20일 '하루 종일' 평균과 시간 보정 없이
    # 비교하면 09:05에 어제 하루치의 2배를 요구하는 셈이 되어 아침 시간대만 과도하게
    # 엄격해짐. 경과 시간 비율만큼 기대치를 낮춰서 비교한다.
    market_open_dt = now.replace(hour=9, minute=0, second=0, microsecond=0)
    elapsed_minutes = max(1.0, min(390.0, (now - market_open_dt).total_seconds() / 60))

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
            ma200 = calc_ma(closes, 200)  # [2026-07-02] 필터에는 미사용, 표시/메시지용으로만 계산
            bb_upper, bb_mid, _, bw = calc_bb(closes, 20)
            in_squeeze = is_daily_bb_squeeze(closes)
            if ma20 is None or bb_upper is None:
                continue
            vol_avg20 = sum(volumes[1:21]) / 20 if len(volumes) >= 21 else None
            cur = get_current_price(token, code, market)
            price = cur['price']
            if price == 0 or cur['open'] == 0 or cur['prev_close'] == 0:
                continue
            # [2026-07-06] 파생상품 ETF/ETN(레버리지·인버스·선물 등) 거래 제외
            if any(kw in cur['name'] for kw in DERIVATIVE_ETF_KEYWORDS):
                continue
            gap = (cur['open'] - cur['prev_close']) / cur['prev_close'] * 100
            # ── 필터 ──────────────────────────────────────────
            if price <= ma20:
                continue
            # [2026-07-02] 당일 신규 돌파 여부 — 제외하지 않고 "우선순위" 태그로만 사용.
            # 어제 종가가 20일선 이하였다가 오늘 처음 뚫은 종목이면 True(1순위 후보),
            # 어제 이미 20일선 위였던 종목(시세가 이미 나온 경우가 많음)은 False(2순위 후보)
            # 로 분류만 하고, 여전히 후보 목록에는 포함시킨다.
            ma20_prev = calc_ma(closes[1:], 20)
            is_new_breakout = (
                ma20_prev is not None and len(closes) > 1 and closes[1] <= ma20_prev
            )
            # [2026-07-02 제거] MA200 필터 — 아래 조건 비활성화
            # if ma200 is not None and price <= ma200:
            #     continue
            # [2026-07-02 제거] MACD 관련 조건 전체 삭제 (calc_macd 호출도 제거됨)
            # BB: 스퀴즈 상태 OR BB 상단 근접 (기존 RSI 조건 대체)
            bb_near_upper = (price >= bb_upper * 0.98)
            if not (in_squeeze or bb_near_upper):
                continue
            if vol_avg20 and cur['volume'] < vol_avg20 * (elapsed_minutes / 390) * 2:
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
            breakout_tag = "당일돌파(1순위)" if is_new_breakout else "기존상단(2순위)"
            print(f"  [일봉] {cur['name']} {breakout_tag} "
                  f"갭={gap:+.1f}% 거래량={vol_ratio:.1f}배 "
                  f"BB={bb_tag} MA200(참고)={ma200_str}")
            candidates.append({
                'code': code, 'name': cur['name'], 'market': market,
                'price': price, 'ma20': ma20, 'ma200': ma200,
                'gap': gap, 'vol_ratio': vol_ratio,
                'in_squeeze': in_squeeze, 'bw': bw,
                'is_new_breakout': is_new_breakout,
            })
            time.sleep(0.06)
        except Exception as e:
            print(f"  오류 {code}: {e}")
            continue
    # [2026-07-02] 정렬: 1순위 당일 신규 돌파 > 2순위 스퀴즈 종목 > 3순위 거래량 비율 순
    candidates.sort(key=lambda x: (-int(x['is_new_breakout']), -int(x['in_squeeze']), -x['vol_ratio']))
    return candidates
# ── [2단계] 1분봉 진입 타이밍 확인 ──────────────────────────────
def check_pullback_reclaim(closes, highs, lows, volumes, lookback=15):
    """
    [2026-07-06 추가] 눌림목+불플래그 진입 필터.
    "이미 26~30% 오른 상태에서 즉시 매수"하던 문제를 해결하기 위해,
    돌파 즉시가 아니라 ①상승(깃대) → ②거래량 줄며 쉬는 눌림 → ③거래량 급증하며
    직전 고점 재돌파, 이 3단계를 확인한 뒤에만 진입 신호를 준다.
    closes/highs/lows/volumes: 최신이 index 0.
    반환: (ok, info_str)
    """
    if len(closes) < lookback + 3 or len(volumes) < lookback + 3:
        return False, "데이터 부족(눌림목 확인 불가)"
    # 분석 편의를 위해 오래된 → 최신 순으로 변환
    asc_c = list(reversed(closes[:lookback]))
    asc_h = list(reversed(highs[:lookback]))
    asc_l = list(reversed(lows[:lookback]))
    asc_v = list(reversed(volumes[:lookback]))

    # 최근 3봉은 "재돌파 확인" 구간으로 남겨두고, 그 이전 구간에서 고점(깃대 상단) 탐색
    search_end = lookback - 3
    if search_end < 3:
        return False, "탐색 구간 부족"
    peak_idx = max(range(search_end), key=lambda i: asc_h[i])
    peak_price = asc_h[peak_idx]
    flag_start = max(0, peak_idx - 3)
    flagpole_vol = sum(asc_v[flag_start:peak_idx + 1]) / max(1, peak_idx + 1 - flag_start)

    # 고점 이후 ~ 직전 봉까지가 눌림(횡보/조정) 구간
    pullback_range = range(peak_idx + 1, lookback - 1)
    if len(list(pullback_range)) < 2:
        return False, "눌림 구간 부족"
    trough_price = min(asc_l[i] for i in pullback_range)
    pullback_vol = sum(asc_v[i] for i in pullback_range) / len(list(pullback_range))

    pullback_pct = (peak_price - trough_price) / peak_price * 100 if peak_price else 0
    cur_close = asc_c[-1]
    cur_vol   = asc_v[-1]

    healthy_pullback = 0.2 <= pullback_pct <= 4.0        # 너무 얕지도(노이즈) 깊지도(추세훼손) 않은 눌림
    volume_dried_up  = pullback_vol < flagpole_vol * 0.8  # 눌림 구간엔 거래량이 줄어야 건강한 조정
    reclaim          = cur_close >= peak_price * 0.997    # 직전 고점 근처까지 재돌파
    volume_surge     = cur_vol >= pullback_vol * 1.5      # 재돌파 시 거래량 급증

    ok = healthy_pullback and volume_dried_up and reclaim and volume_surge
    info = (f"눌림{pullback_pct:.1f}%({'✓' if healthy_pullback else '✗'}) "
            f"거래량감소{'✓' if volume_dried_up else '✗'} "
            f"재돌파{'✓' if reclaim else '✗'} "
            f"거래량급증{'✓' if volume_surge else '✗'}")
    return ok, info
def check_1min_entry(token, code, name, market="J"):
    """
    1분봉 기준 진입 최종 확인
      ① 스토캐스틱RSI: K > D AND K > 20
      ② [2026-07-06 변경] 단순 "BB중심선 위" 대신 눌림목+불플래그(재돌파+거래량급증) 확인
    ATR(14) × 1.5 = 동적 손절가 계산
    returns: (ok, stop_price, target_price, info_str)
    """
    minute = get_minute_ohlcv(token, code, market)
    if not minute:
        return False, 0, 0, "1분봉 데이터 없음"
    closes  = minute['closes']
    highs   = minute['highs']
    lows    = minute['lows']
    volumes = minute['volumes']
    price   = closes[0]
    # ① 스토캐스틱 RSI
    stoch_k, stoch_d = calc_stoch_rsi(closes)
    if stoch_k is None:
        return False, 0, 0, "스토캐스틱RSI 계산 불가"
    stoch_ok = (stoch_k > stoch_d and stoch_k > 20)
    # ② [2026-07-06] 눌림목+불플래그 재돌파 확인 (기존 "BB중심선 위" 단순조건 대체)
    pullback_ok, pullback_info = check_pullback_reclaim(closes, highs, lows, volumes)
    # ③ ATR×1.5 손절가
    atr_1m = calc_atr(highs, lows, closes, period=14)
    if atr_1m:
        stop_price = price - atr_1m * 1.5
        stop_price = min(stop_price, price * 0.985)  # 최소 -1.5% 보호
    else:
        stop_price = price * 0.98  # ATR 계산 불가 시 폴백
    target_price = price * 1.04
    atr_str = f"{atr_1m:.0f}" if atr_1m else "N/A"
    s_tag = "✓" if stoch_ok else "✗"
    info = (f"StochRSI K={stoch_k:.1f}/D={stoch_d:.1f}({s_tag}) "
            f"{pullback_info} "
            f"ATR={atr_str} 손절={stop_price:,.0f}")
    print(f"  [1분봉] {name}: {info}")
    if stoch_ok and pullback_ok:
        return True, stop_price, target_price, info
    return False, 0, 0, info
# ── 보유 포지션 관리 (GitHub Actions·로컬 감시 스크립트 공용) ──────
def manage_position(kis_token, kakao_token, dash, guard, now, h, force_sell_at):
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

    # [2026-07-06 추가] 상한가 익절 — 당일 상한가에 도달하면 다른 조건보다 우선 즉시 전량 익절
    # market 정보가 없는 구(舊) 포지션(이 기능 추가 이전 매수분) 대비, 조회 실패 시 반대 시장으로 재시도
    quote = get_current_price(kis_token, code, pos.get('market', 'J'))
    if quote['upper_limit'] == 0:
        quote = get_current_price(kis_token, code, 'Q' if pos.get('market', 'J') == 'J' else 'J')
    if quote['upper_limit'] > 0 and cur_price >= quote['upper_limit'] * 0.995:
        sell, reason = True, f"상한가 익절 ({pnl:+.2f}%)"

    # [2026-07-06 변경] +2% 도달 시 50% 부분익절 + 나머지 손절선을 본전+0.4%로 상향
    # (기존엔 손절선만 올리고 팔지는 않았음 — 사용자 요청으로 절반은 여기서 확정 실현)
    # 상한가로 이미 전량 익절이 확정된 경우엔 건너뜀(중복 매도 방지)
    if not sell and not pos.get('trailing_activated') and pnl >= 2.0:
        pos['trailing_activated'] = True
        pos['stop_price'] = max(stop_price, avg_price * 1.004)  # 본전+0.4%
        stop_price = pos['stop_price']
        half_qty = qty // 2
        if half_qty >= 1:
            ok, order_result = place_order(kis_token, code, half_qty, "sell")
            if not ok:
                err_msg = f"⚠️ +2% 부분익절 주문 실패\n{name} {half_qty}주\n{order_result.get('msg1', '')}"
                print(err_msg)
                send_kakao(kakao_token, err_msg)
                save_dashboard(dash)
                return
            time.sleep(1)
            _, new_cash = get_balance(kis_token)
            dash['current_balance'] = int(new_cash)
            print(f"  [+2%] 50% 부분익절 ({half_qty}주) → 나머지 손절선: {stop_price:,.0f}")
            save_dashboard(dash)
            return
        else:
            print(f"  [트레일링] +2% 도달 → 손절선 상향: {stop_price:,.0f} (수량 부족으로 부분익절 스킵)")

    # [2026-07-02 추가] 2시간 부분청산 (0~+1% 구간)
    if pos.get('entry_time'):
        entry_dt = datetime.fromisoformat(pos['entry_time'])
        elapsed_sec = (now - entry_dt).total_seconds()
        two_hours = 2 * 3600
        if abs(elapsed_sec - two_hours) <= 120:  # ±2분
            if 0 <= pnl <= 1.0:
                sell_qty = qty // 2
                if sell_qty >= 1:
                    ok, order_result = place_order(kis_token, code, sell_qty, "sell")
                    if not ok:
                        err_msg = f"⚠️ 2시간 부분청산 주문 실패\n{name} {sell_qty}주\n{order_result.get('msg1', '')}"
                        print(err_msg)
                        send_kakao(kakao_token, err_msg)
                        save_dashboard(dash)
                        return
                    time.sleep(1)
                    _, new_cash = get_balance(kis_token)
                    pos['stop_price'] = avg_price * 1.004
                    dash['current_balance'] = int(new_cash)
                    print(f"  [2시간] 50% 부분청산 ({sell_qty}주) → 나머지 손절: {pos['stop_price']:,.0f}")
                    save_dashboard(dash)
                    return

    if sell:
        pass  # 상한가 익절이 이미 확정된 경우 아래 조건으로 덮어쓰지 않음
    elif cur_price >= target_price:
        sell, reason = True, f"익절 ({pnl:+.2f}%)"
    elif cur_price <= stop_price:
        sell, reason = True, f"손절 ({pnl:+.2f}%)"
    elif now >= force_sell_at:
        sell, reason = True, "강제청산 (15:20)"
    if sell:
        ok, order_result = place_order(kis_token, code, qty, "sell")
        if not ok:
            err_msg = f"⚠️ 매도 주문 실패\n{name} {qty}주\n사유: {reason}\n{order_result.get('msg1', '')}"
            print(err_msg)
            send_kakao(kakao_token, err_msg)
            save_dashboard(dash)
            return
        time.sleep(1)
        _, new_cash = get_balance(kis_token)
        pnl_amt = int((cur_price - avg_price) * qty)
        log_sell(dash, name, qty, avg_price, cur_price,
                 pnl, pnl_amt, reason, new_cash)
        guard['consecutive_losses'] = guard.get('consecutive_losses', 0) + 1 if pnl < 0 else 0
        save_dashboard(dash)
        msg = (f"📤 매도\n{name} {qty}주\n"
               f"사유: {reason}\n"
               f"손익: {pnl:+.2f}% ({pnl_amt:+,}원)\n"
               f"💰 잔고: {new_cash:,.0f}원")
        send_kakao(kakao_token, msg)
    else:
        save_dashboard(dash)
# ── 메인 ──────────────────────────────────────────────────────
def main():
    now = datetime.now(KST)
    print(f"\n=== 자동매매 {now.strftime('%m/%d %H:%M:%S')} ===")
    market_open   = now.replace(hour=9,  minute=0,  second=0, microsecond=0)
    force_sell_at = now.replace(hour=15, minute=20, second=0, microsecond=0)
    market_close  = now.replace(hour=15, minute=30, second=0, microsecond=0)
    if now < market_open or now > market_close:
        print("장 시간 외 — 종료")
        return
    kis_token   = get_kis_token()
    kakao_token = get_kakao_token()
    dash = load_dashboard()
    guard = check_daily_guard(dash, now)
    try:
        holdings, cash = get_balance(kis_token)
        # ① 보유 포지션 관리 ──────────────────────────────────
        # [2026-07-05] 계좌 보유종목 중 "봇이 직접 산 종목(dash['position']['code'])"만
        # 내 포지션으로 취급. 사용자가 계좌에 다른 종목을 들고 있어도 그건 건드리지 않는다.
        dash_position = dash.get('position')
        bot_code = dash_position['code'] if dash_position else None
        matched = [h for h in holdings
                   if bot_code and h.get('pdno') == bot_code and int(h.get('hldg_qty', 0)) > 0]
        if not matched and dash_position:
            # 대시보드엔 포지션이 남아있는데 실제 계좌엔 없음(수동 매도 등) — 상태만 정리
            print(f"[경고] 대시보드 포지션({bot_code})이 실제 계좌에 없음 — 상태 초기화")
            dash['position'] = None
            save_dashboard(dash)
            return
        active = matched
        if active:
            manage_position(kis_token, kakao_token, dash, guard, now, active[0], force_sell_at)
            return
        # ② 신규 진입 스캔 (시간대별 진입 가능 여부는 scan_signals 내부에서 판정)
        if guard.get('consecutive_losses', 0) >= DAILY_LOSS_LIMIT:
            if not guard.get('notified'):
                guard['notified'] = True
                save_dashboard(dash)
                send_kakao(kakao_token,
                           f"🛑 오늘 연속 손절 {guard['consecutive_losses']}회 — 신규 진입을 중단합니다 (내일 재개)")
            print(f"[일일 가드] 연속 손절 {guard['consecutive_losses']}회 — 신규 진입 중단")
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
        ok, order_result = place_order(kis_token, chosen['code'], qty, "buy")
        if not ok:
            err_msg = f"⚠️ 매수 주문 실패\n{chosen['name']} {qty}주\n{order_result.get('msg1', '')}"
            print(err_msg)
            send_kakao(kakao_token, err_msg)
            return
        used = int(price * qty)
        log_buy(dash, chosen['code'], chosen['name'], qty, price,
                cash - used, chosen['stop_price'], chosen['target_price'],
                chosen.get('market', 'J'))
        save_dashboard(dash)
        ma200_str   = f"{chosen['ma200']:,.0f}" if chosen['ma200'] else "N/A"
        squeeze_tag = "🔥스퀴즈" if chosen['in_squeeze'] else "📈BB상단"
        breakout_tag = "🆕당일돌파" if chosen['is_new_breakout'] else "기존상단"
        msg = (f"📥 매수 {squeeze_tag} {breakout_tag}\n{chosen['name']} {qty}주\n"
               f"가격: {price:,.0f}원\n"
               f"거래량: {chosen['vol_ratio']:.1f}배\n"
               f"갭: {chosen['gap']:+.1f}% | MA200(참고): {ma200_str}\n"
               f"익절: {chosen['target_price']:,.0f} | ATR손절: {chosen['stop_price']:,.0f}\n"
               f"💰 투입: {used:,.0f}원")
        send_kakao(kakao_token, msg)
    except Exception as e:
        err_msg = f"⚠️ 자동매매 오류\n{str(e)[:150]}"
        print(err_msg)
        try:
            send_kakao(kakao_token, err_msg)
        except Exception:
            pass
if __name__ == '__main__':
    main()

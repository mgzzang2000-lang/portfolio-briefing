#!/usr/bin/env python3
"""
전략 성격 비교용 섀도우 스캐너 — 실제 주문은 절대 내지 않고, 두 가지 대안
조건식으로 포착됐을 후보만 기록한다. 목적은 "지금 실거래 전략(눌림목 재돌파)을
파라미터만 바꿔볼까 vs 아예 다른 성격(초기 모멘텀 돌파)이 나을까"를, 실제 자금을
더 걸지 않고 판단할 데이터를 쌓는 것.

[섀도우 A] 눌림목 전략은 그대로 두되, 일봉 후보 조건에서 "BB상단 98% 근접"
   대신 "20봉 신고가/최고종가" 기준으로 바꾸면 후보군이 어떻게 달라지는지 비교.
   (BB상단98%는 그 종목 자신의 변동성이 커질수록 밴드가 벌어져 "이미 많이
   오른 종목"을 고르는 구조적 편향이 있다는 문제가 논의됐었음 — 신고가 기준은
   가격 자체 기준이라 이 편향이 없음)

[섀도우 B] 성격이 다른 "시가돌파형" — 눌림목 확인 없이 당일 시가를 막 돌파하는
   초기 모멘텀에 바로 반응하는 스타일. 실거래 봇이 "이미 오른 상태에서 진입"
   문제를 피하려고 일부러 버린 방식이라 실거래엔 안 쓰지만, 지금 시장 성격에
   어느 쪽이 더 맞는지 비교하기 위해 후보만 기록.
   [주의] 체결강도(매수/매도 체결강도 비율) 조건은 키움 HTS 전용 지표라
   KIS REST API(inquire-price, inquire-ccnl 확인함)에 해당 필드가 없음.
   대신 inquire-ccnl 틱을 상승틱/하락틱으로 분류해 근사치를 계산해 참고용으로만
   기록하고, 필터링 기준으로는 쓰지 않음(근사치라 오탐 위험이 있어서).

[섀도우 C] FVG(Fair Value Gap) + 유동성 스윕 결합 — "쉽알남" 스마트머니 트레이딩
   노트 참고(2026-07-10). 오더블럭/추세선/채널은 노트 스스로도 "경험적/주관적
   판단이 필요하다"고 인정하는 기준이라 자동화 코드로 명확히 정의하기 어려워
   보류하고, 3개 캔들 구조로 숫자 기준이 딱 떨어지는 FVG만 우선 구현. 눌림목
   (A/B)과 달리 일봉이 아니라 1분봉을 직접 조회해서 판정(`find_fvg_with_sweep`).

셋 다 시가총액 700억원 이상 필터를 공통 적용(기존 실거래 봇엔 없던
조건 — 소형주 슬리피지 문제 방지 목적으로 검색식들에 공통으로 있던 조건).

실행: auto-trading.yml의 5분 사이클에 얹혀서 돈다(GH 자체 schedule 트리거가
그날 아예 발화 안 하는 사고를 이미 한 번 겪었기 때문 — collect_intraday_data.py와
동일한 이유). shadow_data/A_YYYYMMDD.json, B_YYYYMMDD.json, C_YYYYMMDD.json에
사이클별 스냅샷을 누적 저장. [2026-07-10] collect_intraday_data.py가 이 스캔들을
직접 호출하도록 통합되면서, 후보로 잡힌 종목의 1분봉도 같이 수집되기 시작함
(이전엔 후보만 기록되고 그 종목의 실제 가격 흐름은 안 쌓이는 공백이 있었음).

[섀도우 E] 아직 조건식이 아니라 순수 데이터 수집 단계. 사용자가 "프로그램매수
   급증을 급등 초입 신호로 못 쓸까"를 제안했고, 실시간 스냅샷 1회 확인 결과
   대형주(삼성전자)는 프로그램순매수가 크고 방향성이 뚜렷한 반면, 이 봇이 실제
   매매한 중소형주 2종목(069540/005860)은 값이 작고 부호가 계속 뒤집히는 노이즈
   수준이었음 — 다만 표본 1스냅샷이라 결론 내리긴 이름. 임계값을 지금 추측해서
   후보조건으로 만들면 과최적화 위험이 크므로, A~D와 달리 조건 없이 종목별
   프로그램매매 시계열 원본만 minute_data와 같은 구조로 shadow_data/E_{code}_
   {date}.json에 쌓는다(collect_intraday_data.py 참고). 나중에 표본이 쌓이면
   그때 실제 급등 발생 시각과 대조해서 유의미한 임계값이 있는지 분석 예정.

[2026-07-07] 신설(섀도우A/B). [2026-07-10] 섀도우C(FVG) 추가. [2026-07-15] 섀도우E
(프로그램매매 데이터 수집) 추가.
"""
import os, json, time
from datetime import datetime, timezone, timedelta
import market_calendar

KST = timezone(timedelta(hours=9))
BASE_URL = "https://openapi.koreainvestment.com:9443"
KIS_APP_KEY = os.environ['KIS_APP_KEY']
KIS_APP_SECRET = os.environ['KIS_APP_SECRET']
TOKEN_FILE = "kis_token.json"

DATA_DIR = "shadow_data"
MARKET_CAP_MIN = 700  # 억원
DERIVATIVE_ETF_KEYWORDS = ["레버리지", "인버스", "ETN", "선물"]
MAX_EXTENSION_FROM_OPEN_PCT = 5.0  # [2026-07-13] 섀도우A: 오늘 시가 대비 이미 이만큼
# 오른 종목은 "지금 막 오르는 중"이 아니라 "이미 다 오른 뒤"로 보고 제외.
# check_1min_timing의 눌림목 확인은 최근 15봉짜리 좁은 창만 보기 때문에, 003280
# 사례(09:00~09:09 9분간 +9.6% 급등)처럼 국소적으로는 건강한 눌림/재돌파로 보여도
# 하루 전체로는 이미 크게 오른 경우를 놓칠 수 있어 별도로 추가.
PULLBACK_CONFIRM_WINDOW = 3  # [2026-07-18] auto_trading.py와 동일 — 반전봉이 확인돌파
# 시점 기준 너무 옛날 봉이면 뒤늦은 진입이 되므로 최근 N봉 이내만 인정.

TICK_DATA_DIR = "tick_data"
OPENING_RANGE_MINUTES = 5     # 09:00~09:05를 오프닝 레인지로 사용(섀도우D)
# [2026-07-13] 원래 섀도우D는 "1분봉 스토캐스틱RSI가 계산 가능해지는 09:27 이전
# 공백만 메운다"는 취지로 09:00~09:27에만 동작했으나, 실제로 신설 이후 단 한 번도
# 후보를 못 잡았음(shadow_data/D_*.json 파일 자체가 없음) — 27분짜리 창이 너무
# 좁아 표본이 전혀 안 쌓이는 문제. auto_trading.py의 신규진입 게이트와 동일하게
# 09:00~14:50까지로 확장해 표본부터 쌓기로 함. (단, 오프닝레인지 자체는 09:00~09:05
# 고정이라 오후로 갈수록 "이미 지나간 이른 아침 기준선"이라 신호 자체는 약해질 수
# 있음 — 캐치된 시각을 나중에 함께 봐야 함)
DAY_SCAN_END_HHMM = (14, 50)

MARKET_OPEN = (9, 0)
MARKET_CLOSE = (15, 30)  # [2026-07-07] collect_intraday_data.py와 동일하게 맞춤 —
# 매매 실행 루프(5분 반복)가 먼저 도느라 섀도우 스캔 실행 시각이 15:27경까지
# 밀리는데, 마감을 15:20으로 짧게 잡아서 장 막판 데이터가 계속 스킵되고 있었음


# ── KIS API 공용 (collect_intraday_data.py와 동일 패턴 — 같은 kis_token.json 캐시 공유) ──
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
    import requests
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
    import requests
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


def get_volume_rank(token, market="J"):
    scr_code = "20172" if market == "Q" else "20171"
    data = kis_get(token, "/uapi/domestic-stock/v1/quotations/volume-rank", {
        "FID_COND_MRKT_DIV_CODE": market, "FID_COND_SCR_DIV_CODE": scr_code,
        "FID_INPUT_ISCD": "0000", "FID_DIV_CLS_CODE": "0", "FID_BLNG_CLS_CODE": "0",
        "FID_TRGT_CLS_CODE": "111111111", "FID_TRGT_EXLS_CLS_CODE": "000000",
        "FID_INPUT_PRICE_1": "", "FID_INPUT_PRICE_2": "",
        "FID_VOL_CNT": "", "FID_INPUT_DATE_1": ""
    }, "FHPST01710000")
    return [item['mksc_shrn_iscd'] for item in data.get('output', [])[:100]]


def get_daily_ohlcv(token, code, market="J"):
    today = datetime.now(KST).strftime('%Y%m%d')
    start = (datetime.now(KST) - timedelta(days=250)).strftime('%Y%m%d')
    data = kis_get(token, "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice", {
        "FID_COND_MRKT_DIV_CODE": market, "FID_INPUT_ISCD": code,
        "FID_INPUT_DATE_1": start, "FID_INPUT_DATE_2": today,
        "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0",
    }, "FHKST03010100")
    output = data.get('output2', [])
    if len(output) < 21:
        return None
    return {
        'closes':  [float(x['stck_clpr']) for x in output],
        'highs':   [float(x.get('stck_hgpr', x['stck_clpr'])) for x in output],
        'volumes': [int(x.get('acml_vol', 0)) for x in output],
    }


def get_current_price(token, code, market="J"):
    data = kis_get(token, "/uapi/domestic-stock/v1/quotations/inquire-price", {
        "FID_COND_MRKT_DIV_CODE": market, "FID_INPUT_ISCD": code
    }, "FHKST01010100")
    o = data.get('output', {})
    return {
        'price':       float(o.get('stck_prpr', 0)),
        'open':        float(o.get('stck_oprc', 0)),
        'prev_close':  float(o.get('stck_sdpr', 0)),
        'name':        o.get('hts_kor_isnm', code),
        'volume':      int(o.get('acml_vol', 0)),
        'bstp_name':   o.get('bstp_kor_isnm', ''),
        'mrkt_name':   o.get('rprs_mrkt_kor_name', ''),
        'market_cap':  float(o.get('hts_avls', 0) or 0),  # 억원 단위
        'vi_cls_code':      o.get('vi_cls_code', 'N'),       # VI적용구분코드(N=미발동)
        'ovtm_vi_cls_code': o.get('ovtm_vi_cls_code', 'N'),  # 시간외 VI적용구분코드
    }


def get_recent_ticks(token, code, market="J"):
    """체결강도 근사치 계산용 — 최근 체결틱(최신이 index 0)."""
    data = kis_get(token, "/uapi/domestic-stock/v1/quotations/inquire-ccnl", {
        "FID_COND_MRKT_DIV_CODE": market, "FID_INPUT_ISCD": code
    }, "FHKST01010300")
    return data.get('output', [])


def get_program_trade_raw(token, code):
    """종목별 프로그램매매추이(체결) 원본 반환(최신이 index 0).
    [2026-07-15 신설, 섀도우E 데이터수집용] FID_COND_MRKT_DIV_CODE는 코스피/코스닥
    구분이 아니라 거래소 구분(J=KRX,NX=NXT,UN=통합)이라 다른 함수와 달리 market
    파라미터 없이 항상 "J" 고정 — 실측으로 코스닥 종목(069540 등)도 이 값으로
    정상 조회됨을 확인함."""
    data = kis_get(token, "/uapi/domestic-stock/v1/quotations/program-trade-by-stock", {
        "FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code
    }, "FHPPG04650101")
    return data.get('output', [])


def approx_execution_strength(ticks):
    """[근사치 — 참고용] KIS API엔 체결강도 필드가 없어 상승틱/하락틱 거래량으로 대체 추정.
    직전 틱 대비 가격이 같거나 올랐으면 매수 우세로, 내렸으면 매도 우세로 분류."""
    if len(ticks) < 5:
        return None
    buy_vol, sell_vol = 0, 0
    for i in range(len(ticks) - 1):
        try:
            price = float(ticks[i]['stck_prpr'])
            prev_price = float(ticks[i + 1]['stck_prpr'])
            vol = int(ticks[i]['cntg_vol'])
        except (KeyError, ValueError):
            continue
        if price >= prev_price:
            buy_vol += vol
        else:
            sell_vol += vol
    if sell_vol == 0:
        return None
    return round(buy_vol / sell_vol * 100, 1)


def is_derivative_etf(token, code, bstp_name, mrkt_name):
    combined = f"{bstp_name} {mrkt_name}"
    if 'ETN' in combined:
        return True
    if 'ETF' not in combined:
        return False
    data = kis_get(token, "/uapi/domestic-stock/v1/quotations/search-info", {
        "PRDT_TYPE_CD": "300", "PDNO": code
    }, "CTPF1002R")
    ratio = data.get('output', {}).get('etf_chas_erng_rt_dbnb', '').strip()
    if not ratio:
        return False
    try:
        return float(ratio) != 1.0
    except ValueError:
        return True


def calc_ma(closes, period):
    if len(closes) < period:
        return None
    return sum(closes[:period]) / period


def calc_bb(closes, period=20, mult=2.0):
    if len(closes) < period:
        return None, None
    d = closes[:period]
    mid = sum(d) / period
    std = (sum((x - mid) ** 2 for x in d) / period) ** 0.5
    return mid + mult * std, (mid + mult * std - (mid - mult * std)) / mid if mid else 0


def is_bb_squeeze(closes, period=20, lookback=20):
    if len(closes) < period + lookback:
        return False
    _, bw_now = calc_bb(closes, period)
    if bw_now is None:
        return False
    hist = []
    for i in range(1, lookback + 1):
        _, bw = calc_bb(closes[i:], period)
        if bw is not None:
            hist.append(bw)
    if not hist:
        return False
    return bw_now <= sorted(hist)[int(len(hist) * 0.3)]


def calc_rsi(closes, period=14):
    """closes: 최신이 index 0. auto_trading.py와 동일 로직(섀도우 모듈 독립 유지 위해 복제)."""
    if len(closes) < period + 1:
        return None
    diffs  = [closes[i] - closes[i + 1] for i in range(period)]
    gains  = sum(d for d in diffs if d > 0) / period
    losses = sum(abs(d) for d in diffs if d < 0) / period
    if losses == 0:
        return 100.0
    return 100 - (100 / (1 + gains / losses))


def calc_stoch_rsi(closes, rsi_period=14, stoch_period=5, smooth_k=3, smooth_d=3):
    """스토캐스틱 RSI. closes: 최신이 index 0. returns: (K, D) — 매수 신호: K > D AND K > 20.
    auto_trading.py와 동일 로직(섀도우 모듈 독립 유지 위해 복제)."""
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


def check_pullback_support_hold(closes, highs, lows, volumes, lookback=15):
    """[2026-07-18] 눌림목 지지확인. auto_trading.py의 check_pullback_support_hold와
    동일 로직(섀도우 모듈 독립 유지 위해 복제) — "직전고점 재돌파" 대신 눌림 저점의
    반전봉+확인돌파+거래량급증을 트리거로 사용. 반환: (ok, info_str, support_price)"""
    if len(closes) < lookback + 3 or len(volumes) < lookback + 3:
        return False, "데이터 부족(눌림목 확인 불가)", None
    asc_c = list(reversed(closes[:lookback]))
    asc_h = list(reversed(highs[:lookback]))
    asc_l = list(reversed(lows[:lookback]))
    asc_v = list(reversed(volumes[:lookback]))
    search_end = lookback - 3
    if search_end < 3:
        return False, "탐색 구간 부족", None
    peak_idx = max(range(search_end), key=lambda i: asc_h[i])
    peak_price = asc_h[peak_idx]
    pullback_range = list(range(peak_idx + 1, lookback - 1))
    if len(pullback_range) < 2:
        return False, "눌림 구간 부족", None
    trough_idx = min(pullback_range, key=lambda i: asc_l[i])
    trough_price = asc_l[trough_idx]
    pullback_vol = sum(asc_v[i] for i in pullback_range) / len(pullback_range)
    pullback_pct = (peak_price - trough_price) / peak_price * 100 if peak_price else 0
    flagpole_base_range = asc_l[:peak_idx]
    if len(flagpole_base_range) < 2:
        return False, "깃대 기준점 부족", None
    flagpole_base = min(flagpole_base_range)
    up_move = peak_price - flagpole_base
    retracement_ratio = (peak_price - trough_price) / up_move if up_move > 0 else None
    healthy_retracement_ratio = retracement_ratio is not None and 0.3 <= retracement_ratio <= 0.6
    healthy_pullback = 0.2 <= pullback_pct <= 4.0
    reversal_h, reversal_l = asc_h[trough_idx], asc_l[trough_idx]
    reversal_range = reversal_h - reversal_l
    reversal_close = asc_c[trough_idx]
    strong_reversal_candle = (reversal_range > 0 and
                               (reversal_close - reversal_l) / reversal_range >= 0.5)
    cur_close = asc_c[-1]
    cur_vol   = asc_v[-1]
    confirm_break = cur_close > reversal_h
    volume_surge  = cur_vol >= pullback_vol * 1.5
    trough_recent = trough_idx >= (lookback - 1) - PULLBACK_CONFIRM_WINDOW
    ok = (healthy_pullback and healthy_retracement_ratio and strong_reversal_candle
          and confirm_break and volume_surge and trough_recent)
    ratio_str = f"{retracement_ratio*100:.0f}%" if retracement_ratio is not None else "N/A"
    info = (f"눌림{pullback_pct:.1f}%({'✓' if healthy_pullback else '✗'}) "
            f"되돌림비율{ratio_str}({'✓' if healthy_retracement_ratio else '✗'}) "
            f"반전봉{'✓' if strong_reversal_candle else '✗'} "
            f"확인돌파{'✓' if confirm_break else '✗'} "
            f"거래량급증{'✓' if volume_surge else '✗'} "
            f"저점최신성{'✓' if trough_recent else '✗'}")
    return ok, info, trough_price


def check_5min_momentum(closes, n_candles=4):
    """[2026-07-18] auto_trading.py의 check_5min_momentum과 동일 로직(섀도우 모듈
    독립 유지 위해 복제) — 1분봉을 5개씩 묶어 5분봉 종가로 근사 집계해 종목 자체의
    중기 모멘텀이 상승 중인지 확인. 반환: (ok, info_str)"""
    need = (n_candles + 1) * 5
    if len(closes) < need:
        return False, "5분봉 데이터 부족 — 모멘텀 확인 불가"
    closes5 = [closes[i * 5] for i in range(n_candles + 1)]
    momentum_up = closes5[0] > closes5[n_candles]
    holding_up  = closes5[0] > closes5[1]
    ok = momentum_up and holding_up
    info = (f"5분봉모멘텀 {n_candles*5}분전대비{'상승' if momentum_up else '하락'}"
            f"({'✓' if momentum_up else '✗'}) 직전5분봉대비{'상승' if holding_up else '하락'}"
            f"({'✓' if holding_up else '✗'})")
    return ok, info


def check_1min_timing(token, code, market, cur):
    """[2026-07-13] 실거래 봇(auto_trading.check_1min_entry)과 동일한 진입 타이밍
    확인을 섀도우A에도 적용 — 스토캐스틱RSI + 눌림목 지지확인[2026-07-18 재설계] + VI필터.
    섀도우A가 일봉 조건만 통과하면 그 순간 가격을 바로 "매수"로 기록하던 문제
    (003280 사례: 09:00~09:09 9분간 +9.6% 급등한 상태에서 그대로 진입 후 손절)를
    막기 위해 추가. auto_trading.py와 의도적으로 독립된 모듈이라 로직만 복제.
    cur: 호출부에서 이미 조회한 get_current_price() 결과(VI상태 확인용, 중복 API호출 방지)."""
    raw = get_minute_bars_raw(token, code, market)
    if len(raw) < 10:
        return False, "1분봉 데이터 부족"
    closes  = [float(x.get('stck_prpr', 0)) for x in raw]
    highs   = [float(x.get('stck_hgpr', 0)) for x in raw]
    lows    = [float(x.get('stck_lwpr', 0)) for x in raw]
    volumes = [int(x.get('cntg_vol', 0)) for x in raw]
    if any(c == 0 for c in closes[:3]):
        return False, "1분봉 가격 이상"
    if cur['vi_cls_code'] != 'N' or cur['ovtm_vi_cls_code'] != 'N':
        return False, "VI 발동 중"
    if any(v == 0 for v in volumes[:5]):
        return False, "최근 거래정지(VI 추정) 캔들 포함"
    stoch_k, stoch_d = calc_stoch_rsi(closes)
    if stoch_k is None or not (stoch_k > stoch_d and stoch_k > 20):
        return False, "스토캐스틱RSI 미충족"
    pullback_ok, pullback_info, _support_price = check_pullback_support_hold(closes, highs, lows, volumes)
    if not pullback_ok:
        return False, f"눌림목 미충족({pullback_info})"
    momentum_ok, momentum_info = check_5min_momentum(closes)
    if not momentum_ok:
        return False, f"5분봉모멘텀 미충족({momentum_info})"
    return True, "OK"


def load_json(path, default):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_universe(token):
    kospi = get_volume_rank(token, "J")
    time.sleep(1)
    kosdaq = get_volume_rank(token, "Q")
    kospi_set = set(kospi)
    return list(dict.fromkeys(kospi + kosdaq)), kospi_set


# ── 섀도우 A: 눌림목 전략 + BB98% 대신 20봉 신고가 기준 ──────────────────────
def scan_shadow_a(token, stocks, kospi_set):
    now = datetime.now(KST)
    market_open_dt = now.replace(hour=9, minute=0, second=0, microsecond=0)
    elapsed_minutes = max(1.0, min(390.0, (now - market_open_dt).total_seconds() / 60))
    candidates = []
    for code in stocks:
        try:
            market = "J" if code in kospi_set else "Q"
            ohlcv = get_daily_ohlcv(token, code, market)
            if not ohlcv:
                continue
            closes, volumes = ohlcv['closes'], ohlcv['volumes']
            ma20 = calc_ma(closes, 20)
            ma60 = calc_ma(closes, 60)
            ma120 = calc_ma(closes, 120)
            if ma20 is None:
                continue
            cur = get_current_price(token, code, market)
            price = cur['price']
            if price == 0 or cur['market_cap'] < MARKET_CAP_MIN:
                continue
            if any(kw in cur['name'] for kw in DERIVATIVE_ETF_KEYWORDS):
                continue
            if is_derivative_etf(token, code, cur['bstp_name'], cur['mrkt_name']):
                continue
            # 역배열(120>60>20, 장기 하락추세) 종목 제외
            if ma120 and ma60 and ma20 and ma120 > ma60 > ma20:
                continue
            if price <= ma20:
                continue
            in_squeeze = is_bb_squeeze(closes)
            # [교체 지점] BB상단98% 대신 20봉(직전) 신고가/최고종가 기준
            prior_20 = closes[1:21]
            is_new_high = bool(prior_20) and price >= max(prior_20)
            if not (in_squeeze or is_new_high):
                continue
            vol_avg20 = sum(volumes[1:21]) / 20 if len(volumes) >= 21 else None
            if vol_avg20 and cur['volume'] < vol_avg20 * (elapsed_minutes / 390) * 2:
                continue
            # [2026-07-13] ① 오늘 시가 대비 이미 많이 오른 종목은 "이미 다 오른 뒤"
            # 진입이라 제외 (003280 사례: 9분만에 +9.6% 급등한 상태에서 진입 후 손절)
            pct_from_open = ((price - cur['open']) / cur['open'] * 100) if cur['open'] else 0
            if pct_from_open > MAX_EXTENSION_FROM_OPEN_PCT:
                continue
            # [2026-07-13] ② 실거래 봇과 동일한 1분봉 진입 타이밍 확인(스토캐스틱RSI +
            # 눌림목/불플래그 재돌파 + VI필터) — 이걸 통과한 시점에만 "매수"로 기록
            timing_ok, timing_info = check_1min_timing(token, code, market, cur)
            if not timing_ok:
                continue
            candidates.append({
                'code': code, 'name': cur['name'], 'price': price,
                'in_squeeze': in_squeeze, 'is_new_high': is_new_high,
                'market_cap': cur['market_cap'],
                'pct_from_open': round(pct_from_open, 2),
                'timing_info': timing_info,
            })
            time.sleep(0.06)
        except Exception as e:
            print(f"  [섀도우A 오류] {code}: {e}")
    return candidates


# ── 섀도우 B: 시가돌파형(초기 모멘텀) — 눌림목 없이 당일 시가 돌파 시 바로 후보 ──
def scan_shadow_b(token, stocks, kospi_set):
    candidates = []
    for code in stocks:
        try:
            market = "J" if code in kospi_set else "Q"
            cur = get_current_price(token, code, market)
            price, open_p, prev_close = cur['price'], cur['open'], cur['prev_close']
            if price == 0 or open_p == 0 or cur['market_cap'] < MARKET_CAP_MIN:
                continue
            if any(kw in cur['name'] for kw in DERIVATIVE_ETF_KEYWORDS):
                continue
            if is_derivative_etf(token, code, cur['bstp_name'], cur['mrkt_name']):
                continue
            ohlcv = get_daily_ohlcv(token, code, market)
            if not ohlcv:
                continue
            closes, volumes = ohlcv['closes'], ohlcv['volumes']
            ma5, ma20 = calc_ma(closes, 5), calc_ma(closes, 20)
            if ma5 is None or ma20 is None:
                continue
            # !A: 5일선이 아직 20일선 위로 못 올라온 상태(초기 국면)
            if ma5 >= ma20:
                continue
            # B[근사]: 당일 시가가 전일종가 대비 하락 갭이 아님(원래는 "첫분봉 상승"이나
            # 첫분봉 데이터 확보 비용이 커서 시가>=전일종가로 단순화)
            if open_p < prev_close:
                continue
            # C: 주가돌파 — 현재가가 당일 시가를 상향 돌파(0.5% 이상)
            if price < open_p * 1.005:
                continue
            vol_avg20 = sum(volumes[1:21]) / 20 if len(volumes) >= 21 else None
            # E: 거래량비율 — 오늘 누적거래량이 20일평균 대비 이미 붙는 중인지(근사)
            if vol_avg20 and cur['volume'] < vol_avg20 * 0.3:
                continue
            # F[근사, 참고용]: 체결강도 — 필터링엔 안 쓰고 기록만
            ticks = get_recent_ticks(token, code, market)
            approx_cttr = approx_execution_strength(ticks)
            candidates.append({
                'code': code, 'name': cur['name'], 'price': price,
                'open': open_p, 'gap_from_open_pct': round((price - open_p) / open_p * 100, 2),
                'approx_execution_strength': approx_cttr,
                'market_cap': cur['market_cap'],
            })
            time.sleep(0.06)
        except Exception as e:
            print(f"  [섀도우B 오류] {code}: {e}")
    return candidates


# ── 섀도우 C: FVG(Fair Value Gap) + 유동성 스윕 결합 ──────────────────────
# [2026-07-10 신설] 쉽알남(스마트머니 트레이딩 노트) 참고. 오더블럭/추세선/채널은
# "캔들 몸통이 적당히 커야 한다"/"스윙포인트를 어디로 잡을지" 등 노트 스스로도
# 인정하는 주관적 판단이 많이 필요해서 자동화 코드로 명확히 정의하기 어려움.
# FVG는 3개 캔들 구조로 숫자 기준이 딱 떨어져서 셋 중 가장 코드화하기 좋음.
# 여기에 노트에서 공통으로 "신뢰도 상승 조건"으로 언급된 유동성 스윕(직전 저점을
# 살짝 하회했다가 회복)을 결합 — 눌림목(A/B)과 달리 1분봉 패턴이라 일봉이 아닌
# 실시간 1분봉을 직접 조회해서 판정.
def get_minute_bars_raw(token, code, market="J"):
    """현재 시각 기준 최근 1분봉(최대 30개, 최신이 index 0) 원본 반환."""
    data = kis_get(token, "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice", {
        "FID_ETC_CLS_CODE": "",
        "FID_COND_MRKT_DIV_CODE": market,
        "FID_INPUT_ISCD": code,
        "FID_INPUT_HOUR_1": datetime.now(KST).strftime('%H%M%S'),
        "FID_PW_DATA_INCU_YN": "Y",
    }, "FHKST03010200")
    return data.get('output2', [])


def find_fvg_with_sweep(bars_asc, lookback=20):
    """bars_asc: 오래된→최신 순 1분봉. 상승형 FVG(2번 캔들이 몸통 큰 양봉, 1번
    고가<3번 저가 갭)이면서, 그 직전에 유동성 스윕(직전 저점을 살짝 하회 후 회복)이
    있었던 경우만 후보로 인정. 조건 충족 시 dict, 아니면 None."""
    if len(bars_asc) < lookback:
        return None
    recent = bars_asc[-lookback:]

    def v(b, k):
        return float(b[k])

    # 최근 몇 개 캔들 구간에서 3봉 조합(c1,c2,c3)을 뒤에서부터 탐색
    for i in range(len(recent) - 3, max(0, len(recent) - 8), -1):
        c1, c2, c3 = recent[i], recent[i + 1], recent[i + 2]
        c1_high, c3_low = v(c1, 'stck_hgpr'), v(c3, 'stck_lwpr')
        if c1_high >= c3_low:
            continue  # 갭 없음
        body1 = abs(v(c1, 'stck_prpr') - v(c1, 'stck_oprc'))
        body2 = abs(v(c2, 'stck_prpr') - v(c2, 'stck_oprc'))
        body3 = abs(v(c3, 'stck_prpr') - v(c3, 'stck_oprc'))
        avg_side_body = (body1 + body3) / 2
        if avg_side_body <= 0 or body2 < avg_side_body * 2.0:
            continue  # 2번 캔들이 앞뒤 대비 충분히 크지 않음
        if not (v(c2, 'stck_prpr') > v(c2, 'stck_oprc')):
            continue  # 2번 캔들이 양봉이어야 상승형 FVG

        pre = recent[:i]
        if len(pre) < 5:
            continue
        local_low = min(v(b, 'stck_lwpr') for b in pre[:-2])
        last_two_low = min(v(b, 'stck_lwpr') for b in pre[-2:])
        if not (last_two_low < local_low * 0.999):
            continue  # 직전 저점을 살짝이라도 하회(스윕)한 적이 없으면 제외

        cur_price = v(recent[-1], 'stck_prpr')
        if not (c1_high <= cur_price <= c3_low):
            continue  # 갭 구간으로 되돌아온 시점이 아니면 아직 진입 타이밍 아님

        return {
            'gap_low': c1_high, 'gap_high': c3_low, 'price': cur_price,
            'stop_price': min(v(c1, 'stck_lwpr'), v(c2, 'stck_lwpr'), v(c3, 'stck_lwpr')),
            # 익절 목표 = FVG를 만든 파동(c1~c3) 자체의 고점 돌파 — 스윕 전 구간(pre)의
            # 고점은 이 파동이 이미 훨씬 위로 뚫고 올라온 낮은 값이라 목표가로 부적절함.
            'target_price': max(v(c1, 'stck_hgpr'), v(c2, 'stck_hgpr'), v(c3, 'stck_hgpr')),
        }
    return None


def scan_shadow_c(token, stocks, kospi_set):
    candidates = []
    for code in stocks:
        try:
            market = "J" if code in kospi_set else "Q"
            cur = get_current_price(token, code, market)
            if cur['price'] == 0 or cur['market_cap'] < MARKET_CAP_MIN:
                continue
            if any(kw in cur['name'] for kw in DERIVATIVE_ETF_KEYWORDS):
                continue
            if is_derivative_etf(token, code, cur['bstp_name'], cur['mrkt_name']):
                continue
            raw = get_minute_bars_raw(token, code, market)
            if len(raw) < 20:
                continue
            bars_asc = list(reversed(raw))
            found = find_fvg_with_sweep(bars_asc)
            if found is None:
                continue
            candidates.append({
                'code': code, 'name': cur['name'], 'price': found['price'],
                'gap_low': found['gap_low'], 'gap_high': found['gap_high'],
                'stop_price': found['stop_price'], 'target_price': found['target_price'],
                'market_cap': cur['market_cap'],
            })
            time.sleep(0.06)
        except Exception as e:
            print(f"  [섀도우C 오류] {code}: {e}")
    return candidates


# ── 섀도우 D: 오프닝 레인지 브레이크아웃(ORB, 틱 기반) — 09:00~14:50 종일 스캔 ──
# [2026-07-13 신설] 1분봉 기반 스토캐스틱RSI는 27개 봉(≈09:27)이 있어야 계산되는데,
# 그 전엔 "진짜 급등"과 노이즈를 구분할 방법이 없어 실거래봇/섀도우A~C는 이 구간을
# 사실상 건너뜀. 틱(체결) 단위로 보면 이 시점에도 데이터가 이미 쌓여있는 경우가
# 많아, 분봉이 아닌 틱으로 "오프닝 레인지(09:00~09:05 고가) 돌파 + 거래량 확인"을
# 판단해보는 실험으로 시작. [2026-07-13 확장] 처음엔 09:27까지만 동작하게 했으나,
# 신설 이후 후보를 한 번도 못 잡아(D_*.json 파일 자체가 없었음) 표본이 전혀 안
# 쌓이는 문제가 있어, auto_trading.py 신규진입 게이트와 동일한 09:00~14:50까지로
# 스캔 구간을 넓힘. 오프닝레인지 자체(09:00~09:05)는 그대로 고정.
# [2026-07-13 추가] VWAP(거래량가중평균가) 확인 조건 결합 — 리테일 ORB 전략에서
# 흔히 쓰이는 방식으로, "레인지 돌파 + 거래량 위" 조합만으로는 저거래량 구간에서
# 잠깐 튀었다가 되돌리는 가짜 돌파를 못 거른다는 문제가 있음. 돌파 시점에 현재가가
# 당일 VWAP 위에 있는지까지 확인하면, 그 상승이 소수 체결의 노이즈가 아니라
# 실제 매수 참여가 뒷받침된 움직임인지 추가로 검증할 수 있음.
def load_ticks(code, ymd):
    return load_json(f"{TICK_DATA_DIR}/{code}_{ymd}.json", [])


def save_ticks(code, ymd, ticks):
    save_json(f"{TICK_DATA_DIR}/{code}_{ymd}.json", ticks)


def update_ticks(token, code, market, ymd):
    """inquire-ccnl로 조회한 최근 체결틱을 누적 저장.
    [주의] 이 API는 호출 시점 기준 최근 30건 체결만 반환하고 누적거래량 필드가
    없어(체결시각+가격+체결량) 조합으로 중복 제거함 — 5분 주기 호출 사이 30건보다
    많이 체결되는 초고거래량 종목은 중간 틱이 누락될 수 있음(근사치, 실험용)."""
    stored = load_ticks(code, ymd)
    seen = {(t['time'], t['price'], t['vol']) for t in stored}
    new = []
    for t in get_recent_ticks(token, code, market):
        try:
            item = (t.get('stck_cntg_hour', ''), float(t.get('stck_prpr', 0)), int(t.get('cntg_vol', 0)))
        except (TypeError, ValueError):
            continue
        if not item[0] or item in seen:
            continue
        new.append({'time': item[0], 'price': item[1], 'vol': item[2]})
        seen.add(item)
    if new:
        stored.extend(new)
        stored.sort(key=lambda t: t['time'])
        save_ticks(code, ymd, stored)
    return stored


def scan_shadow_d(token, stocks, kospi_set):
    now = datetime.now(KST)
    if (now.hour, now.minute) >= DAY_SCAN_END_HHMM or (now.hour, now.minute) < (9, OPENING_RANGE_MINUTES):
        return []
    ymd = now.strftime('%Y%m%d')
    range_end_hms = f"09{OPENING_RANGE_MINUTES:02d}00"
    candidates = []
    for code in stocks:
        try:
            market = "J" if code in kospi_set else "Q"
            cur = get_current_price(token, code, market)
            if cur['price'] == 0 or cur['market_cap'] < MARKET_CAP_MIN:
                continue
            if any(kw in cur['name'] for kw in DERIVATIVE_ETF_KEYWORDS):
                continue
            if cur['vi_cls_code'] != 'N' or cur['ovtm_vi_cls_code'] != 'N':
                continue
            # 저비용 사전필터: 시가 대비 +2% 미만이면 틱 조회(비용 큰 호출) 생략
            if not cur['open'] or (cur['price'] - cur['open']) / cur['open'] < 0.02:
                continue
            if is_derivative_etf(token, code, cur['bstp_name'], cur['mrkt_name']):
                continue
            ticks = update_ticks(token, code, market, ymd)
            opening = [t for t in ticks if t['time'] < range_end_hms]
            after = [t for t in ticks if t['time'] >= range_end_hms]
            if len(opening) < 5 or not after:
                continue
            range_high = max(t['price'] for t in opening)
            range_vol = sum(t['vol'] for t in opening)
            after_vol = sum(t['vol'] for t in after)
            after_minutes = max(1, (now.hour * 60 + now.minute) - (9 * 60 + OPENING_RANGE_MINUTES))
            vol_surge = (after_vol / after_minutes) >= (range_vol / OPENING_RANGE_MINUTES) * 1.5
            breakout = cur['price'] >= range_high * 1.003
            # [2026-07-13] VWAP 확인 — 당일 09:00부터 지금까지 쌓인 틱 전체(오프닝+이후)로
            # 거래량가중평균가를 계산해, 돌파가 VWAP 위에서 일어났는지 확인
            total_vol = sum(t['vol'] for t in ticks)
            if total_vol == 0:
                continue
            vwap = sum(t['price'] * t['vol'] for t in ticks) / total_vol
            above_vwap = cur['price'] >= vwap
            if not (breakout and vol_surge and above_vwap):
                continue
            candidates.append({
                'code': code, 'name': cur['name'], 'price': cur['price'],
                'range_high': range_high, 'vwap': round(vwap, 1), 'market_cap': cur['market_cap'],
            })
            time.sleep(0.06)
        except Exception as e:
            print(f"  [섀도우D 오류] {code}: {e}")
    return candidates


def append_snapshot(path, candidates):
    if not candidates:
        return
    log = load_json(path, [])
    log.append({'time': datetime.now(KST).strftime('%H:%M:%S'), 'candidates': candidates})
    save_json(path, log)


def main():
    now = datetime.now(KST)
    if (now.hour, now.minute) < MARKET_OPEN or (now.hour, now.minute) > MARKET_CLOSE:
        print("[섀도우 스캔] 장 시간 외 - 종료")
        return
    today_str = now.strftime('%Y%m%d')
    token = get_kis_token()
    if market_calendar.is_trading_day(token, today_str) is False:
        print(f"[휴장일] {today_str} — 섀도우 스캔 건너뜀")
        return
    stocks, kospi_set = get_universe(token)
    if not stocks:
        print("[섀도우 스캔] 스캔 대상 없음")
        return

    a_candidates = scan_shadow_a(token, stocks, kospi_set)
    append_snapshot(f"{DATA_DIR}/A_{today_str}.json", a_candidates)
    print(f"[섀도우A] 후보 {len(a_candidates)}종목: {[c['name'] for c in a_candidates]}")

    b_candidates = scan_shadow_b(token, stocks, kospi_set)
    append_snapshot(f"{DATA_DIR}/B_{today_str}.json", b_candidates)
    print(f"[섀도우B] 후보 {len(b_candidates)}종목: {[c['name'] for c in b_candidates]}")

    c_candidates = scan_shadow_c(token, stocks, kospi_set)
    append_snapshot(f"{DATA_DIR}/C_{today_str}.json", c_candidates)
    print(f"[섀도우C] 후보 {len(c_candidates)}종목: {[c['name'] for c in c_candidates]}")

    d_candidates = scan_shadow_d(token, stocks, kospi_set)
    append_snapshot(f"{DATA_DIR}/D_{today_str}.json", d_candidates)
    print(f"[섀도우D] 후보 {len(d_candidates)}종목: {[c['name'] for c in d_candidates]}")


if __name__ == '__main__':
    main()

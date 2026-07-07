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

두 섀도우 모두 시가총액 700억원 이상 필터를 공통 적용(기존 실거래 봇엔 없던
조건 — 소형주 슬리피지 문제 방지 목적으로 두 검색식에 공통으로 있던 조건).

실행: auto-trading.yml의 5분 사이클에 얹혀서 돈다(GH 자체 schedule 트리거가
그날 아예 발화 안 하는 사고를 이미 한 번 겪었기 때문 — collect_intraday_data.py와
동일한 이유). shadow_data/A_YYYYMMDD.json, shadow_data/B_YYYYMMDD.json 에
사이클별 스냅샷을 누적 저장.

[2026-07-07] 신설.
"""
import os, json, time
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
BASE_URL = "https://openapi.koreainvestment.com:9443"
KIS_APP_KEY = os.environ['KIS_APP_KEY']
KIS_APP_SECRET = os.environ['KIS_APP_SECRET']
TOKEN_FILE = "kis_token.json"

DATA_DIR = "shadow_data"
MARKET_CAP_MIN = 700  # 억원
DERIVATIVE_ETF_KEYWORDS = ["레버리지", "인버스", "ETN", "선물"]

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
    }


def get_recent_ticks(token, code, market="J"):
    """체결강도 근사치 계산용 — 최근 체결틱(최신이 index 0)."""
    data = kis_get(token, "/uapi/domestic-stock/v1/quotations/inquire-ccnl", {
        "FID_COND_MRKT_DIV_CODE": market, "FID_INPUT_ISCD": code
    }, "FHKST01010300")
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
            candidates.append({
                'code': code, 'name': cur['name'], 'price': price,
                'in_squeeze': in_squeeze, 'is_new_high': is_new_high,
                'market_cap': cur['market_cap'],
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


if __name__ == '__main__':
    main()

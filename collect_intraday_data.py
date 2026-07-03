#!/usr/bin/env python3
"""
1분봉 실시간 수집기 (미래 intraday_backtest.py용 히스토리컬 데이터 축적)
=====================================================
[배경] KIS API는 "과거 날짜"의 1분봉을 안정적으로 지원하지 않는 것으로
확인됨(intraday_backtest.py 실행 시 실패). pykrx 등 외부 소스도 국내
주식 분봉은 제공하지 않음. 유일한 방법은 "지금부터" 매일 장중 1분봉을
직접 수집해서 쌓아두는 것.

[동작 방식]
1. 하루 중 최초 실행 시, 일봉 공통 필터(MA20/MA200/MACD/BB/거래량)를
   통과하는 종목 리스트를 산출해 data/watchlist_YYYYMMDD.json 에 저장.
   (시간대/갭 필터는 적용하지 않음 - 연구용으로 폭넓게 수집)
2. 이후 각 실행마다 워치리스트 종목의 최근 1분봉(최대 30개)을 조회해서
   아직 저장 안 된 새 봉만 data/minute/{code}_{YYYYMMDD}.json 에 append.
3. GitHub Actions에서 09:00~15:30(KST) 사이 약 10~15분 간격으로 반복
   실행하면, 며칠 지나면 intraday_backtest.py가 쓸 수 있는 진짜
   히스토리컬 1분봉이 쌓임.

[주의] GitHub Actions 스케줄은 1분 단위 보장이 안 되고 부하 시 지연될
수 있음 - 그래도 한 번 호출에 최근 30개 봉이 오므로 15분 간격이면
빠지는 구간 없이 커버됨.
=====================================================
"""
import os, json, time
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
BASE_URL = "https://openapi.koreainvestment.com:9443"
KIS_APP_KEY = os.environ['KIS_APP_KEY']
KIS_APP_SECRET = os.environ['KIS_APP_SECRET']
TOKEN_FILE = "kis_token.json"

DATA_DIR = "minute_data"
WATCHLIST_MAX = 20

MARKET_OPEN = (9, 0)
MARKET_CLOSE = (15, 30)


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
        "FID_COND_MRKT_DIV_CODE": market,
        "FID_COND_SCR_DIV_CODE": scr_code,
        "FID_INPUT_ISCD": "0000",
        "FID_DIV_CLS_CODE": "0", "FID_BLNG_CLS_CODE": "0",
        "FID_TRGT_CLS_CODE": "111111111",
        "FID_TRGT_EXLS_CLS_CODE": "000000",
        "FID_INPUT_PRICE_1": "", "FID_INPUT_PRICE_2": "",
        "FID_VOL_CNT": "", "FID_INPUT_DATE_1": ""
    }, "FHPST01710000")
    output = data.get('output', [])
    return [(item['mksc_shrn_iscd'], item.get('hts_kor_isnm', '')) for item in output[:100]]


def get_daily_ohlcv(token, code, market="J"):
    today = datetime.now(KST).strftime('%Y%m%d')
    start = (datetime.now(KST) - timedelta(days=280)).strftime('%Y%m%d')
    data = kis_get(token, "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice", {
        "FID_COND_MRKT_DIV_CODE": market,
        "FID_INPUT_ISCD": code,
        "FID_INPUT_DATE_1": start,
        "FID_INPUT_DATE_2": today,
        "FID_PERIOD_DIV_CODE": "D",
        "FID_ORG_ADJ_PRC": "0",
    }, "FHKST03010100")
    output = data.get('output2', [])
    if not output:
        return None
    closes = [float(x['stck_clpr']) for x in output]
    highs = [float(x.get('stck_hgpr', x['stck_clpr'])) for x in output]
    lows = [float(x.get('stck_lwpr', x['stck_clpr'])) for x in output]
    volumes = [int(x.get('acml_vol', 0)) for x in output]
    return {'closes': closes, 'highs': highs, 'lows': lows, 'volumes': volumes}


def get_minute_ohlcv_raw(token, code, market="J"):
    """현재 시각 기준 최근 1분봉(최대 30개, 최신이 index 0)을 원본 필드 그대로 반환."""
    data = kis_get(token, "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice", {
        "FID_ETC_CLS_CODE": "",
        "FID_COND_MRKT_DIV_CODE": market,
        "FID_INPUT_ISCD": code,
        "FID_INPUT_HOUR_1": datetime.now(KST).strftime('%H%M%S'),
        "FID_PW_DATA_INCU_YN": "Y",
    }, "FHKST03010200")
    return data.get('output2', [])


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


def build_watchlist(token):
    """일봉 공통 필터(시간대/갭 필터 제외) 통과 종목을 산출."""
    kospi = get_volume_rank(token, "J")
    time.sleep(1)
    kosdaq = get_volume_rank(token, "Q")
    universe = kospi + kosdaq

    passed = []
    for code, name in universe:
        market = "J" if (code, name) in kospi else "Q"
        ohlcv = get_daily_ohlcv(token, code, market)
        if not ohlcv:
            continue
        closes, highs, lows, volumes = ohlcv['closes'], ohlcv['highs'], ohlcv['lows'], ohlcv['volumes']
        ma20 = calc_ma(closes, 20)
        ma200 = calc_ma(closes, 200)
        macd, sig_line, golden = calc_macd(closes)
        bb_upper, _, _, _ = calc_bb(closes, 20)
        squeeze = is_bb_squeeze(closes)
        if ma20 is None or macd is None or bb_upper is None:
            continue
        price = closes[0]
        if price <= ma20:
            continue
        if ma200 and price <= ma200:
            continue
        if golden is None or macd <= sig_line:
            continue
        if not (squeeze or price >= bb_upper * 0.98):
            continue
        vol_avg20 = sum(volumes[1:21]) / 20 if len(volumes) >= 21 else None
        if vol_avg20 and volumes[0] < vol_avg20 * 2:
            continue
        passed.append({'code': code, 'name': name, 'market': market, 'squeeze': squeeze})
        time.sleep(0.06)

    return passed[:WATCHLIST_MAX]


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


def main():
    now = datetime.now(KST)
    if (now.hour, now.minute) < MARKET_OPEN or (now.hour, now.minute) > MARKET_CLOSE:
        print("장 시간 외 - 종료")
        return

    today_str = now.strftime('%Y%m%d')
    watchlist_path = f"{DATA_DIR}/watchlist_{today_str}.json"

    watchlist = load_json(watchlist_path, None)
    token = get_kis_token()

    if watchlist is None:
        print("오늘 첫 실행 - 워치리스트 산출 중...")
        watchlist = build_watchlist(token)
        save_json(watchlist_path, watchlist)
        print(f"워치리스트 {len(watchlist)}종목 저장: {[w['name'] for w in watchlist]}")

    if not watchlist:
        print("워치리스트 없음 - 종료")
        return

    total_new = 0
    for w in watchlist:
        code, market = w['code'], w['market']
        bars_path = f"{DATA_DIR}/{code}_{today_str}.json"
        stored = load_json(bars_path, [])
        stored_times = {b['stck_cntg_hour'] for b in stored}

        raw = get_minute_ohlcv_raw(token, code, market)
        new_bars = [b for b in raw if b.get('stck_cntg_hour') and b['stck_cntg_hour'] not in stored_times]
        if new_bars:
            stored.extend(new_bars)
            stored.sort(key=lambda b: b['stck_cntg_hour'])
            save_json(bars_path, stored)
            total_new += len(new_bars)
            print(f" {w['name']}({code}): +{len(new_bars)}봉 (누적 {len(stored)})")
        time.sleep(0.1)

    print(f"완료 - 신규 {total_new}봉 저장")


if __name__ == '__main__':
    main()

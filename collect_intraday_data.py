#!/usr/bin/env python3
"""
1분봉 실시간 수집기 (미래 intraday_backtest.py / shadow_backtest.py용
히스토리컬 데이터 축적) — [2026-07-10] 섀도우 스캔(shadow_scan.py)과 통합.
=====================================================
[배경] KIS API는 "과거 날짜"의 1분봉을 안정적으로 지원하지 않는 것으로
확인됨(intraday_backtest.py 실행 시 실패). pykrx 등 외부 소스도 국내
주식 분봉은 제공하지 않음. 유일한 방법은 "지금부터" 매일 장중 1분봉을
직접 수집해서 쌓아두는 것.

[동작 방식]
1. 하루 중 최초 실행 시, 일봉 공통 필터(MA20/BB/거래량, 라이브 전략과 동일)를
   통과하는 종목 리스트를 산출해 data/watchlist_YYYYMMDD.json 에 저장.
2. [2026-07-10 추가] 매 사이클마다 아래 두 그룹도 워치리스트에 합침(중복 제거,
   한 번 들어오면 그날 계속 수집 — 나중에 여러 조건식을 실제 시세로 재생/비교
   하려면 "그 종목의 그 순간 진짜 가격"이 반드시 있어야 하기 때문):
   ① 섀도우A/B/C(대안 조건식) 스캔이 그 사이클에 포착한 후보 — shadow_scan.py가
      따로 돌 때는 후보 "기록"만 하고 1분봉은 안 쌓아서, 나중에 그 종목의 실제
      가격 흐름을 재생할 수 없는 공백이 있었음(2026-07-10 사용자가 백테스트
      돌려보다가 발견). 이제 이 스크립트 안에서 섀도우 스캔까지 실행해서 공백 해소.
   ② 실거래 봇이 오늘 실제로 산/보유 중인 종목(dashboard_data.json) — 라이브
      필터와 섀도우 스캔이 각자 다른 순간에 API를 조회하다 보니(가격>볼린저밴드98%
      같은 경계선 조건이 몇 초 차이로 뒤집힐 수 있음), 실제 라이브가 산 종목인데도
      이 스크립트의 필터 스냅샷에서는 근소하게 탈락해 1분봉이 안 쌓이는 사례가
      실제로 있었음(2026-07-09~10 5건의 실거래 중 3건). 대시보드에서 직접 읽어와
      "실제 산 종목"은 필터 통과 여부와 무관하게 항상 수집 대상에 넣어 이 공백을
      원천 차단.
3. 이후 각 실행마다 워치리스트 종목의 최근 1분봉(최대 30개)을 조회해서
   아직 저장 안 된 새 봉만 minute_data/{code}_{YYYYMMDD}.json 에 append.
   섀도우A/B가 그 사이클에 포착한 후보 스냅샷은 기존과 동일하게
   shadow_data/A_YYYYMMDD.json / B_YYYYMMDD.json 에도 그대로 남김(백테스트가
   "그 모델이 그 순간 무엇을 포착했는지" + "그 이후 실제 가격이 어떻게 움직였는지"
   둘 다 필요하기 때문).
4. GitHub Actions에서 09:00~15:30(KST) 사이 5분 간격으로 반복 실행.

[주의] GitHub Actions 스케줄은 1분 단위 보장이 안 되고 부하 시 지연될
수 있음 - 그래도 한 번 호출에 최근 30개 봉이 오므로 5분 간격이면
빠지는 구간 없이 커버됨.

[한계] 이렇게 해도 "과거"로 소급 적용은 안 됨 — 새로 등록한 조건식(모델)은
등록한 날부터 후보가 잡히고 그 후보 종목의 1분봉이 그날부터 쌓이기 시작함.
=====================================================
"""
import os, json, time
from datetime import datetime, timezone, timedelta
import shadow_scan

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
    """
    일봉 공통 필터(시간대/갭 필터 제외) 통과 종목을 산출.
    [2026-07-06] 라이브(auto_trading.py)와 동일한 필터로 동기화:
    MA200/MACD 조건 제거, 거래량 필터를 경과시간 비례로 수정.
    (그래야 이 워치리스트로 쌓은 1분봉이 실제 라이브 전략의 백테스트에 쓸모있음)
    """
    now = datetime.now(KST)
    market_open_dt = now.replace(hour=9, minute=0, second=0, microsecond=0)
    elapsed_minutes = max(1.0, min(390.0, (now - market_open_dt).total_seconds() / 60))

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
        bb_upper, _, _, _ = calc_bb(closes, 20)
        squeeze = is_bb_squeeze(closes)
        if ma20 is None or bb_upper is None:
            continue
        price = closes[0]
        if price <= ma20:
            continue
        if not (squeeze or price >= bb_upper * 0.98):
            continue
        vol_avg20 = sum(volumes[1:21]) / 20 if len(volumes) >= 21 else None
        if vol_avg20 and volumes[0] < vol_avg20 * (elapsed_minutes / 390) * 2:
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


def resolve_market(token, code):
    """코스피(J)/코스닥(Q) 미상인 코드의 시장 구분을 현재가 조회로 판별."""
    data = kis_get(token, "/uapi/domestic-stock/v1/quotations/inquire-price", {
        "FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code
    }, "FHKST01010100")
    if data.get('output', {}).get('stck_prpr'):
        return "J"
    return "Q"


def get_live_trade_codes_today(today_str):
    """실거래 봇이 오늘 실제로 산/보유중인 종목 코드 — 라이브 필터 통과 여부와
    무관하게 항상 1분봉 수집 대상에 넣기 위한 '있는 그대로의 진실' 소스."""
    dash = load_json("dashboard_data.json", {})
    codes = set()
    today_disp = datetime.strptime(today_str, '%Y%m%d').strftime('%m/%d')
    for t in dash.get('trades', []):
        if t.get('action') == 'buy' and str(t.get('date', '')).startswith(today_disp) and t.get('code'):
            codes.add(t['code'])
    pos = dash.get('position')
    if pos and pos.get('pdno'):
        codes.add(pos['pdno'])
    return codes


def merge_into_watchlist(watchlist, new_codes, token):
    """new_codes(시장 미상)를 기존 워치리스트에 중복없이 합침."""
    existing = {w['code'] for w in watchlist}
    for code in new_codes:
        if code in existing:
            continue
        market = resolve_market(token, code)
        watchlist.append({'code': code, 'name': code, 'market': market, 'squeeze': False})
        existing.add(code)
        time.sleep(0.06)
    return watchlist


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
        print(f"[라이브 필터] {len(watchlist)}종목: {[w['name'] for w in watchlist]}")

    # ── [2026-07-10] 섀도우A/B/C 스캔을 여기서 함께 실행 — 후보 종목을
    # 워치리스트에 합쳐서 1분봉을 같이 쌓고, 기존과 동일하게 shadow_data/에도 기록 ──
    stocks, kospi_set = shadow_scan.get_universe(token)
    a_candidates = shadow_scan.scan_shadow_a(token, stocks, kospi_set)
    shadow_scan.append_snapshot(f"shadow_data/A_{today_str}.json", a_candidates)
    print(f"[섀도우A] 후보 {len(a_candidates)}종목: {[c['name'] for c in a_candidates]}")
    b_candidates = shadow_scan.scan_shadow_b(token, stocks, kospi_set)
    shadow_scan.append_snapshot(f"shadow_data/B_{today_str}.json", b_candidates)
    print(f"[섀도우B] 후보 {len(b_candidates)}종목: {[c['name'] for c in b_candidates]}")
    c_candidates = shadow_scan.scan_shadow_c(token, stocks, kospi_set)
    shadow_scan.append_snapshot(f"shadow_data/C_{today_str}.json", c_candidates)
    print(f"[섀도우C] 후보 {len(c_candidates)}종목: {[c['name'] for c in c_candidates]}")

    shadow_codes = ({c['code'] for c in a_candidates} | {c['code'] for c in b_candidates}
                     | {c['code'] for c in c_candidates})
    live_trade_codes = get_live_trade_codes_today(today_str)
    watchlist = merge_into_watchlist(watchlist, shadow_codes | live_trade_codes, token)
    save_json(watchlist_path, watchlist)

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

    print(f"완료 - 신규 {total_new}봉 저장 (워치리스트 {len(watchlist)}종목)")


if __name__ == '__main__':
    main()

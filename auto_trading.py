# 자동매매 — 다중 필터 단타 전략 (MA크로스 + RSI + 갭)
# 조건: MA5>MA20, RSI<40, 시초가 갭 -1% 이하 동시 충족 시 매수
# 익절 +4%, 손절 -2%, 15:20 강제청산

import os, json, time, requests
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
BASE_URL = "https://openapi.koreainvestment.com:9443"
BUDGET = 1_000_000  # 100만원
ACCOUNT_NO = os.environ['KIS_ACCOUNT_NO']
ACCOUNT_PROD = "01"

KIS_APP_KEY    = os.environ['KIS_APP_KEY']
KIS_APP_SECRET = os.environ['KIS_APP_SECRET']
KAKAO_CLIENT_ID     = os.environ['KAKAO_CLIENT_ID']
KAKAO_CLIENT_SECRET = os.environ['KAKAO_CLIENT_SECRET']
KAKAO_REFRESH_TOKEN = os.environ['KAKAO_REFRESH_TOKEN']


def get_kis_token():
    r = requests.post(f"{BASE_URL}/oauth2/tokenP", json={
        "grant_type": "client_credentials",
        "appkey": KIS_APP_KEY,
        "appsecret": KIS_APP_SECRET
    }, timeout=10)
    data = r.json()
    if 'access_token' not in data:
        raise Exception(f"KIS 토큰 오류: {data}")
    return data['access_token']

def kis_get(token, path, params, tr_id):
    headers = {
        "authorization": f"Bearer {token}",
        "appkey": KIS_APP_KEY,
        "appsecret": KIS_APP_SECRET,
        "tr_id": tr_id,
        "custtype": "P"
    }
    r = requests.get(f"{BASE_URL}{path}", headers=headers, params=params, timeout=10)
    return r.json()

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
    return r.json()

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
        'link': {'web_url': 'https://github.com', 'mobile_web_url': 'https://github.com'}
    })
    requests.post('https://kapi.kakao.com/v2/api/talk/memo/default/send',
                  headers={'Authorization': f'Bearer {kakao_token}'},
                  data={'template_object': obj}, timeout=10)
    print(f"[카톡] {msg[:60]}")

def get_volume_rank(token, market="J"):
    data = kis_get(token, "/uapi/domestic-stock/v1/ranking/volume", {
        "FID_COND_MRKT_DIV_CODE": market,
        "FID_COND_SCR_DIV_CODE": "20171",
        "FID_INPUT_ISCD": "0000",
        "FID_DIV_CLS_CODE": "0",
        "FID_BLNG_CLS_CODE": "0",
        "FID_TRGT_CLS_CODE": "111111111",
        "FID_TRGT_EXLS_CLS_CODE": "000000",
        "FID_INPUT_PRICE_1": "2000",
        "FID_INPUT_PRICE_2": "300000",
        "FID_VOL_CNT": "100000",
        "FID_INPUT_DATE_1": ""
    }, "FHPST01710000")
    return [item['mksc_shrn_iscd'] for item in data.get('output', [])[:100]]

def get_daily_ohlcv(token, code):
    data = kis_get(token, "/uapi/domestic-stock/v1/quotations/inquire-daily-price", {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": code,
        "FID_PERIOD_DIV_CODE": "D",
        "FID_ORG_ADJ_PRC": "0"
    }, "FHKST01010400")
    output = data.get('output', [])
    if len(output) < 21:
        return None
    closes = [float(x['stck_clpr']) for x in output[:25]]
    opens  = [float(x['stck_oprc']) for x in output[:25]]
    return {'closes': closes, 'opens': opens}

def get_current_price(token, code):
    data = kis_get(token, "/uapi/domestic-stock/v1/quotations/inquire-price", {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": code
    }, "FHKST01010100")
    o = data.get('output', {})
    return {
        'price':      float(o.get('stck_prpr', 0)),
        'open':       float(o.get('stck_oprc', 0)),
        'prev_close': float(o.get('stck_sdpr', 0)),
        'name':       o.get('hts_kor_isnm', code)
    }

def calc_ma(closes, period):
    return sum(closes[:period]) / period

def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    diffs = [closes[i] - closes[i+1] for i in range(period)]
    gains  = sum(d for d in diffs if d > 0) / period
    losses = sum(abs(d) for d in diffs if d < 0) / period
    if losses == 0:
        return 100
    return 100 - (100 / (1 + gains / losses))

def get_balance(token):
    data = kis_get(token, "/uapi/domestic-stock/v1/trading/inquire-balance", {
        "CANO": ACCOUNT_NO, "ACNT_PRDT_CD": ACCOUNT_PROD,
        "AFHR_FLPR_YN": "N", "OFL_YN": "", "INQR_DVSN": "02",
        "UNPR_DVSN": "01", "FUND_STTL_ICLD_YN": "N",
        "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "00",
        "CTX_AREA_FK100": "", "CTX_AREA_NK100": ""
    }, "TTTC8434R")
    return data.get('output1', []), float(data.get('output2', [{}])[0].get('dnca_tot_amt', 0))

def place_order(token, code, qty, side="buy"):
    tr_id = "TTTC0802U" if side == "buy" else "TTTC0801U"
    result = kis_post(token, "/uapi/domestic-stock/v1/trading/order-cash", {
        "CANO": ACCOUNT_NO, "ACNT_PRDT_CD": ACCOUNT_PROD,
        "PDNO": code, "ORD_DVSN": "01", "ORD_QTY": str(qty), "ORD_UNPR": "0"
    }, tr_id)
    print(f"[주문] {side} {code} {qty}주 -> {result}")
    return result

def scan_signals(token):
    candidates = []
    kospi  = get_volume_rank(token, "J"); time.sleep(0.3)
    kosdaq = get_volume_rank(token, "Q")
    stocks = list(dict.fromkeys(kospi + kosdaq))
    print(f"스캔: {len(stocks)}종목")
    for code in stocks:
        try:
            ohlcv = get_daily_ohlcv(token, code)
            if not ohlcv: continue
            closes = ohlcv['closes']
            ma5, ma20 = calc_ma(closes, 5), calc_ma(closes, 20)
            rsi = calc_rsi(closes, 14)
            if rsi is None: continue
            cur = get_current_price(token, code)
            if not cur['open'] or not cur['prev_close'] or not cur['price']: continue
            gap = (cur['open'] - cur['prev_close']) / cur['prev_close'] * 100
            if ma5 > ma20 and rsi < 40 and gap <= -1.0:
                candidates.append({'code': code, 'name': cur['name'],
                    'price': cur['price'], 'rsi': rsi, 'ma5': ma5, 'ma20': ma20, 'gap': gap})
                print(f"  신호! {cur['name']} RSI={rsi:.1f} 갭={gap:+.1f}%")
            time.sleep(0.06)
        except Exception as e:
            print(f"  오류 {code}: {e}")
    candidates.sort(key=lambda x: x['rsi'])
    return candidates

def main():
    now = datetime.now(KST)
    print(f"=== 자동매매 {now.strftime('%m/%d %H:%M')} ===")
    if now < now.replace(hour=9, minute=0, second=0, microsecond=0) or        now > now.replace(hour=15, minute=30, second=0, microsecond=0):
        print("장 시간 외"); return

    kis_token = get_kis_token()
    kakao_token = get_kakao_token()
    holdings, cash = get_balance(kis_token)
    active = [h for h in holdings if int(h.get('hldg_qty', 0)) > 0]

    if active:
        h = active[0]
        code, name = h['pdno'], h['prdt_name']
        qty = int(h['hldg_qty'])
        avg_price, cur_price = float(h['pchs_avg_pric']), float(h['prpr'])
        pnl = (cur_price - avg_price) / avg_price * 100
        print(f"보유: {name} {qty}주 ({pnl:+.2f}%)")
        sell, reason = False, ""
        if pnl >= 4.0:   sell, reason = True, f"익절 ({pnl:+.2f}%)"
        elif pnl <= -2.0: sell, reason = True, f"손절 ({pnl:+.2f}%)"
        elif now >= now.replace(hour=15, minute=20, second=0, microsecond=0):
            sell, reason = True, "강제청산"
        if sell:
            place_order(kis_token, code, qty, "sell")
            pnl_amt = int((cur_price - avg_price) * qty)
            send_kakao(kakao_token, f"📤 매도\n{name} {qty}주\n사유: {reason}\n손익: {pnl:+.2f}% ({pnl_amt:+,}원)")
        return

    if now >= now.replace(hour=14, minute=0, second=0, microsecond=0):
        print("14시 이후 진입 없음"); return

    candidates = scan_signals(kis_token)
    if not candidates:
        print("조건 충족 종목 없음"); return

    best = candidates[0]
    price, qty = best['price'], int(BUDGET / best['price'])
    if qty < 1:
        print(f"수량 부족 (가격:{price:,}원)"); return

    place_order(kis_token, best['code'], qty, "buy")
    send_kakao(kakao_token,
        f"📥 매수\n{best['name']} {qty}주\n가격: {price:,.0f}원\n"
        f"RSI: {best['rsi']:.1f} | 갭: {best['gap']:+.1f}%\n"
        f"익절: {price*1.04:,.0f} | 손절: {price*0.98:,.0f}")

if __name__ == '__main__':
    main()

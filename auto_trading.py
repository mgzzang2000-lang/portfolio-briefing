# 자동매매 — 다중 필터 단타 전략 (MA크로스 + RSI + 갭)
# 조건: MA5>MA20, RSI<40, 시초가 갭 -1% 이하 동시 충족 시 매수
# 익절 +4%, 손절 -2%, 15:20 강제청산

import os, json, time, requests
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
BASE_URL = "https://openapi.koreainvestment.com:9443"
MAX_BET = 500_000  # 최대 진입금액 캡 (복리)
ACCOUNT_NO = os.environ['KIS_ACCOUNT_NO']
ACCOUNT_PROD = "01"

KIS_APP_KEY    = os.environ['KIS_APP_KEY']
KIS_APP_SECRET = os.environ['KIS_APP_SECRET']
KAKAO_CLIENT_ID     = os.environ['KAKAO_CLIENT_ID']
KAKAO_CLIENT_SECRET = os.environ['KAKAO_CLIENT_SECRET']
KAKAO_REFRESH_TOKEN = os.environ['KAKAO_REFRESH_TOKEN']

DASHBOARD_FILE = "dashboard_data.json"


# ── KIS 인증 ──────────────────────────────────────────────────
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
    # 잔고 이력 기록 (하루 1회 or 매매 시마다)
    history = data.setdefault('balance_history', [])
    today = datetime.now(KST).strftime('%m/%d')
    entry = {'date': today, 'balance': data.get('current_balance', 500000)}
    # 같은 날짜면 덮어쓰기, 아니면 추가
    if history and history[-1]['date'] == today:
        history[-1] = entry
    else:
        history.append(entry)
    # 최대 60개 유지
    data['balance_history'] = history[-60:]
    with open(DASHBOARD_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"[대시보드] 저장 완료")

def log_buy(dash, code, name, qty, price, cash_after):
    dash['trades'].append({
        'action': 'buy',
        'date': datetime.now(KST).strftime('%m/%d %H:%M'),
        'code': code,
        'stock': name,
        'qty': qty,
        'price': int(price),
        'amount': int(price * qty)
    })
    dash['position'] = {
        'code': code,
        'name': name,
        'qty': qty,
        'avg_price': int(price),
        'current_price': int(price)
    }
    dash['current_balance'] = int(cash_after)

def log_sell(dash, name, qty, avg_price, sell_price, pnl_pct, pnl_amt, reason, new_cash):
    dash['trades'].append({
        'action': 'sell',
        'date': datetime.now(KST).strftime('%m/%d %H:%M'),
        'stock': name,
        'qty': qty,
        'price': int(sell_price),
        'avg_price': int(avg_price),
        'pnl_pct': round(pnl_pct, 2),
        'pnl_amt': int(pnl_amt),
        'reason': reason
    })
    dash['position'] = None
    dash['current_balance'] = int(new_cash)

def update_position_price(dash, current_price):
    if dash.get('position'):
        dash['position']['current_price'] = int(current_price)


# ── 시장 데이터 ───────────────────────────────────────────────
def get_volume_rank(token, market="J"):
    """거래량 상위 종목 (J=코스피, Q=코스닥)"""
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
    """최근 25일 일봉 데이터"""
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
    """현재가 + 시초가 + 전일종가"""
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


# ── 지표 계산 ─────────────────────────────────────────────────
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
    rs = gains / losses
    return 100 - (100 / (1 + rs))


# ── 계좌 조회 ─────────────────────────────────────────────────
def get_balance(token):
    data = kis_get(token, "/uapi/domestic-stock/v1/trading/inquire-balance", {
        "CANO": ACCOUNT_NO,
        "ACNT_PRDT_CD": ACCOUNT_PROD,
        "AFHR_FLPR_YN": "N",
        "OFL_YN": "",
        "INQR_DVSN": "02",
        "UNPR_DVSN": "01",
        "FUND_STTL_ICLD_YN": "N",
        "FNCG_AMT_AUTO_RDPT_YN": "N",
        "PRCS_DVSN": "00",
        "CTX_AREA_FK100": "",
        "CTX_AREA_NK100": ""
    }, "TTTC8434R")
    holdings = data.get('output1', [])
    summary  = data.get('output2', [{}])[0]
    cash = float(summary.get('dnca_tot_amt', 0))
    return holdings, cash


# ── 주문 ──────────────────────────────────────────────────────
def place_order(token, code, qty, side="buy"):
    tr_id = "TTTC0802U" if side == "buy" else "TTTC0801U"
    body = {
        "CANO": ACCOUNT_NO,
        "ACNT_PRDT_CD": ACCOUNT_PROD,
        "PDNO": code,
        "ORD_DVSN": "01",   # 시장가
        "ORD_QTY": str(qty),
        "ORD_UNPR": "0"
    }
    result = kis_post(token, "/uapi/domestic-stock/v1/trading/order-cash", body, tr_id)
    print(f"[주문] {side} {code} {qty}주 → {result}")
    return result


# ── 신호 스캔 ─────────────────────────────────────────────────
def scan_signals(token):
    candidates = []
    kospi  = get_volume_rank(token, "J")
    time.sleep(0.3)
    kosdaq = get_volume_rank(token, "Q")
    all_stocks = list(dict.fromkeys(kospi + kosdaq))  # 중복 제거
    print(f"스캔 대상: {len(all_stocks)}종목")

    for code in all_stocks:
        try:
            ohlcv = get_daily_ohlcv(token, code)
            if not ohlcv:
                continue

            closes = ohlcv['closes']
            ma5  = calc_ma(closes, 5)
            ma20 = calc_ma(closes, 20)
            rsi  = calc_rsi(closes, 14)
            if rsi is None:
                continue

            cur = get_current_price(token, code)
            if cur['open'] == 0 or cur['prev_close'] == 0 or cur['price'] == 0:
                continue

            gap = (cur['open'] - cur['prev_close']) / cur['prev_close'] * 100

            # 3개 조건 동시 충족
            if ma5 > ma20 and rsi < 40 and gap <= -1.0:
                candidates.append({
                    'code': code, 'name': cur['name'],
                    'price': cur['price'], 'rsi': rsi,
                    'ma5': ma5, 'ma20': ma20, 'gap': gap
                })
                print(f"  신호! {cur['name']} RSI={rsi:.1f} 갭={gap:+.1f}%")

            time.sleep(0.06)   # rate limit

        except Exception as e:
            print(f"  오류 {code}: {e}")
            continue

    candidates.sort(key=lambda x: x['rsi'])  # RSI 낮은 순
    return candidates


# ── 메인 ──────────────────────────────────────────────────────
def main():
    now = datetime.now(KST)
    print(f"\n=== 자동매매 {now.strftime('%m/%d %H:%M:%S')} ===")

    market_open   = now.replace(hour=9,  minute=0,  second=0, microsecond=0)
    force_sell_at = now.replace(hour=15, minute=20, second=0, microsecond=0)
    market_close  = now.replace(hour=15, minute=30, second=0, microsecond=0)
    entry_cutoff  = now.replace(hour=14, minute=0,  second=0, microsecond=0)

    if now < market_open or now > market_close:
        print("장 시간 외 — 종료")
        return

    kis_token   = get_kis_token()
    kakao_token = get_kakao_token()
    holdings, cash = get_balance(kis_token)
    dash = load_dashboard()

    # ① 보유 포지션 관리
    active = [h for h in holdings if int(h.get('hldg_qty', 0)) > 0]
    if active:
        h = active[0]
        code      = h['pdno']
        name      = h['prdt_name']
        qty       = int(h['hldg_qty'])
        avg_price = float(h['pchs_avg_pric'])
        cur_price = float(h['prpr'])
        pnl       = (cur_price - avg_price) / avg_price * 100

        print(f"보유: {name} {qty}주 | 평균가:{avg_price:,.0f} 현재:{cur_price:,.0f} ({pnl:+.2f}%)")

        # 현재가 업데이트
        update_position_price(dash, cur_price)

        sell, reason = False, ""
        if pnl >= 4.0:
            sell, reason = True, f"익절 ({pnl:+.2f}%)"
        elif pnl <= -2.0:
            sell, reason = True, f"손절 ({pnl:+.2f}%)"
        elif now >= force_sell_at:
            sell, reason = True, "강제청산 (15:20)"

        if sell:
            place_order(kis_token, code, qty, "sell")
            time.sleep(1)
            _, new_cash = get_balance(kis_token)
            pnl_amt = int((cur_price - avg_price) * qty)
            log_sell(dash, name, qty, avg_price, cur_price, pnl, pnl_amt, reason, new_cash)
            save_dashboard(dash)
            msg = (f"📤 매도\n{name} {qty}주\n"
                   f"사유: {reason}\n"
                   f"손익: {pnl:+.2f}% ({pnl_amt:+,}원)\n"
                   f"💰 잔고: {new_cash:,.0f}원")
            send_kakao(kakao_token, msg)
        else:
            save_dashboard(dash)

        return

    # ② 신규 진입 스캔 (14시 이전만)
    if now >= entry_cutoff:
        print("14시 이후 — 신규 진입 없음")
        return

    print("포지션 없음 → 신호 스캔 시작")
    candidates = scan_signals(kis_token)

    if not candidates:
        print("조건 충족 종목 없음")
        return

    best  = candidates[0]
    price = best['price']
    qty   = int(min(cash, MAX_BET) / price)  # 복리 + 캡

    if qty < 1:
        print(f"매수 수량 부족 (가격:{price:,}원)")
        return

    place_order(kis_token, best['code'], qty, "buy")
    used = int(price * qty)
    log_buy(dash, best['code'], best['name'], qty, price, cash - used)
    save_dashboard(dash)

    tp = price * 1.04
    sl = price * 0.98
    msg = (f"📥 매수\n{best['name']} {qty}주\n"
           f"가격: {price:,.0f}원\n"
           f"RSI: {best['rsi']:.1f} | 갭: {best['gap']:+.1f}%\n"
           f"익절: {tp:,.0f} | 손절: {sl:,.0f}\n"
           f"💰 투입: {used:,.0f}원 | 잔고: {cash-used:,.0f}원")
    send_kakao(kakao_token, msg)


if __name__ == '__main__':
    main()

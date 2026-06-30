# 자동매매 — 추세 추종 단타 전략
# 조건: MA20/MA200 상단, MACD 골든크로스(5일내), RSI 상승+70미만, 거래량 2배, 갭 +3% 미만
# 진입: 9:00~11:00 | 익절 +4%, 손절 -2%, 15:20 강제청산

import os, json, time, requests
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
BASE_URL = "https://openapi.koreainvestment.com:9443"
MAX_BET = 500_000  # 최대 진입금액 캡
ACCOUNT_NO = os.environ['KIS_ACCOUNT_NO']
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

def kis_get(token, path, params, tr_id):
    headers = {
        "authorization": f"Bearer {token}",
        "appkey": KIS_APP_KEY,
        "appsecret": KIS_APP_SECRET,
        "tr_id": tr_id,
        "custtype": "P"
    }
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
    print(f"[대시보드] 저장 완료")

def log_buy(dash, code, name, qty, price, cash_after):
    dash['trades'].append({
        'action': 'buy',
        'date': datetime.now(KST).strftime('%m/%d %H:%M'),
        'code': code, 'stock': name, 'qty': qty,
        'price': int(price), 'amount': int(price * qty)
    })
    dash['position'] = {'code': code, 'name': name, 'qty': qty,
                        'avg_price': int(price), 'current_price': int(price)}
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
    data = kis_get(token, "/uapi/domestic-stock/v1/quotations/volume-rank", {
        "FID_COND_MRKT_DIV_CODE": market,
        "FID_COND_SCR_DIV_CODE": "20171",
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

def get_daily_ohlcv(token, code):
    """일봉 데이터 조회 — MA200 + MACD 계산을 위해 시작일 지정"""
    start = (datetime.now(KST) - timedelta(days=500)).strftime('%Y%m%d')
    data = kis_get(token, "/uapi/domestic-stock/v1/quotations/inquire-daily-price", {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": code,
        "FID_PERIOD_DIV_CODE": "D",
        "FID_ORG_ADJ_PRC": "0",
        "FID_INPUT_DATE_1": start,
    }, "FHKST01010400")
    output = data.get('output', [])
    if len(output) < 30:
        return None
    # output[0]이 최신, output[-1]이 과거 (역순)
    closes  = [float(x['stck_clpr']) for x in output]
    volumes = [int(x.get('acml_vol', 0)) for x in output]
    return {'closes': closes, 'volumes': volumes}

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
        'name':       o.get('hts_kor_isnm', code),
        'volume':     int(o.get('acml_vol', 0)),   # 오늘 누적 거래량
    }

# ── 지표 계산 ─────────────────────────────────────────────────
def calc_ma(closes, period):
    if len(closes) < period:
        return None
    return sum(closes[:period]) / period

def calc_ema(closes_desc, period):
    """closes_desc: 최신이 index 0인 역순. 오름차순으로 변환 후 EMA 계산."""
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
        return 100
    rs = gains / losses
    return 100 - (100 / (1 + rs))

def calc_macd(closes_desc, fast=12, slow=26, signal=9):
    """
    closes_desc: 최신이 index 0인 역순
    returns: (macd, signal_line, golden_days_ago)
             golden_days_ago: 최근 골든크로스 경과일, None이면 5일 내 없음
    """
    if len(closes_desc) < slow + signal + 6:
        return None, None, None

    asc = list(reversed(closes_desc))
    n = len(asc)

    k_f   = 2 / (fast + 1)
    k_s   = 2 / (slow + 1)
    k_sig = 2 / (signal + 1)

    # 초기 EMA (SMA로 시작)
    ema_f = sum(asc[:fast]) / fast
    ema_s = sum(asc[:slow]) / slow

    # fast EMA를 slow 시작점까지 업데이트
    for i in range(fast, slow):
        ema_f = asc[i] * k_f + ema_f * (1 - k_f)

    # MACD 시계열 생성
    macd_series = []
    for i in range(slow, n):
        ema_f = asc[i] * k_f + ema_f * (1 - k_f)
        ema_s = asc[i] * k_s + ema_s * (1 - k_s)
        macd_series.append(ema_f - ema_s)

    if len(macd_series) < signal:
        return None, None, None

    # Signal line 시계열 생성
    sig = sum(macd_series[:signal]) / signal
    sig_series = [sig]
    for v in macd_series[signal:]:
        sig = v * k_sig + sig * (1 - k_sig)
        sig_series.append(sig)

    # 최근 5일 내 골든크로스 확인 (MACD가 Signal 상향 돌파)
    golden_days = None
    check = min(6, len(macd_series) - 1, len(sig_series) - 1)
    for i in range(1, check + 1):
        m_now  = macd_series[-i]
        m_prev = macd_series[-(i+1)]
        s_now  = sig_series[-i]
        s_prev = sig_series[-(i+1)]
        if m_prev < s_prev and m_now >= s_now:
            golden_days = i
            break

    return macd_series[-1], sig_series[-1], golden_days

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

# ── 신호 스캔 ─────────────────────────────────────────────────
def scan_signals(token):
    candidates = []
    kospi = get_volume_rank(token, "J")
    all_stocks = list(dict.fromkeys(kospi))
    print(f"스캔 대상: {len(all_stocks)}종목")

    if not all_stocks:
        print("[경고] 스캔 대상 종목 없음 — API 응답 이상")
        return []

    for code in all_stocks:
        try:
            ohlcv = get_daily_ohlcv(token, code)
            if not ohlcv:
                continue

            closes  = ohlcv['closes']   # 최신이 index 0
            volumes = ohlcv['volumes']

            # ── 지표 계산 ──
            ma20  = calc_ma(closes, 20)
            ma200 = calc_ma(closes, 200)  # 데이터 부족 시 None
            rsi_today     = calc_rsi(closes,     14)
            rsi_yesterday = calc_rsi(closes[1:], 14)
            macd, signal_line, golden_days = calc_macd(closes)

            if ma20 is None or rsi_today is None or rsi_yesterday is None or macd is None:
                continue

            # 20일 평균 거래량 (어제 기준, index 1~20)
            vol_avg20 = sum(volumes[1:21]) / 20 if len(volumes) >= 21 else None

            # 현재가 조회
            cur = get_current_price(token, code)
            price = cur['price']
            if price == 0 or cur['open'] == 0 or cur['prev_close'] == 0:
                continue

            gap = (cur['open'] - cur['prev_close']) / cur['prev_close'] * 100

            # ── 조건 필터 ──
            # 1. MA20 상단
            if price <= ma20:
                continue
            # 2. MA200 상단 (데이터 있을 때만)
            if ma200 is not None and price <= ma200:
                continue
            # 3. MACD 골든크로스 5일 이내 + 현재 MACD > Signal
            if golden_days is None or macd <= signal_line:
                continue
            # 4. RSI 상승 추세 + 과매수 제외
            if rsi_today <= rsi_yesterday or rsi_today >= 70:
                continue
            # 5. 거래량 급증 (20일 평균 2배 이상)
            if vol_avg20 and cur['volume'] < vol_avg20 * 2:
                continue
            # 6. 시초가 갭 +3% 미만
            if gap >= 3.0:
                continue

            vol_ratio = cur['volume'] / vol_avg20 if vol_avg20 else 0
            candidates.append({
                'code': code, 'name': cur['name'],
                'price': price, 'rsi': rsi_today,
                'ma20': ma20, 'ma200': ma200,
                'macd': macd, 'golden_days': golden_days,
                'gap': gap, 'vol_ratio': vol_ratio
            })
            ma200_str = f"{ma200:,.0f}" if ma200 else "N/A"
            print(f"  신호! {cur['name']} RSI={rsi_today:.1f} MACD크로스={golden_days}일전 "
                  f"갭={gap:+.1f}% 거래량={vol_ratio:.1f}배 MA200={ma200_str}")

            time.sleep(0.06)

        except Exception as e:
            print(f"  오류 {code}: {e}")
            continue

    # 거래량 비율 높은 순 정렬 (세력 참여 강도 우선)
    candidates.sort(key=lambda x: -x['vol_ratio'])
    return candidates

# ── 메인 ──────────────────────────────────────────────────────
def main():
    now = datetime.now(KST)
    print(f"\n=== 자동매매 {now.strftime('%m/%d %H:%M:%S')} ===")

    market_open   = now.replace(hour=9,  minute=0,  second=0, microsecond=0)
    force_sell_at = now.replace(hour=15, minute=20, second=0, microsecond=0)
    market_close  = now.replace(hour=15, minute=30, second=0, microsecond=0)
    entry_cutoff  = now.replace(hour=11, minute=0,  second=0, microsecond=0)  # 11시 진입 마감

    if now < market_open or now > market_close:
        print("장 시간 외 — 종료")
        return

    kis_token   = get_kis_token()
    kakao_token = get_kakao_token()
    holdings, cash = get_balance(kis_token)
    dash = load_dashboard()

    try:
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

        # ② 신규 진입 스캔 (11시 이전만)
        if now >= entry_cutoff:
            print("11시 이후 — 신규 진입 없음")
            return

        print("포지션 없음 → 신호 스캔 시작")
        candidates = scan_signals(kis_token)

        if not candidates:
            print("조건 충족 종목 없음")
            return

        best  = candidates[0]
        price = best['price']
        qty   = int(min(cash, MAX_BET) / price)

        if qty < 1:
            print(f"매수 수량 부족 (가격:{price:,}원)")
            return

        place_order(kis_token, best['code'], qty, "buy")
        used = int(price * qty)
        log_buy(dash, best['code'], best['name'], qty, price, cash - used)
        save_dashboard(dash)

        tp = price * 1.04
        sl = price * 0.98
        ma200_str = f"{best['ma200']:,.0f}" if best['ma200'] else "N/A"
        msg = (f"📥 매수\n{best['name']} {qty}주\n"
               f"가격: {price:,.0f}원\n"
               f"RSI: {best['rsi']:.1f} | MACD크로스: {best['golden_days']}일전\n"
               f"거래량: {best['vol_ratio']:.1f}배 | 갭: {best['gap']:+.1f}%\n"
               f"MA200: {ma200_str}\n"
               f"익절: {tp:,.0f} | 손절: {sl:,.0f}\n"
               f"💰 투입: {used:,.0f}원 | 잔고: {cash-used:,.0f}원")
        send_kakao(kakao_token, msg)

    except Exception as e:
        err_msg = f"⚠️ 자동매매 오류\n{str(e)[:150]}"
        print(err_msg)
        try:
            send_kakao(kakao_token, err_msg)
        except:
            pass

if __name__ == '__main__':
    main()

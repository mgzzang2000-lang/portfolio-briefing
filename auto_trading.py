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
import market_calendar
KST = timezone(timedelta(hours=9))
BASE_URL = "https://openapi.koreainvestment.com:9443"
MAX_BET = 300_000  # [2026-07-06] 눌림목+불플래그 진입 로직 검증 전까지 축소 운용 (20만→30만 조정)
DERIVATIVE_ETF_KEYWORDS = ["레버리지", "인버스", "ETN", "선물"]  # [2026-07-06] 파생상품 ETF/ETN 매수 제외
# [2026-07-16] 실거래 로그 16건을 1분봉과 대조 분석한 결과, 손실거래는 평균
# 시가대비 +9.4% 지점에서 진입(승리거래는 +5.8%)한 것으로 나타남 — 눌림목·재돌파
# 조건을 통과해도 "그날 하루로 보면 이미 많이 오른 상태"인지는 걸러내지 못하고
# 있었음. 섀도우A(2026-07-13, shadow_scan.py)에 이미 같은 취지로 검증해둔 상한을
# 실거래에도 동일하게 적용.
MAX_EXTENSION_FROM_OPEN_PCT = 5.0
# [2026-07-16] 실거래 24건 분석 결과: 이긴 거래는 +2% 부분익절로 절반만 실현되는 반면
# 진 거래는 손절 시 전량이 한 번에 나가, 이긴 거래의 평균 투입금(14만원)이 진 거래
# (26.8만원)의 절반 수준밖에 안 됐음 — 승률/평균%는 우호적인데도 금액가중 평균수익률은
# 마이너스(-0.56%)였던 근본 원인. 부분익절 비율을 낮춰 이긴 거래에 더 많은 물량을
# 남겨(계속 추세를 태워) 이 비대칭을 줄임(50%→30%).
PARTIAL_TP_FRACTION = 0.3
# [2026-07-18] 손절을 구조적(눌림 저점) 기준으로 바꾸면서 트레이드마다 리스크폭이
# 달라지는데, 목표가는 여전히 +4% 고정이라 손익비가 트레이드마다 들쭉날쭉했음.
# 목표가를 "진입가-손절가(=리스크)"에 비례하게 바꿔 손익비를 항상 일정하게 유지.
TARGET_R_MULTIPLE = 2.5
# [2026-07-18] 눌림 저점(반전봉)이 확인돌파 시점으로부터 너무 옛날 봉이면, 이미
# 가격이 저점에서 많이 벗어난 뒤에야 뒤늦게 진입하는(재돌파 문제의 축소판) 결과로
# 이어짐 — 반전봉이 확인돌파 캔들 기준 최근 N봉 이내인 경우만 인정.
PULLBACK_CONFIRM_WINDOW = 3
ACCOUNT_NO   = os.environ['KIS_ACCOUNT_NO']
ACCOUNT_PROD = "01"
KIS_APP_KEY    = os.environ['KIS_APP_KEY']
KIS_APP_SECRET = os.environ['KIS_APP_SECRET']
KAKAO_CLIENT_ID     = os.environ['KAKAO_CLIENT_ID']
KAKAO_CLIENT_SECRET = os.environ['KAKAO_CLIENT_SECRET']
KAKAO_REFRESH_TOKEN = os.environ['KAKAO_REFRESH_TOKEN']
DASHBOARD_FILE = "dashboard_data.json"
TOKEN_FILE     = "kis_token.json"
DAILY_CACHE_FILE = "daily_filter_cache.json"
HEARTBEAT_FILE = "watcher_heartbeat.json"
WATCHER_ALIVE_THRESHOLD_SEC = 90  # [2026-07-10] 로컬 watcher.py 하트비트가 이 시간 이내면
# "살아서 포지션을 직접 관리 중"으로 간주하고 GitHub Actions는 양보(중복매도 방지).
# 흥구석유·금호전기·흥아해운에서 두 감시자가 동시에 +2% 부분익절을 각자 실행해
# 매도가 중복 발생했던 레이스 컨디션 때문에 추가.
def watcher_is_alive():
    try:
        with open(HEARTBEAT_FILE, 'r', encoding='utf-8') as f:
            hb = json.load(f)
        alive_at = datetime.fromisoformat(hb['alive_at'])
        age = (datetime.now(KST) - alive_at).total_seconds()
        return age < WATCHER_ALIVE_THRESHOLD_SEC
    except Exception:
        return False
# [2026-07-07] MA20/BB스퀴즈/20일평균거래량은 어제 종가 기준이라 하루 안에서는
# 사실상 안 바뀌는 값인데, 매 5분 사이클마다 상위 ~200종목 전부 다시 계산하느라
# 스캔이 오래 걸렸음(사이클당 35~40초). 이 값들만 종목당 하루 1회 계산해 캐싱하고
# 재사용 — 오늘 거래량순위·갭%·1분봉처럼 실시간으로 봐야 하는 값은 그대로 매번 확인.
def load_daily_cache():
    today_str = datetime.now(KST).strftime('%Y%m%d')
    try:
        with open(DAILY_CACHE_FILE, 'r', encoding='utf-8') as f:
            cached = json.load(f)
        if cached.get('date') == today_str:
            return cached.get('data', {})
    except Exception:
        pass
    return {}
def save_daily_cache(cache):
    today_str = datetime.now(KST).strftime('%Y%m%d')
    try:
        with open(DAILY_CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump({'date': today_str, 'data': cache}, f)
    except Exception as e:
        print(f"[일봉 캐시 저장 실패] {e}")
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
            # [2026-07-09] 매도 직후 재조회에서 HTTP 500이 온 적이 있었는데, 종전엔
            # status를 안 보고 그냥 파싱 시도해서 에러 바디를 "성공"으로 취급했음(잔고 0원 오발송의 원인).
            if r.status_code != 200:
                print(f"[경고] status={r.status_code} 응답: {tr_id} | body={r.text[:200]}")
                if attempt < retries - 1:
                    time.sleep(2)
                    continue
                return {}
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
    # [2026-07-17] trade_id — 한 포지션의 매수+부분익절(들)+최종청산을 하나로 묶어
    # 라운드트립 기준 승률을 계산할 수 있게 함(부분익절도 이제 log_partial_sell로
    # 별도 기록되므로, 같은 포지션의 여러 매도 줄을 구분 없이 다 "1승"으로 세면
    # 승률이 부풀려짐 — index.html에서 이 trade_id로 묶어서 순손익 기준으로 승패 판정).
    trade_id = f"{code}-{entry_time.strftime('%Y%m%d%H%M%S')}"
    dash['trades'].append({
        'action': 'buy', 'trade_id': trade_id,
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
        'trade_id': trade_id,
    }
    dash['current_balance'] = int(cash_after)
def recover_missing_sells(token, dash, dash_position):
    """대시보드엔 포지션이 남아있는데 실계좌엔 없을 때, 조용히 지우기 전에
    오늘 체결내역(TTTC8001R)에서 이 종목의 매도 기록을 찾아 log_sell()로 남긴다.
    [2026-07-10] 로컬 watcher가 실제로 판 뒤 git 동기화 실패로 매도 로그가
    원격에 못 올라가고, 뒤이어 GitHub Actions가 포지션 불일치를 감지해 기록 없이
    상태만 초기화해버리는 사고(흥아해운 건)가 발생해 추가."""
    code = dash_position['code']
    avg_price = dash_position['avg_price']
    # [2026-07-16] 같은 종목을 하루에 두 번 이상 사고팔 때(평단가가 서로 다른 별개
    # 매수분), 이번 포지션보다 "이전" 매도까지 전부 이번 avg_price로 잘못 기록되는
    # 사고 발견(003280 1차 매수분 매도를 2차 매수분 평단가로 기록 → 손익률 오표시).
    # 이번 진입시각 이후 체결만 복구 대상으로 제한한다.
    entry_time_str = dash_position.get('entry_time')
    entry_t = datetime.fromisoformat(entry_time_str).time() if entry_time_str else None
    today = datetime.now(KST).strftime('%Y%m%d')
    data = kis_get(token, "/uapi/domestic-stock/v1/trading/inquire-daily-ccld", {
        "CANO": ACCOUNT_NO, "ACNT_PRDT_CD": ACCOUNT_PROD,
        "INQR_STRT_DT": today, "INQR_END_DT": today,
        "SLL_BUY_DVSN_CD": "01", "PDNO": code, "CCLD_DVSN": "01",
        "ORD_GNO_BRNO": "", "ODNO": "", "INQR_DVSN": "00",
        "INQR_DVSN_1": "", "INQR_DVSN_3": "00", "EXCG_ID_DVSN_CD": "KRX",
        "CTX_AREA_FK100": "", "CTX_AREA_NK100": ""
    }, "TTTC8001R")
    sells = data.get('output1', [])
    if not sells:
        return False
    logged_times = {t['date'] for t in dash['trades'] if t.get('action') == 'sell'}
    recovered = False
    for s in sorted(sells, key=lambda x: x['ord_tmd']):
        qty = int(s.get('tot_ccld_qty', 0))
        if qty <= 0:
            continue
        price = float(s['avg_prvs'])
        t = datetime.strptime(s['ord_tmd'], '%H%M%S')
        if entry_t and t.time() < entry_t:
            continue  # 이번 포지션 진입 이전 체결 — 다른 매수분이므로 건드리지 않음
        date_str = datetime.now(KST).strftime('%m/%d ') + t.strftime('%H:%M')
        if date_str in logged_times:
            continue
        pnl_pct = (price - avg_price) / avg_price * 100
        pnl_amt = int((price - avg_price) * qty)
        dash['trades'].append({
            'action': 'sell', 'date': date_str, 'partial': False,
            'trade_id': dash_position.get('trade_id'),
            'stock': dash_position.get('name', code),
            'qty': qty, 'price': int(price), 'avg_price': int(avg_price),
            'pnl_pct': round(pnl_pct, 2), 'pnl_amt': pnl_amt,
            'reason': f"{'익절' if pnl_pct >= 0 else '손절'} (복구, {pnl_pct:+.2f}%)"
        })
        recovered = True
    return recovered
def log_sell(dash, name, qty, avg_price, sell_price, pnl_pct, pnl_amt, reason, new_cash):
    """포지션의 마지막(전량) 청산 — 기록 후 position을 비운다."""
    dash['trades'].append({
        'action': 'sell', 'partial': False,
        'trade_id': (dash.get('position') or {}).get('trade_id'),
        'date': datetime.now(KST).strftime('%m/%d %H:%M'),
        'stock': name, 'qty': qty, 'price': int(sell_price),
        'avg_price': int(avg_price), 'pnl_pct': round(pnl_pct, 2),
        'pnl_amt': int(pnl_amt), 'reason': reason
    })
    dash['position'] = None
    dash['current_balance'] = int(new_cash)


def log_partial_sell(dash, name, qty, avg_price, sell_price, pnl_pct, pnl_amt, reason, new_cash):
    """[2026-07-17 신설] +2% 부분익절/2시간 부분청산처럼 포지션 일부만 파는 경우 —
    기존엔 log_sell()을 아예 안 불러서 부분익절 자체가 거래기록에 안 남았음(사용자
    지적으로 발견). position은 그대로 두고(나머지 수량은 계속 관리) 거래기록만 남긴다."""
    dash['trades'].append({
        'action': 'sell', 'partial': True,
        'trade_id': (dash.get('position') or {}).get('trade_id'),
        'date': datetime.now(KST).strftime('%m/%d %H:%M'),
        'stock': name, 'qty': qty, 'price': int(sell_price),
        'avg_price': int(avg_price), 'pnl_pct': round(pnl_pct, 2),
        'pnl_amt': int(pnl_amt), 'reason': reason
    })
    dash['current_balance'] = int(new_cash)
def update_position_price(dash, current_price):
    if dash.get('position'):
        dash['position']['current_price'] = int(current_price)
INDEX_CODE = {"J": "0001", "Q": "1001"}  # 코스피종합/코스닥종합 지수코드
MIN_INDEX_CHANGE_PCT = -1.0  # [2026-07-17] 이보다 더 하락한 지수 방향에서는 그 시장
# 종목의 롱(매수) 신규진입을 보류 — "상위 타임프레임(시장 전체) 추세와 반대되는
# 개별 신호는 승률이 떨어진다"는 원칙(SMC/ICT 자료조사, 4번 논의 참고) 적용.
# 지수(개별 종목이 아니라 코스피/코스닥 종합지수) 일간 변동성 표준편차가 대략
# ±0.8~1.2% 수준이라, 처음 잡았던 -0.3%는 "평범한 소폭 하락 마감"조차 걸러버릴
# 만큼 과민한 기준이었음(사용자 지적) — -1.0%로 완화해 "노이즈성 하락"과
# "뚜렷한 하락추세"를 구분.


def get_index_direction(token, market="J"):
    """코스피(J)/코스닥(Q) 종합지수의 당일 등락률(%)을 조회.
    조회 실패 시 None 반환 — 호출측은 None을 "필터 통과"로 취급해야 한다
    (지수 조회 실패로 매수 자체가 막히는 새로운 장애점을 만들지 않기 위함)."""
    data = kis_get(token, "/uapi/domestic-stock/v1/quotations/inquire-index-price", {
        "FID_COND_MRKT_DIV_CODE": "U",
        "FID_INPUT_ISCD": INDEX_CODE[market],
    }, "FHPUP02100000")
    o = data.get('output', {})
    try:
        return float(o.get('bstp_nmix_prdy_ctrt', ''))
    except (TypeError, ValueError):
        return None


# ── 시장 데이터 ───────────────────────────────────────────────
def get_volume_rank(token, market="J"):
    scr_code = "20172" if market == "Q" else "20171"
    data = kis_get(token, "/uapi/domestic-stock/v1/quotations/volume-rank", {
        "FID_COND_MRKT_DIV_CODE": market,
        "FID_COND_SCR_DIV_CODE": scr_code,
        "FID_INPUT_ISCD": "0000",
        "FID_DIV_CLS_CODE": "0",
        # [2026-07-17] "0"(단순 평균거래량=체결 주식 수 기준)은 저가·소형 유통주식수
        # 종목이 회전율만으로 매일 상위권을 독점하는 구조적 편향이 있었음(흥구석유·
        # 빛과전자 등이 반복 포착된 원인) — "3"(거래금액순)으로 변경해 실제 자금이
        # 크게 도는 종목 위주로 후보군을 넓힘.
        "FID_BLNG_CLS_CODE": "3",
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
    # [2026-07-17] 휴장일에도 이 API가 오류 없이 마지막 실제 거래일 데이터를 그대로
    # 돌려줘서 "오늘 데이터"로 오인하던 사고의 2차 방어선 — 휴장일조회(market_calendar)가
    # 놓친 경우에도 여기서 최신 봉의 실제 날짜를 직접 대조한다.
    if not market_calendar.is_fresh_bar(output[0], now.strftime('%Y%m%d')):
        print(f"  [1분봉] {code} 최신 봉 날짜가 오늘과 불일치 — 휴장/데이터지연 추정, 스킵")
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
        'high':        float(o.get('stck_hgpr', 0)),
        'prev_close':  float(o.get('stck_sdpr', 0)),
        'name':        o.get('hts_kor_isnm', code),
        'volume':      int(o.get('acml_vol', 0)),
        'upper_limit': float(o.get('stck_mxpr', 0)),  # 당일 상한가
        # [2026-07-07] inquire-price는 hts_kor_isnm을 채워주지 않아 'name'이 항상 종목코드로
        # 대체되고 있었음(=DERIVATIVE_ETF_KEYWORDS 이름 매칭이 사실상 무의미했음).
        # bstp_kor_isnm/rprs_mrkt_kor_name은 이 API에서 실제로 채워지므로 ETF/ETN 판별용으로 사용.
        'bstp_name':   o.get('bstp_kor_isnm', ''),
        'mrkt_name':   o.get('rprs_mrkt_kor_name', ''),
        'vi_cls_code':      o.get('vi_cls_code', 'N'),       # VI적용구분코드(N=미발동)
        'ovtm_vi_cls_code': o.get('ovtm_vi_cls_code', 'N'),  # 시간외 VI적용구분코드
    }


def is_derivative_etf(token, code, bstp_name, mrkt_name):
    """ETF/ETN 계열 상품 중 실제 파생형(레버리지·인버스 등)인지 search-info로 확인.
    [2026-07-07] 252670(KODEX 200선물인버스2X) 매수 시도가 거래소에서
    '파생ETF 미신청' 오류로 거부된 것을 계기로 추가 — 기존 이름 키워드 필터는
    inquire-price가 종목명을 안 줘서 작동하지 않고 있었음."""
    combined = f"{bstp_name} {mrkt_name}"
    if 'ETN' in combined:
        return True
    if 'ETF' not in combined:
        return False
    data = kis_get(token, "/uapi/domestic-stock/v1/quotations/search-info", {
        "PRDT_TYPE_CD": "300", "PDNO": code
    }, "CTPF1002R")
    o = data.get('output', {})
    ratio = o.get('etf_chas_erng_rt_dbnb', '').strip()
    if not ratio:
        return False  # 정보 없으면 일반 ETF로 간주 (과잉 차단 방지)
    try:
        return float(ratio) != 1.0
    except ValueError:
        return True
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
    # [2026-07-09] API 실패(빈 응답/에러 바디)로 output2가 아예 없는 경우, 종전엔
    # summary={}로 폴백해서 cash=0.0을 "정상 잔고"처럼 반환했음 — 매도 직후 재조회가
    # 실패했을 뿐인데 "잔고 0원"으로 대시보드/카톡에 그대로 찍히는 사고로 이어짐.
    # output2 자체가 없으면 잔고를 신뢰할 수 없다는 뜻이므로 None을 반환해 호출부가
    # "값을 모른다"와 "실제로 0원이다"를 구분하게 함.
    if not data.get('output2'):
        return data.get('output1', []), None
    holdings = data.get('output1', [])
    summary  = data['output2'][0]
    # [2026-07-07] dnca_tot_amt(예수금총금액)는 당일 손익 정산 전 "원금" 그대로라
    # 매매 손실이 나도 줄지 않는 값이었음(대시보드 잔액이 안 줄어드는 버그의 원인,
    # 동시에 매수수량 계산도 실제보다 부풀려진 잔액 기준으로 하고 있던 잠재 위험).
    # nass_amt(순자산금액)는 당일 손익·수수료까지 정산 반영된 실제 순자산.
    cash = float(summary.get('nass_amt', 0) or summary.get('dnca_tot_amt', 0))
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
      ⑤ 거래량 20일 평균 1.5배 이상 (시간 경과 비례 보정) [2026-07-13: 2배→1.5배 완화]
      ⑥ 갭 필터 5% (전 구간 단일 기준) [2026-07-13: 09:00~14:50 단일 구간으로 통합,
         14:00~14:30 고가근접 특례 제거 — 거래 빈도를 늘리기 위한 필터 단순화]
         [2026-07-17: 09:00~09:20 재차단 — 아래 시간대 주석 참고]
      ⑦ [2026-07-17 추가] 지수 방향 필터 — 그 종목이 속한 시장(코스피/코스닥) 지수가
         당일 -0.3% 넘게 하락 중이면 신규 매수 보류
    정렬: 1순위 당일 신규 돌파 > 2순위 스퀴즈 종목 > 3순위 거래량 비율 순
    """
    # [2026-07-13] 진입 가능 시간대 — 기존 09:00~11:00/14:00~14:30 이원화 게이트가
    # 하루 6.5시간 중 2.5시간만 쓰던 것을 09:00~14:50 단일 구간으로 통합(15:20 강제청산
    # 전 최소 30분 여유는 유지). "더 자주 거래"를 위한 필터 완화.
    # [2026-07-17] 1분봉 305건 급등 이벤트 분석 결과 33%가 09시대에 몰려 있었는데, 이
    # 구간은 VI 발동-해제가 만드는 구조적 변동성(진짜 방향성 아닌 경우가 많음)이 섞여
    # 있어 09:00~09:20을 다시 제외 — 스토캐스틱RSI 계산에 필요한 27봉 확보 시점과도
    # 대략 맞물림(섀도우A 7/13 발견 참고).
    now = datetime.now(KST)
    now_hour = now.hour
    now_minute = now.minute

    can_entry = (now_hour == 9 and now_minute >= 20) or (10 <= now_hour < 14) or (now_hour == 14 and now_minute < 50)
    if not can_entry:
        print(f"[{now.strftime('%H:%M')}] 신규 진입 시간 아님 — 스캔 스킵")
        return []

    gap_threshold = 5.0  # 전 구간 단일 갭 기준 (14:00~14:30 고가근접 특례 제거)

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
    # [2026-07-17] 지수 방향 필터 — 종목마다 조회하면 낭비이므로 시장별로 한 번만 조회
    index_pct = {"J": get_index_direction(token, "J"), "Q": get_index_direction(token, "Q")}
    for m, pct in index_pct.items():
        label = "코스피" if m == "J" else "코스닥"
        print(f"[지수] {label} 당일 {pct:+.2f}%" if pct is not None else f"[지수] {label} 조회 실패 — 필터 미적용")
    # [2026-07-07] MA20/BB스퀴즈/20일평균거래량(어제 종가 기준, 하루 안엔 안 바뀜)은
    # 종목당 하루 1회만 계산해 캐싱 — 매 사이클 재계산으로 인한 스캔 지연(35~40초) 완화
    daily_cache = load_daily_cache()
    cache_hit = 0
    for code in stocks:
        try:
            market = "J" if code in kospi_set else "Q"
            cached = daily_cache.get(code)
            if cached:
                ma20 = cached['ma20']; ma200 = cached['ma200']
                bb_upper = cached['bb_upper']; bw = cached['bw']
                in_squeeze = cached['in_squeeze']
                vol_avg20 = cached['vol_avg20']
                ma20_prev = cached['ma20_prev']
                is_new_breakout = cached['is_new_breakout']
                cache_hit += 1
            else:
                ohlcv = get_daily_ohlcv(token, code, market)
                if not ohlcv:
                    continue
                closes  = ohlcv['closes']
                volumes = ohlcv['volumes']
                ma20  = calc_ma(closes, 20)
                ma200 = calc_ma(closes, 200)  # [2026-07-02] 필터에는 미사용, 표시/메시지용으로만 계산
                bb_upper, bb_mid, _, bw = calc_bb(closes, 20)
                in_squeeze = is_daily_bb_squeeze(closes)
                if ma20 is None or bb_upper is None:
                    continue
                vol_avg20 = sum(volumes[1:21]) / 20 if len(volumes) >= 21 else None
                # [2026-07-02] 당일 신규 돌파 여부 — 제외하지 않고 "우선순위" 태그로만 사용.
                # 어제 종가가 20일선 이하였다가 오늘 처음 뚫은 종목이면 True(1순위 후보),
                # 어제 이미 20일선 위였던 종목(시세가 이미 나온 경우가 많음)은 False(2순위 후보)
                # 로 분류만 하고, 여전히 후보 목록에는 포함시킨다.
                ma20_prev = calc_ma(closes[1:], 20)
                is_new_breakout = (
                    ma20_prev is not None and len(closes) > 1 and closes[1] <= ma20_prev
                )
                daily_cache[code] = {
                    'ma20': ma20, 'ma200': ma200, 'bb_upper': bb_upper, 'bw': bw,
                    'in_squeeze': in_squeeze, 'vol_avg20': vol_avg20,
                    'ma20_prev': ma20_prev, 'is_new_breakout': is_new_breakout,
                }
            cur = get_current_price(token, code, market)
            price = cur['price']
            if price == 0 or cur['open'] == 0 or cur['prev_close'] == 0:
                continue
            # [2026-07-06] 파생상품 ETF/ETN(레버리지·인버스·선물 등) 거래 제외
            if any(kw in cur['name'] for kw in DERIVATIVE_ETF_KEYWORDS):
                continue
            # [2026-07-07] 실제 판별은 bstp_name/mrkt_name 기반 is_derivative_etf()로 수행
            if is_derivative_etf(token, code, cur.get('bstp_name', ''), cur.get('mrkt_name', '')):
                continue
            # [2026-07-17] 지수 방향 필터 — 그 종목이 속한 시장 지수가 뚜렷이 하락 중이면
            # 개별 종목의 롱 신호와 상위 타임프레임 추세가 반대라 승률이 떨어짐(4번 논의).
            if index_pct[market] is not None and index_pct[market] < MIN_INDEX_CHANGE_PCT:
                continue
            gap = (cur['open'] - cur['prev_close']) / cur['prev_close'] * 100
            # ── 필터 ──────────────────────────────────────────
            if price <= ma20:
                continue
            # [2026-07-02 제거] MA200 필터 — 아래 조건 비활성화
            # if ma200 is not None and price <= ma200:
            #     continue
            # [2026-07-02 제거] MACD 관련 조건 전체 삭제 (calc_macd 호출도 제거됨)
            # BB: 스퀴즈 상태 OR BB 상단 근접 (기존 RSI 조건 대체)
            bb_near_upper = (price >= bb_upper * 0.98)
            if not (in_squeeze or bb_near_upper):
                continue
            if vol_avg20 and cur['volume'] < vol_avg20 * (elapsed_minutes / 390) * 1.5:
                continue
            # [2026-07-02] 갭 필터 적용
            if gap >= gap_threshold:
                continue
            # [2026-07-16] 시가대비 등락률 상한 — 손실거래 평균(+9.4%)이 승리거래
            # 평균(+5.8%)보다 뚜렷이 높게 나온 실거래 분석 결과 반영(섀도우A와 동일 기준)
            pct_from_open = (price - cur['open']) / cur['open'] * 100
            if pct_from_open > MAX_EXTENSION_FROM_OPEN_PCT:
                continue
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
    save_daily_cache(daily_cache)
    print(f"[일봉 캐시] {cache_hit}/{len(stocks)}종목 재사용")
    # [2026-07-02] 정렬: 1순위 당일 신규 돌파 > 2순위 스퀴즈 종목 > 3순위 거래량 비율 순
    candidates.sort(key=lambda x: (-int(x['is_new_breakout']), -int(x['in_squeeze']), -x['vol_ratio']))
    return candidates
# ── [2단계] 1분봉 진입 타이밍 확인 ──────────────────────────────
def check_pullback_support_hold(closes, highs, lows, volumes, lookback=15):
    """
    [2026-07-18 재설계] 눌림목 진입 트리거 — "직전고점 재돌파" 방식 폐기.
    기존엔 눌림 확인 후 "직전 고점을 다시 돌파"해야 진입했는데, 이는 눌림목(싸게
    사기)과 재돌파(가격이 이미 고점까지 회복한 뒤 사기)가 서로 다른 셋업이라
    모순이라는 걸 사용자와 논의로 확인함 — 실거래에서도 손실거래가 승리거래보다
    시가대비 더 높은 지점(+9.4% vs +5.8%, 2026-07-16 분석)에서 진입한 것과 같은 맥락.
    "직전 고점 재돌파" 대신 "눌림 저점에서 지지를 확인하고 반전이 이어지는지"를
    트리거로 삼아 진입가를 눌림 저점 가까이로 당긴다.
      ① 상승(깃대) → ② 거래량 줄며 쉬는 눌림(절대%+깃대 대비 되돌림비율 둘 다 확인,
      기존과 동일) → ③ 눌림 저점을 만든 봉이 자기 range 상단에서 마감(반전봉) →
      ④ 다음 봉이 그 반전봉의 고가를 돌파(반전이 실제로 이어지는지 확인) →
      ⑤ 거래량 급증
    closes/highs/lows/volumes: 최신이 index 0.
    반환: (ok, info_str, support_price) — support_price는 손절 기준(눌림 저점)으로 사용.
    """
    if len(closes) < lookback + 3 or len(volumes) < lookback + 3:
        return False, "데이터 부족(눌림목 확인 불가)", None
    # 분석 편의를 위해 오래된 → 최신 순으로 변환
    asc_c = list(reversed(closes[:lookback]))
    asc_h = list(reversed(highs[:lookback]))
    asc_l = list(reversed(lows[:lookback]))
    asc_v = list(reversed(volumes[:lookback]))

    # 최근 3봉은 "반전확인" 구간으로 남겨두고, 그 이전 구간에서 고점(깃대 상단) 탐색
    search_end = lookback - 3
    if search_end < 3:
        return False, "탐색 구간 부족", None
    peak_idx = max(range(search_end), key=lambda i: asc_h[i])
    peak_price = asc_h[peak_idx]

    # 고점 이후 ~ 직전 봉까지가 눌림(횡보/조정) 구간
    pullback_range = list(range(peak_idx + 1, lookback - 1))
    if len(pullback_range) < 2:
        return False, "눌림 구간 부족", None
    trough_idx = min(pullback_range, key=lambda i: asc_l[i])
    trough_price = asc_l[trough_idx]
    pullback_vol = sum(asc_v[i] for i in pullback_range) / len(pullback_range)

    pullback_pct = (peak_price - trough_price) / peak_price * 100 if peak_price else 0

    # [2026-07-17] 되돌림 "비율" 확인 — 깃대(상승 시작점) 대비 이번 눌림이 그 상승폭의
    # 30~60% 구간인지 확인. 기존 pullback_pct(peak 대비 절대 %)만으로는 종목별 변동성
    # 차이를 못 걸러냄(변동성 큰 종목은 절대 %는 기준 안에 들어도 실제로는 상승폭의
    # 대부분을 반납한 눌림일 수 있음) — 절대 % 조건에 더해 상대적 위치도 같이 확인.
    flagpole_base_range = asc_l[:peak_idx]
    if len(flagpole_base_range) < 2:
        return False, "깃대 기준점 부족", None
    flagpole_base = min(flagpole_base_range)
    up_move = peak_price - flagpole_base
    retracement_ratio = (peak_price - trough_price) / up_move if up_move > 0 else None
    healthy_retracement_ratio = retracement_ratio is not None and 0.3 <= retracement_ratio <= 0.6
    healthy_pullback = 0.2 <= pullback_pct <= 4.0  # 너무 얕지도(노이즈) 깊지도(추세훼손) 않은 눌림

    # [2026-07-18] 반전봉 — 눌림 저점을 만든 그 봉이 저가에서 밀렸다가 종가는 자기
    # range 상단부(상위 50% 이상)에서 마감했는지 확인. 매도세를 저점에서 매수세가
    # 되받아쳤다는 신호(예: 망치형). 이 봉의 고가가 이후 진입 트리거 기준선이 된다
    # (직전 고점보다 훨씬 낮아서 진입가가 눌림 저점 가까이로 당겨짐).
    reversal_h, reversal_l = asc_h[trough_idx], asc_l[trough_idx]
    reversal_range = reversal_h - reversal_l
    reversal_close = asc_c[trough_idx]
    strong_reversal_candle = (reversal_range > 0 and
                               (reversal_close - reversal_l) / reversal_range >= 0.5)

    cur_close = asc_c[-1]
    cur_vol   = asc_v[-1]
    confirm_break = cur_close > reversal_h                # 반전이 실제로 이어지는지 확인
    volume_surge  = cur_vol >= pullback_vol * 1.5          # 확인 시 거래량 급증
    # [2026-07-18] 반전봉이 확인돌파(마지막 봉) 기준 너무 옛날이면 뒤늦은 진입 —
    # 최근 PULLBACK_CONFIRM_WINDOW봉 이내에서 나온 저점만 인정.
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
    """
    [2026-07-18 신규] 상위 타임프레임(5분봉) 모멘텀 확인 — 1분봉 신호가 좋아도
    종목 자체의 중기 흐름이 하락/횡보 중이면 신뢰도가 떨어진다는 원칙(지수방향필터와
    같은 취지를 종목 개별 흐름에도 적용). 별도 API 호출 없이 이미 가져온 1분봉
    종가를 5개씩 묶어 5분봉 종가로 근사 집계(벽시계 5분 경계 정렬은 안 함 — 모멘텀
    참고용이라 근사치로 충분).
    closes: 1분봉 종가, 최신이 index 0.
    반환: (ok, info_str)
    """
    need = (n_candles + 1) * 5
    if len(closes) < need:
        return False, "5분봉 데이터 부족 — 모멘텀 확인 불가"
    closes5 = [closes[i * 5] for i in range(n_candles + 1)]  # 각 5분 구간의 최신 종가, index0=현재
    momentum_up = closes5[0] > closes5[n_candles]  # n_candles*5분 전 대비 상승
    holding_up  = closes5[0] > closes5[1]          # 직전 5분봉 대비도 꺾이지 않음
    ok = momentum_up and holding_up
    info = (f"5분봉모멘텀 {n_candles*5}분전대비{'상승' if momentum_up else '하락'}"
            f"({'✓' if momentum_up else '✗'}) 직전5분봉대비{'상승' if holding_up else '하락'}"
            f"({'✓' if holding_up else '✗'})")
    return ok, info


def check_1min_entry(token, code, name, market="J"):
    """
    1분봉 기준 진입 최종 확인
      ① 스토캐스틱RSI: K > D AND K > 20
      ② [2026-07-18 재설계] 눌림목 지지확인(check_pullback_support_hold) — 저점에서
         반전봉+확인돌파+거래량급증을 트리거로 사용(기존 "직전고점 재돌파" 폐기)
    손절가 = 눌림 저점 기준(구조적 손절) — 있으면 ATR×1.5보다 우선, 없으면 ATR 폴백
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
    # [2026-07-13] VI(변동성완화장치) 필터 — 002880(07/13) 사례: 급등 캔들 직후
    # 2분간 거래정지(거래량0)된 구간을 "거래량 줄어든 눌림목"으로 오인해 진입,
    # VI 해제 직후 급락으로 이어짐(-3.71%, 1분만에 손절). 실시간 VI상태 + 최근
    # 거래량0 캔들(VI로 얼어붙었던 흔적) 둘 다 확인해 그런 종목은 진입 제외.
    quote = get_current_price(token, code, market)
    if quote['vi_cls_code'] != 'N' or quote['ovtm_vi_cls_code'] != 'N':
        return False, 0, 0, "VI 발동 중 — 진입 제외"
    if any(v == 0 for v in volumes[:5]):
        return False, 0, 0, "최근 거래정지(VI 추정) 캔들 포함 — 진입 제외"
    # ① 스토캐스틱 RSI
    stoch_k, stoch_d = calc_stoch_rsi(closes)
    if stoch_k is None:
        return False, 0, 0, "스토캐스틱RSI 계산 불가"
    stoch_ok = (stoch_k > stoch_d and stoch_k > 20)
    # ② [2026-07-18] 눌림목 지지확인 (기존 "직전고점 재돌파" 대체)
    pullback_ok, pullback_info, support_price = check_pullback_support_hold(closes, highs, lows, volumes)
    # ③ 손절가 — [2026-07-18] 지지가 확인됐으면(눌림 저점) 그 바로 아래를 구조적
    # 손절선으로 우선 사용 — "이 저점이 깨지면 지지확인 시나리오 자체가 무효"라는
    # 원칙과 일치. 지지가 없으면(pullback_ok=False로 어차피 진입 안 하지만, info 표시용
    # 계산은 계속 진행) 기존 ATR×1.5로 폴백.
    atr_1m = calc_atr(highs, lows, closes, period=14)
    if support_price:
        stop_price = support_price * 0.999
    elif atr_1m:
        stop_price = price - atr_1m * 1.5
    else:
        stop_price = None
    if stop_price is not None:
        stop_price = min(stop_price, price * 0.985)  # 최소 -1.5% 보호
        # [2026-07-13] 실거래 로그 확인 결과 손절에 상한이 없어 -2.79%,
        # -2.89%, -3.71% 손실이 실제로 발생함 — 최대 -2.5%로 손실 상한 유지
        stop_price = max(stop_price, price * 0.975)
    else:
        stop_price = price * 0.98  # ATR 계산 불가 시 폴백
    # [2026-07-18] 목표가 = 진입가 + 리스크(진입가-손절가) × TARGET_R_MULTIPLE.
    # 손절이 구조적(눌림 저점) 기준이라 트레이드마다 리스크폭이 다른데, 기존엔
    # 목표가가 +4% 고정이라 손익비가 트레이드마다 들쭉날쭉했음 — 리스크에 비례하게
    # 바꿔 손익비를 일정하게(기본 2.5:1) 유지.
    risk = price - stop_price
    target_price = price + risk * TARGET_R_MULTIPLE
    atr_str = f"{atr_1m:.0f}" if atr_1m else "N/A"
    s_tag = "✓" if stoch_ok else "✗"
    # ④ [2026-07-18 신규] 5분봉 상승 모멘텀 확인
    momentum_ok, momentum_info = check_5min_momentum(closes)
    info = (f"StochRSI K={stoch_k:.1f}/D={stoch_d:.1f}({s_tag}) "
            f"{pullback_info} {momentum_info} "
            f"ATR={atr_str} 손절={stop_price:,.0f} 목표={target_price:,.0f}")
    print(f"  [1분봉] {name}: {info}")
    if stoch_ok and pullback_ok and momentum_ok:
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

    # [2026-07-06 도입, 2026-07-16 비율 조정] +2% 도달 시 부분익절 + 나머지 손절선을 본전+0.4%로 상향
    # (기존엔 손절선만 올리고 팔지는 않았음 — 사용자 요청으로 일부는 여기서 확정 실현)
    # [2026-07-16] 비율을 50%→PARTIAL_TP_FRACTION(30%)로 축소 — 이긴 거래에 더 많은 물량을
    # 남겨 손절(전량 청산)과의 투입금 비대칭을 줄이기 위함(위 상수 정의부 주석 참고).
    # 상한가로 이미 전량 익절이 확정된 경우엔 건너뜀(중복 매도 방지)
    if not sell and not pos.get('trailing_activated') and pnl >= 2.0:
        pos['trailing_activated'] = True
        pos['stop_price'] = max(stop_price, avg_price * 1.004)  # 본전+0.4%
        stop_price = pos['stop_price']
        partial_qty = max(1, round(qty * PARTIAL_TP_FRACTION)) if qty > 1 else 0
        if partial_qty >= 1:
            ok, order_result = place_order(kis_token, code, partial_qty, "sell")
            if not ok:
                err_msg = f"⚠️ +2% 부분익절 주문 실패\n{name} {partial_qty}주\n{order_result.get('msg1', '')}"
                print(err_msg)
                send_kakao(kakao_token, err_msg)
                save_dashboard(dash)
                return
            time.sleep(1)
            _, new_cash = get_balance(kis_token)
            # [2026-07-09] 매도 자체는 이미 성공했으니 잔고 재조회 실패해도 되돌리지 않음.
            # 다만 실패 시(None) 0원으로 덮어쓰지 말고 이전 값을 그대로 유지.
            balance_known = new_cash is not None
            balance_for_dash = new_cash if balance_known else dash.get('current_balance', 0)
            if not balance_known:
                print("  [경고] 부분익절 후 잔고 재조회 실패 — 이전 잔고 유지")
            # [2026-07-17] 부분익절도 거래기록으로 남긴다 — 기존엔 아예 기록이 안 남아
            # 승률/거래내역에서 이 수익실현이 통째로 안 보였음(사용자 지적으로 발견).
            partial_pnl_amt = int((cur_price - avg_price) * partial_qty)
            log_partial_sell(dash, name, partial_qty, avg_price, cur_price,
                              pnl, partial_pnl_amt, f"+2% 부분익절 ({pnl:+.2f}%)", balance_for_dash)
            print(f"  [+2%] {PARTIAL_TP_FRACTION*100:.0f}% 부분익절 ({partial_qty}주) → 나머지 손절선: {stop_price:,.0f}")
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
                    balance_known = new_cash is not None
                    balance_for_dash = new_cash if balance_known else dash.get('current_balance', 0)
                    if not balance_known:
                        print("  [경고] 2시간 부분청산 후 잔고 재조회 실패 — 이전 잔고 유지")
                    # [2026-07-17] 이 부분청산도 거래기록으로 남긴다(위 +2% 부분익절과 동일 이유)
                    partial_pnl_amt = int((cur_price - avg_price) * sell_qty)
                    log_partial_sell(dash, name, sell_qty, avg_price, cur_price,
                                      pnl, partial_pnl_amt, f"2시간 부분청산 ({pnl:+.2f}%)", balance_for_dash)
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
        # [2026-07-09] 매도 직후 잔고 재조회가 실패(None)하면 0원으로 기록/발송하지 않고
        # 직전까지 알던 잔고를 그대로 유지 — 매도 자체(place_order)는 이미 성공했으므로
        # 여기서 실패해도 매도를 되돌리거나 재시도하지 않는다.
        balance_known = new_cash is not None
        balance_for_dash = new_cash if balance_known else dash.get('current_balance', 0)
        log_sell(dash, name, qty, avg_price, cur_price,
                 pnl, pnl_amt, reason, balance_for_dash)
        guard['consecutive_losses'] = guard.get('consecutive_losses', 0) + 1 if pnl < 0 else 0
        save_dashboard(dash)
        balance_line = (f"💰 잔고: {balance_for_dash:,.0f}원" if balance_known
                         else "💰 잔고: 확인 실패 (이전 값 유지, 다음 사이클에 갱신)")
        msg = (f"📤 매도\n{name} {qty}주\n"
               f"사유: {reason}\n"
               f"손익: {pnl:+.2f}% ({pnl_amt:+,}원)\n"
               f"{balance_line}")
        send_kakao(kakao_token, msg)
    else:
        save_dashboard(dash)


# [2026-07-18] GitHub Actions main()과 오라클 클라우드 상시감시(watcher.py) 양쪽에서
# 동일하게 호출하기 위해 "② 신규 진입 스캔" 블록을 함수로 분리 — 두 곳이 서로 다른
# 로직으로 갈라지며 생기는 버그 위험을 없앤다. 호출 전 daily guard/stray 종목 체크까지
# 이 함수 안에서 전부 처리하고, 각 분기가 끝나면 항상 save_dashboard(dash)를 호출한다.
def attempt_entry_scan(kis_token, kakao_token, dash, guard, holdings, cash):
    # [2026-07-16] 계좌에 봇이 모르는 종목이 남아있으면 신규 매수를 하지 않는다 —
    # 003280 부분매도 뒤 포지션을 오판해 279570을 추가 매수하며 한때 두 종목을
    # 동시보유했던 사고(실손실 -16,770원)의 재발 방지용 최종 안전장치.
    stray = [h for h in holdings if int(h.get('hldg_qty', 0)) > 0]
    if stray:
        names = ', '.join(f"{h.get('pdno')}({h.get('hldg_qty')}주)" for h in stray)
        print(f"[경고] 봇이 모르는 보유종목 존재 — 신규매수 보류: {names}")
        send_kakao(kakao_token, f"⚠️ 계좌에 정체불명 보유종목 있음: {names}\n신규 매수를 보류합니다 — 확인 필요")
        save_dashboard(dash)
        return
    if guard.get('consecutive_losses', 0) >= DAILY_LOSS_LIMIT:
        if not guard.get('notified'):
            guard['notified'] = True
            send_kakao(kakao_token,
                       f"🛑 오늘 연속 손절 {guard['consecutive_losses']}회 — 신규 진입을 중단합니다 (내일 재개)")
        save_dashboard(dash)
        print(f"[일일 가드] 연속 손절 {guard['consecutive_losses']}회 — 신규 진입 중단")
        return
    print("포지션 없음 → [1단계] 일봉 신호 스캔")
    candidates = scan_signals(kis_token)
    if not candidates:
        save_dashboard(dash)
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
        save_dashboard(dash)
        print("1분봉 타이밍 조건 미충족 — 다음 스캔 대기")
        return
    # ③ 매수 ──────────────────────────────────────────────
    price = chosen['price']
    qty   = int(min(cash, MAX_BET) / price)
    if qty < 1:
        save_dashboard(dash)
        print(f"매수 수량 부족 (가격:{price:,}원)")
        return
    ok, order_result = place_order(kis_token, chosen['code'], qty, "buy")
    if not ok:
        err_msg = f"⚠️ 매수 주문 실패\n{chosen['name']} {qty}주\n{order_result.get('msg1', '')}"
        print(err_msg)
        send_kakao(kakao_token, err_msg)
        save_dashboard(dash)
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
    # [2026-07-17] 요일(주말)만 체크해서는 법정공휴일 등 평일 휴장일을 못 걸러냄 —
    # 그런 날에도 KIS API가 오류 없이 마지막 실제 거래일 데이터를 그대로 돌려주는 바람에
    # "오늘 신호"로 오인해 매수를 시도한 사고가 있었음. 휴장일이 확인되면 전체 사이클을
    # 건너뛴다(판단 불가 시엔 기존처럼 진행 — API 장애로 봇이 통째로 멈추는 것을 방지).
    trading_today = market_calendar.is_trading_day(kis_token, now.strftime('%Y%m%d'))
    if trading_today is False:
        print(f"[휴장일] {now.strftime('%Y-%m-%d')}은 증시 휴장일 — 종료")
        return
    kakao_token = get_kakao_token()
    dash = load_dashboard()
    guard = check_daily_guard(dash, now)
    try:
        holdings, cash = get_balance(kis_token)
        if cash is None:
            # [2026-07-09] 잔고 조회 실패 시 holdings도 신뢰 불가([]로 비어있을 수 있음).
            # 그대로 진행하면 실제 보유 포지션을 "계좌에 없음"으로 오판해 상태를 지워버릴
            # 위험이 있어, 이번 사이클은 아무 것도 건드리지 않고 다음 사이클에 재시도한다.
            print("[경고] 잔고 조회 실패 — 이번 사이클 건너뜀")
            # [2026-07-16] 003280 손절가 이탈 후 한국투자증권 서버 접속장애(타임아웃)로
            # 여러 사이클 연속 잔고조회 실패 → 이 사이클 자체가 조용히 스킵만 되고
            # 아무 알림도 없어서, watcher.py도 죽어있던 그날 24분간 아무도 포지션을
            # 못 보다가 사용자가 직접 차트 보고서야 눈치챈 사고 발생. 보유 포지션이
            # 있는데 잔고조회가 실패하면(=손절 체크 자체를 못 하는 상황) 반드시
            # 즉시 알린다.
            if dash.get('position'):
                send_kakao(kakao_token,
                           f"⚠️ 잔고조회 실패로 {dash['position'].get('name', dash['position'].get('code'))} "
                           f"손절/익절 확인을 못 했습니다 — 직접 확인 필요")
            return
        # [2026-07-09] 매매가 없는 사이클에도 매 사이클 실시간 잔고로 갱신 — 종전엔
        # 매수/매도가 실제로 일어날 때만 대시보드 잔고가 바뀌어서, 사용자가 계좌에서
        # 직접 입출금해도 다음 매매 전까진 대시보드에 반영이 안 됐음.
        dash['current_balance'] = int(cash)
        # ① 보유 포지션 관리 ──────────────────────────────────
        # [2026-07-05] 계좌 보유종목 중 "봇이 직접 산 종목(dash['position']['code'])"만
        # 내 포지션으로 취급. 사용자가 계좌에 다른 종목을 들고 있어도 그건 건드리지 않는다.
        dash_position = dash.get('position')
        bot_code = dash_position['code'] if dash_position else None
        matched = [h for h in holdings
                   if bot_code and h.get('pdno') == bot_code and int(h.get('hldg_qty', 0)) > 0]
        if not matched and dash_position:
            # [2026-07-16] 부분익절 직후 조회 시차로 실제로는 남아있는 잔고가 일시적으로
            # 0으로 보인 사례(003280, 2회) 발견 — 279570을 중복 매수해 한때 두 종목을
            # 동시보유하고 그중 하나가 봇 관리 밖에서 방치되는 사고로 이어졌음. 바로
            # 지우지 말고 몇 초 뒤 한 번 더 조회해 진짜로 없는지 재확인한다.
            time.sleep(2)
            holdings_recheck, _ = get_balance(kis_token)
            matched_recheck = [h for h in (holdings_recheck or [])
                               if h.get('pdno') == bot_code and int(h.get('hldg_qty', 0)) > 0]
            if matched_recheck:
                print(f"[정보] {bot_code} 최초 조회는 0이었으나 재조회 결과 "
                      f"{matched_recheck[0]['hldg_qty']}주 보유 확인 — 포지션 유지")
                holdings = holdings_recheck
                matched = matched_recheck
            else:
                # 대시보드엔 포지션이 남아있는데 실제 계좌엔 없음(수동 매도, 또는 다른
                # 프로세스가 이미 팔았는데 git 동기화만 실패한 경우) — 지우기 전에
                # 오늘 체결내역에서 매도 기록을 복구 시도
                print(f"[경고] 대시보드 포지션({bot_code})이 실제 계좌에 없음(재조회로 재확인) — 매도 체결내역 복구 시도")
                recovered = recover_missing_sells(kis_token, dash, dash_position)
                if recovered:
                    print("  → 체결내역에서 매도 기록 복구 완료")
                else:
                    print("  → 체결내역 복구 실패, 기록 없이 상태만 초기화")
                    send_kakao(kakao_token,
                                f"⚠️ {dash_position.get('name', bot_code)} 매도 기록 유실 — 체결내역 직접 확인 필요")
                dash['position'] = None
                save_dashboard(dash)
                return
        active = matched
        if active:
            if watcher_is_alive():
                print("[백업 대기] 로컬 실시간감시(watcher.py)가 살아있음 — "
                      "이 사이클은 포지션 관리를 건너뜀 (중복매도 방지, 잔고만 갱신)")
                save_dashboard(dash)
                return
            manage_position(kis_token, kakao_token, dash, guard, now, active[0], force_sell_at)
            return
        # ② 신규 진입 스캔 (시간대별 진입 가능 여부는 scan_signals 내부에서 판정)
        # [2026-07-18] 오라클 클라우드 상시감시(watcher.py)가 살아있으면 매수 스캔도
        # 그쪽에 양보한다 — 포지션 관리(위 ①)에 이미 있던 watcher_is_alive() 가드와
        # 동일한 원칙. watcher.py가 이제 포지션 관리뿐 아니라 매수 스캔까지 훨씬 빠른
        # 주기(20초)로 돌기 때문에, 상시감시자가 살아있는 한 GitHub Actions가 5분 주기로
        # 끼어들어 중복 스캔·중복 매수를 시도할 이유가 없다. 상시감시자가 죽었을 때만
        # (하트비트 90초 초과) 이 5분 cron이 백업으로 매수 스캔을 대신한다.
        if watcher_is_alive():
            print("[백업 대기] 상시감시(watcher.py)가 살아있음 — 매수 스캔도 그쪽에 양보")
            save_dashboard(dash)
            return
        attempt_entry_scan(kis_token, kakao_token, dash, guard, holdings, cash)
    except Exception as e:
        err_msg = f"⚠️ 자동매매 오류\n{str(e)[:150]}"
        print(err_msg)
        try:
            send_kakao(kakao_token, err_msg)
        except Exception:
            pass
if __name__ == '__main__':
    main()

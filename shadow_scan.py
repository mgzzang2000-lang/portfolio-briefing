#!/usr/bin/env python3
"""
전략 성격 비교용 섀도우 스캐너 — 실제 주문은 절대 내지 않고, 대안 조건식으로
포착됐을 후보만 기록한다. 목적은 "지금 실거래 전략(눌림목 재돌파)을 파라미터만
바꿔볼까 vs 아예 다른 성격이 나을까"를, 실제 자금을 더 걸지 않고 판단할 데이터를
쌓는 것.

[섀도우 A] "첫 급등 돌파"(2026-07-31 신설, 2026-08-12 유일 생존 섀도우로 승격) —
   눌림목 없이, 최근 10봉 이상 좁게 횡보(베이스)하다 그 상단을 거래량 동반해서
   (베이스 평균 대비 1.5배 이상) 처음 돌파하는 순간을 포착. 09:00~11:00 시간대만.
   [탐색 경위] 실거래 봇이 쓰는 "눌림목 재상승" 패턴을 minute_data 18일치
   (train 13일/test 5일 분할)로 그리디 다중조건 탐색했더니, train에서는 조건을
   추가할수록 승률·기대값이 계속 좋아졌지만(최종 55%) 그 규칙을 test에 그대로
   적용하면 1단계부터 이미 베이스라인보다 나빠짐 — 전형적 과최적화. 그래서 패턴
   정의 자체를 이 "첫 급등 돌파"로 바꾸고, 조건도 그리디로 쌓지 말고 "각 후보
   조건 단독으로 train/test 둘 다에서 베이스라인 대비 개선되는지"만 확인하는
   보수적 방식으로 재탐색. 거래량비율(≥1.5배)과 시간대(09:00~11:00) 둘 다 단독
   으로 이 기준을 통과했고, 둘을 조합하면 train +0.115R(n=65, 승률46.2%) / test
   +0.315R(n=27, 승률51.9%) — 이 세션에서 시도한 유일한 "train/test 둘 다 베이스
   라인보다 개선"된 조합.
   [2026-08-12 승격 경위] 옛 섀도우A(시가돌파형)/B(FVG+유동성스윕)는 8/12 기준
   각각 25/22거래일 누적 결과 평균손익 -0.10%/-0.20%(승률 32%/23%)로 실거래
   눌림목봇(같은 기간 승률 40%)보다도 못한 성적이 확인돼 사용자 판단으로 폐기.
   이 섀도우C만 평균 +0.29%(승률 38%, 8거래일)로 유일하게 플러스라 A 자리로
   승격했지만, **표본 8건은 여전히 작아 실거래 승격 전 최소 몇 주는 계속 관찰
   필요**(위 탐색 당시 표본 크기 우려가 아직 해소되지 않음).
   [2026-08-12 보완] 승격 직후 코드 재점검에서 발견한 4개 허점을 같은 날 보완:
   ①5분 주기 스캔이 그 사이 지나간 봉을 놓치던 구조적 커버리지 공백(RECHECK_BARS
   도입) ②VI(변동성완화장치) 필터 부재(실거래봇 7/13 사고와 동일 패턴인데 무방비
   였음, get_current_price가 이미 갖고 있던 vi_cls_code 활용) ③시가대비 상한 없음
   (실거래봇의 MAX_EXTENSION_FROM_OPEN_PCT와 동일 원칙 도입) ④손절/목표가가
   종목 구조와 무관한 고정값(-1.5%/+4%)이었던 것을 베이스 저점 기반 손절+R배수
   목표가(TARGET_R_MULTIPLE=2.5, 실거래봇과 동일)로 교체. 후보 여럿일 때 스캔
   순서가 아닌 돌파강도(거래량비율) 순으로 정렬하도록도 변경.

[섀도우 B] "낙폭과대 반등"(2026-08-12 신설) — A와 정반대 성격의 평균회귀형.
   최근 15봉 고점 대비 2% 이상 빠진 뒤, 그 구간에 RSI(14)가 30 이하(과매도)를
   찍었다가 최저치 대비 3 이상 반등하고, 직전 3봉 대비 거래량 1.2배 이상 실린
   양봉으로(신저가 갱신 없이) 반전을 확인하는 시점을 포착.
   [신설 배경] 지금까지 나온 조건식(실거래 눌림목, 폐기된 시가돌파형/FVG,
   섀도우A 첫급등돌파)이 전부 "오르는 걸 사는" 추세추종 성격이라 성격이 다른
   비교군이 전혀 없었음(사용자 지적). 프로그램매매(수급) 기반도 검토했으나
   섀도우E 데이터 19일치를 원시증분/10분누적 두 방식으로 분석한 결과 상관계수
   r=0.03~0.04 수준으로 노이즈에 가까워 조건식화를 보류하고, 대신 이 평균회귀
   패턴을 채택. **아직 백테스트/실전 이력이 전혀 없는 완전 신규 가설** — 파라미터는
   RSI 문헌상 표준값(14, 과매도 30) 위주로 보수적으로 잡았고, 섀도우A에서 나중에
   발견했던 허점(5분 주기 단일봉만 검사, VI 무방비)은 처음부터 반영해뒀음.

[섀도우 C] "눌림목 분할매수"(2026-08-12 신설) — A/B와 또 다른 성격: 오늘 극초반
   (09:00~09:05)에 전일종가 대비 3% 이상 강하게 붙은 종목이, 그 고점 대비 -5%
   눌리면 1차(비중 60%), -7%까지 더 눌리면 2차(비중 40%) 분할매수. 단, 그냥
   %만 보고 사는 게 아니라 직전 저점 갱신 없이 거래량 실린 양봉 반전이 확인될
   때만 진입(섀도우B의 반전확인 로직과 동일 원칙 재사용).
   [설계 근거] minute_data 142건(종목x일)을 직접 집계한 결과, 극초반 고점 대비
   -7%까지 도달한 종목의 61%가 당일 종가까지도 그보다 더 나쁘게 마감했고, 최종
   저점은 평균 -14.4%(중앙값 -12.3%)까지 밀렸음 — 즉 "-7% 눌림"은 저가매수
   기회보다 추세이탈 신호일 확률이 더 높음. 그래서 ①진입에 반드시 반전확인
   신호를 요구하고 ②손절을 극초반고점 대비 -12%(위 중앙값 근거)로 구조적으로
   잡고 ③1차 비중을 2차보다 크게 둬서 더 위험한 2차 트랜치의 노출을 제한함.
   목표가(블렌디드 평단 기준 +3.5%)는 [주의]에 적은 것처럼 스캔 단계가 아니라
   shadow_backtest.py에서 계산 — 2차가 그 이후 사이클에야 체결될 수 있어
   스캔 시점엔 최종 평단을 알 수 없기 때문.
   [우선순위] 한 사이클에 여러 종목이 동시에 신호를 내면 ①1차 트랜치를 2차보다
   우선(더 얕게 눌린 쪽이 위 통계상 더 안전) ②반전확인 캔들의 거래량배율이
   큰 순으로 정렬 — 이 순서 자체가 아직 검증 전 가설이라, 실제로 "1순위가
   2순위보다 잘됐는지"는 표본이 쌓인 뒤 확인 필요.
   [주의] 아직 백테스트/실전 이력이 전혀 없는 완전 신규 가설. A/B에서 이미
   확인된 함정(5분 주기 단일봉만 검사 → RECHECK_BARS, VI 무방비)은 처음부터
   반영해뒀지만, 분할매수 자체가 A/B와 다른 새 리스크(추가매수가 손실을 키움)를
   가지므로 특히 보수적으로 관찰 필요.

시가총액 700억원 이상 필터 적용(기존 실거래 봇엔 없던 조건 — 소형주 슬리피지
문제 방지 목적으로 검색식들에 공통으로 있던 조건).

실행: auto-trading.yml의 5분 사이클에 얹혀서 돈다(GH 자체 schedule 트리거가
그날 아예 발화 안 하는 사고를 이미 한 번 겪었기 때문 — collect_intraday_data.py와
동일한 이유). shadow_data/A_YYYYMMDD.json, B_YYYYMMDD.json에 사이클별 스냅샷을
누적 저장. [2026-07-10] collect_intraday_data.py가 이 스캔들을 직접 호출하도록
통합되면서, 후보로 잡힌 종목의 1분봉도 같이 수집되기 시작함(이전엔 후보만
기록되고 그 종목의 실제 가격 흐름은 안 쌓이는 공백이 있었음).

[섀도우 E] 아직 조건식이 아니라 순수 데이터 수집 단계. 사용자가 "프로그램매수
   급증을 급등 초입 신호로 못 쓸까"를 제안했고, 실시간 스냅샷 1회 확인 결과
   대형주(삼성전자)는 프로그램순매수가 크고 방향성이 뚜렷한 반면, 이 봇이 실제
   매매한 중소형주 2종목(069540/005860)은 값이 작고 부호가 계속 뒤집히는 노이즈
   수준이었음 — 다만 표본 1스냅샷이라 결론 내리긴 이름. 임계값을 지금 추측해서
   후보조건으로 만들면 과최적화 위험이 크므로, A와 달리 조건 없이 종목별
   프로그램매매 시계열 원본만 minute_data와 같은 구조로 shadow_data/E_{code}_
   {date}.json에 쌓는다(collect_intraday_data.py 참고). 나중에 표본이 쌓이면
   그때 실제 급등 발생 시각과 대조해서 유의미한 임계값이 있는지 분석 예정.

[2026-08-12, 같은 날 이어서] 사용자가 실거래 조건식(눌림목) 기반으로 "장 극초반
고점 대비 얼마나 밀렸다가 얼마나 회복하는지"를 물어와 minute_data 142건을 집계
(밀림폭/반등폭/재돌파비율을 30분·60분·120분·종가 기준 각각 산출). 이 결과를
근거로 -5%/-7% 분할매수 아이디어를 구체화하고, 동시신호 우선순위까지 설계해
신규 섀도우C("눌림목 분할매수")로 코드화 — 자세한 내용은 위 [섀도우 C] 항목 참고.

[2026-07-07] 신설(섀도우A/B). [2026-07-10] 섀도우C(FVG) 추가. [2026-07-15] 섀도우E
(프로그램매매 데이터 수집) 추가. [2026-07-29] 원래 섀도우A(신고가 기준 눌림목)와
섀도우D(오프닝레인지 브레이크아웃)는 신설 이후 몇 주 내내 후보가 사실상 0건이라
사용자 판단으로 폐기, 살아있던 섀도우B/C를 각각 A/B로 승격(과거 데이터 파일도
동일하게 개명, 옛 A/D 데이터는 삭제). [2026-07-31] 코스닥 종목이 여러 KIS API에서
FID_COND_MRKT_DIV_CODE="Q" 버그로 통째로 조회 실패하던 것 발견·수정(이 파일의
get_volume_rank/get_current_price/get_minute_bars_raw 전부 해당). 같은 날, 실거래
조건식을 검증 없이 새 규칙으로 바꾸는 건 위험하다고 판단해 실거래는 기존 로직
그대로 유지하고, 새로 탐색한 "첫 급등 돌파" 가설만 신규 섀도우C로 신설해 먼저
관찰하기로 함. [2026-08-12] 옛 섀도우A(시가돌파형)/B(FVG+유동성스윕)를 성과 부진
(둘 다 평균손익 마이너스, 실거래 승률에도 못 미침)으로 폐기하고 옛 섀도우C
("첫 급등 돌파")를 A로 승격 — 자세한 수치는 위 [섀도우 A] 항목 참고. 관련
헬퍼(get_daily_ohlcv/get_recent_ticks/approx_execution_strength/
find_fvg_with_sweep)도 옛 A/B 전용이라 함께 삭제. 같은 날, A 보완(위 [섀도우 A]
[2026-08-12 보완] 참고)에 이어 A와 성격이 다른 신규 섀도우B("낙폭과대 반등",
평균회귀) 신설 — 자세한 설계 배경은 위 [섀도우 B] 항목 참고.
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
    # [2026-07-20] 여러 프로세스가 같은 순간에 토큰을 새로 받으려다 KIS "1분당 1회"
    # 제한(EGW00133)에 걸려 실행 전체가 실패하는 사고 반복 확인 — 이 에러만 한정해
    # 65초 대기 후 한 번 더 시도(분 경계를 넘기면 거의 항상 성공).
    for attempt in range(2):
        r = requests.post(f"{BASE_URL}/oauth2/tokenP", json={
            "grant_type": "client_credentials",
            "appkey": KIS_APP_KEY, "appsecret": KIS_APP_SECRET
        }, timeout=10)
        data = r.json()
        if 'access_token' in data:
            break
        if data.get('error_code') == 'EGW00133' and attempt == 0:
            time.sleep(65)
            continue
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


INDEX_CODE = {"J": "0001", "Q": "1001"}  # 코스피종합/코스닥종합 지수코드 (거래량순위 조회용)


def get_volume_rank(token, market="J"):
    # [2026-07-31] auto_trading.py의 2026-07-20 수정과 동일 적용 — 이 API는
    # FID_COND_MRKT_DIV_CODE="J"/FID_COND_SCR_DIV_CODE="20171" 고정만 유효하고,
    # 코스피/코스닥 구분은 FID_INPUT_ISCD(업종코드)로 해야 함(20172는 존재하지 않는
    # 값이라 이전엔 코스닥 조회가 항상 빈 배열이었음 — shadow_scan.py엔 이 수정이
    # 안 옮겨져 있었던 걸 뒤늦게 발견).
    data = kis_get(token, "/uapi/domestic-stock/v1/quotations/volume-rank", {
        "FID_COND_MRKT_DIV_CODE": "J", "FID_COND_SCR_DIV_CODE": "20171",
        "FID_INPUT_ISCD": INDEX_CODE[market], "FID_DIV_CLS_CODE": "0", "FID_BLNG_CLS_CODE": "0",
        "FID_TRGT_CLS_CODE": "111111111", "FID_TRGT_EXLS_CLS_CODE": "000000",
        "FID_INPUT_PRICE_1": "", "FID_INPUT_PRICE_2": "",
        "FID_VOL_CNT": "", "FID_INPUT_DATE_1": ""
    }, "FHPST01710000")
    return [item['mksc_shrn_iscd'] for item in data.get('output', [])[:100]]


def get_current_price(token, code, market="J"):
    # [2026-07-31] inquire-price도 같은 버그 — "J" 고정.
    data = kis_get(token, "/uapi/domestic-stock/v1/quotations/inquire-price", {
        "FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code
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


# ── 1분봉 조회 (섀도우 A용 공용 헬퍼) ─────────────────────────────────
def get_minute_bars_raw(token, code, market="J"):
    """현재 시각 기준 최근 1분봉(최대 30개, 최신이 index 0) 원본 반환.
    [2026-07-31] 같은 버그 계열 — "J" 고정."""
    data = kis_get(token, "/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice", {
        "FID_ETC_CLS_CODE": "",
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": code,
        "FID_INPUT_HOUR_1": datetime.now(KST).strftime('%H%M%S'),
        "FID_PW_DATA_INCU_YN": "Y",
    }, "FHKST03010200")
    return data.get('output2', [])


# ── 섀도우 A: "첫 급등 돌파" — 눌림목 없이, 좁은 베이스를 거래량 동반 돌파 ────────
# [2026-07-31 신설, 2026-08-12 옛 섀도우C에서 A로 승격] 파일 상단 [섀도우 A] 설명
# 참고 — train/test 분리 탐색에서 유일하게 양쪽 다 베이스라인보다 개선된 조합
# (거래량비율≥1.5배 + 09:00~11:00).
BASE_BARS = 10
BREAKOUT_MARGIN_PCT = 0.3   # 베이스 상단 대비 최소 돌파폭
BREAKOUT_VOL_RATIO_MIN = 1.5  # 돌파봉 거래량 / 베이스 평균거래량
SHADOW_A_TIME_START = (9, 0)
SHADOW_A_TIME_END = (11, 0)
# [2026-08-12] 성과 점검 후 개선 4건 추가:
# 1) RECHECK_BARS — 이 스캔은 5분 주기(GH Actions)로만 도는데 기존엔 "가장 최근
#    1분봉"만 검사해서 그 사이 지나간 나머지 4개 봉의 돌파를 구조적으로 놓치고
#    있었음. 최근 RECHECK_BARS개 봉을 각각 자기 직전 BASE_BARS개 대비 돌파인지
#    확인해 그중 가장 이른 시점을 잡는다(5분 주기+스케줄 지연 여유 포함).
RECHECK_BARS = 6
# 2) VI(변동성완화장치) 필터 — 실거래봇이 7/13에 겪은 "VI로 얼어붙었다 풀리는
#    순간의 가짜 급등"에 진입하는 사고와 동일 패턴에 이 섀도우도 무방비였음.
#    get_current_price가 이미 vi_cls_code를 받아오고 있었는데 그동안 안 쓰고 있었음.
# 3) MAX_EXTENSION_FROM_OPEN_PCT — 실거래봇이 "이미 많이 오른 상태에서 진입"
#    문제로 도입한 것과 동일한 시가대비 상한(auto_trading.py 참고). 10봉 베이스만
#    보면 하루 전체로는 이미 많이 오른 상태를 놓칠 수 있어서 추가.
MAX_EXTENSION_FROM_OPEN_PCT = 5.0
# 4) 손절/목표가 — 기존엔 스캔 단계에서 손절/목표가를 아예 계산 안 해서
#    shadow_backtest.py가 이 종목의 구조와 무관하게 고정값(-1.5%/+4%)을 적용하고
#    있었음. 베이스 저점을 손절 기준으로, 리스크의 R배수를 목표가로 계산해
#    실거래봇과 동일한 손익비 산정 방식을 적용(auto_trading.py TARGET_R_MULTIPLE
#    과 동일값 사용).
TARGET_R_MULTIPLE = 2.5


def _is_vi_frozen(bars):
    """거래량0 캔들이 섞여있으면 VI로 얼어붙었던 구간으로 간주(실거래봇 check_1min_entry와 동일 원칙)."""
    return any(int(b.get('cntg_vol', 0) or 0) == 0 for b in bars)


def scan_shadow_a(token, stocks, kospi_set):
    now = datetime.now(KST)
    if not (SHADOW_A_TIME_START <= (now.hour, now.minute) < SHADOW_A_TIME_END):
        return []
    candidates = []
    for code in stocks:
        try:
            market = "J" if code in kospi_set else "Q"
            cur = get_current_price(token, code, market)
            if cur['price'] == 0 or cur['open'] == 0 or cur['market_cap'] < MARKET_CAP_MIN:
                continue
            if any(kw in cur['name'] for kw in DERIVATIVE_ETF_KEYWORDS):
                continue
            if is_derivative_etf(token, code, cur['bstp_name'], cur['mrkt_name']):
                continue
            if cur['vi_cls_code'] != 'N' or cur['ovtm_vi_cls_code'] != 'N':
                continue  # 지금 이 순간 VI 발동 중
            raw = get_minute_bars_raw(token, code, market)  # 최신이 index0
            bars_asc = list(reversed(raw))  # 오래된 -> 최신
            # [2026-09-01] BASE_BARS=10이 09:00 장시작 직후엔 그 이전(08:5x대)
            # 프리마켓 구간까지 걸치는데, 이 구간 봉 중 저가/고가/종가가 0으로
            # 찍히는 결측 틱이 섞여 들어올 때가 있음. 이게 base_low로 잡히면
            # 손절가가 0원이 되어 손절이 사실상 무력화됨(2026-08-21 282620 건,
            # 실제 -37%대 급락을 그대로 다 맞은 걸로 섀도우 시뮬레이션에 기록됨).
            # base/현재봉 계산 전에 0원 이상틱을 미리 걸러낸다.
            bars_asc = [b for b in bars_asc
                        if float(b.get('stck_lwpr', 0) or 0) > 0
                        and float(b.get('stck_hgpr', 0) or 0) > 0
                        and float(b.get('stck_prpr', 0) or 0) > 0]
            if len(bars_asc) < BASE_BARS + 2:
                continue

            found = None
            start_i = max(BASE_BARS, len(bars_asc) - RECHECK_BARS)
            for i in range(start_i, len(bars_asc)):
                cur_bar = bars_asc[i]
                base = bars_asc[i - BASE_BARS:i]
                base_high = max(float(b['stck_hgpr']) for b in base)
                base_low = min(float(b['stck_lwpr']) for b in base)
                base_avg_vol = sum(int(b.get('cntg_vol', 0)) for b in base) / len(base)
                cur_close = float(cur_bar['stck_prpr'])
                cur_vol = int(cur_bar.get('cntg_vol', 0))
                if cur_close < base_high * (1 + BREAKOUT_MARGIN_PCT / 100):
                    continue
                if not base_avg_vol or cur_vol < base_avg_vol * BREAKOUT_VOL_RATIO_MIN:
                    continue
                if _is_vi_frozen(base + [cur_bar]):
                    continue
                ext_from_open = (cur_close - cur['open']) / cur['open'] * 100
                if ext_from_open > MAX_EXTENSION_FROM_OPEN_PCT:
                    continue
                found = (cur_close, base_high, base_low, cur_vol / base_avg_vol)
                break  # 재확인 구간 중 가장 이른 돌파봉만 채택
            if found is None:
                continue
            cur_close, base_high, base_low, vol_ratio = found
            stop_price = base_low * 0.999  # 베이스 저점(자연스러운 무효화 기준) 살짝 아래
            risk = cur_close - stop_price
            target_price = cur_close + risk * TARGET_R_MULTIPLE if risk > 0 else None
            candidates.append({
                'code': code, 'name': cur['name'], 'price': cur_close,
                'base_high': base_high, 'stop_price': round(stop_price, 1),
                'target_price': round(target_price, 1) if target_price else None,
                'breakout_vol_ratio': round(vol_ratio, 2),
                'market_cap': cur['market_cap'],
            })
            time.sleep(0.06)
        except Exception as e:
            print(f"  [섀도우A 오류] {code}: {e}")
    # [2026-08-12] 후보가 여럿이면 돌파강도(거래량비율) 큰 순으로 — 기존엔 스캔
    # 순서(코스피→코스닥 거래량순위)상 먼저 걸린 종목이 우연히 채택됐음.
    candidates.sort(key=lambda c: c['breakout_vol_ratio'], reverse=True)
    return candidates


# ── 섀도우 B: "낙폭과대 반등"(평균회귀) ────────────────────────────────
# [2026-08-12 신설] 지금까지 만든 모든 조건식(실거래 눌림목, 폐기된 시가돌파형/
# FVG, 섀도우A 첫급등돌파)이 전부 "추세추종"(오르는 걸 사는) 성격이라, 성격이
# 완전히 다른 비교군이 없었음(사용자 지적). 이건 반대로 "단기간 많이 빠진 뒤
# 매도세가 진정되고 반등 확인되는 지점"을 사는 평균회귀형. 프로그램매매(수급)
# 기반도 검토했으나(섀도우E 데이터 19일치 재분석) 상관계수 r=0.03~0.04 수준으로
# 노이즈에 가까워 조건식화 보류 — 대신 가격/거래량 구조가 명확한 이 패턴을 채택.
# [주의] 아직 백테스트/관찰 이력이 전혀 없는 완전 신규 가설이라, 파라미터는
# RSI 문헌상 표준값(14, 30) 위주로 보수적으로 잡음 — 데이터 쌓이기 전엔 손대지 않음.
# 섀도우A에서 발견됐던 허점(5분 주기 단일봉만 검사, VI 무방비)은 처음부터 반영.
DROP_LOOKBACK_BARS = 15
MIN_DROP_PCT = 2.0        # 최근 15봉 고점 대비 최소 하락폭
RSI_PERIOD = 14
RSI_OVERSOLD = 30         # 이 아래로 한 번은 내려갔어야 "과매도"로 인정
RSI_RECOVER_MARGIN = 3    # 과매도 최저치 대비 이만큼은 반등해야 "돌아서는 중"으로 인정
REVERSAL_VOL_RATIO_MIN = 1.2  # 반전봉 거래량 / 직전 3봉 평균거래량


def calc_rsi_series(closes_asc, period=RSI_PERIOD):
    """closes_asc(오래된->최신) 각 시점의 RSI를 순서대로 반환(앞쪽 period개는 계산 불가라 None)."""
    if len(closes_asc) < period + 1:
        return [None] * len(closes_asc)
    diffs = [closes_asc[i] - closes_asc[i-1] for i in range(1, len(closes_asc))]
    out = [None] * (period)  # 인덱스 0..period-1은 계산 불가 (diffs[0]이 closes_asc[1] 시점)
    for end in range(period, len(diffs) + 1):
        window = diffs[end - period:end]
        gains = [max(d, 0) for d in window]
        losses = [max(-d, 0) for d in window]
        avg_gain, avg_loss = sum(gains) / period, sum(losses) / period
        rsi = 100.0 if avg_loss == 0 else 100 - (100 / (1 + avg_gain / avg_loss))
        out.append(rsi)
    return out  # len(out) == len(closes_asc)


def scan_shadow_b(token, stocks, kospi_set):
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
            if cur['vi_cls_code'] != 'N' or cur['ovtm_vi_cls_code'] != 'N':
                continue
            raw = get_minute_bars_raw(token, code, market)
            # [주의] get_minute_bars_raw는 최대 30개 봉만 준다 — DROP_LOOKBACK_BARS(15)+
            # RSI_PERIOD(14)를 그냥 더하면 31이 되어 조건을 영원히 못 만족하는 실수를
            # 할 뻔했음. 실제 필요한 건 인덱싱 가능한 최소치(둘 중 큰 값)+여유분뿐.
            if len(raw) < max(DROP_LOOKBACK_BARS, RSI_PERIOD) + 2:
                continue
            bars_asc = list(reversed(raw))
            closes_asc = [float(b['stck_prpr']) for b in bars_asc]
            rsi_series = calc_rsi_series(closes_asc)

            found = None
            start_i = max(DROP_LOOKBACK_BARS, len(bars_asc) - RECHECK_BARS)
            for i in range(start_i, len(bars_asc)):
                cur_bar = bars_asc[i]
                if rsi_series[i] is None:
                    continue
                window = bars_asc[i - DROP_LOOKBACK_BARS:i]
                window_high = max(float(b['stck_hgpr']) for b in window)
                cur_close = float(cur_bar['stck_prpr'])
                drop_pct = (window_high - cur_close) / window_high * 100
                if drop_pct < MIN_DROP_PCT:
                    continue  # 아직 "많이 빠졌다"고 볼 만큼 하락하지 않음
                rsi_window = [r for r in rsi_series[i - DROP_LOOKBACK_BARS:i + 1] if r is not None]
                if not rsi_window or min(rsi_window) > RSI_OVERSOLD:
                    continue  # 이 구간에 과매도(RSI<30) 구간 자체가 없었음
                if rsi_series[i] < min(rsi_window) + RSI_RECOVER_MARGIN:
                    continue  # 아직 과매도 최저치에서 반등 전
                prev3 = bars_asc[max(0, i - 3):i]
                if not prev3:
                    continue
                prev3_low = min(float(b['stck_lwpr']) for b in prev3)
                if float(cur_bar['stck_lwpr']) < prev3_low:
                    continue  # 신저가 갱신 중 — 아직 반전 확인 안 됨
                if not (cur_close > float(cur_bar['stck_oprc'])):
                    continue  # 반전봉은 양봉이어야 함
                prev3_avg_vol = sum(int(b.get('cntg_vol', 0)) for b in prev3) / len(prev3)
                cur_vol = int(cur_bar.get('cntg_vol', 0))
                if not prev3_avg_vol or cur_vol < prev3_avg_vol * REVERSAL_VOL_RATIO_MIN:
                    continue  # 반등에 거래가 안 붙음
                if _is_vi_frozen(window + [cur_bar]):
                    continue
                swing_low = min(float(b['stck_lwpr']) for b in window + [cur_bar])
                found = (cur_close, swing_low, round(drop_pct, 2), rsi_series[i])
                break
            if found is None:
                continue
            cur_close, swing_low, drop_pct, rsi_now = found
            stop_price = swing_low * 0.999
            risk = cur_close - stop_price
            target_price = cur_close + risk * TARGET_R_MULTIPLE if risk > 0 else None
            candidates.append({
                'code': code, 'name': cur['name'], 'price': cur_close,
                'drop_pct': drop_pct, 'rsi': round(rsi_now, 1),
                'stop_price': round(stop_price, 1),
                'target_price': round(target_price, 1) if target_price else None,
                'market_cap': cur['market_cap'],
            })
            time.sleep(0.06)
        except Exception as e:
            print(f"  [섀도우B 오류] {code}: {e}")
    candidates.sort(key=lambda c: c['drop_pct'], reverse=True)
    return candidates


# ── 섀도우 C: "눌림목 분할매수" ────────────────────────────────────────
# [2026-08-12 신설] 파일 상단 [섀도우 C] 설명 참고. 극초반(09:00~09:05) 고점을
# 하루 동안 기억해둬야 하는데, get_minute_bars_raw는 "호출 시점 기준 최근 30개
# 봉"만 주므로(과거 조회 불가) 장 시작 직후 캐시해두지 않으면 나중엔 영영 알 수
# 없다 — shadow_data/C_state_{date}.json에 종목별 극초반고점+트랜치 진행상태를
# 사이클마다 누적 저장(auto_trading.py의 daily_filter_cache.json과 동일한 이유).
EARLY_HIGH_WINDOW_END = "090500"   # 극초반 고점 산정 구간(09:00~09:05)
EARLY_HIGH_MIN_GAIN_PCT = 3.0      # 극초반 고점이 전일종가 대비 이만큼은 올라야
                                    # "이미 강하게 붙은 종목"만 추적(사용자 원 질문의 전제)
DIP_TRANCHE1_PCT = -5.0
DIP_TRANCHE2_PCT = -7.0
TRANCHE1_WEIGHT, TRANCHE2_WEIGHT = 0.6, 0.4  # 2차(더 위험한 구간)의 비중을 더 작게
# -12%: minute_data 142건 집계에서 -7% 도달 종목의 최종저점 중앙값이 -12.3%였던
# 것에 근거한 구조적 손절선(단순 고정%가 아니라 "이 이상은 정상 눌림목이 아니라
# 추세이탈"로 보는 경계). 익절폭(BLENDED_TARGET_PCT)은 shadow_backtest.py에서
# 블렌디드 평단이 확정된 뒤 계산하므로 여기서는 정의하지 않음.
STRUCTURAL_STOP_FROM_HIGH_PCT = -12.0


def _load_c_state(today_str):
    return load_json(f"{DATA_DIR}/C_state_{today_str}.json", {})


def _save_c_state(today_str, state):
    save_json(f"{DATA_DIR}/C_state_{today_str}.json", state)


def _update_early_high(state, code, bars_asc, now_hms):
    """오늘 09:00~09:05 구간이 이미 지났고 그 구간 1분봉을 실제로 확보했으면
    그 구간 고가를 캐시. 그 구간을 이미 지나쳤는데 데이터가 없으면(장 시작 한참
    후에야 유니버스에 처음 걸린 종목 — 최근 30봉으로는 09:05 이전을 못 봄)
    unavailable로 표시해 오늘 하루는 더 이상 재시도하지 않는다."""
    entry = state.setdefault(code, {
        'early_high': None, 'unavailable': False,
        'tranche1_done': False, 'tranche2_done': False,
    })
    if entry['early_high'] is not None or entry['unavailable']:
        return entry
    if now_hms < EARLY_HIGH_WINDOW_END:
        return entry  # 아직 극초반 구간이 안 끝남 — 다음 사이클에 재시도
    if not bars_asc or bars_asc[0]['stck_cntg_hour'] > EARLY_HIGH_WINDOW_END:
        entry['unavailable'] = True
        return entry
    early_bars = [b for b in bars_asc if b['stck_cntg_hour'] <= EARLY_HIGH_WINDOW_END]
    if early_bars:
        entry['early_high'] = max(float(b['stck_hgpr']) for b in early_bars)
    return entry


def _reversal_signal(bars_asc, i):
    """i번째 봉이 직전 3봉 저점 대비 신저가 갱신 없이 양봉 전환 + 거래량 증가
    (섀도우B와 동일 원칙 — 순수 %트리거만으로 사는 건 위험하다는 설계 근거 참고).
    조건 충족 시 거래량배율(우선순위 타이브레이커용)을, 아니면 None을 반환."""
    cur_bar = bars_asc[i]
    prev3 = bars_asc[max(0, i - 3):i]
    if not prev3:
        return None
    prev3_low = min(float(b['stck_lwpr']) for b in prev3)
    if float(cur_bar['stck_lwpr']) < prev3_low:
        return None  # 신저가 갱신 중 — 아직 반전 확인 안 됨
    if not (float(cur_bar['stck_prpr']) > float(cur_bar['stck_oprc'])):
        return None  # 반전봉은 양봉이어야 함
    prev3_avg_vol = sum(int(b.get('cntg_vol', 0)) for b in prev3) / len(prev3)
    cur_vol = int(cur_bar.get('cntg_vol', 0))
    if not prev3_avg_vol or cur_vol < prev3_avg_vol * REVERSAL_VOL_RATIO_MIN:
        return None  # 반등에 거래가 안 붙음
    return round(cur_vol / prev3_avg_vol, 2)


def scan_shadow_c(token, stocks, kospi_set, today_str):
    state = _load_c_state(today_str)
    now_hms = datetime.now(KST).strftime('%H%M%S')
    candidates = []
    for code in stocks:
        try:
            market = "J" if code in kospi_set else "Q"
            cur = get_current_price(token, code, market)
            if cur['price'] == 0 or cur['market_cap'] < MARKET_CAP_MIN or not cur['prev_close']:
                continue
            if any(kw in cur['name'] for kw in DERIVATIVE_ETF_KEYWORDS):
                continue
            if is_derivative_etf(token, code, cur['bstp_name'], cur['mrkt_name']):
                continue
            if cur['vi_cls_code'] != 'N' or cur['ovtm_vi_cls_code'] != 'N':
                continue
            raw = get_minute_bars_raw(token, code, market)
            if not raw:
                continue
            bars_asc = list(reversed(raw))

            entry = _update_early_high(state, code, bars_asc, now_hms)
            if entry['early_high'] is None or entry['tranche2_done']:
                continue  # 극초반고점 미확정/불가 또는 오늘 두 트랜치 모두 소진
            gain_pct = (entry['early_high'] - cur['prev_close']) / cur['prev_close'] * 100
            if gain_pct < EARLY_HIGH_MIN_GAIN_PCT:
                continue  # "강하게 붙은 종목"만 추적한다는 전제 미달

            found = None
            start_i = max(0, len(bars_asc) - RECHECK_BARS)
            for i in range(start_i, len(bars_asc)):
                cur_close = float(bars_asc[i]['stck_prpr'])
                drop_pct = (cur_close - entry['early_high']) / entry['early_high'] * 100
                tranche = None
                if not entry['tranche1_done'] and drop_pct <= DIP_TRANCHE1_PCT:
                    tranche = 1
                elif entry['tranche1_done'] and not entry['tranche2_done'] and drop_pct <= DIP_TRANCHE2_PCT:
                    tranche = 2
                if tranche is None:
                    continue
                vol_ratio = _reversal_signal(bars_asc, i)
                if vol_ratio is None:
                    continue
                if _is_vi_frozen(bars_asc[max(0, i - 3):i + 1]):
                    continue
                found = (tranche, cur_close, round(drop_pct, 2), vol_ratio)
                break  # 재확인 구간 중 가장 이른 트리거만 채택
            if found is None:
                continue
            tranche, cur_close, drop_pct, vol_ratio = found
            stop_price = entry['early_high'] * (1 + STRUCTURAL_STOP_FROM_HIGH_PCT / 100)
            candidates.append({
                'code': code, 'name': cur['name'], 'tranche': tranche,
                'price': cur_close, 'early_high': entry['early_high'],
                'drop_pct': drop_pct,
                'weight': TRANCHE1_WEIGHT if tranche == 1 else TRANCHE2_WEIGHT,
                'stop_price': round(stop_price, 1),
                'confirm_vol_ratio': vol_ratio,
                'market_cap': cur['market_cap'],
            })
            if tranche == 1:
                entry['tranche1_done'] = True
            else:
                entry['tranche2_done'] = True
            time.sleep(0.06)
        except Exception as e:
            print(f"  [섀도우C 오류] {code}: {e}")
    _save_c_state(today_str, state)
    # [우선순위] 1차(얕은 눌림, 더 안전) > 2차, 같은 트랜치면 반전확인 거래량배율
    # 큰 순 — 동시신호 시 실제 자금은 1종목만 살 수 있어 사용자와 논의해 정한 규칙.
    candidates.sort(key=lambda c: (c['tranche'], -c['confirm_vol_ratio']))
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

    c_candidates = scan_shadow_c(token, stocks, kospi_set, today_str)
    append_snapshot(f"{DATA_DIR}/C_{today_str}.json", c_candidates)
    print(f"[섀도우C] 후보 {len(c_candidates)}종목: {[c['name'] for c in c_candidates]}")


if __name__ == '__main__':
    main()

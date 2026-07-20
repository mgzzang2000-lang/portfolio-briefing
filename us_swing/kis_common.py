"""
미국주식 스윙/장기 프로젝트 공용 KIS API 헬퍼.
국내주식 봇(auto_trading.py 등)과는 별도 프로젝트라 의도적으로 분리 — 실수로
국내 실거래 코드에 영향 주지 않기 위함. kis_token.json 캐시 파일은 계정이
같으므로 국내 스크립트와 동일 파일을 공유해도 무방(토큰 자체가 계좌 단위).

[2026-07-07] 신설.
"""
import os, json, time
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
BASE_URL = "https://openapi.koreainvestment.com:9443"
KIS_APP_KEY = os.environ["KIS_APP_KEY"]
KIS_APP_SECRET = os.environ["KIS_APP_SECRET"]
TOKEN_FILE = "kis_token.json"

# 종목별로 어느 거래소에 상장돼있는지 모를 때 순서대로 시도 (시세 조회용 코드)
EXCD_CANDIDATES = ["NAS", "NYS", "AMS"]
# 시세조회(EXCD)와 주문/잔고조회(OVRS_EXCG_CD)는 거래소 코드 표기가 다름
QUOTE_TO_TRADE_EXCD = {"NAS": "NASD", "NYS": "NYSE", "AMS": "AMEX"}


def get_kis_token():
    try:
        with open(TOKEN_FILE, "r") as f:
            cached = json.load(f)
        issued_at = datetime.fromisoformat(cached["issued_at"])
        age = (datetime.now(KST) - issued_at).total_seconds()
        if age < 23 * 3600 and cached.get("access_token"):
            return cached["access_token"]
    except Exception:
        pass
    import requests
    # [2026-07-20] 미국주식 스캔·모니터링 등 여러 워크플로우가 같은 순간(미장 개장 등)에
    # 트리거되면서 토큰을 동시에 새로 받으려다 KIS "1분당 1회" 제한(EGW00133)에 걸려
    # 포지션 모니터링(손절체크)이 통째로 실패한 사고 실제 발생(2026-07-20 22:00 KST) —
    # 이 에러만 한정해 65초 대기 후 한 번 더 시도(분 경계를 넘기면 거의 항상 성공).
    for attempt in range(2):
        r = requests.post(f"{BASE_URL}/oauth2/tokenP", json={
            "grant_type": "client_credentials",
            "appkey": KIS_APP_KEY, "appsecret": KIS_APP_SECRET
        }, timeout=10)
        data = r.json()
        if "access_token" in data:
            break
        if data.get("error_code") == "EGW00133" and attempt == 0:
            time.sleep(65)
            continue
        raise Exception(f"KIS 토큰 오류: {data}")
    with open(TOKEN_FILE, "w") as f:
        json.dump({"access_token": data["access_token"],
                   "issued_at": datetime.now(KST).isoformat()}, f)
    return data["access_token"]


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


def get_weekly_ohlcv(token, symbol, excd=None):
    """해외주식 주봉 OHLCV(HHDFS76240000, GUBN=1) — 최신이 index 0, 최대 100주(~2년).
    excd를 모르면 NAS/NYS/AMS 순으로 시도해서 되는 거래소를 함께 반환.
    반환: (dict{closes,highs,lows,volumes}, excd_used) 또는 실패 시 (None, None)
    """
    candidates = [excd] if excd else EXCD_CANDIDATES
    for code in candidates:
        data = kis_get(token, "/uapi/overseas-price/v1/quotations/dailyprice", {
            "AUTH": "", "EXCD": code, "SYMB": symbol,
            "GUBN": "1", "BYMD": "", "MODP": "0"
        }, "HHDFS76240000")
        output = data.get("output2", [])
        if output:
            rows = [x for x in output if x.get("clos")]
            if len(rows) >= 30:
                ohlcv = {
                    "closes":  [float(x["clos"]) for x in rows],
                    "highs":   [float(x.get("high") or x["clos"]) for x in rows],
                    "lows":    [float(x.get("low") or x["clos"]) for x in rows],
                    "volumes": [int(float(x.get("tvol") or 0)) for x in rows],
                }
                return ohlcv, code
        time.sleep(0.1)
    return None, None


def get_quote(token, symbol, excd):
    """실시간에 가까운 현재가(HHDFS00000300) — 주문 직전 최신 가격 확인용."""
    data = kis_get(token, "/uapi/overseas-price/v1/quotations/price", {
        "AUTH": "", "EXCD": excd, "SYMB": symbol
    }, "HHDFS00000300")
    out = data.get("output", {})
    last = out.get("last")
    return float(last) if last else None


def calc_ma(values, period):
    if len(values) < period:
        return None
    return sum(values[:period]) / period


def calc_atr(highs, lows, closes, period=14):
    """최신이 index 0인 배열 기준 ATR(단순평균 방식)."""
    if len(highs) < period + 1:
        return None
    trs = []
    for i in range(period):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i + 1]),
            abs(lows[i] - closes[i + 1]),
        )
        trs.append(tr)
    return sum(trs) / len(trs)


def get_usd_krw_rate():
    """USD/KRW 환율 — 무료 공개 API(frankfurter.app, ECB 기준). 실패 시 보수적 고정값 폴백."""
    import requests
    try:
        r = requests.get("https://api.frankfurter.app/latest",
                          params={"from": "USD", "to": "KRW"}, timeout=10)
        rate = r.json().get("rates", {}).get("KRW")
        if rate:
            return float(rate)
    except Exception:
        pass
    return 1450.0  # 폴백(대략치) — 이 경우 로그로 남겨서 실제 환율과 괴리 확인 필요


def kis_post(token, path, body, tr_id, retries=3):
    import requests
    headers = {
        "authorization": f"Bearer {token}",
        "appkey": KIS_APP_KEY, "appsecret": KIS_APP_SECRET,
        "tr_id": tr_id, "custtype": "P", "content-type": "application/json; charset=utf-8",
    }
    for attempt in range(retries):
        try:
            r = requests.post(f"{BASE_URL}{path}", headers=headers,
                               json=body, timeout=10)
            if not r.text.strip():
                return {}
            return r.json()
        except Exception:
            if attempt < retries - 1:
                time.sleep(2)
    return {}


def place_order(token, cano, acnt_prdt_cd, trade_excd, symbol, qty, limit_price, side):
    """해외주식 주문(TTTT1002U 매수/TTTT1006U 매도) — 지정가만 지원, 시장가는 없음
    (KIS 공식 문서 기준). trade_excd는 'NASD'/'NYSE'/'AMEX' 등 주문용 거래소코드
    (시세조회용 EXCD와 다름 — QUOTE_TO_TRADE_EXCD로 변환).
    반환: (성공여부, 응답 dict)
    """
    tr_id = "TTTT1002U" if side == "buy" else "TTTT1006U"
    body = {
        "CANO": cano, "ACNT_PRDT_CD": acnt_prdt_cd,
        "OVRS_EXCG_CD": trade_excd, "PDNO": symbol,
        "ORD_QTY": str(qty), "OVRS_ORD_UNPR": f"{limit_price:.2f}",
        "CTAC_TLNO": "", "MGCO_APTM_ODNO": "",
        "SLL_TYPE": "" if side == "buy" else "00",
        "ORD_SVR_DVSN_CD": "0", "ORD_DVSN": "00",  # 00: 지정가
    }
    data = kis_post(token, "/uapi/overseas-stock/v1/trading/order", body, tr_id)
    return data.get("rt_cd") == "0", data


def _touch_balance_history(state):
    history = state.setdefault("balance_history", [])
    today = datetime.now(KST).strftime("%m/%d")
    entry = {"date": today, "balance": state["current_balance"]}
    if history and history[-1]["date"] == today:
        history[-1] = entry
    else:
        history.append(entry)
    state["balance_history"] = history[-60:]


def record_balance(state, delta_usd):
    """매수/매도로 변한 현금을 반영하고, 대시보드용 잔고 추이(일별)에 기록.
    KR 봇의 save_dashboard() 잔고추이 로직과 동일한 패턴.

    [2026-07-19] delta_usd를 pending_cash_deltas에도 같이 남긴다 — 해외주식은
    결제(settlement)가 며칠 늦게 반영돼서, 이 직후 사이클의 sync_live_balance()가
    KIS 실계좌 조회값으로 current_balance를 덮어쓸 때 아직 이 거래가 반영 안 된
    "옛날" 현금을 그대로 가져올 수 있음. pending_cash_deltas가 있으면
    sync_live_balance()가 그 옛날 값 위에 이 델타를 다시 얹어서, 실제로 이미
    쓴/받은 돈이 화면에서 사라지거나(매수) 이중으로 잡히지(매도 대금+보유금액)
    않게 한다. 실제 KIS 잔고가 움직여서 결제가 확인되면 자동으로 정리됨(아래
    sync_live_balance 참고)."""
    state["current_balance"] = round(state.get("current_balance", 0) + delta_usd, 2)
    today = datetime.now(KST).strftime("%Y-%m-%d")
    state.setdefault("pending_cash_deltas", []).append({"date": today, "delta": delta_usd})
    _touch_balance_history(state)


def sync_live_balance(token, cano, acnt_prdt_cd, state):
    """[2026-07-09] 매매 여부와 상관없이 매 사이클 실제 계좌 잔고로 갱신.
    종전엔 초기값(또는 직전 값)에 거래 손익만 누적하는 장부 계산이라, 사용자가
    계좌에서 직접 입출금해도 대시보드엔 절대 반영되지 않았음.

    [2026-07-09 수정] 처음엔 output3.tot_asst_amt(총자산, 원화환산)를 썼는데,
    한국투자증권은 국내·해외 계좌가 하나로 연동돼 있어서 이 값이 "국내 원화
    예수금 + 해외 외화평가액"을 합친 숫자였음(사용자가 방금 환전한 $1974.98과
    대시보드에 찍힌 $2294.69가 안 맞는 걸 보고 발견). 국내 예수금이 섞이지 않은
    output2의 통화별 배열에서 USD 행의 frcr_dncl_amt_2(외화예수금액2)를 쓰면
    이미 USD 단위라 환율 계산도 필요 없고, 국내 계좌와도 안 섞임.
    조회 실패 시 기존 값을 그대로 두고 아무 것도 하지 않는다.

    [2026-07-19 수정] 해외주식 결제 지연 때문에 방금 산/판 종목의 대금이
    이 raw 값에 며칠간 반영 안 될 수 있음(UAL 손절 매도 + DOC 매수 직후,
    total_assets가 DOC 매수금액만큼 이중계산돼 손절 손실이 가려지고 누적손익이
    +로 보였던 사고가 계기). raw 값이 지난 사이클과 그대로면(=아직 결제 전)
    미정산 델타(pending_cash_deltas)를 이번에도 얹어서 쓰고, raw 값이
    달라졌으면(=결제가 실제로 반영됨) 그 시점 이전 델타는 전부 정리한다."""
    data = kis_get(token, "/uapi/overseas-stock/v1/trading/inquire-present-balance", {
        "CANO": cano, "ACNT_PRDT_CD": acnt_prdt_cd,
        "WCRC_FRCR_DVSN_CD": "02", "NATN_CD": "840",
        "TR_MKET_CD": "00", "INQR_DVSN_CD": "00",
    }, "CTRP6504R")
    usd_row = next(
        (row for row in (data.get("output2") or []) if row.get("crcy_cd") == "USD"),
        None,
    )
    if not usd_row:
        return
    try:
        usd_balance = float(usd_row.get("frcr_dncl_amt_2", 0) or 0)
    except (TypeError, ValueError):
        return
    if usd_balance <= 0:
        return

    last_raw = state.get("_last_raw_cash")
    if last_raw is not None and abs(usd_balance - last_raw) > 0.01:
        # KIS raw 잔고 자체가 지난 사이클과 달라졌다 = 그 사이 결제가 실제로
        # 반영됐다는 뜻이므로, 그때까지 쌓인 미정산 델타는 이제 raw 안에 이미
        # 녹아있다고 보고 정리(계속 남겨두면 다음부터 반대로 이중차감/이중가산됨).
        state["pending_cash_deltas"] = []
    state["_last_raw_cash"] = usd_balance

    pending_total = sum(d["delta"] for d in state.get("pending_cash_deltas", []))
    state["current_balance"] = round(usd_balance + pending_total, 2)
    _touch_balance_history(state)


def sync_total_assets(state, holdings):
    """[2026-07-14] current_balance는 순수 현금(frcr_dncl_amt_2)이라, 종목을 매수해
    보유하고 있는 동안엔 그 종목 가격이 오르내려도 화면에 전혀 반영이 안 됐음
    (사용자 피드백: "현재 자산이 매수하고 남은 잔고만 보여준다", "계속 일정 금액에
    멈춰있어 재미없다"). 대시보드 표시(총자산/차트)는 현금 + 보유종목 평가금액
    합계를 따로 저장한 total_assets를 쓰고, current_balance는 실제 매매 시
    현금흐름을 정확히 추적해야 하는 내부 장부 값이라 건드리지 않는다."""
    position_value = sum(h["qty"] * h["current_price"] for h in holdings)
    state["total_assets"] = round(state.get("current_balance", 0) + position_value, 2)
    history = state.setdefault("total_assets_history", [])
    today = datetime.now(KST).strftime("%m/%d")
    entry = {"date": today, "balance": state["total_assets"]}
    if history and history[-1]["date"] == today:
        history[-1] = entry
    else:
        history.append(entry)
    state["total_assets_history"] = history[-60:]


def get_holdings(token, cano, acnt_prdt_cd):
    """현재 보유 중인 해외주식 목록(잔고조회, TTTS3012R) — ground truth.
    반환: [{'symbol','qty','avg_price','current_price','pnl_pct'}, ...]
    """
    data = kis_get(token, "/uapi/overseas-stock/v1/trading/inquire-balance", {
        "CANO": cano, "ACNT_PRDT_CD": acnt_prdt_cd,
        "OVRS_EXCG_CD": "NASD", "TR_CRCY_CD": "USD",
        "CTX_AREA_FK200": "", "CTX_AREA_NK200": "",
    }, "TTTS3012R")
    holdings = []
    for row in data.get("output1", []) or []:
        qty = int(float(row.get("ovrs_cblc_qty", 0) or 0))
        if qty <= 0:
            continue
        holdings.append({
            "symbol": row.get("ovrs_pdno", ""),
            "qty": qty,
            "avg_price": float(row.get("pchs_avg_pric", 0) or 0),
            "current_price": float(row.get("now_pric2", 0) or 0),
            "pnl_pct": float(row.get("evlu_pfls_rt", 0) or 0),
        })
    return holdings

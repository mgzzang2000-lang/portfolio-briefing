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

# 종목별로 어느 거래소에 상장돼있는지 모를 때 순서대로 시도
EXCD_CANDIDATES = ["NAS", "NYS", "AMS"]


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
    r = requests.post(f"{BASE_URL}/oauth2/tokenP", json={
        "grant_type": "client_credentials",
        "appkey": KIS_APP_KEY, "appsecret": KIS_APP_SECRET
    }, timeout=10)
    data = r.json()
    if "access_token" not in data:
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
    반환: (closes, volumes, excd_used) 또는 실패 시 (None, None, None)
    """
    candidates = [excd] if excd else EXCD_CANDIDATES
    for code in candidates:
        data = kis_get(token, "/uapi/overseas-price/v1/quotations/dailyprice", {
            "AUTH": "", "EXCD": code, "SYMB": symbol,
            "GUBN": "1", "BYMD": "", "MODP": "0"
        }, "HHDFS76240000")
        output = data.get("output2", [])
        if output:
            closes = [float(x["clos"]) for x in output if x.get("clos")]
            volumes = [int(float(x["tvol"])) for x in output if x.get("tvol")]
            if len(closes) >= 30:
                return closes, volumes, code
        time.sleep(0.1)
    return None, None, None


def calc_ma(values, period):
    if len(values) < period:
        return None
    return sum(values[:period]) / period

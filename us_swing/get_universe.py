"""
S&P500 구성종목 리스트(+ GICS 섹터) 확보/캐싱.
공식 유료 데이터 없이, 공개된 constituents.csv(datasets/s-and-p-500-companies)를
가져와서 로컬에 캐싱 — 구성종목은 1년에 몇 번밖에 안 바뀌므로 7일에 한 번만
갱신하면 충분함(매 실행마다 외부 요청 안 보내도 됨).

[2026-07-07] 신설.
"""
import json, os, csv, io
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
CACHE_FILE = os.path.join(os.path.dirname(__file__), "sp500_universe.json")
CSV_URL = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/master/data/constituents.csv"
CACHE_MAX_AGE_DAYS = 7


def _fetch_from_source():
    import requests
    r = requests.get(CSV_URL, timeout=15)
    r.raise_for_status()
    # [2026-07-07] 회사명/본사주소 필드에 콤마가 들어간 행(따옴표로 감싸짐)이 있어
    # 단순 split(",")로는 컬럼이 밀리는 버그가 있었음 — csv 모듈로 안전하게 파싱
    reader = csv.DictReader(io.StringIO(r.text))
    universe = []
    for row in reader:
        symbol = row.get("Symbol", "").strip()
        sector = row.get("GICS Sector", "").strip()
        if symbol:
            universe.append({"symbol": symbol, "sector": sector})
    return universe


def get_universe(force_refresh=False):
    if not force_refresh:
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                cached = json.load(f)
            age_days = (datetime.now(KST) - datetime.fromisoformat(cached["fetched_at"])).total_seconds() / 86400
            if age_days < CACHE_MAX_AGE_DAYS and cached.get("universe"):
                return cached["universe"]
        except Exception:
            pass
    universe = _fetch_from_source()
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump({"fetched_at": datetime.now(KST).isoformat(), "universe": universe}, f, ensure_ascii=False, indent=2)
    return universe


if __name__ == "__main__":
    u = get_universe(force_refresh=True)
    print(f"S&P500 {len(u)}종목 확보")
    sectors = sorted(set(x["sector"] for x in u))
    print(f"섹터 {len(sectors)}개: {sectors}")

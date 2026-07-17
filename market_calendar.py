# [2026-07-17] 증시 휴장일(제헌절 등 법정공휴일 포함)에도 요일만 체크하던 기존 코드가
# 통과시켜 매수 시도가 여러 차례 발생한 사고를 계기로 추가.
# 원인: KIS 1분봉/현재가 API는 휴장일에도 오류 없이 "마지막 실제 거래일" 데이터를
# 그대로 반환하는데(가격·거래량 모두 그날 마감값으로 고정), 기존 코드는 이 데이터의
# 실제 날짜(stck_bsop_date)를 한 번도 확인하지 않고 "오늘 데이터"로 오인해 필터를 통과시켰음.
# 이 모듈은 KIS 공식 국내휴장일조회 API(CTCA0903R)로 오늘이 실제 개장일인지 판별한다.
import os
import requests

BASE_URL = "https://openapi.koreainvestment.com:9443"
KIS_APP_KEY = os.environ['KIS_APP_KEY']
KIS_APP_SECRET = os.environ['KIS_APP_SECRET']


def is_trading_day(token, date_str):
    """date_str: 'YYYYMMDD'.
    반환: True(개장일)/False(휴장일 확인됨)/None(API 호출 실패 등으로 판단 불가).
    호출측은 None을 "확실히 개장일"로 취급하지 말고 별도로 처리할 것."""
    headers = {
        "authorization": f"Bearer {token}",
        "appkey": KIS_APP_KEY, "appsecret": KIS_APP_SECRET,
        "tr_id": "CTCA0903R", "custtype": "P"
    }
    try:
        r = requests.get(
            f"{BASE_URL}/uapi/domestic-stock/v1/quotations/chk-holiday",
            headers=headers,
            params={"BASS_DT": date_str, "CTX_AREA_NK": "", "CTX_AREA_FK": ""},
            timeout=10,
        )
        if r.status_code != 200:
            print(f"[휴장일조회] status={r.status_code} — 판단 불가")
            return None
        output = r.json().get('output', [])
        row = next((o for o in output if o.get('bass_dt') == date_str), None)
        if row is None:
            print(f"[휴장일조회] {date_str} 응답에 없음 — 판단 불가")
            return None
        return row.get('opnd_yn') == 'Y'
    except Exception as e:
        print(f"[휴장일조회] 오류: {e} — 판단 불가")
        return None


def is_fresh_bar(bar, date_str):
    """1분봉/체결 데이터 1건의 stck_bsop_date(또는 동일 의미 필드)가 오늘 날짜와
    일치하는지 확인 — 휴장일조회 API가 놓치는 경우(임시휴장 등)에 대비한 2차 방어선."""
    bsop_date = bar.get('stck_bsop_date') or bar.get('bsop_date')
    if not bsop_date:
        return True  # 날짜 필드 자체가 없는 API 응답은 판단 보류(기존 동작 유지)
    return bsop_date == date_str

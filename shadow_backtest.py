#!/usr/bin/env python3
"""
섀도우 스캐너(shadow_scan.py) 후보를 실제 손익으로 근사 환산하는 간이 백테스트.

shadow_data/A_YYYYMMDD.json 에는 그날 스캔 사이클마다 포착된
후보 종목 스냅샷만 있고(진입가만 있고 그 뒤 가격은 안 담겨있음), 실제 체결/청산을
시뮬레이션하는 로직은 없었음(shadow_scan.py는 "포착만" 목적). 이 스크립트는 그
공백을 메우기 위해:
  1. 그날 가장 먼저 포착된 후보 1종목만 "그날의 거래"로 간주
     (실거래 봇의 동시보유 1종목 제약과 맞추기 위한 간이 규칙 — 섀도우 자체엔
     우선순위 로직이 없어서 단순히 시간순 최초 포착 종목을 씀)
  2. 그 날짜의 일봉(시가/고가/저가/종가)을 KIS API로 조회해서, 포착 시점 가격으로
     매수했다고 가정하고 라이브 봇과 동일한 청산 규칙(손절 -1.5%, 익절 +4%, 그
     외엔 종가청산)을 그날 하루짜리 근사로 적용

[한계 — 반드시 참고]
  - 일봉 고가/저가만으로 손절과 익절 중 어느 게 먼저 닿았는지 판정 불가 →
    보수적으로 "손절 먼저 확인"으로 처리(us_swing/backtest.py와 동일한 관례).
  - 당일 09:00~15:30 사이 정확히 언제 손절/익절에 닿았는지 모르므로 "당일 하루"
    스윙처럼 근사한 것 — 실제 라이브 봇(1분봉 타이밍)보다 훨씬 거친 근사치.
  - 표본 4일(2026-07-07~10)뿐이라 통계적으로 거의 의미 없음 — 방향성 참고용.

[2026-07-10] 신설 — 사용자가 "국내주식 단타모델 3개(눌림목 실거래/섀도우A/섀도우B)
비교"를 요청해서 만듦. [2026-08-12] 옛 섀도우A(시가돌파형)/B(FVG+유동성스윕) 폐기,
옛 섀도우C("첫 급등 돌파")를 A로 승격해 유일한 섀도우로 운영. 같은 날 이어서
신규 섀도우B("낙폭과대 반등")와 섀도우C("눌림목 분할매수") 추가 — 섀도우C는
분할매수(1차/2차) 구조라 기존 run_model()의 단일 진입가 가정이 안 맞아
run_model_c()를 별도로 신설(블렌디드 평단 계산, 아래 함수 주석 참고).
"""
import os, json, glob, re, time
from datetime import datetime, timezone, timedelta

if hasattr(__import__('sys').stdout, 'reconfigure'):
    import sys
    sys.stdout.reconfigure(encoding='utf-8')

KST = timezone(timedelta(hours=9))
BASE_URL = "https://openapi.koreainvestment.com:9443"
KIS_APP_KEY = os.environ['KIS_APP_KEY']
KIS_APP_SECRET = os.environ['KIS_APP_SECRET']
TOKEN_FILE = "kis_token.json"

STOP_LOSS_PCT = 0.015
TAKE_PROFIT_PCT = 0.04

# [2026-08-12, 섀도우C "눌림목 분할매수" 전용] shadow_scan.py와 동일 값을 여기서도
# 중복 정의(이 파일의 기존 관례 — get_kis_token 등도 각 파일에 중복 구현돼있음).
# 목표가는 1차만/1+2차 블렌디드 평단이 확정된 뒤에야 계산 가능해서 스캐너가 아닌
# 여기서 계산한다(shadow_scan.py [섀도우 C] 설명 참고).
BLENDED_TARGET_PCT = 3.5


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


def get_day_ohlc(token, code, ymd, market="J"):
    """ymd 하루치 일봉(시/고/저/종).
    [주의] 이건 하루 '전체' 고저가라 포착 시점 '이전'에 이미 찍힌 저가/고가까지
    섞여있음 — 포착 이후에도 그 가격을 다시 건드렸다는 보장이 없으므로 손절/익절
    판정에 그대로 쓰면 안 됨(과거에 여기서 오판 발견, 아래 종가 전용으로만 사용).
    [2026-07-31] market="Q"면 항상 빈 결과(같은 버그 계열) — "J" 고정, 폴백 제거."""
    data = kis_get(token, "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice", {
        "FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code,
        "FID_INPUT_DATE_1": ymd, "FID_INPUT_DATE_2": ymd,
        "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0",
    }, "FHKST03010100")
    rows = data.get('output2', [])
    if not rows:
        return None
    r = rows[0]
    try:
        return {
            'open': float(r['stck_oprc']), 'high': float(r['stck_hgpr']),
            'low': float(r['stck_lwpr']), 'close': float(r['stck_clpr']),
        }
    except (KeyError, ValueError):
        return None


def load_minute_bars_after(code, ymd, entry_hms):
    """minute_data/{code}_{ymd}.json 중 entry_hms(HHMMSS) 이후 봉만 시간 오름차순으로."""
    bars = load_json(f"minute_data/{code}_{ymd}.json", [])
    if not bars:
        return None
    after = [b for b in bars if b.get('stck_cntg_hour', '') >= entry_hms]
    return after or None


def find_days(prefix):
    files = glob.glob(f"shadow_data/{prefix}_*.json")
    days = []
    for f in files:
        m = re.search(rf'{prefix}_(\d{{8}})\.json$', f)
        if m:
            days.append(m.group(1))
    return sorted(days)


def load_json(path, default):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def first_candidate_per_day(prefix, ymd):
    """그날 스냅샷을 시간순으로 훑어 가장 먼저 등장한 종목 1개(코드/이름/포착가/포착시각)를 반환."""
    log = load_json(f"shadow_data/{prefix}_{ymd}.json", [])
    for snap in sorted(log, key=lambda s: s['time']):
        if snap['candidates']:
            c = dict(snap['candidates'][0])
            c['snapshot_time'] = snap['time']
            return c
    return None


def simulate_from_minutes(entry_price, bars_after, stop_price=None, target_price=None):
    """포착 시점 '이후' 1분봉만 순서대로 재생 — 정확한 손절/익절 판정 가능.
    [2026-08-12] 후보가 자체 stop_price/target_price를 갖고 있으면(섀도우A의 베이스
    저점 기반 손절+R배수 목표가) 그걸 쓰고, 없으면(다른 섀도우용 하위호환) 기존
    고정폭(-1.5%/+4%)으로 폴백."""
    if stop_price is None:
        stop_price = entry_price * (1 - STOP_LOSS_PCT)
    if target_price is None:
        target_price = entry_price * (1 + TAKE_PROFIT_PCT)
    for b in bars_after:
        try:
            hi, lo = float(b['stck_hgpr']), float(b['stck_lwpr'])
        except (KeyError, ValueError):
            continue
        if lo <= stop_price:
            return (stop_price - entry_price) / entry_price * 100, 'STOP'
        if hi >= target_price:
            return (target_price - entry_price) / entry_price * 100, 'TARGET'
    last_close = float(bars_after[-1]['stck_prpr'])
    return (last_close - entry_price) / entry_price * 100, 'CLOSE(수집종료시점)'


def simulate_close_only(entry_price, ohlc):
    """1분봉 없을 때의 대체 근사 — 손절/익절 판정 없이 '그날 종가까지 들고 있었다면'만 계산.
    (하루 전체 고저가로 손절/익절을 판정하면 포착 '이전' 가격까지 섞여 오판되므로 안 씀)"""
    return (ohlc['close'] - entry_price) / entry_price * 100, 'CLOSE(종가 근사, 저정밀)'


def _tranche_events_for_code(prefix, ymd, code):
    """그날 스냅샷 전체를 시간순으로 훑어, 특정 종목의 트랜치(1차/2차)별 '최초'
    이벤트만 골라 반환. {1: {...}, 2: {...}} 형태 — 해당 트랜치가 그날 아예
    안 일어났으면 키 자체가 없음."""
    log = load_json(f"shadow_data/{prefix}_{ymd}.json", [])
    seen = {}
    for snap in sorted(log, key=lambda s: s['time']):
        for c in snap['candidates']:
            if c['code'] != code or c['tranche'] in seen:
                continue
            seen[c['tranche']] = {**c, 'snapshot_time': snap['time']}
    return seen


def run_model_c(token):
    """섀도우C(눌림목 분할매수) 전용 — A/B와 달리 한 종목을 두 단계로 나눠 살 수
    있어서 run_model()의 단일 진입가 가정이 안 맞음. 1차만 체결됐으면 1차가격
    그대로, 2차까지 체결됐으면 비중가중 블렌디드 평단으로 계산.
    [한계] 1차 체결 이후~2차 체결 시점 사이의 가격 흐름은 시뮬레이션에서 빠짐
    (2차 체결 시점부터 블렌디드 평단·목표가로 일괄 재생) — 손절선(-12%)이 2차
    트리거선(-7%)보다 항상 낮아 그 구간에서 먼저 손절될 일은 없지만, 정교한
    분할 포지션 회계는 아니라는 점은 감안할 것."""
    label = '섀도우C (눌림목 분할매수)'
    print(f"\n{'='*60}\n[{label}]\n{'='*60}")
    days = find_days('C')
    trades = []
    for ymd in days:
        first = first_candidate_per_day('C', ymd)
        if first is None:
            print(f"  {ymd}: 후보 없음")
            continue
        code = first['code']
        events = _tranche_events_for_code('C', ymd, code)
        e1 = events.get(1)
        if e1 is None:
            print(f"  {ymd}: {code} 1차 이벤트 누락 — 스킵")
            continue
        e2 = events.get(2)
        if e2:
            blended_entry = (e1['price'] * e1['weight'] + e2['price'] * e2['weight']) / (e1['weight'] + e2['weight'])
            entry_hms = e2['snapshot_time'].replace(':', '')
        else:
            blended_entry = e1['price']
            entry_hms = e1['snapshot_time'].replace(':', '')
        stop_price = e1['stop_price']  # 극초반고점 기반이라 1차/2차 동일값
        target_price = blended_entry * (1 + BLENDED_TARGET_PCT / 100)
        bars_after = load_minute_bars_after(code, ymd, entry_hms)
        if bars_after:
            pnl_pct, reason = simulate_from_minutes(blended_entry, bars_after, stop_price, target_price)
        else:
            ohlc = get_day_ohlc(token, code, ymd)
            if ohlc is None:
                print(f"  {ymd}: {code}({first.get('name')}) 데이터 조회 실패 — 스킵")
                continue
            pnl_pct, reason = simulate_close_only(blended_entry, ohlc)
        trades.append({'date': ymd, 'code': code, 'name': first.get('name'),
                        'entry': blended_entry, 'tranches': 2 if e2 else 1,
                        'pnl_pct': pnl_pct, 'reason': reason})
        stage = '1+2차 블렌디드' if e2 else '1차만'
        print(f"  {ymd}: 매수 {code}({first.get('name')}) {stage} 평단 {blended_entry:,.0f} "
              f"-> {pnl_pct:+.2f}% ({reason})")
        time.sleep(0.1)
    if trades:
        avg = sum(t['pnl_pct'] for t in trades) / len(trades)
        wins = [t for t in trades if t['pnl_pct'] > 0]
        two_stage = len([t for t in trades if t['tranches'] == 2])
        print(f"  거래일수: {len(trades)}일  평균손익: {avg:+.2f}%  승률: {len(wins)}/{len(trades)}  "
              f"2차까지 간 날: {two_stage}/{len(trades)}")
    else:
        print("  거래 없음")
    return trades


def run_model(prefix, label, token):
    print(f"\n{'='*60}\n[{label}]\n{'='*60}")
    days = find_days(prefix)
    trades = []
    for ymd in days:
        c = first_candidate_per_day(prefix, ymd)
        if c is None:
            print(f"  {ymd}: 후보 없음")
            continue
        entry_price = c['price']
        entry_hms = c['snapshot_time'].replace(':', '')
        bars_after = load_minute_bars_after(c['code'], ymd, entry_hms)
        if bars_after:
            pnl_pct, reason = simulate_from_minutes(
                entry_price, bars_after, c.get('stop_price'), c.get('target_price'))
        else:
            ohlc = get_day_ohlc(token, c['code'], ymd)
            if ohlc is None:
                print(f"  {ymd}: {c['code']}({c.get('name')}) 데이터 조회 실패 — 스킵")
                continue
            pnl_pct, reason = simulate_close_only(entry_price, ohlc)
        trades.append({'date': ymd, 'code': c['code'], 'name': c.get('name'),
                        'entry': entry_price, 'pnl_pct': pnl_pct, 'reason': reason})
        print(f"  {ymd}: 매수 {c['code']}({c.get('name')}) @ {entry_price:,.0f} ({c['snapshot_time']} 포착) "
              f"-> {pnl_pct:+.2f}% ({reason})")
        time.sleep(0.1)
    if trades:
        avg = sum(t['pnl_pct'] for t in trades) / len(trades)
        wins = [t for t in trades if t['pnl_pct'] > 0]
        print(f"  거래일수: {len(trades)}일  평균손익: {avg:+.2f}%  승률: {len(wins)}/{len(trades)}")
    else:
        print("  거래 없음")
    return trades


def main():
    token = get_kis_token()
    a_trades = run_model('A', '섀도우A (첫 급등 돌파)', token)
    b_trades = run_model('B', '섀도우B (낙폭과대 반등)', token)
    c_trades = run_model_c(token)
    d_trades = run_model('D', '섀도우D (눌림목 재상승, 완화판)', token)

    print(f"\n{'='*60}\n[요약 비교]\n{'='*60}")
    for label, trades in [('섀도우A', a_trades), ('섀도우B', b_trades), ('섀도우C', c_trades), ('섀도우D', d_trades)]:
        if trades:
            avg = sum(t['pnl_pct'] for t in trades) / len(trades)
            wins = len([t for t in trades if t['pnl_pct'] > 0])
            print(f"  {label}: {len(trades)}거래, 평균 {avg:+.2f}%, 승률 {wins}/{len(trades)}")
        else:
            print(f"  {label}: 거래 없음")


if __name__ == '__main__':
    main()

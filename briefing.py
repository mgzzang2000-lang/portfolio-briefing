# 포트폴리오 브리핑 — 매일 오전 7시 KST 자동 실행
import os, json, re, requests, urllib.parse
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
TODAY = datetime.now(KST).strftime('%m.%d')
KAKAO_CLIENT_ID = os.environ['KAKAO_CLIENT_ID']
KAKAO_CLIENT_SECRET = os.environ['KAKAO_CLIENT_SECRET']
KAKAO_REFRESH_TOKEN = os.environ['KAKAO_REFRESH_TOKEN']
NAVER_CLIENT_ID = os.environ['NAVER_CLIENT_ID']
NAVER_CLIENT_SECRET = os.environ['NAVER_CLIENT_SECRET']
UA = {'User-Agent': 'Mozilla/5.0'}

# [2026-07-20] "미국 경제지표" 섹션이 처음부터 날짜가 하드코딩(7/2, 7/14 등)돼 있어서
# 매일 오래된 내용이 나가고 있었음. FOMC(연준 공식 발표)·CPI(BLS 공식 일정)는 미리
# 확정 발표되는 고정 일정이라 연 1회만 갱신하면 되는 상수로 박아두고, 그날 기준으로
# "향후 2주"만 골라 보여주도록 변경. NFP는 통상 매월 첫째 금요일(공휴일로 밀리는
# 예외적인 달이 가끔 있음 — 근사치).
FOMC_DATES_2026 = [
    ("1/27~28", (2026, 1, 28)), ("3/17~18", (2026, 3, 18)),
    ("4/28~29", (2026, 4, 29)), ("6/16~17", (2026, 6, 17)),
    ("7/28~29", (2026, 7, 29)), ("9/15~16", (2026, 9, 16)),
    ("10/27~28", (2026, 10, 28)), ("12/8~9", (2026, 12, 9)),
]
CPI_DATES_2026 = [
    (2026, 1, 13), (2026, 2, 13), (2026, 3, 11), (2026, 4, 10),
    (2026, 5, 12), (2026, 6, 10), (2026, 7, 14), (2026, 8, 12),
    (2026, 9, 11), (2026, 10, 14), (2026, 11, 10), (2026, 12, 10),
]

def _first_friday(year, month):
    d = datetime(year, month, 1)
    return d + timedelta(days=(4 - d.weekday()) % 7)

def get_us_events(days=14):
    today = datetime.now(KST).date()
    end = today + timedelta(days=days)
    events = []
    for m_off in (0, 1):
        y, m = today.year, today.month + m_off
        if m > 12:
            y, m = y + 1, m - 12
        nfp = _first_friday(y, m).date()
        if today <= nfp <= end:
            events.append((nfp, f"{nfp.month}/{nfp.day} NFP 비농업고용 ⭐⭐⭐"))
    for (y, m, d) in CPI_DATES_2026:
        dt = datetime(y, m, d).date()
        if today <= dt <= end:
            events.append((dt, f"{m}/{d} CPI 소비자물가 ⭐⭐⭐"))
    for label, (y, m, d) in FOMC_DATES_2026:
        dt = datetime(y, m, d).date()
        if today <= dt <= end:
            events.append((dt, f"{label} FOMC 금리결정 ⭐⭐⭐"))
    events.sort(key=lambda x: x[0])
    return "\n".join(e[1] for e in events) if events else "향후 2주 내 주요 지표 없음"

def get_access_token():
    r = requests.post('https://kauth.kakao.com/oauth/token', timeout=10, data={
        'grant_type': 'refresh_token',
        'client_id': KAKAO_CLIENT_ID,
        'client_secret': KAKAO_CLIENT_SECRET,
        'refresh_token': KAKAO_REFRESH_TOKEN,
    })
    print(f"Token response: {r.status_code} {r.text}")
    data = r.json()
    if 'access_token' not in data:
        raise Exception(f"Token error: {data}")
    return data['access_token']
    

def send_memo(token, text):
    text = text[:200]
    obj = json.dumps({'object_type': 'text', 'text': text, 'link': {'web_url': 'https://github.com', 'mobile_web_url': 'https://github.com'}})
    r = requests.post('https://kapi.kakao.com/v2/api/talk/memo/default/send', headers={'Authorization': f'Bearer {token}'}, data={'template_object': obj})
    print(f"[{r.status_code}] {text[:40]}")

def get_fear_greed():
    try:
        import yfinance as yf
        vix = yf.Ticker('^VIX').fast_info.last_price
        if vix >= 30: label = '😱극단공포'
        elif vix >= 20: label = '😨공포'
        elif vix >= 15: label = '😐중립'
        elif vix >= 12: label = '😏탐욕'
        else: label = '🤑극단탐욕'
        return f"VIX {vix:.1f} {label}"
    except Exception as e:
        print(f"FG error: {e}")
        return "N/A"
        
def get_night_futures():
    try:
        r = requests.get('https://esignal.co.kr/kospi200-futures-night/', headers=UA, timeout=10)
        t = re.search(r'<title>(.*?)</title>', r.text)
        return t.group(1).replace('야간선물: ', '').strip() if t else "N/A"
    except Exception as e:
        print(f"Futures error: {e}")
        return "N/A"

def get_naver_news(query, n=3):
    url = f"https://news.google.com/rss/search?q={urllib.parse.quote(query)}&hl=ko&gl=KR&ceid=KR:ko"
    try:
        r = requests.get(url, headers=UA, timeout=10)
        titles = re.findall(r'<title>(.*?)</title>', r.text)
        # [2026-07-20] 구글뉴스 RSS는 채널 제목(index 0) 바로 다음에 <image><title>Google
        # 뉴스</title>가 한 번 더 끼어있어서(index 1), 그동안 이게 실제 기사 제목인 것처럼
        # 1번 자리에 들어가고 진짜 기사는 하나 밀려서 n개 중 마지막 1개가 누락되고 있었음.
        titles = [t for t in titles[1:] if t != 'Google 뉴스']
        result = [re.sub('<[^>]+>', '', t)[:50] for t in titles[:n]]
        # [2026-07-20] 구글 뉴스가 에러 없이 빈 결과만 주는 경우(일시적 차단/지연 추정)가
        # 있어서, 예외가 안 났어도 결과가 비어있으면 그냥 빈 항목(①②③ 공란)으로 조용히
        # 나가지 않고 실패했다는 걸 알 수 있게 표시.
        return result if result else ['뉴스 수집 실패']
    except Exception as e:
        print(f"News error ({query}): {e}")
        return ['뉴스 수집 실패']
        
def get_price(symbol):
    try:
        import yfinance as yf
        fi = yf.Ticker(symbol).fast_info
        p, c = fi.last_price, (fi.last_price - fi.previous_close) / fi.previous_close * 100
        return round(p, 2), round(c, 2)
    except Exception as e:
        print(f"Price error {symbol}: {e}")
        return None, None

def fmt(p, c, w=False):
    if p is None: return "N/A"
    s = '+' if c >= 0 else ''
    return f"{p:,.0f}원 ({s}{c:.2f}%)" if w else f"${p:,.2f} ({s}{c:.2f}%)"

def get_global_news():
    try:
        url = "https://news.google.com/rss/search?q=글로벌+증시+미국+주식&hl=ko&gl=KR&ceid=KR:ko"
        r = requests.get(url, headers=UA, timeout=10)
        titles = re.findall(r'<title>(.*?)</title>', r.text)
        titles = [t for t in titles[1:] if t != 'Google 뉴스']  # get_naver_news와 동일 이유
        result = [re.sub('<[^>]+>', '', t)[:60] for t in titles[:3]]
        # [2026-07-20] 2026-07-20 아침 브리핑에서 실제로 발생한 사고 — 에러 없이 빈
        # 리스트만 와서 ①②③이 전부 공란으로 나갔음. 결과 비어있으면 실패로 표시.
        return result if result else ['글로벌 뉴스 수집 실패', '', '']
    except Exception as e:
        print(f"Global news error: {e}")
        return ['글로벌 뉴스 수집 실패', '', '']
        
def nl(lst, i):
    return lst[i] if i < len(lst) else ''

def main():
    print(f"=== 브리핑 {TODAY} ===")
    token = get_access_token()
    fg = get_fear_greed()
    fut = get_night_futures()
    qqqm_p, qqqm_c = get_price('QQQM')
    asml_p, asml_c = get_price('ASML')
    nasa_p, nasa_c = get_price('NASA')
    humn_p, humn_c = get_price('HUMN')
    tsla_p, tsla_c = get_price('TSLA')
    nvda_p, nvda_c = get_price('NVDA')
    sec_p, sec_c = get_price('005930.KS')
    skh_p, skh_c = get_price('000660.KS')
    hmc_p, hmc_c = get_price('005380.KS')
    sn = get_naver_news('삼성전자')
    hn = get_naver_news('SK하이닉스')
    gn = get_global_news()

    msgs = [
        f"📰 포트폴리오 브리핑 {TODAY}\n━━━━━━━━━━━━━━\n📊 시장 심리 & 야간선물\n🇺🇸 공포탐욕: {fg}\n🌙 야간선물: {fut}",
        f"🗓️ 미국 경제지표 (향후 2주)\n━━━━━━━━━━━━━━\n{get_us_events()}",
        f"🌐 글로벌 빅이슈\n━━━━━━━━━━━━━━\n① {nl(gn,0)}\n② {nl(gn,1)}\n③ {nl(gn,2)}",
        f"🇰🇷 삼성전자 {fmt(sec_p,sec_c,True)}\n━━━━━━━━━━━━━━\n① {nl(sn,0)}\n② {nl(sn,1)}\n③ {nl(sn,2)}",
        f"🇰🇷 SK하이닉스 {fmt(skh_p,skh_c,True)}\n━━━━━━━━━━━━━━\n① {nl(hn,0)}\n② {nl(hn,1)}\n③ {nl(hn,2)}",
        f"🌐 QQQM {fmt(qqqm_p,qqqm_c)}\n① 나스닥100 ETF. 기술주 핵심 추종.\n🌍 ASML {fmt(asml_p,asml_c)}\n① EUV 독점 장비. 반도체 설비 핵심.",
        f"🚀 NASA {fmt(nasa_p,nasa_c)}\n① Tema 우주혁신 ETF.\n🤖 HUMN {fmt(humn_p,humn_c)}\n[Tesla] {fmt(tsla_p,tsla_c)}\n[NVIDIA] {fmt(nvda_p,nvda_c)}\n[현대차] {fmt(hmc_p,hmc_c,True)}",
        "🔑 오늘의 핵심\n━━━━━━━━━━━━━━\n① 연준 매파 기조 — 단기 변동성 유의\n② 삼성·SK하닉 반도체 모멘텀 점검\n③ 야간선물 흐름으로 개장 방향 판단",
    ]

    for i, msg in enumerate(msgs, 1):
        print(f"\n[메시지 {i}]")
        send_memo(token, msg)
    print(f"\n✅ 브리핑 전송 완료 ({TODAY})")

if __name__ == '__main__':
    main()

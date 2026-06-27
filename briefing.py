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
        return [re.sub('<[^>]+>', '', t)[:50] for t in titles[1:n+1]]
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
        r = requests.get('https://feeds.finance.yahoo.com/rss/2.0/headline?s=^GSPC&region=US&lang=en-US', headers=UA, timeout=10)
        titles = re.findall(r'<title><!\[CDATA\[(.*?)\]\]></title>', r.text)
        return [t[:60] for t in titles[1:4]]
    except:
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
        "🗓️ 미국 경제지표 (향후 2주)\n━━━━━━━━━━━━━━\n7/2(목) NFP 비농업고용 ⭐⭐⭐\n7/14(월) CPI 소비자물가 ⭐⭐⭐\n7/28~29(화수) FOMC 금리결정 ⭐⭐⭐\n※ NFP 독립기념일 영향 7/2 조기 발표",
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

# 장중 브리핑 — 평일 오후 1시 KST 자동 실행
import os, json, re, requests, urllib.parse
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
TODAY = datetime.now(KST).strftime('%m.%d')
NOW = datetime.now(KST).strftime('%H:%M')
KAKAO_CLIENT_ID = os.environ['KAKAO_CLIENT_ID']
KAKAO_CLIENT_SECRET = os.environ['KAKAO_CLIENT_SECRET']
KAKAO_REFRESH_TOKEN = os.environ['KAKAO_REFRESH_TOKEN']
UA = {'User-Agent': 'Mozilla/5.0'}

def get_access_token():
    r = requests.post('https://kauth.kakao.com/oauth/token', timeout=10, data={
        'grant_type': 'refresh_token',
        'client_id': KAKAO_CLIENT_ID,
        'client_secret': KAKAO_CLIENT_SECRET,
        'refresh_token': KAKAO_REFRESH_TOKEN,
    })
    data = r.json()
    if 'access_token' not in data:
        raise Exception(f"Token error: {data}")
    return data['access_token']

def send_memo(token, text):
    text = text[:200]
    obj = json.dumps({'object_type': 'text', 'text': text, 'link': {'web_url': 'https://github.com', 'mobile_web_url': 'https://github.com'}})
    r = requests.post('https://kapi.kakao.com/v2/api/talk/memo/default/send',
                      headers={'Authorization': f'Bearer {token}'}, data={'template_object': obj})
    print(f"[{r.status_code}] {text[:40]}")

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

def get_news(query, n=3):
    url = f"https://news.google.com/rss/search?q={urllib.parse.quote(query)}&hl=ko&gl=KR&ceid=KR:ko"
    try:
        r = requests.get(url, headers=UA, timeout=10)
        titles = re.findall(r'<title>(.*?)</title>', r.text)
        return [re.sub('<[^>]+>', '', t)[:50] for t in titles[1:n+1]]
    except Exception as e:
        print(f"News error ({query}): {e}")
        return ['뉴스 수집 실패']

def get_global_news():
    try:
        url = "https://news.google.com/rss/search?q=미국+주식+나스닥+증시&hl=ko&gl=KR&ceid=KR:ko"
        r = requests.get(url, headers=UA, timeout=10)
        titles = re.findall(r'<title>(.*?)</title>', r.text)
        return [re.sub('<[^>]+>', '', t)[:60] for t in titles[1:4]]
    except Exception as e:
        print(f"Global news error: {e}")
        return ['글로벌 뉴스 수집 실패', '', '']

def nl(lst, i):
    return lst[i] if i < len(lst) else ''

def main():
    print(f"=== 장중 브리핑 {TODAY} {NOW} ===")
    token = get_access_token()

    sec_p, sec_c = get_price('005930.KS')
    skh_p, skh_c = get_price('000660.KS')
    qqqm_p, qqqm_c = get_price('QQQM')
    nvda_p, nvda_c = get_price('NVDA')

    sn = get_news('삼성전자')
    hn = get_news('SK하이닉스')
    gn = get_global_news()

    msgs = [
        f"📊 장중 브리핑 {TODAY} {NOW}\n━━━━━━━━━━━━━━\n🇰🇷 삼성전자 {fmt(sec_p,sec_c,True)}\n🇰🇷 SK하이닉스 {fmt(skh_p,skh_c,True)}\n🌐 QQQM {fmt(qqqm_p,qqqm_c)}\n🟩 NVIDIA {fmt(nvda_p,nvda_c)}",
        f"📰 장중 국내 뉴스\n━━━━━━━━━━━━━━\n[삼성] ① {nl(sn,0)}\n② {nl(sn,1)}\n[SK하닉] ① {nl(hn,0)}\n② {nl(hn,1)}",
        f"🌐 글로벌 이슈 업데이트\n━━━━━━━━━━━━━━\n① {nl(gn,0)}\n② {nl(gn,1)}\n③ {nl(gn,2)}",
    ]

    for i, msg in enumerate(msgs, 1):
        print(f"\n[메시지 {i}]")
        send_memo(token, msg)

    print(f"\n✅ 장중 브리핑 전송 완료 ({TODAY} {NOW})")

if __name__ == '__main__':
    main()

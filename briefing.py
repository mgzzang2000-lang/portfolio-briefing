import os
import json
import re
import requests
import urllib.parse
from datetime import datetime, timezone, timedelta

KST = timezone(timedelta(hours=9))
TODAY = datetime.now(KST).strftime('%m.%d')

KAKAO_CLIENT_ID     = os.environ['KAKAO_CLIENT_ID']
KAKAO_CLIENT_SECRET = os.environ['KAKAO_CLIENT_SECRET']
KAKAO_REFRESH_TOKEN = os.environ['KAKAO_REFRESH_TOKEN']
NAVER_CLIENT_ID     = os.environ['NAVER_CLIENT_ID']
NAVER_CLIENT_SECRET = os.environ['NAVER_CLIENT_SECRET']

HEADERS = {'User-Agent': 'Mozilla/5.0'}

def get_access_token():
    r = requests.post('https://kauth.kakao.com/oauth/token', data={
        'grant_type':    'refresh_token',
        'client_id':     KAKAO_CLIENT_ID,
        'client_secret': KAKAO_CLIENT_SECRET,
        'refresh_token': KAKAO_REFRESH_TOKEN,
    }, timeout=10)
    return r.json()['access_token']

def send_memo(token, text):
    text = text[:200]
    r = requests.post(
        'https://kapi.kakao.com/v2/api/talk/memo/default/send',
        headers={'Authorization': f'Bearer {token}'},
        data={'template_object': json.dumps({
            'object_type': 'text',
            'text': text,
            'link': {'web_url': 'https://github.com', 'mobile_web_url': 'https://github.com'}
        })}
    )
    print(f"  [{r.status_code}] {text[:40]}...")

def get_fear_greed():
    try:
        r = requests.get(

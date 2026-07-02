#!/usr/bin/env python3
"""
백테스트 스크립트 - 2단계 추세 추종 단타 전략
=====================================================
대상 기간 : 저번주 2026-06-23(월) ~ 2026-06-27(금)
스캔 대상 : 실행 시점 코스피+코스닥 거래량 상위 200종목
진입 가정 : 1분봉 StochRSI/BB 조건 생략 -> 시가(open) 진입
청산 기준 : 일봉 고가>=익절가 -> 익절가로 청산
            일봉 저가<=손절가 -> 손절가로 청산
            익절/손절 미달 + 다음날도 포지션 -> 종가 강제청산
=====================================================
"""

import os, json, time, requests
from datetime import datetime, timezone, timedelta, date

# -- 설정 --
KST      = timezone(timedelta(hours=9))
BASE_URL = "https://openapi.koreainvestment.com:9443"
MAX_BET  = 500_000

KIS_APP_KEY    = os.environ['KIS_APP_KEY']
KIS_APP_SECRET = os.environ['KIS_APP_SECRET']
TOKEN_FILE     = "kis_token.json"

BACKTEST_DAYS = [
    date(2026, 6, 23),
    date(2026, 6, 24),
    date(2026, 6, 25),
    date(2026, 6, 26),
    date(2026, 6, 27),
]
DATA_START   = "20260101"   # FHKST03010100 시작일 (약 120 거래일)
BACKTEST_END = "20260627"   # 마지막 백테스트일


# -- KIS 인증 --
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
    r = requests.post(f"{BASE_URL}/oauth2/tokenP", json={
        "grant_type": "client_credentials",
        "appkey": KIS_APP_KEY, "appsecret": KIS_APP_SECRET
    }, timeout=10)
    data = r.json()
    if 'access_token' not in data:
        raise Exception(f"KIS 토큰 오류: {data}")
    with open(TOKEN_FILE, 'w') as f:
        json.dump({'access_token': data['access_token'],
                   'issued_at': datetime.now(KST).isoformat()}, f)
    return data['access_token']


def kis_get(token, path, params, tr_id, retries=3):
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
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2)
    return {}


# -- 데이터 수집 --
def get_volume_rank(token, market="J"):
    scr_code = "20172" if market == "Q" else "20171"
    data = kis_get(token, "/uapi/domestic-stock/v1/quotations/volume-rank", {
        "FID_COND_MRKT_DIV_CODE": market,
        "FID_COND_SCR_DIV_CODE": scr_code,
        "FID_INPUT_ISCD": "0000",
        "FID_DIV_CLS_CODE": "0", "FID_BLNG_CLS_CODE": "0",
        "FID_TRGT_CLS_CODE": "111111111",
        "FID_TRGT_EXLS_CLS_CODE": "000000",
        "FID_INPUT_PRICE_1": "", "FID_INPUT_PRICE_2": "",
        "FID_VOL_CNT": "", "FID_INPUT_DATE_1": ""
    }, "FHPST01710000")
    return [x['mksc_shrn_iscd'] for x in data.get('output', [])[:100]]


def get_daily_raw(token, code, market="J"):
    """일봉 원시 데이터 반환 (최신순). FHKST03010100 날짜범위 API 사용. KOSDAQ 폴백 포함."""
    data = kis_get(token, "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice", {
        "FID_COND_MRKT_DIV_CODE": market,
        "FID_INPUT_ISCD": code,
        "FID_INPUT_DATE_1": DATA_START,
        "FID_INPUT_DATE_2": BACKTEST_END,
        "FID_PERIOD_DIV_CODE": "D",
        "FID_ORG_ADJ_PRC": "0",
    }, "FHKST03010100")
    rows = data.get('output2', [])
    if not rows and market == "J":
        return get_daily_raw(token, code, market="Q")
    return rows


# -- 지표 계산 --
def calc_ma(closes, period):
    if len(closes) < period:
        return None
    return sum(closes[:period]) / period


def calc_macd(closes, fast=12, slow=26, signal=9):
    if len(closes) < slow + signal + 6:
        return None, None, None
    asc = list(reversed(closes))
    n   = len(asc)
    kf, ks, ksig = 2/(fast+1), 2/(slow+1), 2/(signal+1)
    ef = sum(asc[:fast]) / fast
    es = sum(asc[:slow]) / slow
    for i in range(fast, slow):
        ef = asc[i]*kf + ef*(1-kf)
    macd_s = []
    for i in range(slow, n):
        ef = asc[i]*kf + ef*(1-kf)
        es = asc[i]*ks + es*(1-ks)
        macd_s.append(ef - es)
    if len(macd_s) < signal:
        return None, None, None
    sig = sum(macd_s[:signal]) / signal
    sig_s = [sig]
    for v in macd_s[signal:]:
        sig = v*ksig + sig*(1-ksig)
        sig_s.append(sig)
    golden = None
    check = min(6, len(macd_s)-1, len(sig_s)-1)
    for i in range(1, check+1):
        if macd_s[-(i+1)] < sig_s[-(i+1)] and macd_s[-i] >= sig_s[-i]:
            golden = i
            break
    return macd_s[-1], sig_s[-1], golden


def calc_bb(closes, period=20, mult=2.0):
    if len(closes) < period:
        return None, None, None, None
    d   = closes[:period]
    mid = sum(d) / period
    std = (sum((x-mid)**2 for x in d) / period) ** 0.5
    upper = mid + mult*std
    lower = mid - mult*std
    bw    = (upper-lower)/mid if mid else 0
    return upper, mid, lower, bw


def is_bb_squeeze(closes, period=20, lookback=20):
    if len(closes) < period + lookback:
        return False
    _, _, _, bw_now = calc_bb(closes, period)
    if bw_now is None:
        return False
    hist = []
    for i in range(1, lookback+1):
        _, _, _, bw = calc_bb(closes[i:], period)
        if bw is not None:
            hist.append(bw)
    if not hist:
        return False
    threshold = sorted(hist)[int(len(hist)*0.3)]
    return bw_now <= threshold


def calc_atr(highs, lows, closes, period=14):
    if len(closes) < period+1:
        return None
    trs = [max(highs[i]-lows[i],
               abs(highs[i]-closes[i+1]),
               abs(lows[i]-closes[i+1])) for i in range(period)]
    return sum(trs) / period


# -- 메인 백테스트 --
def run_backtest():
    print("=" * 55)
    print("  백테스트: 2026-06-23 ~ 06-27 (저번주)")
    print("=" * 55)

    token = get_kis_token()
    print("\n[1] 스캔 대상 수집 중...")
    kospi  = get_volume_rank(token, "J")
    time.sleep(1)
    kosdaq = get_volume_rank(token, "Q")
    universe = list(dict.fromkeys(kospi + kosdaq))
    print(f"    코스피 {len(kospi)} + 코스닥 {len(kosdaq)} = {len(universe)}종목")

    scan_list = universe[:80]

    print(f"\n[2] 일봉 데이터 로딩 중 ({len(scan_list)}종목)...")
    print(f"    기간: {DATA_START} ~ {BACKTEST_END}")
    stock_data = {}
    for i, code in enumerate(scan_list):
        try:
            rows = get_daily_raw(token, code)
            if len(rows) >= 42:
                stock_data[code] = rows
        except Exception:
            pass
        time.sleep(0.1)
        if (i+1) % 20 == 0:
            print(f"    {i+1}/{len(scan_list)}...")
    print(f"    유효 데이터: {len(stock_data)}종목\n")

    date_index = {}
    for code, rows in stock_data.items():
        date_index[code] = {r['stck_bsop_date']: idx for idx, r in enumerate(rows)}

    portfolio = {'cash': MAX_BET, 'position': None}
    trade_log = []
    daily_log = []

    for bday in BACKTEST_DAYS:
        day_str  = bday.strftime('%Y%m%d')
        day_disp = bday.strftime('%m/%d(%a)')

        # 전날 강제청산
        if portfolio['position']:
            pos = portfolio['position']
            if pos['entry_date'] < bday:
                pcode = pos['code']
                if pcode in date_index and day_str in date_index[pcode]:
                    idx   = date_index[pcode][day_str]
                    close = float(stock_data[pcode][idx].get('stck_clpr', pos['entry']))
                    _close_position(portfolio, trade_log, bday, close, "강제청산")

        # 당일 익절/손절
        if portfolio['position']:
            pos   = portfolio['position']
            pcode = pos['code']
            if pcode in date_index and day_str in date_index[pcode]:
                idx  = date_index[pcode][day_str]
                row  = stock_data[pcode][idx]
                high = float(row.get('stck_hgpr', 0))
                low  = float(row.get('stck_lwpr', 0))
                if high >= pos['target']:
                    _close_position(portfolio, trade_log, bday, pos['target'], "익절 +4%")
                elif low <= pos['stop']:
                    _close_position(portfolio, trade_log, bday, pos['stop'], "ATR손절")

        # 신규 매수
        candidates = []
        f_no_data = 0
        f_ma20    = 0
        f_ma200   = 0
        f_macd    = 0
        f_bb      = 0
        f_vol     = 0
        f_gap     = 0
        f_pass    = 0

        if portfolio['position'] is None:
            for code, rows in stock_data.items():
                try:
                    if day_str not in date_index[code]:
                        f_no_data += 1
                        continue
                    idx = date_index[code][day_str]

                    today = rows[idx]
                    past  = rows[idx+1:]
                    if len(past) < 30:
                        f_no_data += 1
                        continue

                    today_open  = float(today.get('stck_oprc', 0))
                    today_close = float(today.get('stck_clpr', 0))
                    today_vol   = int(today.get('acml_vol', 0))
                    prev_close  = float(past[0].get('stck_clpr', 0))

                    if today_open == 0 or prev_close == 0:
                        f_no_data += 1
                        continue

                    closes  = [float(r['stck_clpr']) for r in past]
                    highs   = [float(r.get('stck_hgpr', r['stck_clpr'])) for r in past]
                    lows    = [float(r.get('stck_lwpr', r['stck_clpr'])) for r in past]
                    volumes = [int(r.get('acml_vol', 0)) for r in past]

                    ma20  = calc_ma(closes, 20)
                    ma200 = calc_ma(closes, 200)
                    macd, sig_line, golden = calc_macd(closes)
                    bb_upper, _, _, _ = calc_bb(closes, 20)
                    squeeze = is_bb_squeeze(closes)

                    if ma20 is None or macd is None or bb_upper is None:
                        f_no_data += 1
                        continue

                    vol_avg20 = sum(volumes[:20]) / 20 if len(volumes) >= 20 else None
                    gap = (today_open - prev_close) / prev_close * 100

                    if today_close <= ma20:
                        f_ma20 += 1
                        continue
                    if ma200 and today_close <= ma200:
                        f_ma200 += 1
                        continue
                    if golden is None or macd <= sig_line:
                        f_macd += 1
                        continue
                    if not (squeeze or today_close >= bb_upper * 0.98):
                        f_bb += 1
                        continue
                    if vol_avg20 and today_vol < vol_avg20 * 2:
                        f_vol += 1
                        continue
                    if gap >= 3.0:
                        f_gap += 1
                        continue
                    f_pass += 1

                    vol_ratio = today_vol / vol_avg20 if vol_avg20 else 0
                    atr = calc_atr(highs[:15], lows[:15], closes[:15])
                    stop_price = today_open - (atr * 1.5 if atr else today_open * 0.02)
                    stop_price = min(stop_price, today_open * 0.995)

                    candidates.append({
                        'code': code,
                        'open': today_open,
                        'squeeze': squeeze,
                        'vol_ratio': vol_ratio,
                        'stop': stop_price,
                        'target': today_open * 1.04,
                        'atr': atr,
                    })
                except Exception:
                    continue

            candidates.sort(key=lambda x: (-int(x['squeeze']), -x['vol_ratio']))

            if candidates:
                c   = candidates[0]
                qty = int(min(portfolio['cash'], MAX_BET) / c['open'])
                if qty >= 1:
                    cost = c['open'] * qty
                    portfolio['cash'] -= cost
                    portfolio['position'] = {
                        'code': c['code'], 'entry': c['open'],
                        'qty': qty, 'stop': c['stop'],
                        'target': c['target'], 'entry_date': bday,
                    }
                    trade_log.append({
                        'action': 'BUY', 'date': day_disp,
                        'code': c['code'], 'price': c['open'], 'qty': qty,
                        'stop': c['stop'], 'target': c['target'],
                        'tag': 'squeeze' if c['squeeze'] else 'BB상단',
                        'vol_ratio': c['vol_ratio'],
                    })

        pos_val = 0
        if portfolio['position']:
            p = portfolio['position']
            if p['code'] in date_index and day_str in date_index[p['code']]:
                idx   = date_index[p['code']][day_str]
                close = float(stock_data[p['code']][idx].get('stck_clpr', p['entry']))
                pos_val = close * p['qty']
        total = portfolio['cash'] + pos_val
        daily_log.append({
            'date': day_disp, 'total': total, 'candidates': len(candidates),
            'f_no_data': f_no_data, 'f_ma20': f_ma20, 'f_ma200': f_ma200,
            'f_macd': f_macd, 'f_bb': f_bb, 'f_vol': f_vol, 'f_gap': f_gap,
            'f_pass': f_pass,
        })

    # 마지막 포지션 청산
    if portfolio['position']:
        p = portfolio['position']
        last_day = BACKTEST_DAYS[-1]
        last_str = last_day.strftime('%Y%m%d')
        if p['code'] in date_index and last_str in date_index[p['code']]:
            idx   = date_index[p['code']][last_str]
            close = float(stock_data[p['code']][idx].get('stck_clpr', p['entry']))
        else:
            close = p['entry']
        _close_position(portfolio, trade_log, last_day, close, "기간종료 청산")

    # 결과 출력
    sells     = [t for t in trade_log if t['action'] == 'SELL']
    wins      = [t for t in sells if t['pnl'] > 0]
    total_pnl = sum(t['pnl'] for t in sells)
    final     = MAX_BET + total_pnl

    print("=" * 55)
    print("  결과 요약")
    print("=" * 55)
    print(f"  초기 자금  : {MAX_BET:>10,.0f}원")
    print(f"  최종 자금  : {final:>10,.0f}원")
    print(f"  총 손익    : {total_pnl:>+10,.0f}원  ({total_pnl/MAX_BET*100:+.1f}%)")
    print(f"  총 거래 수 : {len(sells)}회")
    if sells:
        print(f"  승률       : {len(wins)}/{len(sells)} ({len(wins)/len(sells)*100:.0f}%)")
        best  = max(sells, key=lambda t: t['pnl_pct'])
        worst = min(sells, key=lambda t: t['pnl_pct'])
        print(f"  최대 익절  : {best['pnl_pct']:+.1f}% ({best['code']} {best['date']})")
        print(f"  최대 손절  : {worst['pnl_pct']:+.1f}% ({worst['code']} {worst['date']})")

    print("\n  일별 포트폴리오 (필터 통계)")
    print("  " + "-"*52)
    for d in daily_log:
        chg = d['total'] - MAX_BET
        print(f"  {d['date']}  평가액 {d['total']:>9,.0f}원  ({chg:>+8,.0f})  후보 {d['candidates']}종목")
        print(f"    [필터] 데이터={d['f_no_data']} MA20={d['f_ma20']} MA200={d['f_ma200']} "
              f"MACD={d['f_macd']} BB={d['f_bb']} 거래량={d['f_vol']} 갭={d['f_gap']} 통과={d['f_pass']}")

    print("\n  거래 내역")
    print("  " + "-"*40)
    for t in trade_log:
        if t['action'] == 'BUY':
            print(f"  {t['date']} 매수 {t['code']} {t['qty']}주 @ {t['price']:,.0f}"
                  f"  [{t['tag']}] 거래량x{t['vol_ratio']:.1f}")
            print(f"    손절 {t['stop']:,.0f}  익절 {t['target']:,.0f}")
        else:
            sign = "OK" if t['pnl'] > 0 else "NG"
            print(f"  {t['date']} 매도 {t['code']} {t['pnl_pct']:+.1f}%"
                  f"  ({t['pnl']:>+,.0f}원)  {t['reason']}  {sign}")

    print("\n주의: 1분봉 진입 조건(StochRSI/BB중심선) 미적용 - 실제 결과와 다를 수 있음")
    print("=" * 55)


def _close_position(portfolio, trade_log, bday, price, reason):
    pos = portfolio['position']
    qty = pos['qty']
    pnl = (price - pos['entry']) * qty
    pnl_pct = (price - pos['entry']) / pos['entry'] * 100
    portfolio['cash'] += price * qty
    portfolio['position'] = None
    trade_log.append({
        'action': 'SELL', 'date': bday.strftime('%m/%d(%a)'),
        'code': pos['code'], 'price': price, 'qty': qty,
        'pnl': pnl, 'pnl_pct': pnl_pct, 'reason': reason,
    })


if __name__ == '__main__':
    run_backtest()

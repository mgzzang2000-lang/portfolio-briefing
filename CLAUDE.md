# portfolio-briefing — 프로젝트 메모 (Cowork 세션 인계용, 2026-07-03 작성)

한국투자증권(KIS) Open API 기반 국내주식 자동매매 봇. GitHub Actions로 스케줄 실행, 카카오톡으로 알림.

## 저장소 구조
- `auto_trading.py` — **현재 GitHub에 올라가 있는 라이브 매매 스크립트** (GitHub Actions가 평일 09:00~15:30 KST 동안 분 단위로 실행)
- `auto_trading_relaxed.py` — 로컬에서 준비한 필터 완화 버전. **아직 auto_trading.py에 반영 안 됨** — 사용자가 직접 검토 후 붙여넣을 예정
- `collect_intraday_data.py` + `.github/workflows/collect-intraday.yml` — 1분봉 히스토리 수집기. 2026-07-03에 GitHub 푸시 완료, 평일 15분 간격 실행. **다음 개장일부터 데이터 축적 시작**
- `backtest.py`, `backtest_pykrx.py`, `backtest_scenarios.py`, `intraday_backtest.py` — 백테스트 스크립트 (아래 "데이터 한계" 참고)
- `dashboard_data.json`, `index.html` — 웹 대시보드 (GitHub Pages로 배포)

## 전략 구조 (2단계)
1. **[일봉] 종목 선정** (`scan_signals()`): MA20 상단 + (BB 스퀴즈 OR BB상단 98% 근접) + 거래량 20일평균 대비 시간비례 2배 + 시간대별 갭 필터
   - 09:00~09:10, 09:10~11:00: 갭 5% 이내
   - 14:00~14:30: 갭 조건 대신 "당일 고가 98% 이상 근접" (문자 그대로 당일 고점 근처에서만 신호)
   - MA200, MACD는 2026-07-02에 필터에서 제거함 (relaxed 버전 기준)
2. **[1분봉] 진입 타이밍 확인** (`check_1min_entry()`): 스토캐스틱RSI(K>D, K>20) AND 현재가>=1분봉 BB중심선. ATR(14)×1.5 동적 손절(최소 -1.5%), +4% 익절, 15:20 강제청산

**중요한 설계 의도**: 1단계가 "이미 강한/고점 근처 종목"을 고르고, 2단계가 그 안에서 "방금 짧게 눌렸다가 반등 시작하는 순간"을 잡는 구조. 1단계만 보면 "고점에서 신호가 뜬다"는 인상을 받을 수 있는데(사실이고 필터 설계상 당연함), 2단계가 그 타이밍을 걸러주는 역할.

## 2026-07-03에 발견/수정한 버그
1. **`get_minute_ohlcv()`의 TR ID 오류** (가장 치명적): `/inquire-time-itemchartprice`(1분봉 엔드포인트)를 호출하면서 TR ID를 일봉용 `FHKST03010100`으로 잘못 넣고 있었음 → 1분봉 조회가 100% 실패("데이터 부족 0봉") → 2단계가 사실상 무조건 패스 → **체결 0건의 근본 원인**. `FHKST03010200`으로 수정 완료 (auto_trading_relaxed.py에 반영, 라이브 auto_trading.py는 아직 미반영 — 사용자가 직접 붙여넣기로 함)
2. 손절 하한 `price*0.995`(-0.5%) → `price*0.985`(-1.5%)로 수정, `entry_cutoff` 데드코드 제거 (14:00~14:30 진입 막던 버그) — **이미 GitHub에 커밋됨**
3. 거래량 필터 시간 비례 보정 추가 (`elapsed_minutes/390`) — **이미 GitHub에 커밋됨**

## 데이터 한계 (중요, 반복해서 재확인한 사실)
- 한국 주식 1분봉을 무료로 제공하는 소스가 없음. pykrx, 네이버 금융 API 모두 시도했으나 분봉 미제공/차단됨.
- KIS API도 "과거 날짜"의 1분봉은 안정적으로 지원 안 함(intraday_backtest.py 실행 시 실패 확인) — 오직 "당일 최근 30봉"만 조회 가능.
- 그래서 진짜 백테스팅은 불가능하고, 유일한 방법은 `collect_intraday_data.py`로 **지금부터** 매일 조금씩 쌓는 것. 2026-07-03 오후에 워크플로우 푸시 완료, 몇 주 지나야 `intraday_backtest.py`로 쓸만한 양이 됨.
- pykrx 일봉 스냅샷(`get_market_ohlcv_by_ticker`, 전종목 조회)이 이 Cowork 샌드박스 IP에서는 간헐적으로 차단됨(로컬/Claude Code 환경에서는 안 그럴 가능성 높음) — `FALLBACK_UNIVERSE`(하드코딩 80종목)로 우회해둠.

## 보류 중인 논의
- BB 98% 근접 / 14시 고가 98% 근접 기준을 더 완화할지: 일단 현재 상태로 며칠 지켜본 뒤 재조정하기로 함 (2026-07-03 결정)
- 1분봉 수집기가 축적되면 그때 정확한 백테스트로 승률/기대수익 재검토 필요

## 환경변수 (GitHub Secrets)
`KIS_APP_KEY`, `KIS_APP_SECRET`, `KIS_ACCOUNT_NO`, `KAKAO_CLIENT_ID`, `KAKAO_CLIENT_SECRET`, `KAKAO_REFRESH_TOKEN` — 코드에 하드코딩 금지, 항상 `os.environ[...]`로만 사용.

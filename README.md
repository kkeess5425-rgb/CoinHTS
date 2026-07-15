# CoinHTS — Professional Crypto Trading Terminal

> TradingView + Bookmap + Exocharts + AI를 합친 수준의 Python 암호화폐 트레이딩 터미널

[![Python](https://img.shields.io/badge/Python-3.13-blue)](https://python.org)
[![Tests](https://img.shields.io/badge/Tests-199%20passed-brightgreen)]()
[![Files](https://img.shields.io/badge/Files-83%20py%20|%2015K%20lines-orange)]()
[![License](https://img.shields.io/badge/License-MIT-green)]()

---

## 핵심 기능

### 📊 차트 & UI
| 기능 | 설명 |
|------|------|
| 캔들차트 | 60FPS (PyQtGraph + OpenGL), 1초봉~4H |
| 멀티차트 | 1×1 / 2×1 / 2×2 / 1+3 레이아웃 |
| 드로잉 도구 | 수평선, 추세선, 피보나치, 박스 |
| SMC 오버레이 | FVG/OB/Breaker/EQH/EQL/PDZ 차트 위 표시 |
| AI 요약 패널 | 자연어 시장 분석 (6섹션) |
| 포지션 패널 | 실시간 PnL, 부분익절/BE/청산 버튼 |

### 🎯 전략 엔진

**ICT (Inner Circle Trader)**
- BOS / CHoCH / Displacement
- FVG (Fair Value Gap, 50% 채움 필터)
- Order Block / OTE (골든포켓 0.618~0.786)
- Liquidity Sweep

**SMC (Smart Money Concept)**
- BOS / CHoCH / FVG / Order Block
- Breaker Block / Mitigation Block
- Equal High / Equal Low
- Premium / Discount Zone
- SMT Divergence

### ⚡ 오더플로우
- Footprint 차트 (Bookmap 수준, 169K ticks/sec)
- Stacked Imbalance / Unfinished Auction
- Absorption / Exhaustion
- Iceberg Detection / Hidden Liquidity
- Aggressive Buyer/Seller
- Delta Divergence

### 🤖 AI 분석
- 진입 점수 (0~100), 청산 점수, 추세 강도, 변동성 점수
- 신뢰도 (%), 자연어 시장 설명
- AI 차트 요약: "현재는 상승 추세이며 CVD는 상승하지만 OI는 감소 중..."
- 매매일지 자동 생성
- 실수 감지 (추격매수/복수매매/FOMO/RR부족)

### 📈 통계
- 승률, Profit Factor, Sharpe, Sortino, Expectancy, MDD
- 시간대별 / 요일별 승률 차트
- Monte Carlo, Walk Forward, 유전자 알고리즘 최적화

### 🔍 스캐너
| 신호 | 설명 |
|------|------|
| VOLUME_SPIKE | 볼륨 3배 이상 급증 |
| OI_SURGE | 미결제약정 2% 급증 |
| CVD_BULL/BEAR_DIV | CVD 다이버전스 |
| SMC_BOS/CHOCH | 구조 돌파/전환 |
| SMC_SWEEP | 유동성 스윕 |
| FP_ABSORPTION | Absorption 신호 |
| LIQUIDATION_SURGE | 대형 청산 급증 |

### 💰 자동매매
- 페이퍼 트레이딩 (가상 자금)
- **실거래** (OKX API, 샌드박스 지원)
- 부분 익절 (1R 도달 시 50% 익절)
- 브레이크이븐 자동 이동
- ATR 트레일링 스탑
- 일일 최대 손실 제한 (3%)
- 최대 포지션 수 제한

### 🐋 외부 데이터
- 고래 추적 (대형 이체, 거래소 유입/유출, 스테이블코인)
- 경제 캘린더 (CPI/FOMC/PPI/NFP)
- AI 뉴스 요약

### 🤖 Telegram 봇
```
/status    — 앱 상태 요약
/positions — 열린 포지션
/signals   — 최근 전략 신호
/scanner   — 스캐너 알림
/whale     — 고래 트래커
/news      — 경제 캘린더
/journal   — 매매일지 요약
/stop      — 자동매매 중지
/start_trading — 재개
```

---

## 설치

```bash
git clone https://github.com/kkeess5425-rgb/CoinHTS.git
cd CoinHTS
pip install -r requirements.txt
```

---

## 실행

### 데스크탑 앱
```bash
python main.py
```

### 웹 버전
```bash
# 백엔드
python web/backend/server.py

# 프론트엔드 (별도 터미널)
cd web/frontend && npm install && npm run dev
# → http://localhost:5173
```

### Docker
```bash
cp .env.example .env   # API 키 설정
docker-compose up -d   # Redis 포함
# → http://localhost:8000
```

---

## 환경변수

```bash
export OKX_API_KEY="..."
export OKX_API_SECRET="..."
export OKX_PASSPHRASE="..."
export TELEGRAM_TOKEN="..."
export TELEGRAM_CHAT_ID="..."
export DISCORD_WEBHOOK="..."
```

---

## 실거래 사용법

```python
from trading.live_trader import OKXLiveTrader, TradingConfig

trader = OKXLiveTrader(
    api_key="...", api_secret="...", passphrase="...",
    config=TradingConfig(risk_per_trade=0.5),  # 잔고의 0.5%
    sandbox=True,   # ⚠️ 먼저 샌드박스로 테스트!
)
```

---

## 프로젝트 구조

```
CoinHTS/
├── core/           모델, EventBus, 설정, 오케스트레이터, 성능
├── exchange/       OKX REST API
├── websocket/      실시간 WebSocket 피드
├── indicators/     EMA/ATR/RSI/VWAP (Numba JIT)
├── orderflow/      Footprint + 고급 오더플로우
├── strategy/       ICT 엔진 + SMC 엔진
├── orderbook/      DOM 분석 (Spoofing/Whale)
├── scanner/        기본 + 고급 스캐너
├── ai/             AI 점수 + 요약 + 매매일지
├── stats/          통계 엔진
├── optimization/   Walk Forward / MC / GA
├── news/           경제 캘린더 + 뉴스
├── whale/          고래 추적
├── trading/        페이퍼 + 실거래
├── replay/         틱/Footprint 리플레이
├── risk/           리스크 매니저
├── alert/          Telegram / Discord / 음성 / 봇
├── plugins/        플러그인 시스템
├── database/       SQLite / PostgreSQL
├── ui/             PySide6 데스크탑 UI (11개 위젯)
├── web/            FastAPI 백엔드 + React 프론트엔드
└── tests/          199개 단위/통합 테스트
```

---

## 성능

| 항목 | 결과 | 목표 |
|------|------|------|
| Footprint 처리 | **169,000 ticks/sec** | 100K |
| Numba EMA (1000봉) | **0.023ms** | 5ms |
| ICT 분석 (200봉) | **< 30ms** | 100ms |
| SMC 분석 (300봉) | **< 50ms** | 100ms |
| 전체 파이프라인 | **> 50K ticks/sec** | 50K |
| 단위 테스트 | **199/199 통과** | — |

---

## 테스트

```bash
# 전체
python -m pytest tests/ -v

# 통합 테스트
python -m pytest tests/test_integration.py -v

# 성능 벤치마크
python -m pytest tests/test_performance.py -v
```

---

## 라이선스

MIT License

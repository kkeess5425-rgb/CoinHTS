# CoinHTS — 전문 코인 HTS

> TradingView + Bookmap + Exocharts를 합친 수준의 Python 기반 암호화폐 트레이딩 터미널

[![Python](https://img.shields.io/badge/Python-3.13-blue)](https://python.org)
[![PySide6](https://img.shields.io/badge/PySide6-6.6+-green)](https://doc.qt.io/qtforpython)
[![Tests](https://img.shields.io/badge/Tests-121%20passed-brightgreen)]()
[![Performance](https://img.shields.io/badge/Performance-169K%20ticks%2Fsec-orange)]()

---

## 기능

### 📈 차트
- 1초봉 ~ 4시간봉 전 타임프레임
- 60FPS (PyQtGraph + OpenGL)
- 멀티차트 (1×1 / 2×1 / 2×2 / 1+3)
- 드로잉 도구 (수평선, 추세선, 피보나치, 박스)
- 줌 / 스크롤

### 📊 오더플로우
| 기능 | 설명 |
|------|------|
| Footprint | 가격 레벨별 매수/매도 볼륨 (Bookmap 스타일) |
| Delta / CVD | 실시간 볼륨 델타 및 누적 델타 |
| Imbalance | 4:1 이상 불균형 하이라이트 |
| Absorption | 가격 정체 + 큰 볼륨 감지 |
| Time & Sales | 실시간 체결 내역 + Whale 하이라이트 |

### 📉 오더북
- 실시간 400단계 호가 (CRC32 체크섬 검증)
- 유동성 히트맵 (CoinGlass 스타일)

### 🔍 스캐너
- 볼륨 급증 (평균 3배 이상)
- OI 급증 (2% 이상)
- 펀딩비 극단값
- Delta 폭증
- Absorption 감지
- Liquidity Sweep

### 🎯 ICT 전략 엔진
```
BOS / CHoCH → Liquidity Sweep → Displacement
    → FVG / Order Block / OTE (골든포켓 0.618~0.786)
    → AI 100점 스코어링 → 알림
```
8단계 필터로 가짜 신호 최소화.

### 🤖 AI 스코어 (0~100점)
| 조건 | 배점 |
|------|------|
| BOS / CHoCH | 20점 |
| 유동성 스윕 전환 | 15점 |
| 볼륨 급증 | 15점 |
| OI 증가 | 10점 |
| 양성 Delta | 15점 |
| Footprint 매수 우세 | 15점 |
| Absorption | 10점 |
| EMA 추세 | 10점 |
| 펀딩 과열 (감점) | -5점 |

**80점+ → BUY, 20점- → SELL**

### ⚠️ Risk Management
- ATR 기반 손절
- Trailing Stop
- 일일 최대 손실 제한
- Kelly Criterion 포지션 사이즈

---

## 설치

```bash
git clone https://github.com/kkeess5425-rgb/CoinHTS.git
cd CoinHTS
pip install -r requirements.txt
```

### PostgreSQL 사용 시 (선택)
```bash
pip install asyncpg
```

---

## 실행

```bash
python main.py
```

### 설정 파일 (`config.json`)
```json
{
  "telegram_token":   "YOUR_BOT_TOKEN",
  "telegram_chat_id": "YOUR_CHAT_ID",
  "discord_webhook":  "YOUR_WEBHOOK_URL",
  "default_symbols":  ["BTC-USDT-SWAP", "ETH-USDT-SWAP"]
}
```

OKX API 키는 환경변수 권장:
```bash
export OKX_API_KEY="your_key"
export OKX_API_SECRET="your_secret"
export OKX_PASSPHRASE="your_passphrase"
```

---

## 프로젝트 구조

```
CoinHTS/
├── core/           # 데이터 모델, EventBus, 설정, 오케스트레이터
├── exchange/       # OKX REST API (Binance/Bybit 추가 가능)
├── websocket/      # OKX WebSocket 실시간 피드
├── indicators/     # EMA/ATR/RSI/VWAP/VolumeProfile (Numba JIT)
├── orderflow/      # Footprint 엔진 (Imbalance/Absorption)
├── strategy/       # ICT 엔진 (BOS/FVG/OB/OTE/Displacement)
├── scanner/        # 실시간 멀티 조건 스캐너
├── database/       # SQLite / PostgreSQL 저장소
├── replay/         # 틱 리플레이 (1x~1000x)
├── risk/           # ATR 손절 / Trailing / Kelly
├── alert/          # Telegram / Discord 알림
├── ai/             # AI 통합 100점 스코어
├── ui/             # PySide6 UI 전체
│   ├── main_window.py       # 메인 윈도우
│   ├── chart_widget.py      # 고성능 캔들 차트
│   ├── footprint_widget.py  # Footprint 차트
│   ├── heatmap_widget.py    # 유동성 히트맵
│   ├── scanner_widget.py    # 스캐너 패널
│   ├── time_sales.py        # Time & Sales
│   ├── drawing_tools.py     # 드로잉 도구
│   ├── multi_chart.py       # 멀티차트
│   └── settings_dialog.py   # 설정 다이얼로그
└── tests/          # 단위 테스트 (121개)
```

---

## 데이터 흐름

```
OKX WebSocket
    ↓ tick (169,000 ticks/sec)
FootprintEngine  →  EventBus("footprint")
    ↓ bar_close
MarketScanner    →  EventBus("scanner_signal")  →  AlertManager
ICTEngine (15분 주기)
    ↓
AIScoreEngine    →  EventBus("strategy_signal") →  AlertManager
    ↓
DataStorage (SQLite / PostgreSQL)
```

모든 모듈은 **EventBus**를 통해 통신 → 직접 참조 없음 → SOLID 준수.

---

## 성능

| 항목 | 결과 | 목표 |
|------|------|------|
| 틱 처리 속도 | **169,000 ticks/sec** | 100,000 |
| Numba EMA (1000봉) | **< 1ms** | 5ms |
| ICT 분석 (200봉) | **< 30ms** | 100ms |
| UI 렌더링 | **60FPS** (OpenGL) | 60FPS |
| 단위 테스트 | **121개 통과** | - |

---

## 거래소 추가 방법

`exchange/base.py`의 `BaseExchange`를 구현하면 됩니다:

```python
from exchange.base import BaseExchange

class BinanceExchange(BaseExchange):
    @property
    def name(self) -> str:
        return "Binance"

    async def get_candles(self, symbol, timeframe, limit=300, after=None):
        # Binance API 구현
        ...
```

---

## 테스트

```bash
# 전체 테스트
python -m pytest tests/ -v

# 성능 벤치마크만
python -m pytest tests/test_performance.py -v

# 특정 모듈
python -m pytest tests/test_footprint.py -v
```

---

## 라이선스

MIT License

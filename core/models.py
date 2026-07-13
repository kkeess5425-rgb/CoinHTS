"""
core/models.py
==============
시스템 전체에서 사용하는 공통 데이터 모델.
msgspec.Struct 기반으로 직렬화/역직렬화 성능을 극대화하고,
Pydantic 없이도 타입 안전성을 보장한다.
"""
from __future__ import annotations

from enum import Enum, auto
from typing import Optional
import msgspec


# ── 열거형 ──────────────────────────────────────────────
class Exchange(str, Enum):
    OKX     = "okx"
    BINANCE = "binance"
    BYBIT   = "bybit"


class InstType(str, Enum):
    SPOT   = "SPOT"
    SWAP   = "SWAP"    # 무기한 선물
    FUTURES = "FUTURES"
    OPTION = "OPTION"


class Side(str, Enum):
    BUY  = "buy"
    SELL = "sell"


class Timeframe(str, Enum):
    S1  = "1s"
    S5  = "5s"
    S15 = "15s"
    S30 = "30s"
    M1  = "1m"
    M3  = "3m"
    M5  = "5m"
    M15 = "15m"
    H1  = "1H"
    H4  = "4H"
    D1  = "1D"

    @property
    def seconds(self) -> int:
        mapping = {
            "1s": 1, "5s": 5, "15s": 15, "30s": 30,
            "1m": 60, "3m": 180, "5m": 300, "15m": 900,
            "1H": 3600, "4H": 14400, "1D": 86400,
        }
        return mapping[self.value]

    def to_okx(self) -> str:
        """OKX API bar 파라미터 변환"""
        mapping = {
            "1s":"1s","5s":"5s","15s":"15s","30s":"30s",
            "1m":"1m","3m":"3m","5m":"5m","15m":"15m",
            "1H":"1H","4H":"4H","1D":"1D",
        }
        return mapping[self.value]


# ── 기본 틱 (최소 단위 체결) ─────────────────────────────
class Tick(msgspec.Struct, gc=False):
    """
    단일 체결(거래) 데이터.
    gc=False: GC 오버헤드 제거 → 100K ticks/sec 목표 달성용.
    """
    ts:     float   # 타임스탬프 (Unix epoch, 초 단위)
    price:  float
    size:   float
    side:   Side
    symbol: str


# ── 캔들(OHLCV) ─────────────────────────────────────────
class Candle(msgspec.Struct, gc=False):
    ts:        float   # 봉 시작 타임스탬프
    open:      float
    high:      float
    low:       float
    close:     float
    volume:    float
    symbol:    str
    timeframe: Timeframe
    confirmed: bool = False   # OKX: 마감된 봉인지 여부


# ── 호가(오더북 레벨) ────────────────────────────────────
class BookLevel(msgspec.Struct, gc=False):
    price: float
    size:  float


class OrderBook(msgspec.Struct):
    symbol:    str
    ts:        float
    bids:      list[BookLevel]   # 가격 내림차순
    asks:      list[BookLevel]   # 가격 오름차순
    checksum:  Optional[int] = None


# ── Footprint 셀 ─────────────────────────────────────────
class FootprintCell(msgspec.Struct, gc=False):
    """가격 레벨별 매수/매도 체결 누적"""
    price:    float
    buy_vol:  float = 0.0
    sell_vol: float = 0.0

    @property
    def delta(self) -> float:
        return self.buy_vol - self.sell_vol

    @property
    def total(self) -> float:
        return self.buy_vol + self.sell_vol

    @property
    def imbalance_ratio(self) -> float:
        """매수/매도 비율 (sell > 0이어야 의미있음)"""
        if self.sell_vol < 1e-9:
            return float('inf')
        return self.buy_vol / self.sell_vol


# ── Footprint 봉 ────────────────────────────────────────
class FootprintBar(msgspec.Struct):
    """캔들 1개에 대응하는 Footprint 데이터"""
    candle:    Candle
    cells:     list[FootprintCell]   # 가격 오름차순
    delta:     float = 0.0           # 전체 델타
    cvd:       float = 0.0           # 누적 볼륨 델타
    poc:       Optional[float] = None  # Point of Control


# ── Volume Profile ───────────────────────────────────────
class VolumeProfileLevel(msgspec.Struct, gc=False):
    price:    float
    buy_vol:  float
    sell_vol: float

    @property
    def total(self) -> float:
        return self.buy_vol + self.sell_vol


class VolumeProfile(msgspec.Struct):
    symbol:    str
    levels:    list[VolumeProfileLevel]
    poc:       float   # Point of Control
    vah:       float   # Value Area High (70%)
    val:       float   # Value Area Low (70%)
    total_vol: float


# ── Open Interest / Funding ──────────────────────────────
class OIData(msgspec.Struct):
    symbol:     str
    ts:         float
    oi:         float    # 미결제약정 (계약 수)
    oi_ccy:     float    # 미결제약정 (기초자산 단위)


class FundingData(msgspec.Struct):
    symbol:          str
    ts:              float
    funding_rate:    float
    next_rate:       Optional[float] = None
    funding_time:    Optional[float] = None


class LiquidationData(msgspec.Struct):
    symbol:    str
    ts:        float
    side:      Side
    price:     float
    size:      float


# ── 스캐너 신호 ──────────────────────────────────────────
class ScannerSignal(msgspec.Struct):
    symbol:    str
    ts:        float
    signal_type: str     # "VOLUME_SPIKE", "OI_SURGE", "DELTA_BURST" 등
    value:     float
    threshold: float
    message:   str


# ── 전략 신호 ────────────────────────────────────────────
class StrategySignal(msgspec.Struct):
    symbol:    str
    ts:        float
    direction: str       # "LONG" / "SHORT"
    score:     float     # 0~100점
    entry:     float
    sl:        float
    tp:        float
    reasons:   list[str]
    exchange:  Exchange = Exchange.OKX

    @property
    def rr(self) -> float:
        risk = abs(self.entry - self.sl)
        reward = abs(self.tp - self.entry)
        return reward / risk if risk > 0 else 0.0


# ── 심볼 정보 ────────────────────────────────────────────
class SymbolInfo(msgspec.Struct):
    symbol:     str
    base:       str      # BTC
    quote:      str      # USDT
    inst_type:  InstType
    exchange:   Exchange
    tick_size:  float    # 최소 가격 단위
    lot_size:   float    # 최소 수량 단위
    min_size:   float    # 최소 주문 크기

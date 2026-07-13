"""
exchange/base.py
================
거래소 추상 인터페이스.
OKX, Binance, Bybit 모두 이 인터페이스를 구현하여
상위 레이어(strategy, ui 등)가 거래소에 종속되지 않도록 한다.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator, Optional

from core.models import (
    Candle, OIData, FundingData, LiquidationData,
    SymbolInfo, Timeframe
)


class BaseExchange(ABC):
    """모든 거래소 클라이언트가 구현해야 하는 인터페이스."""

    # ── REST API ──────────────────────────────────────
    @abstractmethod
    async def get_candles(
        self,
        symbol: str,
        timeframe: Timeframe,
        limit: int = 300,
        after: Optional[int] = None,   # 이 ts 이전 데이터 (ms)
    ) -> list[Candle]:
        """과거 캔들 데이터 조회."""
        ...

    @abstractmethod
    async def get_symbols(self) -> list[SymbolInfo]:
        """거래 가능한 심볼 목록 조회."""
        ...

    @abstractmethod
    async def get_oi(self, symbol: str) -> OIData:
        """미결제약정 조회."""
        ...

    @abstractmethod
    async def get_funding(self, symbol: str) -> FundingData:
        """펀딩비 조회."""
        ...

    @abstractmethod
    async def get_liquidations(
        self, symbol: str, limit: int = 100
    ) -> list[LiquidationData]:
        """최근 청산 데이터 조회."""
        ...

    # ── 상태 ──────────────────────────────────────────
    @property
    @abstractmethod
    def name(self) -> str:
        """거래소 이름."""
        ...

    @abstractmethod
    async def ping(self) -> bool:
        """거래소 서버 연결 확인."""
        ...

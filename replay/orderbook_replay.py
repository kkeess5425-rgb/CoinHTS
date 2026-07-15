"""
replay/orderbook_replay.py
==========================
OrderBook 리플레이 엔진.
DOM 히스토리를 재생해서 과거 호가창 변화를 분석한다.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional, Callable

from core.events import EventBus, get_event_bus
from core.models import OrderBook, BookLevel
from orderbook.analyzer import DOMSnapshot, OrderBookAnalyzer

logger = logging.getLogger(__name__)


@dataclass
class OrderBookReplayConfig:
    speed:      float = 1.0       # 1x ~ 100x
    start_ts:   float = 0.0
    end_ts:     float = 0.0


class OrderBookReplayEngine:
    """
    오더북 DOM 히스토리 리플레이.
    과거 스냅샷 시퀀스를 실시간처럼 재생한다.
    """

    def __init__(
        self,
        config:    Optional[OrderBookReplayConfig] = None,
        event_bus: Optional[EventBus] = None,
    ) -> None:
        self.cfg      = config or OrderBookReplayConfig()
        self._bus     = event_bus or get_event_bus()
        self._snapshots: list[DOMSnapshot] = []
        self._cur_idx:   int  = 0
        self._running:   bool = False
        self._analyzer   = OrderBookAnalyzer()

        self.on_snapshot: Optional[Callable[[DOMSnapshot], None]] = None
        self.on_progress: Optional[Callable[[float], None]] = None

    def load(self, snapshots: list[DOMSnapshot]) -> None:
        """DOM 스냅샷 시퀀스 로드."""
        filtered = snapshots
        if self.cfg.start_ts:
            filtered = [s for s in filtered if s.ts >= self.cfg.start_ts]
        if self.cfg.end_ts:
            filtered = [s for s in filtered if s.ts <= self.cfg.end_ts]
        self._snapshots = sorted(filtered, key=lambda s: s.ts)
        self._cur_idx   = 0
        logger.info(f"[OB Replay] {len(self._snapshots)}개 스냅샷 로드")

    async def start(self) -> None:
        """리플레이 시작."""
        self._running = True
        await self._loop()

    async def stop(self) -> None:
        self._running = False

    def seek(self, ts: float) -> None:
        """특정 시점으로 이동."""
        idx = next((i for i, s in enumerate(self._snapshots) if s.ts >= ts), 0)
        self._cur_idx = idx
        self._analyzer = OrderBookAnalyzer()

    def set_speed(self, speed: float) -> None:
        self.cfg.speed = max(0.1, min(100.0, speed))

    async def _loop(self) -> None:
        n = len(self._snapshots)
        if not n:
            return
        prev_ts = self._snapshots[self._cur_idx].ts

        while self._cur_idx < n and self._running:
            snap = self._snapshots[self._cur_idx]
            dt   = (snap.ts - prev_ts) / self.cfg.speed
            if 0 < dt < 2.0:
                await asyncio.sleep(dt)

            # 스냅샷을 OrderBook으로 변환해서 분석
            book = self._snapshot_to_orderbook(snap)
            self._analyzer.on_orderbook(book)

            if self.on_snapshot:
                self.on_snapshot(snap)
            await self._bus.publish("replay_orderbook", snap)

            prev_ts = snap.ts
            self._cur_idx += 1

            if self.on_progress and self._cur_idx % 10 == 0:
                self.on_progress(self._cur_idx / n * 100)

        logger.info(f"[OB Replay] 완료: {self._cur_idx}개 재생")

    @staticmethod
    def _snapshot_to_orderbook(snap: DOMSnapshot) -> OrderBook:
        return OrderBook(
            symbol="REPLAY",
            ts=snap.ts,
            bids=[BookLevel(price=p, size=s) for p, s in snap.bids],
            asks=[BookLevel(price=p, size=s) for p, s in snap.asks],
        )

    @property
    def analyzer(self) -> OrderBookAnalyzer:
        return self._analyzer

    @property
    def progress(self) -> float:
        n = len(self._snapshots)
        return self._cur_idx / max(n, 1) * 100

    @property
    def current_ts(self) -> float:
        if self._cur_idx < len(self._snapshots):
            return self._snapshots[self._cur_idx].ts
        return 0.0

"""
replay/replay_engine.py
=======================
틱 리플레이 엔진.
저장된 틱 데이터를 실시간처럼 재생해서 전략을 검증한다.
배속(1x~100x) 지원, 일시정지/재개 가능.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional

import polars as pl

from core.events import EventBus, get_event_bus
from core.models import Side, Tick
from database.storage import DataStorage
from orderflow.footprint import FootprintEngine
from strategy.ict_engine import ICTEngine

logger = logging.getLogger(__name__)


@dataclass
class ReplayStats:
    """리플레이 통계."""
    total_ticks:    int   = 0
    elapsed_secs:   float = 0.0
    current_price:  float = 0.0
    ticks_per_sec:  float = 0.0
    progress_pct:   float = 0.0


class ReplayEngine:
    """
    틱 데이터 리플레이 엔진.
    DB에서 틱을 불러와 EventBus로 발행하여
    실시간과 동일한 처리 흐름으로 전략을 검증한다.
    """

    def __init__(
        self,
        storage:    DataStorage,
        event_bus:  Optional[EventBus] = None,
        speed:      float = 1.0,         # 재생 배속 (1.0=실시간, 10.0=10배속)
    ) -> None:
        self._storage  = storage
        self._bus      = event_bus or get_event_bus()
        self._speed    = speed
        self._paused   = False
        self._stopped  = False
        self._stats    = ReplayStats()

    @property
    def stats(self) -> ReplayStats:
        return self._stats

    def pause(self)  -> None: self._paused = True
    def resume(self) -> None: self._paused = False
    def stop(self)   -> None: self._stopped = True

    def set_speed(self, speed: float) -> None:
        self._speed = max(0.1, min(speed, 1000.0))

    async def replay(
        self,
        symbol:   str,
        since_ts: float,
        until_ts: Optional[float] = None,
        chunk:    int = 10_000,
    ) -> ReplayStats:
        """
        틱 리플레이 실행.
        since_ts ~ until_ts 구간의 틱을 순서대로 재생.
        """
        logger.info(f"[Replay] {symbol} 리플레이 시작 (배속 {self._speed}x)")
        t_start = time.time()
        prev_ts: Optional[float] = None
        total   = 0

        df = await self._storage.get_ticks(symbol, since_ts, until_ts, limit=chunk)
        if df.is_empty():
            logger.warning("[Replay] 틱 데이터 없음")
            return self._stats

        total_rows = len(df)

        for row in df.iter_rows(named=True):
            if self._stopped:
                break
            while self._paused:
                await asyncio.sleep(0.05)

            tick = Tick(
                ts=     row["ts"],
                price=  row["price"],
                size=   row["size"],
                side=   Side.BUY if row["side"] == "buy" else Side.SELL,
                symbol= symbol,
            )

            # 실시간 타이밍 시뮬레이션
            if prev_ts is not None and self._speed < 900:
                gap = (tick.ts - prev_ts) / self._speed
                if 0 < gap < 5.0:
                    await asyncio.sleep(gap)

            # 이벤트 발행
            await self._bus.publish("tick", tick)
            prev_ts = tick.ts
            total  += 1

            # 통계 업데이트 (1000틱마다)
            if total % 1000 == 0:
                elapsed = time.time() - t_start
                self._stats = ReplayStats(
                    total_ticks=   total,
                    elapsed_secs=  elapsed,
                    current_price= tick.price,
                    ticks_per_sec= total / elapsed if elapsed > 0 else 0,
                    progress_pct=  total / total_rows * 100,
                )
                logger.debug(f"[Replay] {total}/{total_rows} ticks | {self._stats.ticks_per_sec:.0f} t/s")

        elapsed = time.time() - t_start
        self._stats = ReplayStats(
            total_ticks=   total,
            elapsed_secs=  elapsed,
            current_price= prev_ts or 0,
            ticks_per_sec= total / elapsed if elapsed > 0 else 0,
            progress_pct=  100.0,
        )
        logger.info(f"[Replay] 완료: {total}틱 / {elapsed:.1f}초 / {self._stats.ticks_per_sec:.0f} t/s")
        return self._stats

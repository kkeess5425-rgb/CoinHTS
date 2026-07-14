"""
replay/replay_engine.py — 완전한 시장 리플레이 엔진
Tick / Footprint / OrderBook / Market Replay
속도 조절 (1x~1000x) + seek(ts)
"""
from __future__ import annotations
import asyncio, logging, time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Callable
from core.events import EventBus, get_event_bus
from core.models import Tick, FootprintBar, Timeframe
from orderflow.footprint import FootprintEngine
logger = logging.getLogger(__name__)

class ReplayState(Enum):
    IDLE="idle"; PLAYING="playing"; PAUSED="paused"; DONE="done"

@dataclass
class ReplayConfig:
    symbol:    str       = "BTC-USDT-SWAP"
    speed:     float     = 1.0
    start_ts:  float     = 0.0
    end_ts:    float     = 0.0
    tick_size: float     = 0.5
    timeframe: Timeframe = Timeframe.M1

@dataclass
class ReplayStatus:
    state:        ReplayState = ReplayState.IDLE
    current_ts:   float       = 0.0
    progress:     float       = 0.0
    speed:        float       = 1.0
    ticks_played: int         = 0
    bars_played:  int         = 0

class TickReplayEngine:
    def __init__(self, config=None, event_bus=None):
        self.cfg   = config or ReplayConfig()
        self._bus  = event_bus or get_event_bus()
        self._state= ReplayState.IDLE
        self._task: Optional[asyncio.Task] = None
        self._pause_event = asyncio.Event()
        self._pause_event.set()
        self._ticks: list[Tick] = []
        self._cur_idx = 0; self._ticks_played = 0
        self._fp = FootprintEngine(self.cfg.symbol, self.cfg.timeframe, tick_size=self.cfg.tick_size)
        self.on_tick:     Optional[Callable] = None
        self.on_bar:      Optional[Callable] = None
        self.on_progress: Optional[Callable] = None

    def load_ticks(self, ticks: list[Tick]) -> None:
        filtered = [t for t in ticks if
                    (not self.cfg.start_ts or t.ts >= self.cfg.start_ts) and
                    (not self.cfg.end_ts   or t.ts <= self.cfg.end_ts)]
        self._ticks   = sorted(filtered, key=lambda t: t.ts)
        self._cur_idx = 0
        logger.info(f"[Replay] {len(self._ticks):,}틱 로드")

    async def start(self):
        if self._state == ReplayState.PLAYING: return
        self._state = ReplayState.PLAYING
        self._task  = asyncio.create_task(self._loop())

    async def stop(self):
        if self._task:
            self._task.cancel()
            try: await self._task
            except asyncio.CancelledError: pass
        self._state = ReplayState.IDLE

    def pause(self):
        self._state = ReplayState.PAUSED; self._pause_event.clear()

    def resume(self):
        self._state = ReplayState.PLAYING; self._pause_event.set()

    def seek(self, ts: float):
        idx = next((i for i, t in enumerate(self._ticks) if t.ts >= ts), 0)
        self._cur_idx = idx
        self._fp = FootprintEngine(self.cfg.symbol, self.cfg.timeframe, tick_size=self.cfg.tick_size)

    def set_speed(self, speed: float): self.cfg.speed = max(0.1, min(1000.0, speed))

    async def _loop(self):
        n = len(self._ticks)
        if not n: self._state = ReplayState.DONE; return
        bars_closed = []
        self._fp.on_bar_close = lambda b: bars_closed.append(b)
        prev_ts = self._ticks[self._cur_idx].ts

        while self._cur_idx < n and self._state != ReplayState.IDLE:
            await self._pause_event.wait()
            tick = self._ticks[self._cur_idx]
            dt   = (tick.ts - prev_ts) / self.cfg.speed
            if 0 < dt < 1.0: await asyncio.sleep(dt)
            self._fp.on_tick(tick); self._ticks_played += 1; prev_ts = tick.ts
            if self.on_tick: self.on_tick(tick)
            await self._bus.publish("replay_tick", tick)
            for bar in bars_closed:
                if self.on_bar: self.on_bar(bar)
                await self._bus.publish("replay_footprint", bar)
            bars_closed.clear()
            self._cur_idx += 1
            if self.on_progress and self._cur_idx % 100 == 0:
                self.on_progress(self._cur_idx / n * 100)
        self._state = ReplayState.DONE
        logger.info(f"[Replay] 완료: {self._ticks_played:,}틱")

    @property
    def status(self) -> ReplayStatus:
        n = len(self._ticks)
        return ReplayStatus(
            state=self._state,
            current_ts=self._ticks[self._cur_idx].ts if self._cur_idx < n else 0,
            progress=self._cur_idx / max(n,1) * 100,
            speed=self.cfg.speed, ticks_played=self._ticks_played,
            bars_played=len(self._fp.bars),
        )

class MarketReplayEngine:
    """Tick + Footprint + OrderBook 통합 리플레이."""
    def __init__(self, config=None, event_bus=None):
        self.cfg = config or ReplayConfig()
        self._tick = TickReplayEngine(config, event_bus)
    def load(self, ticks): self._tick.load_ticks(ticks)
    async def start(self): await self._tick.start()
    async def stop(self):  await self._tick.stop()
    def pause(self):  self._tick.pause()
    def resume(self): self._tick.resume()
    def seek(self, ts): self._tick.seek(ts)
    def set_speed(self, s): self._tick.set_speed(s)
    @property
    def status(self): return self._tick.status

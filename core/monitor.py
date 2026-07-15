"""
core/monitor.py
===============
시스템 성능 모니터.

실시간 수집:
- CPU / 메모리 / 디스크 사용률
- 틱 처리 속도 (ticks/sec)
- WebSocket 지연 (latency)
- 이벤트 큐 크기
- 활성 태스크 수
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class SystemSnapshot:
    """시스템 스냅샷."""
    ts:           float
    cpu_pct:      float = 0.0
    mem_pct:      float = 0.0
    mem_used_mb:  float = 0.0
    disk_pct:     float = 0.0
    ticks_per_sec:float = 0.0
    ws_latency_ms:float = 0.0
    active_tasks: int   = 0
    event_queue:  int   = 0


@dataclass
class PerformanceStats:
    """누적 성능 통계."""
    avg_cpu:       float = 0.0
    max_cpu:       float = 0.0
    avg_mem:       float = 0.0
    max_mem:       float = 0.0
    avg_tps:       float = 0.0
    peak_tps:      float = 0.0
    avg_latency:   float = 0.0
    uptime_sec:    float = 0.0
    total_ticks:   int   = 0
    error_count:   int   = 0


class SystemMonitor:
    """
    실시간 시스템 성능 모니터.
    psutil로 CPU/메모리를 수집하고 틱 처리 속도를 측정한다.
    """

    def __init__(
        self,
        history_len:  int   = 300,   # 5분 (1초 간격)
        interval:     float = 1.0,   # 수집 간격 (초)
    ) -> None:
        self._interval     = interval
        self._history:     deque[SystemSnapshot] = deque(maxlen=history_len)
        self._start_ts     = time.time()
        self._total_ticks  = 0
        self._tick_window: deque[float] = deque(maxlen=10000)  # 최근 틱 타임스탬프
        self._error_count  = 0
        self._task:        Optional[asyncio.Task] = None
        self._psutil_ok    = False

        try:
            import psutil
            self._psutil_ok = True
        except ImportError:
            logger.info("[Monitor] psutil 없음 — pip install psutil")

    # ── 외부 이벤트 ──────────────────────────────────
    def on_tick(self) -> None:
        """틱 수신 시 호출."""
        self._total_ticks += 1
        self._tick_window.append(time.time())

    def on_error(self) -> None:
        """오류 발생 시 호출."""
        self._error_count += 1

    def on_ws_latency(self, latency_ms: float) -> None:
        """WebSocket 왕복 지연 기록."""
        if self._history:
            self._history[-1].ws_latency_ms = latency_ms

    # ── 루프 ─────────────────────────────────────────
    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try: await self._task
            except asyncio.CancelledError: pass

    async def _loop(self) -> None:
        while True:
            snap = await asyncio.get_event_loop().run_in_executor(
                None, self._collect
            )
            self._history.append(snap)
            await asyncio.sleep(self._interval)

    def _collect(self) -> SystemSnapshot:
        """스냅샷 수집 (동기)."""
        now = time.time()
        snap = SystemSnapshot(ts=now)

        # psutil
        if self._psutil_ok:
            try:
                import psutil
                snap.cpu_pct     = psutil.cpu_percent(interval=None)
                mem              = psutil.virtual_memory()
                snap.mem_pct     = mem.percent
                snap.mem_used_mb = mem.used / 1024 / 1024
                disk             = psutil.disk_usage("/")
                snap.disk_pct    = disk.percent
            except Exception:
                pass

        # 틱 처리 속도 (최근 1초)
        cutoff = now - 1.0
        recent_ticks = sum(1 for t in self._tick_window if t >= cutoff)
        snap.ticks_per_sec = float(recent_ticks)

        # 활성 태스크 수
        try:
            snap.active_tasks = len([t for t in asyncio.all_tasks() if not t.done()])
        except RuntimeError:
            snap.active_tasks = 0

        return snap

    # ── 조회 ─────────────────────────────────────────
    @property
    def latest(self) -> Optional[SystemSnapshot]:
        return self._history[-1] if self._history else None

    @property
    def stats(self) -> PerformanceStats:
        snaps = list(self._history)
        if not snaps:
            return PerformanceStats()

        cpus  = [s.cpu_pct     for s in snaps]
        mems  = [s.mem_pct     for s in snaps]
        tps   = [s.ticks_per_sec for s in snaps]
        lats  = [s.ws_latency_ms for s in snaps if s.ws_latency_ms > 0]

        return PerformanceStats(
            avg_cpu=      round(sum(cpus) / len(cpus), 1)   if cpus else 0.0,
            max_cpu=      round(max(cpus), 1)                if cpus else 0.0,
            avg_mem=      round(sum(mems) / len(mems), 1)   if mems else 0.0,
            max_mem=      round(max(mems), 1)                if mems else 0.0,
            avg_tps=      round(sum(tps)  / len(tps), 0)    if tps  else 0.0,
            peak_tps=     round(max(tps), 0)                 if tps  else 0.0,
            avg_latency=  round(sum(lats) / len(lats), 1)   if lats else 0.0,
            uptime_sec=   round(time.time() - self._start_ts, 0),
            total_ticks=  self._total_ticks,    # 스냅샷 관계없이 직접 누적
            error_count=  self._error_count,
        )

    def summary(self) -> str:
        s = self.stats
        uptime = f"{int(s.uptime_sec//3600)}h {int((s.uptime_sec%3600)//60)}m"
        return (
            f"⏱ 가동: {uptime} | "
            f"CPU: {s.avg_cpu:.0f}% | "
            f"MEM: {s.avg_mem:.0f}% | "
            f"TPS: {s.avg_tps:.0f} | "
            f"틱: {s.total_ticks:,} | "
            f"오류: {s.error_count}"
        )

    def get_history_dict(self, n: int = 60) -> dict:
        """히스토리를 dict 리스트로 반환 (웹 API용)."""
        snaps = list(self._history)[-n:]
        return {
            "cpu":     [s.cpu_pct      for s in snaps],
            "mem":     [s.mem_pct      for s in snaps],
            "tps":     [s.ticks_per_sec for s in snaps],
            "latency": [s.ws_latency_ms for s in snaps],
            "ts":      [s.ts            for s in snaps],
        }

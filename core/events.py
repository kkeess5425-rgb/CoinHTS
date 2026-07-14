"""
core/events.py
==============
비동기 이벤트 버스.
모듈 간 직접 참조 없이 Pub/Sub 패턴으로 통신한다.
구독자는 asyncio coroutine 또는 일반 callable 모두 지원.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)


class EventBus:
    """
    전역 이벤트 버스.
    tick, candle, orderbook, signal 등 모든 데이터 흐름의 중심.

    사용 예:
        bus = EventBus()
        bus.subscribe("tick", handler)
        await bus.publish("tick", tick_data)
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[Callable]] = defaultdict(list)
        self._loop: asyncio.AbstractEventLoop | None = None

    # ── 구독 ──────────────────────────────────────────
    def subscribe(self, event: str, handler: Callable) -> None:
        """이벤트 구독. handler는 async def 또는 일반 함수 모두 가능."""
        self._subscribers[event].append(handler)

    def unsubscribe(self, event: str, handler: Callable) -> None:
        """구독 해제."""
        try:
            self._subscribers[event].remove(handler)
        except ValueError:
            pass

    # ── 발행 ──────────────────────────────────────────
    async def publish(self, event: str, data: Any = None) -> None:
        """
        이벤트 발행. 구독자를 순서대로 호출한다.
        async 핸들러는 await, 일반 함수는 직접 호출.
        핸들러 예외는 로그만 남기고 다음 핸들러로 계속 진행.
        """
        for handler in self._subscribers.get(event, []):
            try:
                result = handler(data)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.exception(f"[EventBus] {event} 핸들러 오류: {handler.__name__}: {e}")

    def publish_nowait(self, event: str, data: Any = None) -> None:
        """
        동기 컨텍스트에서 이벤트 발행 (fire-and-forget).
        이벤트 루프가 실행 중이어야 한다.
        """
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.publish(event, data))
        except RuntimeError:
            # 이벤트 루프가 없는 경우 (테스트 등) — 무시
            pass

    # ── 이벤트 이름 상수 ─────────────────────────────
    class Events:
        # 시세 데이터
        TICK        = "tick"
        CANDLE      = "candle"
        ORDERBOOK   = "orderbook"

        # 오더플로우
        FOOTPRINT   = "footprint"
        DELTA       = "delta"
        CVD         = "cvd"

        # 시장 데이터
        OI          = "oi"
        FUNDING     = "funding"
        LIQUIDATION = "liquidation"

        # 신호
        STRATEGY_SIGNAL = "strategy_signal"
        SCANNER_SIGNAL  = "scanner_signal"
        ALERT           = "alert"

        # 연결 상태
        CONNECTED    = "connected"
        DISCONNECTED = "disconnected"
        ERROR        = "error"


# 전역 싱글턴
_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus

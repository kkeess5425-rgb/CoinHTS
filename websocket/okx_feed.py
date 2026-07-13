"""
websocket/okx_feed.py
=====================
OKX WebSocket 실시간 데이터 피드.
- trades 채널: 체결(틱) 데이터
- books 채널: 전구도 오더북 (최대 400단계)
- 자동 재연결 (지수 백오프)
- msgspec 기반 고성능 파싱
- EventBus로 데이터 전파
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import zlib
from typing import Optional

import websockets

from core.events import EventBus, get_event_bus
from core.models import BookLevel, OrderBook, Side, Tick

logger = logging.getLogger(__name__)

OKX_WS_PUBLIC = "wss://ws.okx.com:8443/ws/v5/public"


class OrderBookState:
    """
    오더북 증분 업데이트 상태 관리.
    CRC32 체크섬 검증으로 데이터 무결성 보장.
    """
    __slots__ = ("bids", "asks", "_checksum")

    def __init__(self) -> None:
        self.bids: dict[float, float] = {}   # price → size
        self.asks: dict[float, float] = {}
        self._checksum: Optional[int] = None

    def apply_snapshot(self, data: dict) -> None:
        self.bids.clear()
        self.asks.clear()
        for price, size, *_ in data.get("bids", []):
            p, s = float(price), float(size)
            if s > 0: self.bids[p] = s
        for price, size, *_ in data.get("asks", []):
            p, s = float(price), float(size)
            if s > 0: self.asks[p] = s

    def apply_update(self, data: dict) -> None:
        for price, size, *_ in data.get("bids", []):
            p, s = float(price), float(size)
            if s == 0: self.bids.pop(p, None)
            else:      self.bids[p] = s
        for price, size, *_ in data.get("asks", []):
            p, s = float(price), float(size)
            if s == 0: self.asks.pop(p, None)
            else:      self.asks[p] = s

    def verify_checksum(self, expected: int) -> bool:
        """OKX CRC32 체크섬 검증."""
        # 상위 25개 bid/ask 가격을 "bid:ask:bid:ask:..." 형태로 결합
        bids_sorted = sorted(self.bids.items(), reverse=True)[:25]
        asks_sorted = sorted(self.asks.items())[:25]
        parts = []
        for (bp, bs), (ap, as_) in zip(bids_sorted, asks_sorted):
            parts += [f"{bp}:{bs}", f"{ap}:{as_}"]
        checksum_str = ":".join(parts)
        actual = zlib.crc32(checksum_str.encode()) & 0xFFFFFFFF
        # OKX가 보내는 체크섬은 signed int32
        expected_unsigned = expected & 0xFFFFFFFF
        return actual == expected_unsigned

    def to_book(self, symbol: str, ts: float, depth: int = 20) -> OrderBook:
        bids = [BookLevel(p, s) for p, s in sorted(self.bids.items(), reverse=True)[:depth]]
        asks = [BookLevel(p, s) for p, s in sorted(self.asks.items())[:depth]]
        return OrderBook(symbol=symbol, ts=ts, bids=bids, asks=asks)


class OKXWebSocketFeed:
    """
    OKX WebSocket 피드.
    여러 심볼의 trades + books를 동시에 구독한다.
    """

    def __init__(
        self,
        symbols:   list[str],
        event_bus: Optional[EventBus] = None,
        depth:     int = 400,
    ) -> None:
        self.symbols    = symbols
        self.bus        = event_bus or get_event_bus()
        self.depth      = depth
        self._books:    dict[str, OrderBookState] = {s: OrderBookState() for s in symbols}
        self._running   = False
        self._ws        = None
        self._reconnect_delay = 1.0

        # 성능 카운터
        self._tick_count  = 0
        self._last_stats  = time.time()

    async def start(self) -> None:
        """피드 시작 (태스크로 실행)."""
        self._running = True
        while self._running:
            try:
                await self._connect()
            except Exception as e:
                logger.warning(f"[WS] 연결 오류: {e}, {self._reconnect_delay:.1f}초 후 재시도")
                await self.bus.publish("disconnected", {"reason": str(e)})
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(self._reconnect_delay * 2, 60.0)
            else:
                self._reconnect_delay = 1.0

    async def stop(self) -> None:
        self._running = False
        if self._ws:
            await self._ws.close()

    async def _connect(self) -> None:
        logger.info(f"[WS] OKX 연결 중... {self.symbols}")
        async with websockets.connect(
            OKX_WS_PUBLIC,
            ping_interval=20,
            ping_timeout=10,
            max_size=10 * 1024 * 1024,   # 10MB (400단계 오더북)
        ) as ws:
            self._ws = ws
            await self._subscribe(ws)
            await self.bus.publish("connected", {"exchange": "OKX"})
            logger.info("[WS] OKX 연결 완료")

            async for raw in ws:
                await self._dispatch(raw)

    async def _subscribe(self, ws) -> None:
        """trades + books 채널 구독."""
        args = []
        for sym in self.symbols:
            args.append({"channel": "trades",            "instId": sym})
            args.append({"channel": f"books{self.depth}", "instId": sym})
        await ws.send(json.dumps({"op": "subscribe", "args": args}))

    async def _dispatch(self, raw: str) -> None:
        """수신된 메시지 파싱 및 이벤트 발행."""
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        event = msg.get("event")
        if event in ("subscribe", "unsubscribe", "error"):
            if event == "error":
                logger.error(f"[WS] 오류: {msg}")
            return

        arg  = msg.get("arg", {})
        data = msg.get("data", [])
        channel = arg.get("channel", "")
        symbol  = arg.get("instId", "")

        if channel == "trades":
            await self._handle_trades(symbol, data)
        elif channel.startswith("books"):
            action = msg.get("action", "snapshot")
            await self._handle_books(symbol, data, action)

    # ── trades ────────────────────────────────────────
    async def _handle_trades(self, symbol: str, data: list) -> None:
        for item in data:
            try:
                tick = Tick(
                    ts=     int(item["ts"]) / 1000.0,
                    price=  float(item["px"]),
                    size=   float(item["sz"]),
                    side=   Side.BUY if item["side"] == "buy" else Side.SELL,
                    symbol= symbol,
                )
                await self.bus.publish("tick", tick)
                self._tick_count += 1
            except (KeyError, ValueError) as e:
                logger.debug(f"[WS] 틱 파싱 오류: {e}")

        # 성능 통계 (10초마다)
        now = time.time()
        if now - self._last_stats >= 10:
            rate = self._tick_count / (now - self._last_stats)
            logger.debug(f"[WS] 틱 처리 속도: {rate:.0f} ticks/sec")
            self._tick_count = 0
            self._last_stats = now

    # ── books ─────────────────────────────────────────
    async def _handle_books(self, symbol: str, data: list, action: str) -> None:
        state = self._books.get(symbol)
        if state is None:
            return

        for item in data:
            ts = int(item.get("ts", 0)) / 1000.0
            if action == "snapshot":
                state.apply_snapshot(item)
            else:
                state.apply_update(item)

            # 체크섬 검증
            checksum = item.get("checksum")
            if checksum is not None and not state.verify_checksum(checksum):
                logger.warning(f"[WS] {symbol} 오더북 체크섬 불일치 → 재구독")
                await self._resubscribe(symbol)
                return

            book = state.to_book(symbol, ts, depth=20)
            await self.bus.publish("orderbook", book)

    async def _resubscribe(self, symbol: str) -> None:
        """특정 심볼 books 채널 재구독."""
        if self._ws is None:
            return
        channel = f"books{self.depth}"
        await self._ws.send(json.dumps({"op": "unsubscribe", "args": [{"channel": channel, "instId": symbol}]}))
        await asyncio.sleep(0.1)
        await self._ws.send(json.dumps({"op": "subscribe",   "args": [{"channel": channel, "instId": symbol}]}))
        self._books[symbol] = OrderBookState()

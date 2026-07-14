"""
orderbook/analyzer.py
=====================
오더북 고급 분석.

- Spoofing Detection  : 대형 주문 등장 후 빠른 취소
- Pulling / Stacking  : 호가 당기기/쌓기
- Whale Order 추적    : 대형 주문 감지 및 추적
- Order Book Imbalance: 매수/매도 잔량 불균형
- DOM 히스토리        : 실시간 호가 변화 기록
"""
from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from core.models import OrderBook, BookLevel

logger = logging.getLogger(__name__)


@dataclass
class SpoofingAlert:
    """스푸핑 감지 — 대형 주문 등장 후 빠른 소멸."""
    ts:        float
    price:     float
    side:      str    # "bid" | "ask"
    size:      float
    duration:  float  # 주문이 존재한 시간 (초)


@dataclass
class PullingAlert:
    """Pulling — 주문이 갑자기 사라짐."""
    ts:    float
    price: float
    side:  str
    size:  float  # 사라진 수량


@dataclass
class StackingAlert:
    """Stacking — 특정 레벨에 주문이 계속 쌓임."""
    ts:        float
    price:     float
    side:      str
    total_size: float  # 쌓인 총 수량


@dataclass
class WhaleOrder:
    """Whale Order — 대형 주문."""
    ts:    float
    price: float
    side:  str
    size:  float
    is_whale: bool  # 평균의 N배 이상


@dataclass
class OrderBookImbalance:
    """오더북 불균형."""
    ts:         float
    bid_vol:    float
    ask_vol:    float
    ratio:      float   # bid/ask (>1이면 매수 우세)
    imbalance:  str     # "bull" | "bear" | "neutral"


@dataclass
class DOMSnapshot:
    """DOM 스냅샷 — 특정 시점 호가창 상태."""
    ts:    float
    bids:  list[tuple[float, float]]  # (price, size)
    asks:  list[tuple[float, float]]
    mid:   float


class OrderBookAnalyzer:
    """
    실시간 오더북 분석기.
    연속적인 오더북 스냅샷을 비교해서 이상 패턴을 감지한다.
    """

    def __init__(
        self,
        spoof_min_size:     float = 10.0,    # 이 이상 크기만 스푸핑 감시
        spoof_max_duration: float = 3.0,     # 이 시간(초) 내 소멸 → 스푸핑
        whale_mult:         float = 20.0,    # 평균의 N배 이상 → 고래
        pull_threshold:     float = 0.5,     # 이 비율 이상 감소 → Pulling
        stack_threshold:    float = 2.0,     # 이 비율 이상 증가 → Stacking
        history_len:        int   = 100,     # DOM 히스토리 유지 수
    ) -> None:
        self.spoof_min_size     = spoof_min_size
        self.spoof_max_duration = spoof_max_duration
        self.whale_mult         = whale_mult
        self.pull_threshold     = pull_threshold
        self.stack_threshold    = stack_threshold

        # DOM 히스토리
        self._dom_history: deque[DOMSnapshot] = deque(maxlen=history_len)

        # 대형 주문 추적 (price → (size, first_seen_ts))
        self._large_orders: dict[float, tuple[float, float]] = {}

        # 레벨별 수량 누적 추적
        self._prev_book: Optional[OrderBook] = None
        self._avg_size:  float               = 1.0

        # 결과 버퍼
        self.spoofing_alerts: deque[SpoofingAlert]   = deque(maxlen=50)
        self.pulling_alerts:  deque[PullingAlert]    = deque(maxlen=50)
        self.stacking_alerts: deque[StackingAlert]   = deque(maxlen=50)
        self.whale_orders:    deque[WhaleOrder]      = deque(maxlen=100)
        self.imbalance_log:   deque[OrderBookImbalance] = deque(maxlen=200)

    def on_orderbook(self, book: OrderBook) -> dict:
        """오더북 업데이트 처리 → 각종 분석 실행."""
        ts = book.ts or time.time()

        # DOM 스냅샷 저장
        mid = (book.asks[0].price if book.asks else 0)
        snap = DOMSnapshot(
            ts=ts,
            bids=[(b.price, b.size) for b in book.bids[:20]],
            asks=[(a.price, a.size) for a in book.asks[:20]],
            mid=mid,
        )
        self._dom_history.append(snap)

        # 평균 주문 크기 업데이트
        all_sizes = [b.size for b in book.bids[:10]] + [a.size for a in book.asks[:10]]
        if all_sizes:
            self._avg_size = float(sum(all_sizes) / len(all_sizes))

        # 분석 실행
        self._detect_spoofing(book, ts)
        self._detect_pulling_stacking(book, ts)
        self._detect_whale_orders(book, ts)
        imb = self._calc_imbalance(book, ts)

        self._prev_book = book

        return {
            "imbalance":  imb,
            "spoofing":   list(self.spoofing_alerts)[-3:],
            "pulling":    list(self.pulling_alerts)[-3:],
            "stacking":   list(self.stacking_alerts)[-3:],
            "whales":     list(self.whale_orders)[-5:],
        }

    # ── 스푸핑 감지 ───────────────────────────────────
    def _detect_spoofing(self, book: OrderBook, ts: float) -> None:
        """대형 주문이 등장 후 빠르게 사라지면 스푸핑."""
        current_prices = {b.price: b.size for b in book.bids + book.asks}

        # 새로 등장한 대형 주문 추적
        for level in book.bids + book.asks:
            if level.size >= self.spoof_min_size:
                if level.price not in self._large_orders:
                    self._large_orders[level.price] = (level.size, ts)

        # 사라진 대형 주문 검사
        to_remove = []
        for price, (size, first_seen) in self._large_orders.items():
            if price not in current_prices or current_prices[price] < size * 0.3:
                duration = ts - first_seen
                if duration <= self.spoof_max_duration:
                    self.spoofing_alerts.append(SpoofingAlert(
                        ts=ts, price=price,
                        side="bid" if any(b.price == price for b in (book.bids or [])) else "ask",
                        size=size, duration=duration,
                    ))
                    logger.debug(f"[Spoofing] 가격 {price:.2f} 주문 {size:.2f} → {duration:.1f}초 만에 소멸")
                to_remove.append(price)

        for p in to_remove:
            del self._large_orders[p]

    # ── Pulling / Stacking ────────────────────────────
    def _detect_pulling_stacking(self, book: OrderBook, ts: float) -> None:
        if not self._prev_book:
            return

        prev_bids = {b.price: b.size for b in self._prev_book.bids}
        prev_asks = {a.price: a.size for a in self._prev_book.asks}

        for level in book.bids + book.asks:
            side = "bid" if any(b.price == level.price for b in book.bids) else "ask"
            prev_size = prev_bids.get(level.price, 0) if side == "bid" else prev_asks.get(level.price, 0)

            if prev_size > 0:
                ratio = level.size / prev_size
                if ratio < (1 - self.pull_threshold) and prev_size > self._avg_size * 3:
                    self.pulling_alerts.append(PullingAlert(
                        ts=ts, price=level.price, side=side,
                        size=prev_size - level.size,
                    ))
                elif ratio > (1 + self.stack_threshold) and level.size > self._avg_size * 5:
                    self.stacking_alerts.append(StackingAlert(
                        ts=ts, price=level.price, side=side,
                        total_size=level.size,
                    ))

    # ── Whale 주문 감지 ───────────────────────────────
    def _detect_whale_orders(self, book: OrderBook, ts: float) -> None:
        threshold = self._avg_size * self.whale_mult
        for level in book.bids[:10] + book.asks[:10]:
            if level.size >= threshold:
                side = "bid" if any(b.price == level.price for b in book.bids) else "ask"
                self.whale_orders.append(WhaleOrder(
                    ts=ts, price=level.price, side=side,
                    size=level.size, is_whale=True,
                ))

    # ── 오더북 불균형 ─────────────────────────────────
    def _calc_imbalance(self, book: OrderBook, ts: float) -> OrderBookImbalance:
        bid_vol = sum(b.size for b in book.bids[:10])
        ask_vol = sum(a.size for a in book.asks[:10])
        ratio   = bid_vol / max(ask_vol, 1e-9)

        if ratio > 1.5:   imbalance = "bull"
        elif ratio < 0.67: imbalance = "bear"
        else:              imbalance = "neutral"

        imb = OrderBookImbalance(
            ts=ts, bid_vol=bid_vol, ask_vol=ask_vol,
            ratio=round(ratio, 3), imbalance=imbalance,
        )
        self.imbalance_log.append(imb)
        return imb

    # ── DOM 히스토리 조회 ──────────────────────────────
    def get_dom_history(self, n: int = 20) -> list[DOMSnapshot]:
        return list(self._dom_history)[-n:]

    def get_heatmap_data(self) -> dict:
        """히트맵용 시간×가격 누적 데이터."""
        price_vol: dict[float, float] = {}
        for snap in self._dom_history:
            for price, size in snap.bids + snap.asks:
                price_vol[price] = price_vol.get(price, 0) + size
        return price_vol

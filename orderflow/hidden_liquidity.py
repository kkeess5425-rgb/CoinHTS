"""
orderflow/hidden_liquidity.py
=============================
Hidden Liquidity (숨겨진 유동성) 감지.

호가창에 표시되지 않는 숨겨진 대형 주문을 체결 패턴으로 추론한다.

감지 방법:
1. Iceberg Pattern   : 동일 크기 주문이 반복 체결 → 숨겨진 대형 주문
2. Price Absorption  : 가격이 레벨에서 멈추고 대량 체결 → 숨겨진 매수/매도
3. DOM 공백 돌파     : 호가 공백에도 체결 발생 → 숨겨진 주문 활성화
4. Volume Cluster    : 특정 가격대 볼륨 집중 → 기관 주문 추정
"""
from __future__ import annotations

import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from core.models import Tick, Side, OrderBook

logger = logging.getLogger(__name__)


@dataclass
class HiddenOrder:
    """추정 숨겨진 주문."""
    price:       float
    est_size:    float       # 추정 총 수량
    side:        str         # "buy" | "sell"
    kind:        str         # "iceberg" | "absorption" | "dom_gap" | "cluster"
    confidence:  float       # 0~1 신뢰도
    ts:          float
    hit_count:   int = 1     # 감지 횟수


@dataclass
class HiddenLiquidityResult:
    """Hidden Liquidity 분석 결과."""
    orders:        list[HiddenOrder] = field(default_factory=list)
    total_hidden_buy:  float = 0.0
    total_hidden_sell: float = 0.0
    dominant_side:     str   = "neutral"  # "buy" | "sell" | "neutral"


class HiddenLiquidityDetector:
    """
    Hidden Liquidity 실시간 감지기.
    틱 스트림 + 오더북을 분석해서 숨겨진 주문을 추론한다.
    """

    def __init__(
        self,
        iceberg_min_hits:    int   = 4,      # N회 이상 동일 크기 체결 → Iceberg
        price_bucket_size:   float = 10.0,   # 가격 집계 버킷 크기
        cluster_vol_mult:    float = 5.0,    # 평균의 N배 이상 → Cluster
        dom_gap_threshold:   float = 5.0,    # N레벨 이상 공백 → DOM Gap
        history_len:         int   = 1000,
    ) -> None:
        self.iceberg_min_hits  = iceberg_min_hits
        self.price_bucket_size = price_bucket_size
        self.cluster_vol_mult  = cluster_vol_mult
        self.dom_gap_threshold = dom_gap_threshold

        # 내부 상태
        self._tick_buffer:     deque[Tick]  = deque(maxlen=history_len)
        self._size_count:      dict[float, list[Tick]] = defaultdict(list)  # size → ticks
        self._price_vol:       dict[int, float] = defaultdict(float)  # bucket → vol
        self._prev_book:       Optional[OrderBook] = None
        self._hidden_orders:   list[HiddenOrder] = []

    def on_tick(self, tick: Tick) -> Optional[HiddenOrder]:
        """틱 수신 → Iceberg/Cluster 감지."""
        self._tick_buffer.append(tick)

        # 가격 버킷 볼륨 누적
        bucket = int(tick.price / self.price_bucket_size) * int(self.price_bucket_size)
        self._price_vol[bucket] += tick.size

        # Iceberg 추적 (동일 크기 반복 체결)
        size_key = round(tick.size, 2)
        self._size_count[size_key].append(tick)

        # 오래된 항목 정리
        cutoff_ts = tick.ts - 60.0
        self._size_count[size_key] = [
            t for t in self._size_count[size_key] if t.ts >= cutoff_ts
        ]

        # Iceberg 감지
        ticks_same_size = self._size_count[size_key]
        if len(ticks_same_size) >= self.iceberg_min_hits:
            sides  = [t.side for t in ticks_same_size]
            dom    = Side.BUY if sides.count(Side.BUY) >= sides.count(Side.SELL) else Side.SELL
            order  = HiddenOrder(
                price=      tick.price,
                est_size=   size_key * len(ticks_same_size),
                side=       dom.value,
                kind=       "iceberg",
                confidence= min(len(ticks_same_size) / 10, 1.0),
                ts=         tick.ts,
                hit_count=  len(ticks_same_size),
            )
            self._register(order)
            # 감지 후 초기화
            self._size_count[size_key] = []
            return order

        return None

    def on_orderbook(self, book: OrderBook) -> list[HiddenOrder]:
        """오더북 업데이트 → DOM 공백 + Absorption 감지."""
        new_orders = []

        if self._prev_book:
            # DOM 공백 감지 (이전에 없던 가격대에 체결이 생기면 숨겨진 주문 추정)
            prev_prices = {b.price for b in self._prev_book.bids + self._prev_book.asks}
            cur_prices  = {b.price for b in book.bids + book.asks}
            gaps        = prev_prices - cur_prices   # 사라진 레벨

            for p in gaps:
                # 이 가격대에 최근 체결이 있었으면 → DOM Gap Hidden
                recent = [t for t in list(self._tick_buffer)[-50:]
                          if abs(t.price - p) < self.price_bucket_size]
                if recent:
                    total = sum(t.size for t in recent)
                    side  = "buy" if sum(1 for t in recent if t.side == Side.BUY) > len(recent)/2 else "sell"
                    order = HiddenOrder(
                        price=p, est_size=total, side=side,
                        kind="dom_gap", confidence=0.6, ts=book.ts or 0,
                    )
                    self._register(order)
                    new_orders.append(order)

        self._prev_book = book
        return new_orders

    def get_clusters(self) -> list[HiddenOrder]:
        """볼륨 집중 구간 = 숨겨진 유동성 클러스터."""
        if not self._price_vol:
            return []

        vols   = np.array(list(self._price_vol.values()))
        avg    = float(vols.mean()) if len(vols) > 0 else 0
        orders = []

        for bucket, vol in self._price_vol.items():
            if vol > avg * self.cluster_vol_mult:
                # 해당 버킷의 방향 추정
                recent = [t for t in list(self._tick_buffer)[-500:]
                          if bucket <= t.price < bucket + self.price_bucket_size]
                if not recent:
                    continue
                buy_vol  = sum(t.size for t in recent if t.side == Side.BUY)
                sell_vol = sum(t.size for t in recent if t.side == Side.SELL)
                side     = "buy" if buy_vol >= sell_vol else "sell"
                confidence = min((vol / avg) / 10, 1.0)

                orders.append(HiddenOrder(
                    price=      float(bucket + self.price_bucket_size / 2),
                    est_size=   vol,
                    side=       side,
                    kind=       "cluster",
                    confidence= confidence,
                    ts=         list(self._tick_buffer)[-1].ts if self._tick_buffer else 0,
                ))
        return sorted(orders, key=lambda o: o.est_size, reverse=True)[:5]

    def analyze(self) -> HiddenLiquidityResult:
        """전체 Hidden Liquidity 분석 결과 반환."""
        clusters = self.get_clusters()
        all_orders = list(self._hidden_orders[-20:]) + clusters

        total_buy  = sum(o.est_size for o in all_orders if o.side == "buy")
        total_sell = sum(o.est_size for o in all_orders if o.side == "sell")

        if total_buy > total_sell * 1.5:
            dominant = "buy"
        elif total_sell > total_buy * 1.5:
            dominant = "sell"
        else:
            dominant = "neutral"

        return HiddenLiquidityResult(
            orders=all_orders,
            total_hidden_buy=round(total_buy, 4),
            total_hidden_sell=round(total_sell, 4),
            dominant_side=dominant,
        )

    def _register(self, order: HiddenOrder) -> None:
        # 중복 병합 (같은 가격대, 5초 이내)
        for existing in self._hidden_orders:
            if (existing.kind == order.kind and
                    abs(existing.price - order.price) < self.price_bucket_size and
                    abs(existing.ts - order.ts) < 5.0):
                existing.est_size  += order.est_size
                existing.hit_count += 1
                existing.confidence = min(existing.confidence + 0.1, 1.0)
                return
        self._hidden_orders.append(order)
        if len(self._hidden_orders) > 100:
            self._hidden_orders = self._hidden_orders[-50:]

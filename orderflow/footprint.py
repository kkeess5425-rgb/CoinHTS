"""
orderflow/footprint.py
======================
실시간 Footprint 차트 엔진.
틱 단위 체결 데이터를 가격 레벨별로 집계해서
Footprint Bar (매수/매도 볼륨, 델타, CVD)를 생성한다.

성능:
- 내부 저장소: dict 기반 O(1) 틱 처리
- 캔들 마감 시 Numba 지표 계산
- 메모리: deque로 최대 봉 수 제한
"""
from __future__ import annotations

import logging
import time
from collections import deque, defaultdict
from dataclasses import dataclass, field
from typing import Callable, Optional

from core.models import Candle, FootprintBar, FootprintCell, Side, Tick, Timeframe
from core.events import EventBus, get_event_bus

logger = logging.getLogger(__name__)


@dataclass
class _CellAccumulator:
    """진행 중인 가격 레벨 집계 (마감 전 임시 상태)."""
    buy_vol:  float = 0.0
    sell_vol: float = 0.0


class FootprintEngine:
    """
    실시간 Footprint 집계 엔진.
    틱을 수신하면 현재 진행 중인 봉에 집계하고,
    봉 마감 시 FootprintBar를 발행한다.
    """

    def __init__(
        self,
        symbol:       str,
        timeframe:    Timeframe,
        tick_size:    float = 0.5,         # 가격 레벨 단위 (BTC: 0.5, ETH: 0.01)
        max_bars:     int   = 500,
        event_bus:    Optional[EventBus] = None,
    ) -> None:
        self.symbol    = symbol
        self.timeframe = timeframe
        self.tick_size = tick_size
        self.max_bars  = max_bars
        self.bus       = event_bus or get_event_bus()

        # 진행 중 봉 상태
        self._bar_start: Optional[float] = None   # 현재 봉 시작 ts
        self._cells: dict[float, _CellAccumulator] = defaultdict(_CellAccumulator)
        self._open = self._high = self._low = self._close = 0.0
        self._buy_vol = self._sell_vol = 0.0
        self._cumulative_delta = 0.0

        # 완료된 봉 히스토리
        self._bars: deque[FootprintBar] = deque(maxlen=max_bars)

        # 콜백 (UI에서 등록 가능)
        self.on_bar_update: Optional[Callable[[FootprintBar], None]] = None
        self.on_bar_close:  Optional[Callable[[FootprintBar], None]] = None

    # ── 틱 처리 ──────────────────────────────────────
    def on_tick(self, tick: Tick) -> None:
        """체결 틱 수신 → 현재 봉에 집계."""
        if tick.symbol != self.symbol:
            return

        bar_ts = self._get_bar_start(tick.ts)

        # 새 봉 시작
        if self._bar_start is None:
            self._bar_start = bar_ts
            self._open = self._high = self._low = self._close = tick.price
        elif bar_ts > self._bar_start:
            # 이전 봉 마감
            self._close_bar()
            self._bar_start = bar_ts
            self._open = self._high = self._low = self._close = tick.price
            self._cells.clear()
            self._buy_vol = self._sell_vol = 0.0

        # 현재 봉 집계
        p = tick.price
        self._high  = max(self._high, p)
        self._low   = min(self._low,  p)
        self._close = p

        # 틱 사이즈 반올림
        level_price = round(round(p / self.tick_size) * self.tick_size, 8)
        cell = self._cells[level_price]

        if tick.side == Side.BUY:
            cell.buy_vol  += tick.size
            self._buy_vol += tick.size
        else:
            cell.sell_vol  += tick.size
            self._sell_vol += tick.size

        self._cumulative_delta += tick.size * (1 if tick.side == Side.BUY else -1)

        # 진행 중 봉 업데이트 이벤트
        if self.on_bar_update:
            bar = self._build_current_bar(tick.ts)
            self.on_bar_update(bar)

    def _get_bar_start(self, ts: float) -> float:
        """타임스탬프 → 봉 시작 시각 (floor)."""
        period = self.timeframe.seconds
        return (ts // period) * period

    def _close_bar(self) -> None:
        """현재 봉 마감 처리."""
        if self._bar_start is None:
            return
        bar = self._build_current_bar(self._bar_start)
        self._bars.append(bar)
        if self.on_bar_close:
            self.on_bar_close(bar)

    def _build_current_bar(self, ts: float) -> FootprintBar:
        """현재 상태로 FootprintBar 구성."""
        delta = self._buy_vol - self._sell_vol
        cells = [
            FootprintCell(
                price=    price,
                buy_vol=  acc.buy_vol,
                sell_vol= acc.sell_vol,
            )
            for price, acc in sorted(self._cells.items())
        ]
        # POC (가장 많은 거래 발생 레벨)
        poc: Optional[float] = None
        if cells:
            poc = max(cells, key=lambda c: c.total).price

        candle = Candle(
            ts=        self._bar_start or ts,
            open=      self._open,
            high=      self._high,
            low=       self._low,
            close=     self._close,
            volume=    self._buy_vol + self._sell_vol,
            symbol=    self.symbol,
            timeframe= self.timeframe,
        )
        return FootprintBar(
            candle= candle,
            cells=  cells,
            delta=  delta,
            cvd=    self._cumulative_delta,
            poc=    poc,
        )

    # ── 쿼리 ─────────────────────────────────────────
    @property
    def bars(self) -> list[FootprintBar]:
        """완료된 봉 목록 (과거→최신)."""
        return list(self._bars)

    @property
    def current_bar(self) -> Optional[FootprintBar]:
        """현재 진행 중인 봉."""
        if self._bar_start is None:
            return None
        return self._build_current_bar(time.time())

    def get_delta_series(self) -> list[float]:
        """봉별 델타 시리즈."""
        return [b.delta for b in self._bars]

    def get_cvd_series(self) -> list[float]:
        """누적 CVD 시리즈."""
        return [b.cvd for b in self._bars]

    # ── Imbalance 감지 ────────────────────────────────
    def detect_imbalances(
        self,
        bar: FootprintBar,
        ratio: float = 4.0,
    ) -> list[tuple[float, str]]:
        """
        단일 봉 내 Imbalance 레벨 감지.
        반환: [(price, "bull"|"bear")]
        """
        results = []
        for cell in bar.cells:
            if cell.sell_vol > 0 and cell.buy_vol / cell.sell_vol >= ratio:
                results.append((cell.price, "bull"))
            elif cell.buy_vol > 0 and cell.sell_vol / cell.buy_vol >= ratio:
                results.append((cell.price, "bear"))
        return results

    # ── Absorption 감지 ───────────────────────────────
    def detect_absorption(
        self,
        bar: FootprintBar,
        delta_pct_threshold: float = 0.75,
    ) -> Optional[str]:
        """
        가격이 거의 안 움직였는데 큰 볼륨 → 흡수 신호.
        반환: "bull_absorption" | "bear_absorption" | None
        """
        candle = bar.candle
        body = abs(candle.close - candle.open)
        atr_approx = (candle.high - candle.low) * 0.5
        if atr_approx < 1e-9:
            return None

        # 작은 몸통 + 큰 볼륨 → 흡수
        if body < atr_approx * 0.3 and candle.volume > 0:
            if abs(bar.delta) / candle.volume >= delta_pct_threshold:
                return "bull_absorption" if bar.delta > 0 else "bear_absorption"
        return None

"""
orderflow/advanced.py
=====================
고급 오더플로우 분석.

- Stacked Imbalance   : 연속 N레벨 이상 임밸런스
- Unfinished Auction  : 가격이 한 방향으로만 움직인 구간 (열린 경매)
- Absorption          : 큰 볼륨에 비해 가격 미이동 → 반대 세력 흡수
- Exhaustion          : CVD와 가격 방향 불일치 → 추세 소진
- Iceberg Detection   : 분할 매도/매수로 위장한 대형 주문
- Hidden Liquidity    : 호가창에 없는 숨겨진 주문
- Aggressive Buyer/Seller : 시장가로 공격적 진입
- Delta Divergence    : 가격과 CVD 방향 불일치
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from core.models import FootprintBar, BookLevel, OrderBook, Tick, Side

logger = logging.getLogger(__name__)


# ── 데이터 구조 ───────────────────────────────────────
@dataclass
class StackedImbalance:
    """연속 임밸런스 구간."""
    price_top:  float
    price_bot:  float
    direction:  str     # "bull" | "bear"
    levels:     int     # 연속 레벨 수
    avg_ratio:  float   # 평균 불균형 비율


@dataclass
class UnfinishedAuction:
    """미완료 경매 — 가격이 한 방향으로만 이동한 구간."""
    ts:        float
    price:     float   # UA 레벨 (단방향 이동의 끝)
    direction: str     # "bull" | "bear" (UA 발생 방향)
    # UA가 존재하면 가격이 다시 그 레벨로 돌아올 가능성이 높음


@dataclass
class AbsorptionSignal:
    """흡수 신호 — 큰 볼륨에 비해 가격이 거의 안 움직임."""
    ts:        float
    price:     float
    volume:    float
    delta:     float
    direction: str    # "bull_absorption" | "bear_absorption"
    strength:  float  # 0~1 (강도)


@dataclass
class ExhaustionSignal:
    """소진 신호 — CVD와 가격의 방향 불일치."""
    ts:        float
    price:     float
    direction: str    # "bull_exhaustion" | "bear_exhaustion"
    # 가격은 오르는데 CVD는 내려감 → 매수 소진


@dataclass
class IcebergSignal:
    """아이스버그 주문 — 반복되는 비슷한 크기의 체결."""
    ts:        float
    price:     float
    side:      str
    est_total: float   # 추정 총 수량


@dataclass
class AggressiveFlow:
    """공격적 매수/매도 — 시장가 대량 진입."""
    ts:        float
    price:     float
    side:      str
    volume:    float
    consecutive_count: int  # 연속 같은 방향 체결 수


@dataclass
class DeltaDivergence:
    """Delta Divergence — 가격과 CVD 불일치."""
    ts:        float
    kind:      str    # "bullish_div" | "bearish_div"
    price:     float
    cvd:       float


@dataclass
class AdvancedOrderFlowResult:
    """고급 오더플로우 분석 결과."""
    stacked_imbalances:  list[StackedImbalance]  = field(default_factory=list)
    unfinished_auctions: list[UnfinishedAuction] = field(default_factory=list)
    absorptions:         list[AbsorptionSignal]  = field(default_factory=list)
    exhaustions:         list[ExhaustionSignal]  = field(default_factory=list)
    icebergs:            list[IcebergSignal]     = field(default_factory=list)
    aggressive_flows:    list[AggressiveFlow]    = field(default_factory=list)
    delta_divergences:   list[DeltaDivergence]   = field(default_factory=list)


class AdvancedOrderFlowAnalyzer:
    """
    고급 오더플로우 분석기.
    Footprint 봉 + 틱 스트림 + 오더북을 종합 분석한다.
    """

    def __init__(
        self,
        imbalance_ratio:   float = 4.0,
        min_stack_levels:  int   = 3,
        absorption_delta:  float = 0.75,
        iceberg_repeat:    int   = 5,       # N회 이상 반복 → 아이스버그
        aggressive_vol:    float = 5.0,     # 평균의 N배 이상 → 공격적
        cvd_div_lookback:  int   = 20,
    ) -> None:
        self.imbalance_ratio  = imbalance_ratio
        self.min_stack_levels = min_stack_levels
        self.absorption_delta = absorption_delta
        self.iceberg_repeat   = iceberg_repeat
        self.aggressive_vol   = aggressive_vol
        self.cvd_div_lookback = cvd_div_lookback

        # 내부 상태
        self._tick_buffer:   list[Tick]        = []
        self._bar_buffer:    list[FootprintBar] = []
        self._price_history: list[float]       = []
        self._cvd_history:   list[float]       = []
        self._iceberg_track: dict[tuple, int]  = {}  # (price, size) → count

    # ── 데이터 수신 ──────────────────────────────────
    def on_tick(self, tick: Tick) -> Optional[AggressiveFlow]:
        """틱 수신 → 공격적 플로우 + 아이스버그 감지."""
        self._tick_buffer.append(tick)
        if len(self._tick_buffer) > 5000:
            self._tick_buffer = self._tick_buffer[-2000:]

        # 아이스버그 추적
        key = (round(tick.price, 2), round(tick.size, 3))
        self._iceberg_track[key] = self._iceberg_track.get(key, 0) + 1
        if self._iceberg_track[key] >= self.iceberg_repeat:
            del self._iceberg_track[key]
            return None  # 아이스버그 감지 (on_bar에서 반환)

        return None

    def on_bar(self, bar: FootprintBar) -> AdvancedOrderFlowResult:
        """Footprint 봉 마감 → 전체 분석 실행."""
        self._bar_buffer.append(bar)
        if len(self._bar_buffer) > 200:
            self._bar_buffer = self._bar_buffer[-100:]

        self._price_history.append(bar.candle.close)
        self._cvd_history.append(bar.cvd)
        if len(self._price_history) > 100:
            self._price_history = self._price_history[-50:]
            self._cvd_history   = self._cvd_history[-50:]

        result = AdvancedOrderFlowResult()
        result.stacked_imbalances  = self._detect_stacked_imbalance(bar)
        result.unfinished_auctions = self._detect_unfinished_auction(bar)
        result.absorptions.extend(self._detect_absorption(bar))
        result.exhaustions.extend(self._detect_exhaustion(bar))
        result.icebergs.extend(self._detect_iceberg())
        result.aggressive_flows.extend(self._detect_aggressive())
        result.delta_divergences.extend(self._detect_delta_divergence(bar))
        return result

    # ── 개별 감지 메서드 ────────────────────────────
    def _detect_stacked_imbalance(self, bar: FootprintBar) -> list[StackedImbalance]:
        """연속 N레벨 이상 임밸런스 감지."""
        cells  = sorted(bar.cells, key=lambda c: c.price)
        result = []

        run_dir   = None
        run_start = 0
        run_ratios= []

        for i, cell in enumerate(cells):
            if cell.sell_vol > 0 and cell.buy_vol / cell.sell_vol >= self.imbalance_ratio:
                d = "bull"
            elif cell.buy_vol > 0 and cell.sell_vol / cell.buy_vol >= self.imbalance_ratio:
                d = "bear"
            else:
                d = None

            if d and d == run_dir:
                ratio = cell.buy_vol/cell.sell_vol if d=="bull" and cell.sell_vol>0 else (
                        cell.sell_vol/cell.buy_vol if cell.buy_vol>0 else 0)
                run_ratios.append(ratio)
            else:
                if run_dir and len(run_ratios) >= self.min_stack_levels:
                    result.append(StackedImbalance(
                        price_top= cells[i-1].price,
                        price_bot= cells[run_start].price,
                        direction= run_dir,
                        levels=    len(run_ratios),
                        avg_ratio= float(np.mean(run_ratios)),
                    ))
                run_dir    = d
                run_start  = i
                run_ratios = []
                if d:
                    ratio = cell.buy_vol/max(cell.sell_vol,1e-9) if d=="bull" else cell.sell_vol/max(cell.buy_vol,1e-9)
                    run_ratios = [ratio]

        return result

    def _detect_unfinished_auction(self, bar: FootprintBar) -> list[UnfinishedAuction]:
        """미완료 경매 — 봉의 최고가/최저가 레벨에서 한 방향만 체결."""
        cells = sorted(bar.cells, key=lambda c: c.price)
        if not cells:
            return []

        result = []
        top_cell = cells[-1]
        bot_cell = cells[0]

        # 상단에서 매도만 있고 매수 없음 → 상단 UA (미완료 경매 위)
        if top_cell.buy_vol < top_cell.sell_vol * 0.1 and top_cell.sell_vol > 0:
            result.append(UnfinishedAuction(
                ts=bar.candle.ts, price=top_cell.price, direction="bear"
            ))
        # 하단에서 매수만 있고 매도 없음 → 하단 UA
        if bot_cell.sell_vol < bot_cell.buy_vol * 0.1 and bot_cell.buy_vol > 0:
            result.append(UnfinishedAuction(
                ts=bar.candle.ts, price=bot_cell.price, direction="bull"
            ))
        return result

    def _detect_absorption(self, bar: FootprintBar) -> list[AbsorptionSignal]:
        """흡수 — 큰 볼륨에 비해 가격이 거의 안 움직임."""
        candle = bar.candle
        body   = abs(candle.close - candle.open)
        spread = candle.high - candle.low
        if spread < 1e-9:
            return []

        result = []
        if body < spread * 0.3 and candle.volume > 0:
            delta_pct = abs(bar.delta) / candle.volume
            if delta_pct >= self.absorption_delta:
                direction = "bull_absorption" if bar.delta > 0 else "bear_absorption"
                result.append(AbsorptionSignal(
                    ts=candle.ts, price=candle.close,
                    volume=candle.volume, delta=bar.delta,
                    direction=direction,
                    strength=min(delta_pct, 1.0),
                ))
        return result

    def _detect_exhaustion(self, bar: FootprintBar) -> list[ExhaustionSignal]:
        """소진 — 가격 방향과 CVD 반대."""
        if len(self._price_history) < 3 or len(self._cvd_history) < 3:
            return []

        price_up = self._price_history[-1] > self._price_history[-3]
        cvd_up   = self._cvd_history[-1]   > self._cvd_history[-3]

        if price_up and not cvd_up:
            return [ExhaustionSignal(
                ts=bar.candle.ts, price=bar.candle.close, direction="bull_exhaustion"
            )]
        if not price_up and cvd_up:
            return [ExhaustionSignal(
                ts=bar.candle.ts, price=bar.candle.close, direction="bear_exhaustion"
            )]
        return []

    def _detect_iceberg(self) -> list[IcebergSignal]:
        """아이스버그 — 반복되는 비슷한 크기의 체결."""
        if len(self._tick_buffer) < 50:
            return []

        recent = self._tick_buffer[-200:]
        size_counts: dict[float, list[Tick]] = {}
        for t in recent:
            key = round(t.size, 2)
            size_counts.setdefault(key, []).append(t)

        result = []
        for size, ticks in size_counts.items():
            if len(ticks) >= self.iceberg_repeat:
                sides   = [t.side for t in ticks]
                dom_side = "buy" if sides.count(Side.BUY) > sides.count(Side.SELL) else "sell"
                result.append(IcebergSignal(
                    ts=ticks[-1].ts, price=ticks[-1].price,
                    side=dom_side, est_total=size * len(ticks)
                ))
        return result[:3]  # 최대 3개

    def _detect_aggressive(self) -> list[AggressiveFlow]:
        """공격적 매수/매도 — 연속 시장가 대량 진입."""
        if len(self._tick_buffer) < 10:
            return []

        result = []
        recent = self._tick_buffer[-100:]
        avg_size = float(np.mean([t.size for t in recent])) or 0.001

        i = 0
        while i < len(recent):
            t    = recent[i]
            if t.size < avg_size * self.aggressive_vol:
                i += 1
                continue
            # 연속 같은 방향 대형 체결
            count = 1
            j = i + 1
            while j < len(recent) and recent[j].side == t.side and recent[j].size >= avg_size * 2:
                count += 1; j += 1
            if count >= 3:
                total = sum(recent[k].size for k in range(i, j))
                result.append(AggressiveFlow(
                    ts=t.ts, price=t.price,
                    side=t.side.value, volume=total,
                    consecutive_count=count,
                ))
            i = j
        return result[:5]

    def _detect_delta_divergence(self, bar: FootprintBar) -> list[DeltaDivergence]:
        """Delta Divergence — 가격 신고점/신저점인데 CVD가 반대."""
        if len(self._price_history) < self.cvd_div_lookback:
            return []

        prices = self._price_history[-self.cvd_div_lookback:]
        cvds   = self._cvd_history[-self.cvd_div_lookback:]

        cur_p = prices[-1]; prev_max_p = max(prices[:-1])
        cur_c = cvds[-1];   prev_max_c = max(cvds[:-1])
        prev_min_p = min(prices[:-1]); prev_min_c = min(cvds[:-1])

        result = []
        # 가격 신고점 + CVD 더 낮음 → Bearish Divergence
        if cur_p > prev_max_p and cur_c < prev_max_c:
            result.append(DeltaDivergence(
                ts=bar.candle.ts, kind="bearish_div",
                price=cur_p, cvd=cur_c,
            ))
        # 가격 신저점 + CVD 더 높음 → Bullish Divergence
        if cur_p < prev_min_p and cur_c > prev_min_c:
            result.append(DeltaDivergence(
                ts=bar.candle.ts, kind="bullish_div",
                price=cur_p, cvd=cur_c,
            ))
        return result

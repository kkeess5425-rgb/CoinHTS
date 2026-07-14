"""
scanner/advanced_scanner.py
============================
고급 스캐너 확장.

기존 MarketScanner에 추가:
- CVD Divergence  : 가격 신고점 + CVD 하락 (베어리시)
- SMC 신호        : BOS/CHoCH/FVG/OB 컨플루언스
- 청산 급증       : 숏/롱 강제 청산 급증 감지
- Footprint 신호  : Absorption/Exhaustion/Delta Burst
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from typing import Optional

from core.events import EventBus, get_event_bus
from core.models import ScannerSignal, FootprintBar
from strategy.smc_engine import SMCEngine, SMCResult
from orderflow.advanced import AdvancedOrderFlowResult

logger = logging.getLogger(__name__)


class AdvancedScanner:
    """
    고급 스캐너 — 기존 스캐너에 SMC/CVD/청산 신호 추가.
    """

    def __init__(
        self,
        symbols:     list[str],
        event_bus:   Optional[EventBus] = None,
        cooldown:    float = 300.0,
        cvd_window:  int   = 20,
        liq_window:  int   = 10,
        liq_mult:    float = 3.0,
    ) -> None:
        self._symbols    = symbols
        self._bus        = event_bus or get_event_bus()
        self._cooldown   = cooldown
        self._cvd_window = cvd_window
        self._liq_mult   = liq_mult

        self._smc_engine = SMCEngine()

        # 내부 히스토리
        self._price_hist: dict[str, deque] = defaultdict(lambda: deque(maxlen=50))
        self._cvd_hist:   dict[str, deque] = defaultdict(lambda: deque(maxlen=50))
        self._liq_hist:   dict[str, deque] = defaultdict(lambda: deque(maxlen=30))
        self._last_signal: dict[str, float] = {}

        # 신호 버퍼
        self.signals: deque[ScannerSignal] = deque(maxlen=200)

    # ── CVD Divergence ────────────────────────────────
    def on_footprint_bar(self, symbol: str, bar: FootprintBar, of_result: Optional[AdvancedOrderFlowResult] = None) -> None:
        """Footprint 봉 수신 → CVD/Footprint 신호 감지."""
        self._price_hist[symbol].append(bar.candle.close)
        self._cvd_hist[symbol].append(bar.cvd)

        # CVD Divergence
        self._scan_cvd_divergence(symbol, bar.candle.ts)

        # Footprint 신호
        if of_result:
            self._scan_footprint_signals(symbol, bar, of_result)

    def _scan_cvd_divergence(self, symbol: str, ts: float) -> None:
        prices = list(self._price_hist[symbol])
        cvds   = list(self._cvd_hist[symbol])
        if len(prices) < self._cvd_window:
            return

        window_p = prices[-self._cvd_window:]
        window_c = cvds[-self._cvd_window:]

        cur_p = window_p[-1]; prev_max_p = max(window_p[:-1])
        cur_c = window_c[-1]; prev_max_c = max(window_c[:-1])
        prev_min_p = min(window_p[:-1]); prev_min_c = min(window_c[:-1])

        # Bearish CVD Divergence: 가격 신고점 + CVD 하락
        if cur_p > prev_max_p and cur_c < prev_max_c * 0.9:
            self._emit(symbol, "CVD_BEAR_DIV", cur_p, prev_max_p,
                       f"가격 신고점({cur_p:.0f}) + CVD 하락 → 베어리시 다이버전스", ts)

        # Bullish CVD Divergence: 가격 신저점 + CVD 상승
        if cur_p < prev_min_p and cur_c > prev_min_c * 0.9:
            self._emit(symbol, "CVD_BULL_DIV", cur_p, prev_min_p,
                       f"가격 신저점({cur_p:.0f}) + CVD 상승 → 불리시 다이버전스", ts)

    def _scan_footprint_signals(self, symbol: str, bar: FootprintBar, of: AdvancedOrderFlowResult) -> None:
        ts = bar.candle.ts
        if of.absorptions:
            a = of.absorptions[-1]
            self._emit(symbol, "FP_ABSORPTION", a.strength, 0.7,
                       f"Absorption: {a.direction} (강도 {a.strength:.0%})", ts)
        if of.exhaustions:
            e = of.exhaustions[-1]
            self._emit(symbol, "FP_EXHAUSTION", 1.0, 0.5,
                       f"Exhaustion: {e.direction}", ts)
        if of.stacked_imbalances:
            s = of.stacked_imbalances[-1]
            self._emit(symbol, "FP_STACKED_IMB", s.levels, 3,
                       f"Stacked Imbalance {s.levels}레벨 ({s.direction})", ts)
        if of.delta_divergences:
            d = of.delta_divergences[-1]
            self._emit(symbol, "FP_DELTA_DIV", abs(d.cvd), 0,
                       f"Delta Divergence: {d.kind}", ts)

    # ── SMC 신호 스캐닝 ──────────────────────────────
    def scan_smc(self, symbol: str, candles: list, ts: float = None) -> Optional[SMCResult]:
        """캔들로 SMC 분석 → 신호 발행."""
        if len(candles) < 50:
            return None
        ts = ts or time.time()
        try:
            result = self._smc_engine.analyze(candles)
        except Exception as e:
            logger.error(f"[AdvScanner] SMC 분석 오류 {symbol}: {e}")
            return None

        # BOS
        if result.last_bos:
            ms = "불리시" if result.bull_ms else "베어리시"
            self._emit(symbol, "SMC_BOS", result.last_bos, 0,
                       f"[SMC] {ms} BOS @ {result.last_bos:.0f}", ts)

        # CHoCH
        if result.last_choch:
            self._emit(symbol, "SMC_CHOCH", result.last_choch, 0,
                       f"[SMC] CHoCH @ {result.last_choch:.0f} — 추세 전환 가능", ts)

        # FVG 컨플루언스 (미체결 + 현재가 근접)
        for fvg in result.fvg_zones[:2]:
            d = "불리시" if fvg.direction == "bull" else "베어리시"
            self._emit(symbol, "SMC_FVG", fvg.midpoint, 0,
                       f"[SMC] {d} FVG {fvg.bottom:.0f}~{fvg.top:.0f} (채움 {fvg.filled_pct:.0%})", ts)

        # Order Block
        for ob in result.order_blocks[:2]:
            if not ob.broken:
                d = "불리시" if ob.direction == "bull" else "베어리시"
                self._emit(symbol, "SMC_OB", ob.midpoint, 0,
                           f"[SMC] {d} Order Block {ob.low:.0f}~{ob.high:.0f}", ts)

        # Liquidity Sweep
        for sw in result.liquidity_sweeps[-2:]:
            self._emit(symbol, "SMC_SWEEP", sw.swept_level, 0,
                       f"[SMC] {sw.direction} @ {sw.swept_level:.0f} (회복 {sw.recovery_dist:.1f}ATR)", ts)

        # Equal High/Low (유동성 타깃)
        for eqh in result.equal_highs[-1:]:
            self._emit(symbol, "SMC_EQH", eqh.price, 0,
                       f"[SMC] EQH @ {eqh.price:.0f} — 유동성 타깃", ts)
        for eql in result.equal_lows[-1:]:
            self._emit(symbol, "SMC_EQL", eql.price, 0,
                       f"[SMC] EQL @ {eql.price:.0f} — 유동성 타깃", ts)

        # SMT Divergence
        for smt in result.smt_divergences:
            self._emit(symbol, "SMC_SMT", 1.0, 0,
                       f"[SMC] SMT {smt.kind} ({smt.symbol_a}/{smt.symbol_b})", ts)

        return result

    # ── 청산 급증 ─────────────────────────────────────
    def on_liquidation(self, symbol: str, liq_amount: float, side: str, ts: float = None) -> None:
        """청산 데이터 수신 → 급증 감지."""
        ts = ts or time.time()
        self._liq_hist[symbol].append((liq_amount, side, ts))

        recent = list(self._liq_hist[symbol])[-self._liq_mult.__class__(10):]
        if len(recent) < 5:
            return

        total   = sum(l[0] for l in recent)
        avg     = total / len(recent)
        cur_liq = recent[-1][0]

        if cur_liq > avg * self._liq_mult:
            label = "롱" if side == "buy" else "숏"
            self._emit(symbol, "LIQUIDATION_SURGE", cur_liq, avg,
                       f"{label} 청산 급증: {cur_liq:,.0f} USD (평균의 {cur_liq/avg:.1f}배)", ts)

    # ── 공통 발신 ─────────────────────────────────────
    def _emit(self, symbol: str, signal_type: str, value: float, threshold: float, message: str, ts: float = None) -> None:
        key = f"{symbol}:{signal_type}"
        now = ts or time.time()
        if now - self._last_signal.get(key, 0) < self._cooldown:
            return
        self._last_signal[key] = now

        sig = ScannerSignal(
            symbol=symbol, ts=now,
            signal_type=signal_type,
            value=value, threshold=threshold,
            message=message,
        )
        self.signals.append(sig)
        self._bus.publish_nowait("scanner_signal", sig)
        logger.info(f"[AdvScanner] {symbol} {signal_type}: {message}")

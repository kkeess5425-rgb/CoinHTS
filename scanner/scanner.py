"""
scanner/scanner.py
==================
실시간 멀티 조건 스캐너.
볼륨 급증, OI 급증, Funding 이상, Delta 폭증, Absorption, Liquidity Sweep 등
다양한 조건을 동시에 모니터링하고 ScannerSignal을 발행한다.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np

from core.events import EventBus, get_event_bus
from core.models import (
    Candle, FootprintBar, OIData, FundingData,
    ScannerSignal, Side, Tick
)

logger = logging.getLogger(__name__)


@dataclass
class ScannerConfig:
    # 볼륨 급증
    volume_spike_mult:    float = 3.0    # 평균 대비 N배 이상
    volume_window:        int   = 20

    # OI 급증
    oi_surge_pct:         float = 2.0    # N% 이상 변화
    oi_window:            int   = 5

    # Funding 이상
    funding_extreme:      float = 0.05   # |0.05%| 이상

    # Delta 폭증
    delta_burst_mult:     float = 5.0    # EMA 대비 N배 이상
    delta_ema_len:        int   = 10

    # Absorption 감지
    absorption_enabled:   bool  = True
    absorption_delta_pct: float = 0.75

    # Liquidity Sweep
    sweep_enabled:        bool  = True
    sweep_lookback:       int   = 20
    sweep_confirm_dist:   float = 0.3    # ATR 배수
    cooldown_seconds:     float = 60.0   # 신호 쿨다운 (초)


class MarketScanner:
    """
    실시간 멀티 심볼 스캐너.
    각 심볼의 데이터를 독립적으로 추적하고 조건 충족 시 신호 발행.
    """

    def __init__(
        self,
        symbols:    list[str],
        config:     Optional[ScannerConfig] = None,
        event_bus:  Optional[EventBus]      = None,
    ) -> None:
        self.symbols  = symbols
        self.cfg      = config or ScannerConfig()
        self.bus      = event_bus or get_event_bus()

        # 심볼별 상태
        self._candle_history: dict[str, deque[Candle]]       = {s: deque(maxlen=200) for s in symbols}
        self._bar_history:    dict[str, deque[FootprintBar]]  = {s: deque(maxlen=100) for s in symbols}
        self._oi_history:     dict[str, deque[float]]         = {s: deque(maxlen=20)  for s in symbols}
        self._tick_vol:       dict[str, deque[float]]         = {s: deque(maxlen=1000) for s in symbols}
        self._delta_ema:      dict[str, float]                = {s: 0.0 for s in symbols}
        self._last_funding:   dict[str, float]                = {s: 0.0 for s in symbols}

        # 신호 쿨다운 (같은 신호 반복 방지)
        self._last_signal: dict[str, float] = {}
        self._cooldown     = self.cfg.cooldown_seconds

    # ── 데이터 수신 ──────────────────────────────────
    def on_candle(self, candle: Candle) -> None:
        """완료된 캔들 수신 → 볼륨/스윕 스캔."""
        if candle.symbol not in self.symbols:
            return
        self._candle_history[candle.symbol].append(candle)
        self._scan_volume(candle.symbol)
        self._scan_sweep(candle.symbol)

    def on_footprint_bar(self, bar: FootprintBar) -> None:
        """완료된 Footprint 봉 수신 → Delta/Absorption 스캔."""
        sym = bar.candle.symbol
        if sym not in self.symbols:
            return
        self._bar_history[sym].append(bar)
        self._scan_delta(sym, bar)
        self._scan_absorption(sym, bar)

    def on_tick(self, tick: Tick) -> None:
        """틱 수신 → 실시간 볼륨 추적."""
        if tick.symbol not in self.symbols:
            return
        self._tick_vol[tick.symbol].append(tick.size)

    def on_oi(self, oi: OIData) -> None:
        """OI 업데이트 → OI 급증 스캔."""
        if oi.symbol not in self.symbols:
            return
        self._oi_history[oi.symbol].append(oi.oi_ccy)
        self._scan_oi(oi.symbol)

    def on_funding(self, funding: FundingData) -> None:
        """펀딩비 업데이트 → 극단값 스캔."""
        if funding.symbol not in self.symbols:
            return
        self._last_funding[funding.symbol] = funding.funding_rate
        self._scan_funding(funding.symbol)

    # ── 스캔 로직 ────────────────────────────────────
    def _scan_volume(self, sym: str) -> None:
        """볼륨 급증 감지."""
        candles = list(self._candle_history[sym])
        if len(candles) < self.cfg.volume_window + 1:
            return
        recent_vols = [c.volume for c in candles[-(self.cfg.volume_window+1):-1]]
        avg_vol     = np.mean(recent_vols)
        cur_vol     = candles[-1].volume

        if avg_vol > 0 and cur_vol >= avg_vol * self.cfg.volume_spike_mult:
            self._emit(sym, "VOLUME_SPIKE", cur_vol, avg_vol * self.cfg.volume_spike_mult,
                       f"볼륨 급증: {cur_vol:.0f} (평균 {avg_vol:.0f}의 {cur_vol/avg_vol:.1f}배)")

    def _scan_oi(self, sym: str) -> None:
        """OI 급증 감지."""
        oi_list = list(self._oi_history[sym])
        if len(oi_list) < self.cfg.oi_window + 1:
            return
        old_oi = oi_list[-self.cfg.oi_window - 1]
        new_oi = oi_list[-1]
        if old_oi <= 0:
            return
        change_pct = abs(new_oi - old_oi) / old_oi * 100
        if change_pct >= self.cfg.oi_surge_pct:
            direction = "증가" if new_oi > old_oi else "감소"
            self._emit(sym, "OI_SURGE", change_pct, self.cfg.oi_surge_pct,
                       f"OI {direction}: {change_pct:.2f}% (현재 {new_oi:.0f})")

    def _scan_funding(self, sym: str) -> None:
        """펀딩비 극단값 감지."""
        rate = self._last_funding[sym]
        if abs(rate) >= self.cfg.funding_extreme / 100:
            side = "롱 과열" if rate > 0 else "숏 과열"
            self._emit(sym, "FUNDING_EXTREME", rate * 100, self.cfg.funding_extreme,
                       f"펀딩비 극단값: {rate*100:.4f}% ({side})")

    def _scan_delta(self, sym: str, bar: FootprintBar) -> None:
        """Delta 폭증 감지."""
        k = 2.0 / (self.cfg.delta_ema_len + 1)
        self._delta_ema[sym] = bar.delta * k + self._delta_ema[sym] * (1 - k)
        ema_val = self._delta_ema[sym]

        if abs(ema_val) > 0 and abs(bar.delta) >= abs(ema_val) * self.cfg.delta_burst_mult:
            direction = "매수" if bar.delta > 0 else "매도"
            self._emit(sym, "DELTA_BURST", abs(bar.delta), abs(ema_val) * self.cfg.delta_burst_mult,
                       f"Delta 폭증 ({direction}): {bar.delta:+.2f} (EMA {ema_val:+.2f}의 {bar.delta/ema_val:.1f}배)")

    def _scan_absorption(self, sym: str, bar: FootprintBar) -> None:
        """Absorption 감지."""
        if not self.cfg.absorption_enabled:
            return
        candle = bar.candle
        body   = abs(candle.close - candle.open)
        spread = candle.high - candle.low
        if spread <= 0 or candle.volume <= 0:
            return
        if body < spread * 0.3 and abs(bar.delta) / candle.volume >= self.cfg.absorption_delta_pct:
            direction = "Bull" if bar.delta > 0 else "Bear"
            self._emit(sym, f"{direction.upper()}_ABSORPTION", abs(bar.delta), 0,
                       f"{direction} Absorption: 가격 거의 안 움직이는데 강한 {direction} 주도권")

    def _scan_sweep(self, sym: str) -> None:
        """Liquidity Sweep 감지."""
        if not self.cfg.sweep_enabled:
            return
        candles = list(self._candle_history[sym])
        if len(candles) < self.cfg.sweep_lookback + 2:
            return

        cur    = candles[-1]
        window = candles[-(self.cfg.sweep_lookback+1):-1]
        wh     = max(c.high for c in window)
        wl     = min(c.low  for c in window)

        from indicators.base_indicators import atr as compute_atr
        h = np.array([c.high  for c in candles])
        l = np.array([c.low   for c in candles])
        c = np.array([c.close for c in candles])
        atr_cur = float(compute_atr(h, l, c, 14)[-1])

        confirm = atr_cur * self.cfg.sweep_confirm_dist
        if cur.low < wl and cur.close > wl + confirm:
            self._emit(sym, "BULL_SWEEP", wl, 0,
                       f"Bullish Liquidity Sweep: {wl:.2f} 저점 돌파 후 회복")
        elif cur.high > wh and cur.close < wh - confirm:
            self._emit(sym, "BEAR_SWEEP", wh, 0,
                       f"Bearish Liquidity Sweep: {wh:.2f} 고점 돌파 후 하락")

    # ── 신호 발행 ────────────────────────────────────
    def _emit(self, sym: str, signal_type: str, value: float, threshold: float, message: str) -> None:
        """쿨다운 체크 후 신호 발행."""
        key = f"{sym}:{signal_type}"
        now = time.time()
        if now - self._last_signal.get(key, 0) < self._cooldown:
            return
        self._last_signal[key] = now

        sig = ScannerSignal(
            symbol=      sym,
            ts=          now,
            signal_type= signal_type,
            value=       value,
            threshold=   threshold,
            message=     message,
        )
        self.bus.publish_nowait("scanner_signal", sig)
        logger.info(f"[SCANNER] {sym} {signal_type}: {message}")

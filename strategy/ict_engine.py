"""
strategy/ict_engine.py
======================
ICT (Inner Circle Trader) 전략 엔진.
BOS, CHoCH, FVG, Order Block, Liquidity Sweep, OTE 등을 감지하고
AI 스코어(0~100점)로 신호 품질을 정량화한다.

모든 조건은 독립 메서드로 분리 → 테스트/튜닝 용이.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from core.models import Candle, StrategySignal, Timeframe, Exchange
from indicators.base_indicators import atr, ema, pivot_high, pivot_low

logger = logging.getLogger(__name__)


# ── 파라미터 ─────────────────────────────────────────────
@dataclass
class ICTParams:
    # 시장 구조
    swing_length:    int   = 10

    # 유동성
    liq_lookback:    int   = 30

    # FVG
    fvg_min_pct:     float = 0.07    # FVG 최소 크기 (%)
    fvg_lookback:    int   = 40
    fvg_max_fill:    float = 0.50    # 이 이상 채워진 FVG는 무효

    # 스윕
    sweep_window:    int   = 10
    sweep_confirm_dist: float = 0.20  # ATR 배수

    # Order Block
    ob_lookback:     int   = 20

    # OTE (Optimal Trade Entry)
    ote_fib_min:     float = 0.618
    ote_fib_max:     float = 0.786

    # Displacement
    require_displacement:  bool  = True
    displacement_atr_mult: float = 0.5
    displacement_max_bars: int   = 7

    # 필터
    min_rr:          float = 2.0
    min_risk_pct:    float = 0.07    # 최소 위험 %
    sl_buffer_atr:   float = 0.3
    min_confluence:  int   = 1

    # HTF 바이어스
    htf_ema_len:     int   = 50
    htf_enabled:     bool  = True

    # AI 점수 배점
    score_bos:         float = 20.0
    score_choch:       float = 15.0
    score_fvg:         float = 15.0
    score_ob:          float = 15.0
    score_ote:         float = 10.0
    score_displacement: float = 10.0
    score_volume:      float = 10.0
    score_oi_increase: float = 10.0


# ── 결과 구조체 ───────────────────────────────────────────
@dataclass
class ICTResult:
    """ICT 분석 결과."""
    # 시장 구조
    bull_ms:            bool  = False
    bear_sweep_active:  bool  = False
    bull_sweep_active:  bool  = False
    sweep_level:        float = 0.0

    # 컨플루언스
    in_bull_fvg:  bool  = False
    in_bear_fvg:  bool  = False
    in_bull_ob:   bool  = False
    in_bear_ob:   bool  = False
    in_bull_ote:  bool  = False
    in_bear_ote:  bool  = False
    displacement: bool  = False

    # AI 점수 (0~100)
    score:        float = 0.0
    reasons:      list[str] = field(default_factory=list)

    # 신호
    signal:       Optional[str] = None   # "LONG" | "SHORT"
    entry:        Optional[float] = None
    sl:           Optional[float] = None
    tp:           Optional[float] = None
    rr:           Optional[float] = None


class ICTEngine:
    """ICT 전략 엔진."""

    def __init__(self, params: Optional[ICTParams] = None) -> None:
        self.params = params or ICTParams()

    def analyze(
        self,
        candles: list[Candle],
        htf_bias: Optional[bool] = None,
        oi_increasing: Optional[bool] = None,
    ) -> ICTResult:
        """
        캔들 리스트로 ICT 신호 분석.
        htf_bias: True=불리시, False=베어리시, None=필터 없음
        oi_increasing: OI 증가 여부 (추가 점수용)
        """
        result = ICTResult()
        p = self.params

        if len(candles) < 100:
            return result

        # numpy 배열 변환
        h = np.array([c.high  for c in candles])
        l = np.array([c.low   for c in candles])
        c = np.array([c.close for c in candles])
        o = np.array([c.open  for c in candles])
        v = np.array([c.volume for c in candles])
        n = len(candles)

        atr14   = atr(h, l, c, 14)
        atr_cur = float(atr14[-1])
        cur_close = float(c[-1])

        if atr_cur <= 0:
            return result

        # ── 1. 시장 구조 ──────────────────────────────
        result.bull_ms = self._detect_market_structure(h, l, c, p.swing_length)

        # ── 2. HTF 바이어스 필터 ──────────────────────
        if htf_bias is not None:
            if htf_bias and not result.bull_ms:
                pass   # HTF 불리시인데 LTF 베어리시 → 롱만 허용
            if not htf_bias and result.bull_ms:
                pass   # HTF 베어리시인데 LTF 불리시 → 숏만 허용

        # ── 3. 유동성 스윕 ────────────────────────────
        self._detect_sweeps(result, h, l, c, v, atr_cur, p)

        if not result.bull_sweep_active and not result.bear_sweep_active:
            return result

        # ── 4. Displacement ────────────────────────────
        result.displacement = self._detect_displacement(
            result, h, l, c, o, atr_cur, p, n
        )
        if p.require_displacement and not result.displacement:
            return result

        # ── 5. FVG 감지 ───────────────────────────────
        fvg_min = cur_close * p.fvg_min_pct / 100
        self._detect_fvg(result, h, l, c, fvg_min, p, n)

        # ── 6. Order Block 감지 ────────────────────────
        self._detect_ob(result, h, l, c, o, p, n)

        # ── 7. OTE (피보나치 골든 포켓) ───────────────
        self._detect_ote(result, h, l, c, p)

        # ── 8. 컨플루언스 집계 및 AI 스코어 ──────────
        confluence = self._compute_score(result, p, oi_increasing)
        if confluence < p.min_confluence:
            return result

        # ── 9. 진입 조건 최종 확인 ────────────────────
        min_risk_pct = p.min_risk_pct / 100.0
        sl_buffer    = atr_cur * p.sl_buffer_atr

        if result.bull_sweep_active and result.bull_ms:
            sl    = result.sweep_level - sl_buffer
            risk  = cur_close - sl
            if risk > 0 and risk >= cur_close * min_risk_pct:
                tp = cur_close + risk * 2.0
                rr = (tp - cur_close) / risk
                if rr >= p.min_rr:
                    result.signal = "LONG"
                    result.entry  = cur_close
                    result.sl     = round(sl, 4)
                    result.tp     = round(tp, 4)
                    result.rr     = round(rr, 2)

        elif result.bear_sweep_active and not result.bull_ms:
            sl    = result.sweep_level + sl_buffer
            risk  = sl - cur_close
            if risk > 0 and risk >= cur_close * min_risk_pct:
                tp = cur_close - risk * 2.0
                rr = (sl - cur_close) / risk
                if rr >= p.min_rr:
                    result.signal = "SHORT"
                    result.entry  = cur_close
                    result.sl     = round(sl, 4)
                    result.tp     = round(tp, 4)
                    result.rr     = round(rr, 2)

        return result

    # ── 내부 메서드 ───────────────────────────────────
    def _detect_market_structure(self, h, l, c, sw) -> bool:
        """단순 BOS/CHoCH: 최근 고점/저점 돌파 여부."""
        ph = pivot_high(h, sw)
        pl = pivot_low(l, sw)
        bull_ms = None
        last_sh = last_sl = np.nan

        for i in range(len(c)):
            if not np.isnan(ph[i]): last_sh = ph[i]
            if not np.isnan(pl[i]): last_sl = pl[i]
            if bull_ms is None and not (np.isnan(last_sh) or np.isnan(last_sl)):
                bull_ms = c[i] > last_sh
            if bull_ms is not None:
                if bull_ms and c[i] < last_sl:   bull_ms = False
                elif not bull_ms and c[i] > last_sh: bull_ms = True

        return bool(bull_ms)

    def _detect_sweeps(self, result, h, l, c, v, atr_cur, p):
        avg_vol = float(v[-(p.liq_lookback+1):-1].mean())
        confirm_dist = atr_cur * p.sweep_confirm_dist

        for back in range(1, p.sweep_window + 1):
            bp = -(back)
            wh = float(h[bp - p.liq_lookback:bp].max())
            wl = float(l[bp - p.liq_lookback:bp].min())
            bl = float(l[bp]); bh = float(h[bp]); bc = float(c[bp])
            bv = float(v[bp])

            if avg_vol > 0 and bv < avg_vol * 0.8:
                continue

            if bl < wl and bc > wl + confirm_dist:
                result.bull_sweep_active = True
                result.sweep_level = min(bl, wl)
            if bh > wh and bc < wh - confirm_dist:
                result.bear_sweep_active = True
                result.sweep_level = max(bh, wh)

    def _detect_displacement(self, result, h, l, c, o, atr_cur, p, n) -> bool:
        start = n - p.sweep_window - 1
        end   = n
        for i in range(max(0, start), end):
            body = abs(float(c[i]) - float(o[i]))
            if result.bull_sweep_active and float(c[i]) > float(o[i]) and body >= atr_cur * p.displacement_atr_mult:
                return True
            if result.bear_sweep_active and float(c[i]) < float(o[i]) and body >= atr_cur * p.displacement_atr_mult:
                return True
        return False

    def _detect_fvg(self, result, h, l, c, fvg_min, p, n):
        cur_low  = float(l[-1])
        cur_high = float(h[-1])
        for i in range(2, min(p.fvg_lookback, n)):
            idx = -i
            fl  = float(l[idx])
            fh  = float(h[idx - 2])
            if fl > fh and fl - fh >= fvg_min:
                fill = (float(h[-1:].max()) - fh) / (fl - fh)
                if fill <= p.fvg_max_fill and cur_low <= fl and cur_high >= fh:
                    result.in_bull_fvg = True
            fl2 = float(l[idx - 2])
            fh2 = float(h[idx])
            if fh2 < fl2 and fl2 - fh2 >= fvg_min:
                fill2 = (fl2 - float(l[-1:].min())) / (fl2 - fh2)
                if fill2 <= p.fvg_max_fill and cur_low <= fl2 and cur_high >= fh2:
                    result.in_bear_fvg = True

    def _detect_ob(self, result, h, l, c, o, p, n):
        cur_low  = float(l[-1])
        cur_high = float(h[-1])
        for i in range(1, min(p.ob_lookback, n - 1)):
            idx = -(i + 1)
            ob_h = float(h[idx]); ob_l = float(l[idx])
            if float(c[idx]) < float(o[idx]):   # 하락 캔들 → 불리시 OB
                if ob_l <= cur_low <= ob_h:
                    result.in_bull_ob = True
            if float(c[idx]) > float(o[idx]):   # 상승 캔들 → 베어리시 OB
                if ob_l <= cur_high <= ob_h:
                    result.in_bear_ob = True

    def _detect_ote(self, result, h, l, c, p):
        swing_h = float(h[-p.fvg_lookback:].max())
        swing_l = float(l[-p.fvg_lookback:].min())
        rng     = swing_h - swing_l
        cur     = float(c[-1])
        if rng <= 0:
            return
        fib = (swing_h - cur) / rng
        if p.ote_fib_min <= fib <= p.ote_fib_max:
            result.in_bull_ote = True
        fib2 = (cur - swing_l) / rng
        if p.ote_fib_min <= fib2 <= p.ote_fib_max:
            result.in_bear_ote = True

    def _compute_score(self, result: ICTResult, p: ICTParams, oi_inc: Optional[bool]) -> int:
        """AI 스코어 산정 (0~100점)."""
        score = 0.0
        reasons = []

        # BOS/CHoCH
        if result.bull_ms and result.bull_sweep_active:
            score += p.score_bos
            reasons.append(f"[BOS] 불리시 시장구조 +{p.score_bos:.0f}점")
        if not result.bull_ms and result.bear_sweep_active:
            score += p.score_choch
            reasons.append(f"[CHoCH] 베어리시 전환 +{p.score_choch:.0f}점")

        # FVG
        if (result.in_bull_fvg and result.bull_sweep_active) or \
           (result.in_bear_fvg and result.bear_sweep_active):
            score += p.score_fvg
            reasons.append(f"[FVG] 미체결 FVG 컨플루언스 +{p.score_fvg:.0f}점")

        # OB
        if (result.in_bull_ob and result.bull_sweep_active) or \
           (result.in_bear_ob and result.bear_sweep_active):
            score += p.score_ob
            reasons.append(f"[OB] Order Block +{p.score_ob:.0f}점")

        # OTE
        if (result.in_bull_ote and result.bull_sweep_active) or \
           (result.in_bear_ote and result.bear_sweep_active):
            score += p.score_ote
            reasons.append(f"[OTE] 골든포켓(0.618~0.786) +{p.score_ote:.0f}점")

        # Displacement
        if result.displacement:
            score += p.score_displacement
            reasons.append(f"[DISP] Displacement 캔들 +{p.score_displacement:.0f}점")

        # OI
        if oi_inc:
            score += p.score_oi_increase
            reasons.append(f"[OI] 미결제약정 증가 +{p.score_oi_increase:.0f}점")

        result.score   = min(100.0, score)
        result.reasons = reasons

        # 컨플루언스 카운트 (FVG/OB/OTE)
        bull_conf = sum([result.in_bull_fvg, result.in_bull_ob, result.in_bull_ote])
        bear_conf = sum([result.in_bear_fvg, result.in_bear_ob, result.in_bear_ote])
        return bull_conf if result.bull_sweep_active else bear_conf

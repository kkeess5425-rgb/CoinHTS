"""
ai/score_engine.py
==================
통합 AI 스코어 엔진 (0~100점).
ICT 전략 조건 + 오더플로우 지표 + 시장 컨텍스트를
가중합산하여 신호 품질을 정량화한다.

점수표 (기본값):
  BOS/CHoCH         20점
  CHoCH 시장구조전환 15점
  볼륨 스파이크      15점
  OI 증가           10점
  긍정적 Delta      15점
  Footprint Buy     15점
  Absorption        10점
  EMA 추세          10점
  ───────────────
  만점              100점 (80점+ → BUY, 20점- → SELL)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from core.models import FootprintBar, OIData, FundingData
from strategy.ict_engine import ICTResult

logger = logging.getLogger(__name__)


@dataclass
class ScoreWeights:
    """점수 배점 설정 (합계 = 100)."""
    bos_choch:        float = 20.0   # 시장구조 (BOS/CHoCH)
    choch_transition: float = 15.0   # CHoCH 전환
    volume_spike:     float = 15.0   # 볼륨 급증
    oi_increase:      float = 10.0   # OI 증가
    positive_delta:   float = 15.0   # 양성 Delta
    footprint_buy:    float = 15.0   # Footprint 매수 우세
    absorption:       float = 10.0   # Absorption 신호
    ema_trend:        float = 10.0   # EMA 추세 일치

    # 감점 항목
    funding_extreme:  float = -5.0   # 펀딩 과열 시 감점
    low_volume:       float = -5.0   # 볼륨 낮을 때 감점


@dataclass
class ScoreContext:
    """스코어 계산에 필요한 컨텍스트 데이터."""
    ict_result:       Optional[ICTResult] = None
    fp_bar:           Optional[FootprintBar] = None
    oi_data:          Optional[OIData]       = None
    funding_data:     Optional[FundingData]  = None
    volume_spike:     bool  = False
    avg_volume:       float = 0.0
    cur_volume:       float = 0.0
    ema20:            float = 0.0
    ema50:            float = 0.0
    cur_price:        float = 0.0
    htf_bias:         Optional[bool] = None   # True=불리시


@dataclass
class ScoreResult:
    """최종 스코어 결과."""
    total:        float = 0.0
    breakdown:    dict  = field(default_factory=dict)
    reasons:      list  = field(default_factory=list)
    signal:       str   = "NEUTRAL"   # "BUY" | "SELL" | "NEUTRAL"
    confidence:   str   = "LOW"       # "HIGH" | "MEDIUM" | "LOW"


class AIScoreEngine:
    """
    AI 통합 스코어 엔진.
    개별 조건의 충족 여부를 평가하고 가중합산한다.
    """

    BUY_THRESHOLD  = 80.0
    SELL_THRESHOLD = 20.0

    def __init__(self, weights: Optional[ScoreWeights] = None) -> None:
        self.weights = weights or ScoreWeights()

    def score(self, ctx: ScoreContext, direction: str = "LONG") -> ScoreResult:
        """
        주어진 컨텍스트로 신호 점수를 산정한다.
        direction: "LONG" 또는 "SHORT" (어느 방향의 신호를 평가할지)
        """
        w   = self.weights
        total = 0.0
        bd: dict[str, float] = {}
        reasons: list[str]   = []
        is_long = direction == "LONG"

        # ── 1. BOS / CHoCH (ICT) ─────────────────────
        if ctx.ict_result:
            r = ctx.ict_result
            if (is_long and r.bull_ms) or (not is_long and not r.bull_ms):
                total += w.bos_choch
                bd["BOS/CHoCH"] = w.bos_choch
                reasons.append(f"[BOS] 시장구조 {'불리시' if is_long else '베어리시'} +{w.bos_choch:.0f}점")
            if (is_long and r.bull_sweep_active) or (not is_long and r.bear_sweep_active):
                total += w.choch_transition
                bd["CHoCH_전환"] = w.choch_transition
                reasons.append(f"[SWEEP] 유동성 스윕 후 전환 +{w.choch_transition:.0f}점")

        # ── 2. 볼륨 급증 ──────────────────────────────
        if ctx.volume_spike and ctx.avg_volume > 0:
            vol_mult = ctx.cur_volume / ctx.avg_volume
            if vol_mult >= 2.0:
                pts = min(w.volume_spike, w.volume_spike * (vol_mult - 1) / 2)
                total += pts
                bd["볼륨급증"] = pts
                reasons.append(f"[VOL] 볼륨 {vol_mult:.1f}배 +{pts:.0f}점")

        # ── 3. OI 증가 ────────────────────────────────
        if ctx.oi_data:
            # OI 증가를 단순히 양수로 처리 (이전 OI와 비교는 스캐너에서)
            total += w.oi_increase
            bd["OI증가"] = w.oi_increase
            reasons.append(f"[OI] OI 증가 확인 +{w.oi_increase:.0f}점")

        # ── 4. Delta ─────────────────────────────────
        if ctx.fp_bar:
            delta = ctx.fp_bar.delta
            if (is_long and delta > 0) or (not is_long and delta < 0):
                pts = min(w.positive_delta, abs(delta) / max(ctx.fp_bar.candle.volume, 1) * w.positive_delta * 2)
                pts = round(min(pts, w.positive_delta), 1)
                total += pts
                bd["Delta"] = pts
                reasons.append(f"[DELTA] {'양성' if is_long else '음성'} Delta +{pts:.0f}점")

        # ── 5. Footprint 매수/매도 우세 ───────────────
        if ctx.fp_bar:
            cells = ctx.fp_bar.cells
            if cells:
                total_buy  = sum(c.buy_vol  for c in cells)
                total_sell = sum(c.sell_vol for c in cells)
                dominant   = total_buy > total_sell if is_long else total_sell > total_buy
                if dominant:
                    ratio = (total_buy / total_sell if is_long and total_sell > 0
                             else total_sell / total_buy if not is_long and total_buy > 0
                             else 1.0)
                    pts = min(w.footprint_buy, ratio * 5)
                    pts = round(min(pts, w.footprint_buy), 1)
                    total += pts
                    bd["Footprint"] = pts
                    reasons.append(f"[FP] Footprint {'매수' if is_long else '매도'} 우세 +{pts:.0f}점")

        # ── 6. Absorption ─────────────────────────────
        if ctx.fp_bar:
            candle = ctx.fp_bar.candle
            body   = abs(candle.close - candle.open)
            spread = candle.high - candle.low
            if spread > 0 and body < spread * 0.3:
                if (is_long and ctx.fp_bar.delta > 0) or (not is_long and ctx.fp_bar.delta < 0):
                    total += w.absorption
                    bd["Absorption"] = w.absorption
                    reasons.append(f"[ABS] Absorption 신호 +{w.absorption:.0f}점")

        # ── 7. EMA 추세 ───────────────────────────────
        if ctx.ema20 > 0 and ctx.ema50 > 0 and ctx.cur_price > 0:
            ema_bull = ctx.ema20 > ctx.ema50 and ctx.cur_price > ctx.ema20
            ema_bear = ctx.ema20 < ctx.ema50 and ctx.cur_price < ctx.ema20
            if (is_long and ema_bull) or (not is_long and ema_bear):
                total += w.ema_trend
                bd["EMA추세"] = w.ema_trend
                reasons.append(f"[EMA] EMA 추세 일치 +{w.ema_trend:.0f}점")

        # ── 8. 감점 항목 ──────────────────────────────
        if ctx.funding_data:
            rate = ctx.funding_data.funding_rate
            if abs(rate) >= 0.0005:
                # 과열 방향 신호에 감점
                if (is_long and rate > 0) or (not is_long and rate < 0):
                    total += w.funding_extreme
                    bd["펀딩과열"] = w.funding_extreme
                    reasons.append(f"[FUND] 펀딩 과열 {w.funding_extreme:.0f}점")

        # ── 최종 집계 ─────────────────────────────────
        total   = max(0.0, min(100.0, total))
        signal  = ("BUY"  if total >= self.BUY_THRESHOLD  else
                   "SELL" if total <= self.SELL_THRESHOLD  else "NEUTRAL")
        confidence = ("HIGH"   if total >= 85 or total <= 15  else
                      "MEDIUM" if total >= 70 or total <= 30  else "LOW")

        return ScoreResult(
            total=      round(total, 1),
            breakdown=  bd,
            reasons=    reasons,
            signal=     signal,
            confidence= confidence,
        )

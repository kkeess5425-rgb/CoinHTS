"""
ai/advanced_scorer.py
=====================
고급 AI 분석 엔진.
- 진입 점수 (0~100)
- 청산 점수 (포지션 종료 적기 판단)
- 추세 강도 점수
- 변동성 점수
- 신뢰도 (%)
- 자연어 시장 설명
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from core.models import Candle, FootprintBar
from strategy.ict_engine import ICTResult
from strategy.smc_engine import SMCResult
from orderflow.advanced import AdvancedOrderFlowResult
from orderbook.analyzer import OrderBookImbalance

logger = logging.getLogger(__name__)


@dataclass
class AdvancedScoreResult:
    """다차원 AI 점수 결과."""
    # 점수 (0~100)
    entry_score:      float = 0.0
    exit_score:       float = 0.0    # 포지션 종료 적기
    trend_score:      float = 0.0    # 추세 강도
    volatility_score: float = 0.0    # 변동성 (높으면 주의)
    confidence:       float = 0.0    # 전체 신뢰도 %

    # 세부 근거
    entry_reasons:    list[str] = field(default_factory=list)
    exit_reasons:     list[str] = field(default_factory=list)
    warnings:         list[str] = field(default_factory=list)

    # 자연어 설명
    market_narrative: str = ""
    entry_narrative:  str = ""

    # 신호
    recommendation:   str = "WAIT"   # "LONG" | "SHORT" | "WAIT" | "EXIT"


class AdvancedScoringEngine:
    """
    다차원 AI 스코어링 엔진.
    SMC + ICT + 오더플로우 + 오더북 + 시장 컨텍스트를 종합한다.
    """

    def score(
        self,
        candles:     list[Candle],
        ict_result:  Optional[ICTResult]               = None,
        smc_result:  Optional[SMCResult]               = None,
        fp_bar:      Optional[FootprintBar]            = None,
        of_result:   Optional[AdvancedOrderFlowResult] = None,
        ob_imbalance:Optional[OrderBookImbalance]      = None,
        oi_increasing: Optional[bool]                  = None,
        funding_rate:  Optional[float]                 = None,
        direction:   str = "LONG",
    ) -> AdvancedScoreResult:
        """종합 점수 계산."""
        result = AdvancedScoreResult()
        is_long = direction == "LONG"

        # ── 1. 진입 점수 ──────────────────────────────
        self._score_entry(result, ict_result, smc_result, fp_bar, of_result,
                          ob_imbalance, oi_increasing, is_long)

        # ── 2. 추세 강도 점수 ─────────────────────────
        self._score_trend(result, candles)

        # ── 3. 변동성 점수 ────────────────────────────
        self._score_volatility(result, candles)

        # ── 4. 청산 점수 ──────────────────────────────
        self._score_exit(result, of_result, funding_rate)

        # ── 5. 신뢰도 ─────────────────────────────────
        result.confidence = self._calc_confidence(result)

        # ── 6. 자연어 설명 ────────────────────────────
        result.market_narrative = self._generate_market_narrative(
            candles, ict_result, smc_result, of_result, funding_rate
        )
        result.entry_narrative = self._generate_entry_narrative(
            result, direction, ict_result, smc_result
        )

        # ── 7. 최종 추천 ──────────────────────────────
        if result.entry_score >= 75 and result.confidence >= 60:
            result.recommendation = direction
        elif result.exit_score >= 70:
            result.recommendation = "EXIT"
        else:
            result.recommendation = "WAIT"

        return result

    # ── 진입 점수 계산 ────────────────────────────────
    def _score_entry(self, result, ict, smc, fp, of_res, ob_imb, oi_inc, is_long):
        score = 0.0
        reasons = []

        # ICT (최대 35점)
        if ict:
            if ict.bull_ms == is_long:
                score += 15; reasons.append(f"[ICT] {'불리시' if is_long else '베어리시'} 시장구조 +15점")
            if (is_long and ict.bull_sweep_active) or (not is_long and ict.bear_sweep_active):
                score += 10; reasons.append("[ICT] 유동성 스윕 확인 +10점")
            if ict.displacement:
                score += 10; reasons.append("[ICT] Displacement 캔들 +10점")

        # SMC (최대 30점)
        if smc:
            if smc.bull_ms == is_long:
                score += 10; reasons.append("[SMC] 구조 일치 +10점")
            if (is_long and any(z.direction=="bull" for z in smc.fvg_zones)):
                score += 10; reasons.append("[SMC] 불리시 FVG 컨플루언스 +10점")
            elif (not is_long and any(z.direction=="bear" for z in smc.fvg_zones)):
                score += 10; reasons.append("[SMC] 베어리시 FVG 컨플루언스 +10점")
            if smc.order_blocks:
                score += 10; reasons.append(f"[SMC] Order Block {len(smc.order_blocks)}개 +10점")

        # 오더플로우 (최대 25점)
        if fp:
            if (is_long and fp.delta > 0) or (not is_long and fp.delta < 0):
                pct = abs(fp.delta) / max(fp.candle.volume, 1e-9)
                pts = min(15, pct * 20)
                score += pts; reasons.append(f"[OF] 델타 {'양성' if is_long else '음성'} +{pts:.0f}점")
            if of_res:
                if of_res.absorptions:
                    score += 5; reasons.append("[OF] Absorption 신호 +5점")
                if of_res.stacked_imbalances:
                    score += 5; reasons.append("[OF] Stacked Imbalance +5점")

        # 오더북 불균형 (최대 10점)
        if ob_imb:
            if (is_long and ob_imb.imbalance=="bull") or (not is_long and ob_imb.imbalance=="bear"):
                score += 10; reasons.append(f"[OB] 오더북 {'매수' if is_long else '매도'} 우세 +10점")

        # OI (최대 10점)
        if oi_inc:
            score += 10; reasons.append("[OI] 미결제약정 증가 +10점")

        result.entry_score   = round(min(100, score), 1)
        result.entry_reasons = reasons

    # ── 추세 강도 ─────────────────────────────────────
    def _score_trend(self, result, candles):
        if len(candles) < 50:
            return

        from indicators.base_indicators import ema, atr
        closes = np.array([c.close for c in candles])
        highs  = np.array([c.high  for c in candles])
        lows   = np.array([c.low   for c in candles])

        e20 = ema(closes, 20); e50 = ema(closes, 50)
        a14 = atr(highs, lows, closes, 14)

        cur = closes[-1]; atr_cur = a14[-1]

        # 추세 정렬: cur > EMA20 > EMA50
        if cur > e20[-1] > e50[-1]:
            score = 80
        elif cur > e20[-1]:
            score = 60
        elif cur < e20[-1] < e50[-1]:
            score = 20
        else:
            score = 40

        # ATR 대비 EMA 기울기
        slope = abs(e20[-1] - e20[-5]) / max(atr_cur, 1e-9)
        score = min(100, score + slope * 5)

        result.trend_score = round(score, 1)

    # ── 변동성 점수 (높으면 위험) ──────────────────────
    def _score_volatility(self, result, candles):
        if len(candles) < 20:
            return
        from indicators.base_indicators import atr
        closes = np.array([c.close for c in candles])
        highs  = np.array([c.high  for c in candles])
        lows   = np.array([c.low   for c in candles])
        a14    = atr(highs, lows, closes, 14)

        # ATR / 현재가 비율
        atr_pct = a14[-1] / max(closes[-1], 1e-9) * 100
        # 0.5% 미만 → 낮은 변동성(안전) → 100점
        # 3% 이상 → 매우 높은 변동성(위험) → 10점
        score = max(10, min(100, 100 - (atr_pct - 0.5) / 2.5 * 90))
        result.volatility_score = round(score, 1)

        if atr_pct > 2.0:
            result.warnings.append(f"⚠️ 변동성 높음 (ATR={atr_pct:.2f}%) — 포지션 크기 주의")

    # ── 청산 점수 ─────────────────────────────────────
    def _score_exit(self, result, of_res, funding_rate):
        score = 0.0
        reasons = []

        if of_res:
            if of_res.exhaustions:
                score += 40; reasons.append("[EXIT] 추세 소진 신호")
            if of_res.delta_divergences:
                score += 30; reasons.append("[EXIT] Delta 다이버전스")

        if funding_rate and abs(funding_rate) > 0.001:
            score += 20; reasons.append(f"[EXIT] 펀딩비 과열 ({funding_rate*100:.4f}%)")

        result.exit_score   = round(min(100, score), 1)
        result.exit_reasons = reasons

    # ── 신뢰도 ────────────────────────────────────────
    def _calc_confidence(self, result) -> float:
        """다차원 점수의 일관성으로 신뢰도 계산."""
        scores = [result.entry_score, result.trend_score]
        valid  = [s for s in scores if s > 0]
        if not valid:
            return 0.0
        mean = np.mean(valid)
        std  = np.std(valid) if len(valid) > 1 else 0
        # 점수들이 일관적일수록 신뢰도 높음
        return round(max(0, min(100, mean - std * 0.5)), 1)

    # ── 자연어 설명 ───────────────────────────────────
    def _generate_market_narrative(self, candles, ict, smc, of_res, funding):
        """현재 시장 상황을 자연어로 설명."""
        parts = []

        if ict:
            ms = "상승" if ict.bull_ms else "하락"
            parts.append(f"현재 시장 구조는 {ms} 추세")
            if ict.last_choch:
                parts.append(f"최근 CHoCH({ict.last_choch:.0f})에서 추세 전환 감지")

        if smc:
            z = smc.premium_discount
            if z:
                parts.append(f"현재 {z.current_zone.upper()} 존 ({z.equilibrium:.0f} 기준)")
            if smc.equal_highs:
                parts.append(f"EQH {smc.equal_highs[-1].price:.0f} 레벨에 유동성 존재")
            if smc.equal_lows:
                parts.append(f"EQL {smc.equal_lows[-1].price:.0f} 레벨에 유동성 존재")

        if of_res:
            if of_res.absorptions:
                parts.append("Absorption 신호 — 대형 세력이 매물을 흡수 중")
            if of_res.exhaustions:
                e = of_res.exhaustions[-1]
                parts.append(f"{'매수' if 'bull' in e.direction else '매도'} 소진 징후")

        if funding is not None:
            if funding > 0.001:
                parts.append(f"펀딩비 양수({funding*100:.4f}%) — 롱 과열 주의")
            elif funding < -0.001:
                parts.append(f"펀딩비 음수({funding*100:.4f}%) — 숏 과열 주의")

        return ". ".join(parts) + "." if parts else "분석 데이터 부족."

    def _generate_entry_narrative(self, result, direction, ict, smc):
        """진입 근거를 자연어로 설명."""
        if result.entry_score < 50:
            return f"현재 {direction} 진입 조건 미충족 (점수 {result.entry_score:.0f}/100)."

        reasons_short = [r.split("]")[-1].strip() for r in result.entry_reasons[:3]]
        qual = "강력한" if result.entry_score >= 80 else "적당한"

        return (
            f"{direction} 방향으로 {qual} 진입 근거 확인 (점수 {result.entry_score:.0f}/100). "
            f"주요 근거: {', '.join(reasons_short)}. "
            f"신뢰도 {result.confidence:.0f}%."
        )

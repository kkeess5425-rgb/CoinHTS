"""
strategy/confluence_engine.py
=============================
전략 통합 컨플루언스 엔진.

ICT + SMC + OrderFlow + OrderBook + 고래/뉴스를
단일 100점 점수로 통합한다.

레이어별 가중치:
  ICT 구조 / 스윕 / Displacement   30점
  SMC FVG / OB / EQH / PDZ         25점
  MTF 정렬                          15점
  오더플로우 (델타/CVD/Absorption)  15점
  오더북 불균형                      8점
  외부 데이터 (OI/펀딩/고래)        7점
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from strategy.ict_engine import ICTResult
from strategy.smc_engine import SMCResult
from strategy.mtf_engine import MTFResult
from orderflow.advanced import AdvancedOrderFlowResult
from orderbook.analyzer import OrderBookImbalance

logger = logging.getLogger(__name__)


@dataclass
class ConfluenceScore:
    """통합 컨플루언스 점수."""
    total:        float = 0.0     # 0~100
    direction:    str   = ""      # "LONG" | "SHORT" | ""
    grade:        str   = ""      # "A+" | "A" | "B" | "C" | "D"

    # 레이어별 점수
    ict_score:    float = 0.0
    smc_score:    float = 0.0
    mtf_score:    float = 0.0
    of_score:     float = 0.0
    ob_score:     float = 0.0
    external_score: float = 0.0

    # 상세 근거
    reasons:      list[str] = field(default_factory=list)
    warnings:     list[str] = field(default_factory=list)

    # 진입 추천
    entry:        Optional[float] = None
    sl:           Optional[float] = None
    tp:           Optional[float] = None
    tp2:          Optional[float] = None
    rr:           Optional[float] = None

    @property
    def is_tradeable(self) -> bool:
        """거래 가능 신호 여부 (B 이상)."""
        return self.grade in ("A+", "A", "B")


def _grade(score: float) -> str:
    if score >= 85: return "A+"
    if score >= 75: return "A"
    if score >= 65: return "B"
    if score >= 50: return "C"
    return "D"


class ConfluenceEngine:
    """
    전략 통합 컨플루언스 엔진.
    모든 분석 결과를 단일 점수로 통합한다.
    """

    # 레이어 최대 점수
    ICT_MAX      = 30.0
    SMC_MAX      = 25.0
    MTF_MAX      = 15.0
    OF_MAX       = 15.0
    OB_MAX       = 8.0
    EXTERNAL_MAX = 7.0

    def score(
        self,
        direction:   str,
        ict_result:  Optional[ICTResult]               = None,
        smc_result:  Optional[SMCResult]               = None,
        mtf_result:  Optional[MTFResult]               = None,
        of_result:   Optional[AdvancedOrderFlowResult] = None,
        ob_imbalance:Optional[OrderBookImbalance]      = None,
        oi_data:     Optional[object]                  = None,
        funding_rate:Optional[float]                   = None,
        whale_signal:Optional[str]                     = None,   # "bullish" | "bearish"
    ) -> ConfluenceScore:
        is_long = direction == "LONG"
        result  = ConfluenceScore(direction=direction)
        reasons, warnings = [], []

        # ── 1. ICT 레이어 (30점) ──────────────────────
        ict_s = 0.0
        if ict_result:
            # 시장 구조 (12점)
            if ict_result.bull_ms == is_long:
                ict_s += 12; reasons.append("✅ ICT 시장구조 일치")
            # 스윕 (10점)
            if (is_long  and ict_result.bull_sweep_active) or \
               (not is_long and ict_result.bear_sweep_active):
                ict_s += 10; reasons.append("✅ ICT 유동성 스윕")
            # Displacement (8점)
            if ict_result.displacement:
                ict_s += 8;  reasons.append("✅ ICT Displacement")
        result.ict_score = round(min(ict_s, self.ICT_MAX), 1)

        # ── 2. SMC 레이어 (25점) ──────────────────────
        smc_s = 0.0
        if smc_result:
            # 구조 (5점)
            if smc_result.bull_ms == is_long:
                smc_s += 5; reasons.append("✅ SMC 구조 일치")
            # FVG (6점)
            fvg = [z for z in smc_result.fvg_zones
                   if z.direction == ("bull" if is_long else "bear")]
            if fvg:
                smc_s += 6; reasons.append(f"✅ SMC FVG {len(fvg)}개 ({fvg[0].bottom:.0f}~{fvg[0].top:.0f})")
            # OB (5점)
            ob = [o for o in smc_result.order_blocks
                  if o.direction == ("bull" if is_long else "bear") and not o.broken]
            if ob:
                smc_s += 5; reasons.append(f"✅ SMC Order Block {ob[0].low:.0f}~{ob[0].high:.0f}")
            # Breaker Block (3점)
            if smc_result.breaker_blocks:
                smc_s += 3; reasons.append("✅ Breaker Block")
            # EQH/EQL (3점)
            if is_long and smc_result.equal_lows:
                smc_s += 3; reasons.append(f"✅ EQL @ {smc_result.equal_lows[-1].price:.0f}")
            elif not is_long and smc_result.equal_highs:
                smc_s += 3; reasons.append(f"✅ EQH @ {smc_result.equal_highs[-1].price:.0f}")
            # PDZ (3점)
            if smc_result.premium_discount:
                pd = smc_result.premium_discount
                if (is_long and pd.current_zone == "discount") or \
                   (not is_long and pd.current_zone == "premium"):
                    smc_s += 3; reasons.append(f"✅ {pd.current_zone.upper()} 존")
                elif pd.current_zone == "equilibrium":
                    smc_s += 1
            # SMT (패널티)
            if smc_result.smt_divergences:
                d = smc_result.smt_divergences[-1]
                if (is_long  and d.kind == "bearish_smt") or \
                   (not is_long and d.kind == "bullish_smt"):
                    smc_s -= 5; warnings.append(f"⚠️ SMT 역방향 다이버전스")
                else:
                    smc_s += 3; reasons.append("✅ SMT 동방향 다이버전스")
        result.smc_score = round(max(0, min(smc_s, self.SMC_MAX)), 1)

        # ── 3. MTF 레이어 (15점) ──────────────────────
        mtf_s = 0.0
        if mtf_result:
            if mtf_result.aligned and mtf_result.direction == direction:
                mtf_s = self.MTF_MAX
                reasons.append(f"✅ MTF 완전 정렬 ({mtf_result.summary})")
            elif mtf_result.direction == direction:
                mtf_s = self.MTF_MAX * 0.6
                reasons.append(f"⚠️ MTF 부분 정렬")
        result.mtf_score = round(mtf_s, 1)

        # ── 4. 오더플로우 레이어 (15점) ───────────────
        of_s = 0.0
        if of_result:
            # Absorption (5점)
            for ab in of_result.absorptions:
                if (is_long  and "bull" in ab.direction) or \
                   (not is_long and "bear" in ab.direction):
                    of_s += 5 * ab.strength
                    reasons.append(f"✅ Absorption ({ab.direction})")
                    break
            # Delta Divergence (4점)
            for dd in of_result.delta_divergences:
                if (is_long  and "bull" in dd.kind) or \
                   (not is_long and "bear" in dd.kind):
                    of_s += 4; reasons.append("✅ Delta 불리시 다이버전스")
                    break
            # Stacked Imbalance (3점)
            for si in of_result.stacked_imbalances:
                if si.direction == ("bull" if is_long else "bear"):
                    of_s += 3; reasons.append(f"✅ Stacked Imbalance {si.levels}레벨")
                    break
            # Exhaustion 패널티
            for ex in of_result.exhaustions:
                if (is_long  and "bull" in ex.direction) or \
                   (not is_long and "bear" in ex.direction):
                    of_s -= 5; warnings.append("⚠️ 오더플로우 소진 신호")
                    break
        result.of_score = round(max(0, min(of_s, self.OF_MAX)), 1)

        # ── 5. 오더북 레이어 (8점) ────────────────────
        ob_s = 0.0
        if ob_imbalance:
            if (is_long  and ob_imbalance.imbalance == "bull") or \
               (not is_long and ob_imbalance.imbalance == "bear"):
                ratio = ob_imbalance.ratio if is_long else 1 / max(ob_imbalance.ratio, 0.01)
                ob_s  = min(self.OB_MAX, ratio * 3)
                reasons.append(f"✅ 오더북 {'매수' if is_long else '매도'} 우세 ({ob_imbalance.ratio:.2f})")
        result.ob_score = round(ob_s, 1)

        # ── 6. 외부 데이터 레이어 (7점) ───────────────
        ext_s = 0.0
        if oi_data and hasattr(oi_data, 'oi_ccy') and oi_data.oi_ccy > 0:
            ext_s += 2; reasons.append("✅ OI 데이터 확인")
        if funding_rate is not None:
            if abs(funding_rate) < 0.0005:
                ext_s += 2; reasons.append("✅ 펀딩비 중립")
            elif (is_long and funding_rate < -0.0005) or \
                 (not is_long and funding_rate > 0.0005):
                ext_s += 3; reasons.append(f"✅ 펀딩비 유리 ({funding_rate*100:.4f}%)")
            elif abs(funding_rate) > 0.001:
                ext_s -= 3; warnings.append(f"⚠️ 펀딩비 과열 ({funding_rate*100:.4f}%)")
        if whale_signal:
            if (is_long  and whale_signal == "bullish") or \
               (not is_long and whale_signal == "bearish"):
                ext_s += 2; reasons.append("✅ 고래 방향 일치")
            elif whale_signal in ("bullish", "bearish"):
                ext_s -= 2; warnings.append("⚠️ 고래 방향 역행")
        result.external_score = round(max(0, min(ext_s, self.EXTERNAL_MAX)), 1)

        # ── 7. 최종 집계 ──────────────────────────────
        total = (result.ict_score + result.smc_score + result.mtf_score +
                 result.of_score + result.ob_score + result.external_score)
        result.total    = round(min(100, max(0, total)), 1)
        result.grade    = _grade(result.total)
        result.reasons  = reasons
        result.warnings = warnings

        # ── 8. 진입 레벨 ──────────────────────────────
        # MTF → LTF → SMC 우선순위
        if mtf_result and mtf_result.entry:
            result.entry = mtf_result.entry
            result.sl    = mtf_result.sl
            result.tp    = mtf_result.tp
            result.tp2   = mtf_result.tp2
            result.rr    = mtf_result.rr
        elif ict_result and ict_result.entry:
            result.entry = ict_result.entry
            result.sl    = ict_result.sl
            result.tp    = ict_result.tp
            result.tp2   = ict_result.tp2
            result.rr    = ict_result.rr

        return result

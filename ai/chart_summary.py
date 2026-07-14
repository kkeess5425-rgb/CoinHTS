"""
ai/chart_summary.py
===================
AI 차트 자연어 요약.

"현재는 상승 추세이며 CVD는 상승하지만 OI는 감소 중이라
 숏 커버링 가능성이 있습니다. EQH 67,000에 유동성이 집중되어
 있으며 FVG 65,800~66,200 구간에서 반응 가능성이 높습니다."
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class ChartSummary:
    """차트 요약 결과."""
    headline:      str    # 한 줄 요약
    trend:         str    # 추세 설명
    structure:     str    # 시장 구조 설명
    orderflow:     str    # 오더플로우 설명
    key_levels:    str    # 주요 레벨
    risk:          str    # 리스크 요소
    watchfor:      str    # 주목할 사항
    full_text:     str    # 전체 자연어 설명


class AIChartSummaryEngine:
    """
    AI 기반 차트 자연어 요약 엔진.
    ICT / SMC / 오더플로우 / 고래 데이터를 조합해서
    트레이더가 읽기 쉬운 자연어로 변환한다.
    """

    def summarize(
        self,
        symbol:       str,
        candles:      list,
        ict_result:   Optional[object] = None,
        smc_result:   Optional[object] = None,
        of_result:    Optional[object] = None,
        fp_bar:       Optional[object] = None,
        oi_data:      Optional[object] = None,
        funding_rate: Optional[float]  = None,
        whale_sentiment: Optional[dict]= None,
    ) -> ChartSummary:
        """전체 데이터를 자연어로 요약."""

        sym_short = symbol.replace("-USDT-SWAP", "").replace("-USDT", "")

        # ── 1. 추세 분석 ──
        trend_text = self._analyze_trend(candles, ict_result, smc_result)

        # ── 2. 시장 구조 ──
        structure_text = self._analyze_structure(ict_result, smc_result)

        # ── 3. 오더플로우 ──
        of_text = self._analyze_orderflow(fp_bar, of_result, oi_data, funding_rate)

        # ── 4. 주요 레벨 ──
        levels_text = self._analyze_key_levels(smc_result)

        # ── 5. 리스크 ──
        risk_text = self._analyze_risk(candles, funding_rate, whale_sentiment)

        # ── 6. 주목할 사항 ──
        watch_text = self._analyze_watchfor(ict_result, smc_result, of_result)

        # ── 7. 한 줄 헤드라인 ──
        headline = self._generate_headline(sym_short, ict_result, smc_result, of_result)

        # ── 8. 전체 텍스트 ──
        full = (
            f"📊 {sym_short} 시장 분석\n\n"
            f"📈 추세: {trend_text}\n\n"
            f"🏗 구조: {structure_text}\n\n"
            f"⚡ 오더플로우: {of_text}\n\n"
            f"🎯 주요 레벨: {levels_text}\n\n"
            f"⚠️ 리스크: {risk_text}\n\n"
            f"👀 주목: {watch_text}"
        )

        return ChartSummary(
            headline=headline, trend=trend_text,
            structure=structure_text, orderflow=of_text,
            key_levels=levels_text, risk=risk_text,
            watchfor=watch_text, full_text=full,
        )

    # ── 분석 메서드들 ─────────────────────────────────
    def _analyze_trend(self, candles, ict, smc) -> str:
        if not candles:
            return "데이터 부족"

        from indicators.base_indicators import ema, atr
        closes = np.array([c.close for c in candles])
        highs  = np.array([c.high  for c in candles])
        lows   = np.array([c.low   for c in candles])
        e20 = ema(closes, 20); e50 = ema(closes, 50)
        a14 = atr(highs, lows, closes, 14)

        cur = closes[-1]; atr_v = a14[-1]
        cur_pct = atr_v / cur * 100

        # 추세 방향
        if cur > e20[-1] > e50[-1]:
            direction = "상승 추세"
            strength  = "강한" if (e20[-1] - e50[-1]) > atr_v else "약한"
        elif cur < e20[-1] < e50[-1]:
            direction = "하락 추세"
            strength  = "강한" if (e50[-1] - e20[-1]) > atr_v else "약한"
        else:
            direction = "횡보"
            strength  = "중립"

        # ICT / SMC 추세 확인
        extra = ""
        if ict and hasattr(ict, 'bull_ms'):
            extra = f", ICT {'불리시' if ict.bull_ms else '베어리시'} 구조"
        if smc and hasattr(smc, 'premium_discount') and smc.premium_discount:
            zone = smc.premium_discount.current_zone
            extra += f", 현재 {zone.upper()} 존"

        return f"{strength} {direction} (ATR={atr_v:.0f}, {cur_pct:.2f}%){extra}"

    def _analyze_structure(self, ict, smc) -> str:
        parts = []

        if ict:
            if getattr(ict, 'last_bos', None):
                parts.append(f"BOS @ {ict.last_bos:.0f}")
            if getattr(ict, 'last_choch', None):
                parts.append(f"CHoCH @ {ict.last_choch:.0f} (추세 전환 가능)")

        if smc:
            sw = getattr(smc, 'liquidity_sweeps', [])
            if sw:
                last = sw[-1]
                parts.append(f"{last.direction} @ {last.swept_level:.0f}")
            fvg = getattr(smc, 'fvg_zones', [])
            if fvg:
                z = fvg[-1]
                parts.append(f"{'불리시' if z.direction=='bull' else '베어리시'} FVG {z.bottom:.0f}~{z.top:.0f}")
            eqh = getattr(smc, 'equal_highs', [])
            eql = getattr(smc, 'equal_lows',  [])
            if eqh: parts.append(f"EQH @ {eqh[-1].price:.0f} (유동성 상단)")
            if eql: parts.append(f"EQL @ {eql[-1].price:.0f} (유동성 하단)")

        return ", ".join(parts) if parts else "주요 구조 신호 없음"

    def _analyze_orderflow(self, fp_bar, of_res, oi, funding) -> str:
        parts = []

        if fp_bar:
            delta = fp_bar.delta; vol = fp_bar.candle.volume
            delta_pct = delta / vol * 100 if vol else 0
            bias = "매수" if delta > 0 else "매도"
            parts.append(f"델타 {bias} 우세 ({delta_pct:+.0f}%)")
            if hasattr(fp_bar, 'cvd'):
                cvd_dir = "상승" if fp_bar.cvd > 0 else "하락"
                parts.append(f"CVD {cvd_dir} ({fp_bar.cvd:+.2f})")

        if of_res:
            if getattr(of_res, 'absorptions', []):
                a = of_res.absorptions[-1]
                parts.append(f"Absorption ({a.direction.replace('_absorption','')}, 강도 {a.strength:.0%})")
            if getattr(of_res, 'exhaustions', []):
                e = of_res.exhaustions[-1]
                dir_kr = "매수" if "bull" in e.direction else "매도"
                parts.append(f"{dir_kr} 소진 징후")
            if getattr(of_res, 'icebergs', []):
                parts.append("아이스버그 주문 감지")
            if getattr(of_res, 'delta_divergences', []):
                d = of_res.delta_divergences[-1]
                parts.append(f"Delta {'불리시' if 'bull' in d.kind else '베어리시'} 다이버전스")

        if oi and hasattr(oi, 'oi_ccy'):
            parts.append(f"OI {oi.oi_ccy/1e6:.1f}M")

        if funding is not None:
            if abs(funding) > 0.0005:
                bias = "롱 과열" if funding > 0 else "숏 과열"
                parts.append(f"펀딩 {bias} ({funding*100:.4f}%)")

        return ", ".join(parts) if parts else "오더플로우 데이터 없음"

    def _analyze_key_levels(self, smc) -> str:
        levels = []
        if not smc:
            return "SMC 분석 없음"

        for ob in getattr(smc, 'order_blocks', [])[:2]:
            if not ob.broken:
                levels.append(f"{'불리시' if ob.direction=='bull' else '베어리시'} OB {ob.low:.0f}~{ob.high:.0f}")

        for bb in getattr(smc, 'breaker_blocks', [])[:1]:
            levels.append(f"Breaker Block {bb.low:.0f}~{bb.high:.0f}")

        pd = getattr(smc, 'premium_discount', None)
        if pd:
            levels.append(f"균형점 {pd.equilibrium:.0f}")

        return ", ".join(levels) if levels else "주요 레벨 없음"

    def _analyze_risk(self, candles, funding, whale) -> str:
        risks = []

        if candles and len(candles) > 14:
            from indicators.base_indicators import atr
            closes = np.array([c.close for c in candles])
            highs  = np.array([c.high  for c in candles])
            lows   = np.array([c.low   for c in candles])
            a14 = atr(highs, lows, closes, 14)
            atr_pct = a14[-1] / closes[-1] * 100
            if atr_pct > 2.0:
                risks.append(f"높은 변동성 (ATR {atr_pct:.1f}%)")

        if funding and abs(funding) > 0.001:
            risks.append(f"펀딩비 과열 ({funding*100:.4f}%)")

        if whale and whale.get("signal") == "bearish":
            risks.append(f"거래소 순유입 증가 (매도 압력)")

        return ", ".join(risks) if risks else "특별한 리스크 없음"

    def _analyze_watchfor(self, ict, smc, of_res) -> str:
        items = []

        if smc:
            eqh = getattr(smc, 'equal_highs', [])
            eql = getattr(smc, 'equal_lows',  [])
            if eqh: items.append(f"EQH {eqh[-1].price:.0f} 돌파 여부")
            if eql: items.append(f"EQL {eql[-1].price:.0f} 이탈 여부")
            smt = getattr(smc, 'smt_divergences', [])
            if smt: items.append(f"SMT 다이버전스 ({smt[-1].kind})")

        if of_res:
            ua = getattr(of_res, 'unfinished_auctions', [])
            if ua: items.append(f"미완료 경매 레벨 {ua[-1].price:.0f} 재방문")

        if ict and getattr(ict, 'displacement', False):
            items.append("Displacement 이후 FVG 반응")

        return ", ".join(items) if items else "추가 확인 신호 없음"

    def _generate_headline(self, sym, ict, smc, of_res) -> str:
        """한 줄 헤드라인 생성."""
        is_bull = False
        if ict and getattr(ict, 'bull_ms', None): is_bull = True
        if smc and getattr(smc, 'bull_ms', None): is_bull = True

        direction = "상승" if is_bull else "하락"
        signals   = []

        if smc and getattr(smc, 'liquidity_sweeps', []):
            signals.append("유동성 스윕")
        if of_res and getattr(of_res, 'absorptions', []):
            signals.append("Absorption")
        if of_res and getattr(of_res, 'delta_divergences', []):
            signals.append("Delta Div")

        signal_str = f" + {' + '.join(signals)}" if signals else ""
        return f"{sym} {direction} 추세{signal_str} — {'진입 기회 탐색' if signals else '관망 유지'}"

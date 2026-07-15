"""
strategy/mtf_engine.py
======================
멀티 타임프레임 분석 (MTF).

ICT / SMC 트레이딩의 핵심 원칙:
  HTF (상위봉) → 구조/방향 파악
  MTF (중간봉) → FVG / OB / 스윕 확인
  LTF (하위봉) → 정밀 진입 타이밍

예: 4H 방향 → 1H 컨플루언스 → 15M 진입

지원 흐름:
  4H / 1H / 15M
  1H / 15M / 5M
  1H / 15M / 1M
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from core.models import Candle, Timeframe
from strategy.ict_engine import ICTEngine, ICTParams, ICTResult
from strategy.smc_engine import SMCEngine, SMCResult

logger = logging.getLogger(__name__)


@dataclass
class MTFConfig:
    """MTF 설정."""
    htf: Timeframe = Timeframe.H4    # 상위 타임프레임 (방향)
    mtf: Timeframe = Timeframe.H1    # 중간 타임프레임 (컨플루언스)
    ltf: Timeframe = Timeframe.M15   # 하위 타임프레임 (진입)

    htf_weight: float = 0.4   # HTF 방향 가중치
    mtf_weight: float = 0.35  # MTF 컨플루언스 가중치
    ltf_weight: float = 0.25  # LTF 진입 타이밍 가중치

    require_htf_alignment: bool = True   # HTF 방향과 일치해야 신호 생성


@dataclass
class MTFResult:
    """MTF 분석 결과."""
    # 각 타임프레임 분석
    htf_ict: Optional[ICTResult] = None
    mtf_ict: Optional[ICTResult] = None
    ltf_ict: Optional[ICTResult] = None
    htf_smc: Optional[SMCResult] = None
    mtf_smc: Optional[SMCResult] = None

    # 통합 결과
    aligned:     bool  = False   # HTF/MTF/LTF 방향 일치 여부
    direction:   str   = ""      # "LONG" | "SHORT" | ""
    mtf_score:   float = 0.0     # 통합 점수 (0~100)

    # 진입 레벨 (LTF 기준)
    entry:       Optional[float] = None
    sl:          Optional[float] = None
    tp:          Optional[float] = None
    tp2:         Optional[float] = None
    rr:          Optional[float] = None

    # 근거
    confluence:  list[str] = field(default_factory=list)

    # 요약
    summary:     str = ""


class MTFEngine:
    """
    멀티 타임프레임 분석 엔진.
    HTF/MTF/LTF 캔들 데이터를 받아 통합 신호를 생성한다.
    """

    def __init__(
        self,
        config:     Optional[MTFConfig]  = None,
        ict_params: Optional[ICTParams]  = None,
    ) -> None:
        self.cfg     = config     or MTFConfig()
        self._params = ict_params or ICTParams(require_displacement=False, min_confluence=0, min_rr=1.5)
        self._ict    = ICTEngine(self._params)
        self._smc    = SMCEngine()

    def analyze(
        self,
        htf_candles: list[Candle],
        mtf_candles: list[Candle],
        ltf_candles: list[Candle],
    ) -> MTFResult:
        """3개 타임프레임 통합 분석."""
        result = MTFResult()

        # ── 1. HTF 분석 (방향 결정) ───────────────────
        if len(htf_candles) >= 50:
            result.htf_ict = self._ict.analyze(htf_candles)
            result.htf_smc = self._smc.analyze(htf_candles)
        else:
            logger.warning(f"[MTF] HTF 데이터 부족: {len(htf_candles)}봉")

        # ── 2. MTF 분석 (컨플루언스) ──────────────────
        if len(mtf_candles) >= 50:
            result.mtf_ict = self._ict.analyze(mtf_candles)
            result.mtf_smc = self._smc.analyze(mtf_candles)

        # ── 3. LTF 분석 (진입 타이밍) ────────────────
        if len(ltf_candles) >= 50:
            result.ltf_ict = self._ict.analyze(ltf_candles)

        # ── 4. 방향 정렬 확인 ─────────────────────────
        result.direction, result.aligned = self._check_alignment(result)

        # ── 5. 통합 점수 계산 ─────────────────────────
        result.mtf_score = self._compute_score(result)

        # ── 6. 진입 레벨 결정 ─────────────────────────
        if result.aligned and result.direction:
            self._compute_entry(result)

        # ── 7. 근거 수집 ──────────────────────────────
        result.confluence = self._collect_confluence(result)

        # ── 8. 요약 ───────────────────────────────────
        result.summary = self._build_summary(result)

        return result

    # ── 내부 메서드 ──────────────────────────────────
    def _check_alignment(self, r: MTFResult) -> tuple[str, bool]:
        """HTF / MTF / LTF 방향 일치 확인."""
        directions = []

        htf_bull = None
        if r.htf_ict:
            htf_bull = r.htf_ict.bull_ms
            directions.append("LONG" if htf_bull else "SHORT")
        elif r.htf_smc:
            htf_bull = r.htf_smc.bull_ms
            directions.append("LONG" if htf_bull else "SHORT")

        if r.mtf_ict:
            directions.append("LONG" if r.mtf_ict.bull_ms else "SHORT")

        if r.ltf_ict:
            directions.append("LONG" if r.ltf_ict.bull_ms else "SHORT")

        if not directions:
            return "", False

        # 다수결 방향
        long_count  = directions.count("LONG")
        short_count = directions.count("SHORT")
        dominant    = "LONG" if long_count >= short_count else "SHORT"

        # 정렬 여부 (HTF 우선)
        if self.cfg.require_htf_alignment and htf_bull is not None:
            aligned = all(
                (d == "LONG") == htf_bull
                for d in directions
            )
        else:
            aligned = long_count == len(directions) or short_count == len(directions)

        return dominant, aligned

    def _compute_score(self, r: MTFResult) -> float:
        score = 0.0

        # HTF 점수 (방향 가중치)
        if r.htf_ict:
            score += r.htf_ict.score * self.cfg.htf_weight
        if r.htf_smc:
            score += r.htf_smc.score * (self.cfg.htf_weight * 0.5)

        # MTF 점수 (컨플루언스)
        if r.mtf_ict:
            score += r.mtf_ict.score * self.cfg.mtf_weight
        if r.mtf_smc:
            score += r.mtf_smc.score * (self.cfg.mtf_weight * 0.5)

        # LTF 점수 (진입 타이밍)
        if r.ltf_ict:
            score += r.ltf_ict.score * self.cfg.ltf_weight

        # 정렬 보너스
        if r.aligned:
            score = min(100, score * 1.2)

        return round(score, 1)

    def _compute_entry(self, r: MTFResult) -> None:
        """LTF 기준 진입 레벨 결정."""
        # LTF 신호 우선, 없으면 MTF
        for src in [r.ltf_ict, r.mtf_ict]:
            if src and src.signal == r.direction and src.entry:
                r.entry = src.entry
                r.sl    = src.sl
                r.tp    = src.tp
                r.tp2   = src.tp2
                r.rr    = src.rr
                return

        # ICT 신호 없으면 SMC FVG 기준
        for smc in [r.mtf_smc, r.htf_smc]:
            if smc:
                bull_fvg = [z for z in smc.fvg_zones if z.direction == "bull"]
                bear_fvg = [z for z in smc.fvg_zones if z.direction == "bear"]
                if r.direction == "LONG" and bull_fvg:
                    z = bull_fvg[0]
                    r.entry = z.midpoint
                elif r.direction == "SHORT" and bear_fvg:
                    z = bear_fvg[0]
                    r.entry = z.midpoint

    def _collect_confluence(self, r: MTFResult) -> list[str]:
        conf = []
        cfg  = self.cfg

        if r.aligned:
            conf.append(f"✅ {cfg.htf.value}/{cfg.mtf.value}/{cfg.ltf.value} 방향 정렬 ({r.direction})")
        else:
            conf.append(f"⚠️ 타임프레임 방향 불일치")

        if r.htf_ict and r.htf_ict.bull_ms == (r.direction == "LONG"):
            conf.append(f"[HTF {cfg.htf.value}] 불리시 구조" if r.htf_ict.bull_ms else f"[HTF] 베어리시 구조")

        if r.mtf_smc and r.mtf_smc.fvg_zones:
            z = r.mtf_smc.fvg_zones[0]
            conf.append(f"[MTF {cfg.mtf.value}] FVG {z.bottom:.0f}~{z.top:.0f}")

        if r.mtf_smc and r.mtf_smc.order_blocks:
            ob = r.mtf_smc.order_blocks[0]
            conf.append(f"[MTF] Order Block {ob.low:.0f}~{ob.high:.0f}")

        if r.ltf_ict and r.ltf_ict.signal:
            conf.append(f"[LTF {cfg.ltf.value}] 진입 신호: {r.ltf_ict.signal}")

        if r.htf_smc and r.htf_smc.liquidity_sweeps:
            sw = r.htf_smc.liquidity_sweeps[-1]
            conf.append(f"[HTF] {sw.direction} @ {sw.swept_level:.0f}")

        if r.mtf_smc and r.mtf_smc.premium_discount:
            pd = r.mtf_smc.premium_discount
            conf.append(f"[MTF] {pd.current_zone.upper()} 존")

        if r.mtf_smc and r.mtf_smc.smt_divergences:
            conf.append(f"[MTF] SMT 다이버전스 감지")

        return conf

    def _build_summary(self, r: MTFResult) -> str:
        cfg = self.cfg
        if not r.direction:
            return f"MTF 분석: 방향 불명확"
        align_str = "정렬됨 ✅" if r.aligned else "불일치 ⚠️"
        entry_str = f" | 진입 {r.entry:.0f}" if r.entry else ""
        return (
            f"{cfg.htf.value}/{cfg.mtf.value}/{cfg.ltf.value} MTF — "
            f"{r.direction} ({align_str}) | 점수 {r.mtf_score:.0f}{entry_str}"
        )

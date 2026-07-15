"""
risk/position_sizer.py
======================
ATR 기반 동적 포지션 사이징.

- Volatility-Adjusted Sizing  : 변동성 낮으면 크게, 높으면 작게
- Kelly Criterion             : 이론적 최적 베팅 비율
- Fixed Fractional            : 계좌 대비 고정 비율
- Risk Parity                 : 다중 포지션 시 리스크 균등 분배
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class SizingConfig:
    """포지션 사이징 설정."""
    method:          str   = "atr"      # "atr" | "kelly" | "fixed" | "parity"
    account_size:    float = 10_000.0
    risk_pct:        float = 1.0        # 계좌 대비 리스크 %
    max_size_pct:    float = 10.0       # 최대 포지션 크기 % (레버리지 방어)
    target_atr_risk: float = 1.0        # ATR 몇 배를 리스크로 설정
    kelly_fraction:  float = 0.25       # Kelly의 25% (Quarter Kelly)
    win_rate:        float = 0.55       # Kelly 계산용 예상 승률
    avg_win_r:       float = 2.0        # 평균 승리 R
    avg_loss_r:      float = 1.0        # 평균 손실 R


@dataclass
class SizeResult:
    """포지션 사이징 결과."""
    size:         float   # 계약 수량
    risk_amount:  float   # 리스크 금액 (USD)
    risk_pct:     float   # 리스크 비율 (%)
    notional:     float   # 명목 가치 (USD)
    leverage:     float   # 실효 레버리지
    method:       str
    rationale:    str


class PositionSizer:
    """
    동적 포지션 사이징 엔진.
    시장 변동성과 계좌 상태에 따라 최적 사이즈를 결정한다.
    """

    def __init__(self, config: Optional[SizingConfig] = None) -> None:
        self.cfg = config or SizingConfig()

    def calculate(
        self,
        entry:      float,
        sl:         float,
        atr:        Optional[float] = None,
        price:      Optional[float] = None,
        open_count: int = 0,          # 현재 열린 포지션 수 (Parity용)
    ) -> SizeResult:
        """포지션 크기 계산."""
        method = self.cfg.method
        price  = price or entry

        if method == "atr" and atr:
            return self._atr_sizing(entry, sl, atr, price)
        elif method == "kelly":
            return self._kelly_sizing(entry, sl, price)
        elif method == "parity":
            return self._parity_sizing(entry, sl, atr, price, open_count)
        else:
            return self._fixed_sizing(entry, sl, price)

    # ── ATR 기반 ─────────────────────────────────────
    def _atr_sizing(self, entry, sl, atr, price) -> SizeResult:
        """
        변동성 조정 사이징.
        리스크 = ATR × target_atr_risk → 이 금액이 risk_pct를 초과하지 않도록 설정.
        """
        risk_amount  = self.cfg.account_size * self.cfg.risk_pct / 100
        atr_risk     = atr * self.cfg.target_atr_risk   # ATR 기반 리스크 단위
        sl_distance  = abs(entry - sl)

        # SL이 ATR보다 넓으면 사이즈 줄임
        effective_sl = max(sl_distance, atr_risk * 0.5)
        size         = risk_amount / max(effective_sl, 1e-9)
        size         = self._apply_limits(size, price)

        return SizeResult(
            size=round(size, 4), risk_amount=round(risk_amount, 2),
            risk_pct=self.cfg.risk_pct, notional=round(size * price, 2),
            leverage=round(size * price / self.cfg.account_size, 2),
            method="ATR",
            rationale=f"ATR={atr:.0f}, SL거리={sl_distance:.0f}, 리스크={risk_amount:.0f}$"
        )

    # ── Kelly 기준 ────────────────────────────────────
    def _kelly_sizing(self, entry, sl, price) -> SizeResult:
        """
        Kelly Criterion.
        f = (WR × avgWin - (1-WR) × avgLoss) / avgWin
        Quarter Kelly로 보수적 적용.
        """
        wr    = self.cfg.win_rate
        w     = self.cfg.avg_win_r
        l     = self.cfg.avg_loss_r
        kelly = (wr * w - (1 - wr) * l) / max(w, 1e-9)
        kelly = max(0, kelly) * self.cfg.kelly_fraction   # Quarter Kelly

        risk_amount = self.cfg.account_size * kelly
        sl_distance = abs(entry - sl)
        size        = risk_amount / max(sl_distance, 1e-9)
        size        = self._apply_limits(size, price)

        return SizeResult(
            size=round(size, 4), risk_amount=round(risk_amount, 2),
            risk_pct=round(kelly * 100, 2), notional=round(size * price, 2),
            leverage=round(size * price / self.cfg.account_size, 2),
            method="Kelly",
            rationale=f"Kelly={kelly*100:.1f}% (WR={wr:.0%}, W/L={w}/{l})"
        )

    # ── Fixed Fractional ──────────────────────────────
    def _fixed_sizing(self, entry, sl, price) -> SizeResult:
        """고정 비율 사이징."""
        risk_amount = self.cfg.account_size * self.cfg.risk_pct / 100
        sl_distance = abs(entry - sl)
        size        = risk_amount / max(sl_distance, 1e-9)
        size        = self._apply_limits(size, price)

        return SizeResult(
            size=round(size, 4), risk_amount=round(risk_amount, 2),
            risk_pct=self.cfg.risk_pct, notional=round(size * price, 2),
            leverage=round(size * price / self.cfg.account_size, 2),
            method="Fixed",
            rationale=f"고정 {self.cfg.risk_pct}% 리스크"
        )

    # ── Risk Parity ───────────────────────────────────
    def _parity_sizing(self, entry, sl, atr, price, open_count) -> SizeResult:
        """
        리스크 균등 분배.
        열린 포지션이 많을수록 개별 사이즈를 줄여 총 리스크를 일정하게 유지.
        """
        # 총 허용 리스크를 포지션 수로 나눔
        total_risk_pct = self.cfg.risk_pct * 2   # 총 2% 리스크 예산
        per_pos_pct    = total_risk_pct / max(open_count + 1, 1)
        risk_amount    = self.cfg.account_size * per_pos_pct / 100
        sl_distance    = abs(entry - sl)
        size           = risk_amount / max(sl_distance, 1e-9)
        size           = self._apply_limits(size, price)

        return SizeResult(
            size=round(size, 4), risk_amount=round(risk_amount, 2),
            risk_pct=round(per_pos_pct, 2), notional=round(size * price, 2),
            leverage=round(size * price / self.cfg.account_size, 2),
            method="Parity",
            rationale=f"총 리스크 {total_risk_pct}% ÷ {open_count+1}포지션"
        )

    def _apply_limits(self, size: float, price: float) -> float:
        """최대 포지션 크기 제한."""
        max_size = self.cfg.account_size * self.cfg.max_size_pct / 100 / max(price, 1e-9)
        return min(size, max_size)

    def recommend(
        self,
        entry: float,
        sl:    float,
        atr:   Optional[float] = None,
        win_rate: Optional[float] = None,
    ) -> dict:
        """모든 방법을 비교해서 추천."""
        price = entry

        # 기본 설정
        cfg_base = SizingConfig(**vars(self.cfg))
        if win_rate:
            cfg_base.win_rate = win_rate

        results = {}
        for method in ["fixed", "atr", "kelly"]:
            cfg_base.method = method
            sizer = PositionSizer(cfg_base)
            r = sizer.calculate(entry, sl, atr, price)
            results[method] = {
                "size":        r.size,
                "risk_amount": r.risk_amount,
                "risk_pct":    r.risk_pct,
                "notional":    r.notional,
                "leverage":    r.leverage,
                "rationale":   r.rationale,
            }

        # 추천: ATR 방법 (아 없으면 Fixed)
        recommended = "atr" if atr else "fixed"
        return {
            "recommended": recommended,
            "methods":     results,
            "entry":       entry,
            "sl":          sl,
            "atr":         atr,
        }

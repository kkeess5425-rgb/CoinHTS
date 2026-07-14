"""
stats/statistics.py
===================
완전한 트레이딩 통계 엔진.

- 승률 / Profit Factor
- Sharpe Ratio / Sortino Ratio
- Expectancy (기대값)
- 최대 낙폭 (MDD)
- 평균 보유 시간
- 시간대별 승률
- 요일별 승률
- 연속 승/패 분석
- 손익 분포
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class TradeRecord:
    """단일 트레이드 기록."""
    entry_ts:  float
    exit_ts:   float
    direction: str     # "LONG" | "SHORT"
    entry:     float
    exit_price: float
    sl:        float
    tp:        float
    pnl_r:     float   # R 단위 손익
    pnl_usd:   float = 0.0
    symbol:    str   = ""


@dataclass
class Statistics:
    """전체 트레이딩 통계."""
    # 기본
    total_trades:    int   = 0
    win_trades:      int   = 0
    loss_trades:     int   = 0
    win_rate:        float = 0.0

    # 수익성
    total_r:         float = 0.0
    avg_r:           float = 0.0
    avg_win:         float = 0.0
    avg_loss:        float = 0.0
    profit_factor:   float = 0.0
    expectancy:      float = 0.0

    # 리스크 지표
    sharpe_ratio:    float = 0.0
    sortino_ratio:   float = 0.0
    max_drawdown:    float = 0.0    # MDD (%)
    max_drawdown_r:  float = 0.0    # MDD (R 단위)
    calmar_ratio:    float = 0.0    # 연수익/MDD

    # 연속성
    max_consec_wins:  int  = 0
    max_consec_losses:int  = 0

    # 시간 분석
    avg_hold_minutes: float = 0.0
    hourly_wr:        dict  = field(default_factory=dict)   # {0~23: wr%}
    daily_wr:         dict  = field(default_factory=dict)   # {0~6: wr%}

    # 분포
    r_distribution:   list  = field(default_factory=list)   # R 값 리스트


class StatisticsEngine:
    """
    트레이딩 통계 계산 엔진.
    TradeRecord 리스트로 전체 통계를 산출한다.
    """

    def __init__(self, risk_free_rate: float = 0.02) -> None:
        self.rfr = risk_free_rate / 252   # 일별 무위험 수익률

    def compute(self, trades: list[TradeRecord]) -> Statistics:
        """전체 통계 계산."""
        if not trades:
            return Statistics()

        closed = [t for t in trades if t.pnl_r != 0]
        if not closed:
            return Statistics(total_trades=len(trades))

        s = Statistics()
        s.total_trades = len(closed)

        pnl = np.array([t.pnl_r for t in closed])
        wins   = pnl[pnl > 0]
        losses = pnl[pnl < 0]

        s.win_trades  = len(wins)
        s.loss_trades = len(losses)
        s.win_rate    = len(wins) / len(closed) * 100
        s.total_r     = round(float(pnl.sum()), 3)
        s.avg_r       = round(float(pnl.mean()), 3)
        s.avg_win     = round(float(wins.mean()),  3) if len(wins)   else 0
        s.avg_loss    = round(float(losses.mean()),3) if len(losses) else 0

        # Profit Factor
        gross_win  = float(wins.sum())  if len(wins)   else 0
        gross_loss = abs(float(losses.sum())) if len(losses) else 1e-9
        s.profit_factor = round(gross_win / gross_loss, 3)

        # Expectancy = WR × avgWin - LR × avgLoss
        wr = s.win_rate / 100
        s.expectancy = round(wr * s.avg_win + (1-wr) * s.avg_loss, 3)

        # Sharpe & Sortino
        if len(pnl) > 1:
            std   = float(pnl.std())
            dwnsd = float(pnl[pnl < 0].std()) if len(losses) > 1 else 1e-9
            s.sharpe_ratio  = round((float(pnl.mean()) - self.rfr) / max(std, 1e-9) * np.sqrt(252), 3)
            s.sortino_ratio = round((float(pnl.mean()) - self.rfr) / max(dwnsd, 1e-9) * np.sqrt(252), 3)

        # MDD (최대 낙폭)
        equity = np.cumsum(pnl)
        peak   = np.maximum.accumulate(equity)
        dd     = equity - peak
        s.max_drawdown_r = round(float(dd.min()), 3)
        total_pnl = equity[-1]
        s.max_drawdown = round(abs(s.max_drawdown_r) / max(abs(equity.max()), 1e-9) * 100, 2)
        if s.max_drawdown > 0:
            s.calmar_ratio = round(total_pnl / s.max_drawdown * 100, 3)

        # 연속 승/패
        consec_w = consec_l = max_w = max_l = 0
        for r in pnl:
            if r > 0:
                consec_w += 1; consec_l = 0
                max_w = max(max_w, consec_w)
            else:
                consec_l += 1; consec_w = 0
                max_l = max(max_l, consec_l)
        s.max_consec_wins   = max_w
        s.max_consec_losses = max_l

        # 평균 보유 시간
        hold_times = [(t.exit_ts - t.entry_ts) / 60 for t in closed if t.exit_ts > t.entry_ts]
        s.avg_hold_minutes = round(float(np.mean(hold_times)), 1) if hold_times else 0

        # 시간대별 승률
        s.hourly_wr = self._hourly_wr(closed)
        s.daily_wr  = self._daily_wr(closed)

        # R 분포
        s.r_distribution = [round(float(r), 2) for r in pnl]

        return s

    def _hourly_wr(self, trades: list[TradeRecord]) -> dict:
        hourly: dict[int, list[float]] = {h: [] for h in range(24)}
        for t in trades:
            h = datetime.fromtimestamp(t.entry_ts, tz=timezone.utc).hour
            hourly[h].append(1.0 if t.pnl_r > 0 else 0.0)
        return {
            h: round(float(np.mean(v)) * 100, 1)
            for h, v in hourly.items() if v
        }

    def _daily_wr(self, trades: list[TradeRecord]) -> dict:
        daily: dict[int, list[float]] = {d: [] for d in range(7)}
        day_names = {0:"월",1:"화",2:"수",3:"목",4:"금",5:"토",6:"일"}
        for t in trades:
            d = datetime.fromtimestamp(t.entry_ts, tz=timezone.utc).weekday()
            daily[d].append(1.0 if t.pnl_r > 0 else 0.0)
        return {
            day_names[d]: round(float(np.mean(v)) * 100, 1)
            for d, v in daily.items() if v
        }

    def summary_text(self, s: Statistics) -> str:
        """통계 요약 텍스트."""
        lines = [
            f"총 트레이드: {s.total_trades}건 ({s.win_trades}승/{s.loss_trades}패)",
            f"승률: {s.win_rate:.1f}% | Profit Factor: {s.profit_factor:.2f}",
            f"기대값: {s.expectancy:+.3f}R | 누적R: {s.total_r:+.2f}R",
            f"Sharpe: {s.sharpe_ratio:.2f} | Sortino: {s.sortino_ratio:.2f}",
            f"MDD: {s.max_drawdown:.1f}% ({s.max_drawdown_r:.2f}R)",
            f"최대 연속손실: {s.max_consec_losses}회",
            f"평균 보유: {s.avg_hold_minutes:.0f}분",
        ]
        return "\n".join(lines)

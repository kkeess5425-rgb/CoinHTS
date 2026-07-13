"""
risk/risk_manager.py
====================
리스크 관리 엔진.
- ATR 기반 손절 / Trailing Stop
- 일일 최대 손실 제한
- 포지션 사이즈 계산 (Kelly Criterion 지원)
- Risk/Reward 검증
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class RiskParams:
    # 계좌 설정
    account_size:      float = 10_000.0   # USDT
    risk_per_trade:    float = 1.0         # % (1% 기본)
    max_daily_loss:    float = 3.0         # % (일일 최대 손실)
    max_open_trades:   int   = 3

    # ATR 기반 손절
    sl_atr_mult:       float = 1.5
    trailing_atr_mult: float = 2.0
    trailing_enabled:  bool  = True

    # RR 필터
    min_rr:            float = 2.0

    # Kelly Criterion
    use_kelly:         bool  = False
    win_rate_estimate: float = 0.5        # 기대 승률


@dataclass
class TradePosition:
    """현재 진행 중인 포지션."""
    symbol:       str
    direction:    str      # "LONG" / "SHORT"
    entry:        float
    sl:           float
    tp:           float
    size:         float    # 계약 수
    entry_ts:     float
    trailing_sl:  Optional[float] = None
    pnl:          float = 0.0
    peak_price:   float = 0.0   # Trailing용 최고/최저 기록


class RiskManager:
    """
    포지션 사이즈 및 손절 관리.
    전략 신호 수신 시 실제 진입 파라미터를 계산해서 반환한다.
    """

    def __init__(self, params: Optional[RiskParams] = None) -> None:
        self.params       = params or RiskParams()
        self._daily_loss  = 0.0
        self._open_trades: list[TradePosition] = []
        self._trade_count_today = 0

    # ── 진입 전 검증 ─────────────────────────────────
    def validate_signal(
        self,
        direction: str,
        entry:     float,
        sl:        float,
        tp:        float,
    ) -> tuple[bool, str]:
        """
        신호 진입 가능 여부 검증.
        반환: (True/False, 이유)
        """
        p = self.params

        # RR 검증
        risk   = abs(entry - sl)
        reward = abs(tp - entry)
        if risk <= 0:
            return False, "SL이 진입가와 같음"
        rr = reward / risk
        if rr < p.min_rr:
            return False, f"RR {rr:.2f} < 최소 {p.min_rr}"

        # 일일 손실 제한
        daily_loss_pct = self._daily_loss / p.account_size * 100
        if daily_loss_pct >= p.max_daily_loss:
            return False, f"일일 최대 손실 도달 ({daily_loss_pct:.1f}%)"

        # 동시 포지션 제한
        if len(self._open_trades) >= p.max_open_trades:
            return False, f"최대 동시 포지션 수 도달 ({p.max_open_trades})"

        return True, "OK"

    # ── 포지션 사이즈 계산 ────────────────────────────
    def calc_position_size(
        self,
        entry:      float,
        sl:         float,
        tick_value: float = 1.0,   # 틱당 USDT 가치
    ) -> float:
        """
        리스크 기반 포지션 사이즈 계산.
        risk_per_trade% 손실이 sl 도달 시 발생하도록 사이즈 결정.
        """
        p           = self.params
        risk_amount = p.account_size * p.risk_per_trade / 100
        risk_per_contract = abs(entry - sl) * tick_value

        if risk_per_contract <= 0:
            return 0.0

        size = risk_amount / risk_per_contract

        # Kelly Criterion 보정 (선택)
        if p.use_kelly:
            wr   = p.win_rate_estimate
            rr   = abs(entry - sl) / abs(entry - sl)   # 여기선 1:1 가정 (실제는 tp-entry/entry-sl)
            kelly = wr - (1 - wr) / rr if rr > 0 else 0
            kelly = max(0.0, min(kelly, 0.25))   # 최대 25% Kelly
            size  = min(size, p.account_size * kelly / risk_per_contract)

        return round(size, 4)

    # ── Trailing Stop 업데이트 ────────────────────────
    def update_trailing_stop(self, pos: TradePosition, current_price: float, atr_cur: float) -> None:
        """
        현재 가격으로 Trailing Stop 업데이트.
        ATR 기반: peak_price ± ATR × trailing_mult
        """
        if not self.params.trailing_enabled:
            return

        mult = self.params.trailing_atr_mult

        if pos.direction == "LONG":
            pos.peak_price = max(pos.peak_price, current_price)
            new_sl = pos.peak_price - atr_cur * mult
            if new_sl > pos.sl:
                logger.debug(f"Trailing SL 업데이트: {pos.sl:.2f} → {new_sl:.2f}")
                pos.trailing_sl = new_sl
                pos.sl = new_sl
        else:
            if pos.peak_price == 0.0:
                pos.peak_price = current_price
            pos.peak_price = min(pos.peak_price, current_price)
            new_sl = pos.peak_price + atr_cur * mult
            if new_sl < pos.sl:
                logger.debug(f"Trailing SL 업데이트: {pos.sl:.2f} → {new_sl:.2f}")
                pos.trailing_sl = new_sl
                pos.sl = new_sl

    # ── 포지션 관리 ───────────────────────────────────
    def open_position(self, pos: TradePosition) -> None:
        pos.peak_price = pos.entry
        self._open_trades.append(pos)
        logger.info(f"[RISK] 포지션 진입: {pos.symbol} {pos.direction} "
                    f"진입={pos.entry:.2f} SL={pos.sl:.2f} TP={pos.tp:.2f} Size={pos.size}")

    def close_position(self, pos: TradePosition, exit_price: float) -> float:
        """포지션 종료 → PnL 계산 및 기록."""
        if pos.direction == "LONG":
            pnl = (exit_price - pos.entry) * pos.size
        else:
            pnl = (pos.entry - exit_price) * pos.size

        pos.pnl = pnl
        if pnl < 0:
            self._daily_loss += abs(pnl)

        self._open_trades = [t for t in self._open_trades if t is not pos]
        logger.info(f"[RISK] 포지션 종료: {pos.symbol} PnL={pnl:+.2f} USDT")
        return pnl

    # ── 상태 조회 ─────────────────────────────────────
    @property
    def open_trades(self) -> list[TradePosition]:
        return list(self._open_trades)

    @property
    def daily_loss_pct(self) -> float:
        return self._daily_loss / self.params.account_size * 100

    def reset_daily(self) -> None:
        """자정에 일일 카운터 리셋."""
        self._daily_loss = 0.0
        self._trade_count_today = 0
        logger.info("[RISK] 일일 카운터 리셋")

    def get_stats(self) -> dict:
        return {
            "open_trades":    len(self._open_trades),
            "daily_loss":     round(self._daily_loss, 2),
            "daily_loss_pct": round(self.daily_loss_pct, 2),
            "account_size":   self.params.account_size,
        }

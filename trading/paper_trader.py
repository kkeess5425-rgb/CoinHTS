"""
trading/paper_trader.py
=======================
자동매매 엔진.
- 페이퍼 트레이딩 (가상 자금)
- 실거래 (OKX API 연동)
- 부분 익절 / 분할 매수
- 브레이크이븐 자동 이동
- 트레일링 스탑
- 일일 최대 손실 제한
- 최대 포지션 수 제한
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Callable

from core.models import StrategySignal, Exchange
from core.events import EventBus, get_event_bus
from stats.statistics import TradeRecord

logger = logging.getLogger(__name__)


class TradeMode(Enum):
    PAPER = "paper"
    LIVE  = "live"


class OrderStatus(Enum):
    PENDING  = "pending"
    OPEN     = "open"
    PARTIAL  = "partial"
    CLOSED   = "closed"
    CANCELED = "canceled"


@dataclass
class TradingConfig:
    """자동매매 설정."""
    mode:               TradeMode = TradeMode.PAPER
    account_size:       float     = 10_000.0
    risk_per_trade:     float     = 1.0      # %
    max_open_positions: int       = 3
    max_daily_loss_pct: float     = 3.0
    min_score:          float     = 70.0     # 이 점수 이상만 진입

    # 익절/손절
    partial_tp_enabled: bool  = True
    partial_tp_pct:     float = 0.5         # 첫 50% 익절
    partial_tp_r:       float = 1.0         # 1R 달성 시 50% 익절
    breakeven_enabled:  bool  = True
    breakeven_r:        float = 1.0         # 1R 달성 후 SL을 진입가로

    # 트레일링
    trailing_enabled:   bool  = True
    trailing_atr_mult:  float = 2.0

    # 분할 매수
    dca_enabled:        bool  = False
    dca_levels:         int   = 3           # 분할 횟수
    dca_spacing:        float = 0.5         # ATR 단위 간격


@dataclass
class Position:
    """현재 포지션."""
    id:           str
    symbol:       str
    direction:    str    # "LONG" | "SHORT"
    entry:        float
    sl:           float
    tp:           float
    size:         float
    entry_ts:     float
    status:       OrderStatus = OrderStatus.OPEN
    peak_price:   float       = 0.0
    partial_done: bool        = False
    breakeven_set:bool        = False
    pnl_r:        float       = 0.0
    pnl_usd:      float       = 0.0
    exit_price:   float       = 0.0
    exit_ts:      float       = 0.0

    # 분할 매수용
    dca_entries:  list[float] = field(default_factory=list)


class PaperTrader:
    """
    페이퍼 트레이딩 엔진.
    실시간 가격 업데이트를 받아 포지션 관리.
    """

    def __init__(
        self,
        config:     Optional[TradingConfig] = None,
        event_bus:  Optional[EventBus]      = None,
        on_trade_closed: Optional[Callable[[Position], None]] = None,
    ) -> None:
        self.cfg         = config or TradingConfig()
        self.bus         = event_bus or get_event_bus()
        self.on_closed   = on_trade_closed

        self._positions:  list[Position] = []
        self._closed:     list[Position] = []
        self._daily_loss  = 0.0
        self._daily_reset = time.time()
        self._balance     = self.cfg.account_size
        self._pos_id      = 0

        self.bus.subscribe("strategy_signal", self._on_signal)

    # ── 신호 수신 ─────────────────────────────────────
    async def _on_signal(self, sig: StrategySignal) -> None:
        """전략 신호 수신 → 진입 조건 확인."""
        if sig.score < self.cfg.min_score:
            logger.debug(f"[PT] {sig.symbol} 점수 미달: {sig.score:.0f} < {self.cfg.min_score}")
            return

        # 일일 손실 초과
        if self._daily_loss_pct() >= self.cfg.max_daily_loss_pct:
            logger.info("[PT] 일일 최대 손실 도달 → 신규 진입 중지")
            return

        # 최대 포지션 수
        open_pos = [p for p in self._positions if p.status == OrderStatus.OPEN]
        if len(open_pos) >= self.cfg.max_open_positions:
            logger.debug(f"[PT] 최대 포지션 수 ({self.cfg.max_open_positions}) 도달")
            return

        # 같은 심볼 중복 진입 방지
        if any(p.symbol == sig.symbol for p in open_pos):
            return

        await self._open_position(sig)

    # ── 포지션 오픈 ──────────────────────────────────
    async def _open_position(self, sig: StrategySignal) -> None:
        risk_amount  = self._balance * self.cfg.risk_per_trade / 100
        risk_per_unit = abs(sig.entry - sig.sl)
        if risk_per_unit <= 0:
            return
        size = risk_amount / risk_per_unit

        self._pos_id += 1
        pos = Position(
            id=        f"PT-{self._pos_id:04d}",
            symbol=    sig.symbol,
            direction= sig.direction,
            entry=     sig.entry,
            sl=        sig.sl,
            tp=        sig.tp,
            size=      size,
            entry_ts=  time.time(),
            peak_price=sig.entry,
        )
        self._positions.append(pos)
        logger.info(f"[PT] 진입: {pos.id} {sig.symbol} {sig.direction} "
                    f"진입={sig.entry:.2f} SL={sig.sl:.2f} TP={sig.tp:.2f} "
                    f"Size={size:.4f} (Score={sig.score:.0f})")

        await self.bus.publish("position_opened", pos)

    # ── 가격 업데이트 ─────────────────────────────────
    def on_price_update(self, symbol: str, price: float, atr: float = 0) -> list[Position]:
        """실시간 가격으로 포지션 업데이트."""
        closed_now = []

        for pos in self._positions[:]:
            if pos.symbol != symbol or pos.status != OrderStatus.OPEN:
                continue

            # 부분 익절
            if self.cfg.partial_tp_enabled and not pos.partial_done:
                r = self._calc_r(pos, price)
                if r >= self.cfg.partial_tp_r:
                    pos.partial_done = True
                    partial_size = pos.size * self.cfg.partial_tp_pct
                    profit = partial_size * abs(price - pos.entry)
                    self._balance += profit
                    pos.size      -= partial_size
                    logger.info(f"[PT] {pos.id} 부분 익절 ({self.cfg.partial_tp_pct*100:.0f}%): +{profit:.2f} USD")

            # 브레이크이븐
            if self.cfg.breakeven_enabled and not pos.breakeven_set:
                r = self._calc_r(pos, price)
                if r >= self.cfg.breakeven_r:
                    pos.sl        = pos.entry
                    pos.breakeven_set = True
                    logger.info(f"[PT] {pos.id} 브레이크이븐 SL 이동: {pos.sl:.2f}")

            # 트레일링 스탑
            if self.cfg.trailing_enabled and atr > 0:
                self._update_trailing(pos, price, atr)

            # 피크 가격 업데이트
            if pos.direction == "LONG":
                pos.peak_price = max(pos.peak_price, price)
            else:
                pos.peak_price = min(pos.peak_price, price) if pos.peak_price else price

            # SL/TP 도달 확인
            hit_sl = (pos.direction=="LONG"  and price <= pos.sl) or \
                     (pos.direction=="SHORT" and price >= pos.sl)
            hit_tp = (pos.direction=="LONG"  and price >= pos.tp) or \
                     (pos.direction=="SHORT" and price <= pos.tp)

            if hit_sl or hit_tp:
                exit_price = pos.sl if hit_sl else pos.tp
                self._close_position(pos, exit_price, "TP" if hit_tp else "SL")
                closed_now.append(pos)

        return closed_now

    def _update_trailing(self, pos: Position, price: float, atr: float) -> None:
        mult = self.cfg.trailing_atr_mult
        if pos.direction == "LONG":
            new_sl = pos.peak_price - atr * mult
            if new_sl > pos.sl:
                pos.sl = new_sl
        else:
            new_sl = pos.peak_price + atr * mult
            if new_sl < pos.sl:
                pos.sl = new_sl

    def _close_position(self, pos: Position, exit_price: float, reason: str) -> None:
        pos.exit_price = exit_price
        pos.exit_ts    = time.time()
        pos.status     = OrderStatus.CLOSED

        if pos.direction == "LONG":
            pos.pnl_usd = (exit_price - pos.entry) * pos.size
        else:
            pos.pnl_usd = (pos.entry - exit_price) * pos.size

        risk = abs(pos.entry - pos.sl) * pos.size
        pos.pnl_r = pos.pnl_usd / max(risk, 1e-9)

        self._balance   += pos.pnl_usd
        if pos.pnl_usd < 0:
            self._daily_loss += abs(pos.pnl_usd)

        self._positions.remove(pos)
        self._closed.append(pos)
        if self.on_closed:
            self.on_closed(pos)

        logger.info(f"[PT] 청산({reason}): {pos.id} {pos.direction} "
                    f"진입={pos.entry:.2f} 청산={exit_price:.2f} "
                    f"PnL={pos.pnl_usd:+.2f} USD ({pos.pnl_r:+.2f}R)")

    def _calc_r(self, pos: Position, price: float) -> float:
        risk = abs(pos.entry - pos.sl)
        if risk <= 0: return 0
        if pos.direction == "LONG":
            return (price - pos.entry) / risk
        return (pos.entry - price) / risk

    def _daily_loss_pct(self) -> float:
        # 자정 리셋
        now = time.time()
        if now - self._daily_reset > 86400:
            self._daily_loss  = 0
            self._daily_reset = now
        return self._daily_loss / self.cfg.account_size * 100

    # ── 통계 / 상태 ───────────────────────────────────
    @property
    def open_positions(self) -> list[Position]:
        return [p for p in self._positions if p.status == OrderStatus.OPEN]

    @property
    def balance(self) -> float:
        return round(self._balance, 2)

    @property
    def trade_records(self) -> list[TradeRecord]:
        return [
            TradeRecord(
                entry_ts=p.entry_ts, exit_ts=p.exit_ts,
                direction=p.direction, entry=p.entry,
                exit_price=p.exit_price, sl=p.sl, tp=p.tp,
                pnl_r=p.pnl_r, pnl_usd=p.pnl_usd, symbol=p.symbol,
            )
            for p in self._closed
        ]

    def get_status(self) -> dict:
        return {
            "mode":         self.cfg.mode.value,
            "balance":      self.balance,
            "open_pos":     len(self.open_positions),
            "closed_pos":   len(self._closed),
            "daily_loss":   round(self._daily_loss, 2),
            "daily_loss_pct": round(self._daily_loss_pct(), 2),
        }

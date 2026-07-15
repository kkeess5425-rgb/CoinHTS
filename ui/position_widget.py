"""
ui/position_widget.py
=====================
포지션 관리 패널.

- 현재 열린 포지션 목록
- 실시간 PnL 표시
- 즉시 청산 버튼
- 부분 익절 버튼
- 브레이크이븐 이동 버튼
- 트레일링 스탑 토글
- 매매일지 인라인 표시
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QFrame,
    QSplitter, QTextEdit,
)

from trading.paper_trader import PaperTrader, Position

logger = logging.getLogger(__name__)

_STYLE = """
QTableWidget {
    background: #0d1117;
    color: #c9d1d9;
    border: none;
    gridline-color: #21262d;
    font-size: 12px;
}
QTableWidget::item { padding: 4px 8px; }
QTableWidget::item:selected { background: #1f2937; }
QHeaderView::section {
    background: #161b22;
    color: #8b949e;
    border: none;
    border-bottom: 1px solid #21262d;
    padding: 4px 8px;
    font-size: 11px;
}
QPushButton {
    background: #21262d;
    color: #c9d1d9;
    border: 1px solid #30363d;
    border-radius: 4px;
    padding: 4px 10px;
    font-size: 11px;
}
QPushButton:hover { background: #30363d; }
QPushButton.danger { background: #2d1117; border-color: #f85149; color: #f85149; }
QPushButton.success { background: #0d2b1a; border-color: #3fb950; color: #3fb950; }
"""

COLS = ["심볼", "방향", "진입가", "현재가", "SL", "TP", "크기", "PnL(R)", "PnL(USD)", "점수", "액션"]


class PositionWidget(QWidget):
    """포지션 관리 패널."""

    close_requested   = Signal(str)   # position_id
    partial_requested = Signal(str, float)   # position_id, pct

    def __init__(self, trader: Optional[PaperTrader] = None, parent=None) -> None:
        super().__init__(parent)
        self.trader = trader
        self._current_prices: dict[str, float] = {}
        self._setup_ui()

        # 1초마다 PnL 갱신
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(1000)

    def _setup_ui(self) -> None:
        self.setStyleSheet(_STYLE + "QWidget { background: #0d1117; }")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── 헤더 ──
        header = QWidget()
        header.setStyleSheet("background:#161b22; border-bottom:1px solid #21262d;")
        h_lay  = QHBoxLayout(header)
        h_lay.setContentsMargins(12, 6, 12, 6)

        self._title_lbl = QLabel("📊 포지션 관리")
        self._title_lbl.setStyleSheet("color:#58a6ff; font-weight:bold; font-size:13px;")

        self._mode_lbl  = QLabel("● 페이퍼")
        self._mode_lbl.setStyleSheet("color:#8b949e; font-size:11px;")

        self._balance_lbl = QLabel("잔고: --")
        self._balance_lbl.setStyleSheet("color:#c9d1d9; font-size:12px; font-weight:bold;")

        close_all_btn = QPushButton("전체 청산")
        close_all_btn.setProperty("class", "danger")
        close_all_btn.setFixedWidth(80)
        close_all_btn.clicked.connect(self._close_all)

        h_lay.addWidget(self._title_lbl)
        h_lay.addWidget(self._mode_lbl)
        h_lay.addStretch()
        h_lay.addWidget(self._balance_lbl)
        h_lay.addWidget(close_all_btn)
        layout.addWidget(header)

        # ── 테이블 ──
        self._table = QTableWidget(0, len(COLS))
        self._table.setHorizontalHeaderLabels(COLS)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(10, QHeaderView.Fixed)
        self._table.setColumnWidth(10, 180)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QTableWidget.SelectRows)
        self._table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        layout.addWidget(self._table)

        # ── 일일 손실 표시 ──
        footer = QWidget()
        footer.setStyleSheet("background:#161b22; border-top:1px solid #21262d;")
        f_lay  = QHBoxLayout(footer)
        f_lay.setContentsMargins(12, 4, 12, 4)
        self._daily_lbl = QLabel("일일 손실: 0.00%")
        self._daily_lbl.setStyleSheet("color:#8b949e; font-size:11px;")
        f_lay.addWidget(self._daily_lbl)
        f_lay.addStretch()
        layout.addWidget(footer)

    def set_trader(self, trader: PaperTrader) -> None:
        self.trader = trader

    def update_price(self, symbol: str, price: float) -> None:
        self._current_prices[symbol] = price

    def _refresh(self) -> None:
        """포지션 테이블 갱신."""
        if not self.trader:
            return

        positions = self.trader.open_positions
        self._table.setRowCount(len(positions))

        # 잔고
        self._balance_lbl.setText(f"잔고: ${self.trader.balance:,.2f}")

        # 일일 손실
        dl = self.trader._daily_loss_pct()
        color = "#f85149" if dl > 2 else "#e3b341" if dl > 1 else "#8b949e"
        self._daily_lbl.setText(f"일일 손실: {dl:.2f}%")
        self._daily_lbl.setStyleSheet(f"color:{color}; font-size:11px;")

        for row, pos in enumerate(positions):
            cur  = self._current_prices.get(pos.symbol, pos.entry)
            r    = self._calc_r(pos, cur)
            pnl_usd = (cur - pos.entry) * pos.size if pos.direction == "LONG" else (pos.entry - cur) * pos.size

            vals = [
                pos.symbol.replace("-USDT-SWAP", ""),
                pos.direction,
                f"{pos.entry:.2f}",
                f"{cur:.2f}",
                f"{pos.sl:.2f}",
                f"{pos.tp:.2f}",
                f"{pos.size:.4f}",
                f"{r:+.2f}R",
                f"${pnl_usd:+.1f}",
                "",
            ]
            for col, val in enumerate(vals):
                item = QTableWidgetItem(val)
                item.setTextAlignment(Qt.AlignCenter)
                if col == 1:    # 방향
                    item.setForeground(QColor("#3fb950" if pos.direction=="LONG" else "#f85149"))
                if col == 7:    # PnL R
                    item.setForeground(QColor("#3fb950" if r >= 0 else "#f85149"))
                if col == 8:    # PnL USD
                    item.setForeground(QColor("#3fb950" if pnl_usd >= 0 else "#f85149"))
                self._table.setItem(row, col, item)

            # 액션 버튼들
            action_widget = self._make_action_widget(pos)
            self._table.setCellWidget(row, 10, action_widget)

    def _make_action_widget(self, pos: Position) -> QWidget:
        w   = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(4)
        w.setStyleSheet("background:transparent;")

        # 50% 익절
        tp50 = QPushButton("50%")
        tp50.setFixedSize(38, 22)
        tp50.setStyleSheet("background:#0d2b1a; border:1px solid #3fb950; color:#3fb950; border-radius:3px; font-size:10px;")
        tp50.clicked.connect(lambda _, p=pos: self.partial_requested.emit(p.id, 0.5))

        # 브레이크이븐
        be = QPushButton("BE")
        be.setFixedSize(32, 22)
        be.setStyleSheet("background:#1c1e26; border:1px solid #e3b341; color:#e3b341; border-radius:3px; font-size:10px;")
        be.clicked.connect(lambda _, p=pos: self._set_breakeven(p))

        # 청산
        close_btn = QPushButton("청산")
        close_btn.setFixedSize(38, 22)
        close_btn.setStyleSheet("background:#2d1117; border:1px solid #f85149; color:#f85149; border-radius:3px; font-size:10px;")
        close_btn.clicked.connect(lambda _, p=pos: self.close_requested.emit(p.id))

        lay.addWidget(tp50)
        lay.addWidget(be)
        lay.addWidget(close_btn)
        return w

    def _set_breakeven(self, pos: Position) -> None:
        pos.sl = pos.entry
        pos.breakeven_set = True
        logger.info(f"[Position] {pos.id} 브레이크이븐 설정: SL → {pos.sl:.2f}")

    def _close_all(self) -> None:
        if not self.trader:
            return
        for pos in list(self.trader.open_positions):
            cur = self._current_prices.get(pos.symbol, pos.entry)
            self.trader._close_position(pos, cur, "Manual")

    @staticmethod
    def _calc_r(pos: Position, price: float) -> float:
        risk = abs(pos.entry - pos.sl)
        if risk <= 0: return 0
        if pos.direction == "LONG":  return (price - pos.entry) / risk
        return (pos.entry - price) / risk

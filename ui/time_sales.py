"""
ui/time_sales.py
================
Time & Sales 패널.
실시간 체결 내역을 테이블로 표시한다.
- 매수/매도 색상 구분
- 대량 체결 하이라이트 (Whale Detection)
- 필터 (최소 체결 크기, 방향)
- 1초당 최대 N건 표시 (성능 보호)
"""
from __future__ import annotations

import logging
import time
from collections import deque
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QDoubleSpinBox, QCheckBox, QFrame, QPushButton,
)

from core.events import EventBus, get_event_bus
from core.models import Side, Tick

logger = logging.getLogger(__name__)

C_BUY   = "#3fb950"
C_SELL  = "#f85149"
C_WHALE = "#e3b341"   # 대량 체결 강조색


class TimeSalesWidget(QWidget):
    """
    실시간 Time & Sales.
    틱 데이터를 수신해서 최근 N건의 체결을 테이블로 표시.
    60FPS 렌더링이 아닌 QTimer(100ms) 기반 배치 업데이트로 CPU 절약.
    """

    def __init__(
        self,
        symbol:      str = "BTC-USDT-SWAP",
        max_rows:    int = 200,
        whale_size:  float = 1.0,    # 이 크기 이상은 Whale로 표시
        event_bus:   Optional[EventBus] = None,
        parent:      Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._symbol    = symbol
        self._max_rows  = max_rows
        self._whale_size = whale_size
        self._bus       = event_bus or get_event_bus()

        self._tick_buffer: deque[Tick] = deque(maxlen=500)
        self._min_size    = 0.0
        self._show_buy    = True
        self._show_sell   = True
        self._row_count   = 0

        self._setup_ui()
        self._setup_timer()
        self._connect_events()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # ── 헤더 ──
        header = QHBoxLayout()
        title  = QLabel("⏱ Time & Sales")
        title.setStyleSheet("color: #c9d1d9; font-weight: bold; font-size: 11px;")
        self._tps_label = QLabel("0 t/s")
        self._tps_label.setStyleSheet("color: #8b949e; font-size: 10px;")
        header.addWidget(title)
        header.addStretch()
        header.addWidget(self._tps_label)
        layout.addLayout(header)

        # ── 필터 바 ──
        filter_frame = QFrame()
        filter_frame.setStyleSheet("QFrame { background: #161b22; border-radius: 3px; padding: 2px; }")
        filter_layout = QHBoxLayout(filter_frame)
        filter_layout.setContentsMargins(4, 2, 4, 2)

        filter_layout.addWidget(QLabel("최소:"))
        self._min_spin = QDoubleSpinBox()
        self._min_spin.setRange(0, 1000)
        self._min_spin.setValue(0)
        self._min_spin.setSingleStep(0.1)
        self._min_spin.setDecimals(2)
        self._min_spin.setStyleSheet("QDoubleSpinBox { background: #21262d; color: #c9d1d9; border: 1px solid #30363d; padding: 2px; }")
        self._min_spin.valueChanged.connect(lambda v: setattr(self, '_min_size', v))
        filter_layout.addWidget(self._min_spin)

        self._buy_cb  = QCheckBox("매수")
        self._sell_cb = QCheckBox("매도")
        for cb, attr in [(self._buy_cb, '_show_buy'), (self._sell_cb, '_show_sell')]:
            cb.setChecked(True)
            cb.setStyleSheet("QCheckBox { color: #8b949e; font-size: 10px; }")
            cb.stateChanged.connect(lambda s, a=attr: setattr(self, a, bool(s)))
            filter_layout.addWidget(cb)

        filter_layout.addStretch()
        clear_btn = QPushButton("초기화")
        clear_btn.setStyleSheet("QPushButton { background: #21262d; color: #8b949e; border: 1px solid #30363d; padding: 2px 6px; border-radius: 3px; font-size: 10px; }")
        clear_btn.clicked.connect(self._clear)
        filter_layout.addWidget(clear_btn)
        layout.addWidget(filter_frame)

        # ── 테이블 ──
        self._table = QTableWidget()
        self._table.setColumnCount(4)
        self._table.setHorizontalHeaderLabels(["시간", "가격", "수량", "방향"])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.setShowGrid(False)
        self._table.setStyleSheet("""
            QTableWidget {
                background: #0d1117; color: #c9d1d9;
                border: none; font-size: 11px; font-family: monospace;
            }
            QTableWidget::item { padding: 1px 4px; border: none; }
            QTableWidget::item:selected { background: #1f6feb22; }
            QHeaderView::section {
                background: #161b22; color: #8b949e;
                border: none; border-bottom: 1px solid #21262d;
                padding: 3px; font-size: 10px;
            }
        """)
        layout.addWidget(self._table)

        # ── 통계 바 ──
        stats = QHBoxLayout()
        self._buy_vol_label  = QLabel("매수: 0")
        self._sell_vol_label = QLabel("매도: 0")
        self._ratio_label    = QLabel("비율: -")
        for lbl in [self._buy_vol_label, self._sell_vol_label, self._ratio_label]:
            lbl.setStyleSheet("color: #8b949e; font-size: 10px;")
            stats.addWidget(lbl)
        stats.addStretch()
        layout.addLayout(stats)

        self._buy_vol_total  = 0.0
        self._sell_vol_total = 0.0

    def _setup_timer(self) -> None:
        """100ms 배치 업데이트 타이머."""
        self._update_timer = QTimer(self)
        self._update_timer.setInterval(100)
        self._update_timer.timeout.connect(self._flush_buffer)
        self._update_timer.start()

        # TPS 계산용
        self._tick_count = 0
        self._last_tps   = time.time()
        self._tps_timer  = QTimer(self)
        self._tps_timer.setInterval(1000)
        self._tps_timer.timeout.connect(self._update_tps)
        self._tps_timer.start()

    def _connect_events(self) -> None:
        self._bus.subscribe("tick", self._on_tick)

    def set_symbol(self, symbol: str) -> None:
        """심볼 변경."""
        self._symbol = symbol
        self._clear()

    # ── 틱 수신 ──────────────────────────────────
    def _on_tick(self, tick: Tick) -> None:
        if tick.symbol != self._symbol:
            return
        self._tick_buffer.append(tick)
        self._tick_count += 1

        if tick.side == Side.BUY:
            self._buy_vol_total  += tick.size
        else:
            self._sell_vol_total += tick.size

    # ── 배치 업데이트 ─────────────────────────────
    def _flush_buffer(self) -> None:
        if not self._tick_buffer:
            return

        # 버퍼에서 최대 50건 꺼내기
        batch = []
        for _ in range(min(50, len(self._tick_buffer))):
            if self._tick_buffer:
                batch.append(self._tick_buffer.popleft())

        for tick in batch:
            if tick.size < self._min_size:
                continue
            if tick.side == Side.BUY  and not self._show_buy:
                continue
            if tick.side == Side.SELL and not self._show_sell:
                continue

            self._add_row(tick)

        # 최대 행 수 유지
        while self._table.rowCount() > self._max_rows:
            self._table.removeRow(self._table.rowCount() - 1)

        self._update_stats()

    def _add_row(self, tick: Tick) -> None:
        is_buy  = tick.side == Side.BUY
        color   = C_BUY if is_buy else C_SELL
        is_whale = tick.size >= self._whale_size

        ts_str    = time.strftime("%H:%M:%S", time.localtime(tick.ts))
        dir_str   = "▲ BUY" if is_buy else "▼ SELL"
        bg_color  = QColor(C_WHALE).darker(180) if is_whale else QColor(0, 0, 0, 0)

        self._table.insertRow(0)
        row_data = [ts_str, f"{tick.price:.2f}", f"{tick.size:.4f}", dir_str]
        for col, text in enumerate(row_data):
            item = QTableWidgetItem(text)
            item.setForeground(QColor(C_WHALE if is_whale else color))
            item.setBackground(bg_color)
            if col == 2 and is_whale:
                font = item.font()
                font.setBold(True)
                item.setFont(font)
            self._table.setItem(0, col, item)

        # 행 높이
        self._table.setRowHeight(0, 18)

    def _update_tps(self) -> None:
        now     = time.time()
        elapsed = now - self._last_tps
        tps     = self._tick_count / elapsed if elapsed > 0 else 0
        self._tps_label.setText(f"{tps:.0f} t/s")
        self._tick_count = 0
        self._last_tps   = now

    def _update_stats(self) -> None:
        total = self._buy_vol_total + self._sell_vol_total
        ratio = (self._buy_vol_total / total * 100) if total > 0 else 50
        self._buy_vol_label.setStyleSheet(f"color: {C_BUY}; font-size: 10px;")
        self._sell_vol_label.setStyleSheet(f"color: {C_SELL}; font-size: 10px;")
        self._buy_vol_label.setText(f"매수: {self._buy_vol_total:.2f}")
        self._sell_vol_label.setText(f"매도: {self._sell_vol_total:.2f}")
        buy_color = C_BUY if ratio >= 50 else C_SELL
        self._ratio_label.setStyleSheet(f"color: {buy_color}; font-size: 10px;")
        self._ratio_label.setText(f"매수비: {ratio:.1f}%")

    def _clear(self) -> None:
        self._table.setRowCount(0)
        self._buy_vol_total  = 0.0
        self._sell_vol_total = 0.0
        self._update_stats()

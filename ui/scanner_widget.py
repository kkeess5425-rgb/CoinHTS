"""
ui/scanner_widget.py
====================
실시간 스캐너 결과 패널.
볼륨급증/OI급증/Absorption/LiquiditySweep 등을
테이블 형태로 표시하고 신호 클릭 시 차트 이동.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QPushButton, QComboBox, QCheckBox, QFrame,
)

from core.events import EventBus, get_event_bus
from core.models import ScannerSignal

logger = logging.getLogger(__name__)

# 신호 타입별 색상/아이콘
SIGNAL_STYLES: dict[str, tuple[str, str]] = {
    "VOLUME_SPIKE":     ("#e3b341", "📊"),
    "OI_SURGE":         ("#58a6ff", "📈"),
    "FUNDING_EXTREME":  ("#f85149", "⚠️"),
    "DELTA_BURST":      ("#bc8cff", "⚡"),
    "BULL_ABSORPTION":  ("#3fb950", "🐂"),
    "BEAR_ABSORPTION":  ("#f85149", "🐻"),
    "BULL_SWEEP":       ("#3fb950", "🎯"),
    "BEAR_SWEEP":       ("#f85149", "🎯"),
}


class SignalBadge(QLabel):
    """신호 타입 배지 (색상 + 아이콘)."""

    def __init__(self, signal_type: str) -> None:
        color, icon = SIGNAL_STYLES.get(signal_type, ("#8b949e", "📡"))
        super().__init__(f"{icon} {signal_type.replace('_', ' ')}")
        self.setStyleSheet(f"""
            QLabel {{
                color: {color};
                background: {color}22;
                border: 1px solid {color}44;
                border-radius: 3px;
                padding: 2px 6px;
                font-size: 10px;
                font-weight: bold;
            }}
        """)


class ScannerWidget(QWidget):
    """
    실시간 스캐너 결과 패널.
    EventBus의 scanner_signal을 구독해서 테이블에 표시한다.
    """

    # 신호 클릭 시 외부로 전달 (메인 차트에서 해당 심볼로 이동)
    signal_clicked = Signal(str, float)   # symbol, ts

    def __init__(
        self,
        event_bus: Optional[EventBus] = None,
        parent:    Optional[QWidget]  = None,
    ) -> None:
        super().__init__(parent)
        self._bus     = event_bus or get_event_bus()
        self._signals: list[ScannerSignal] = []
        self._filters: set[str] = set()   # 비활성 필터 타입

        self._setup_ui()
        self._connect_events()
        self._start_cleanup_timer()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # ── 헤더 ──
        header = QHBoxLayout()
        title  = QLabel("🔍 실시간 스캐너")
        title.setStyleSheet("color: #c9d1d9; font-weight: bold; font-size: 12px;")
        self._count_label = QLabel("0건")
        self._count_label.setStyleSheet("color: #8b949e; font-size: 11px;")
        clear_btn = QPushButton("🗑 초기화")
        clear_btn.setStyleSheet("""
            QPushButton { background: #21262d; color: #8b949e; border: 1px solid #30363d;
                          padding: 2px 8px; border-radius: 3px; font-size: 10px; }
            QPushButton:hover { background: #30363d; }
        """)
        clear_btn.clicked.connect(self._clear)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(self._count_label)
        header.addWidget(clear_btn)
        layout.addLayout(header)

        # ── 필터 체크박스 ──
        filter_frame = QFrame()
        filter_frame.setStyleSheet("QFrame { background: #161b22; border: 1px solid #21262d; border-radius: 4px; }")
        filter_layout = QHBoxLayout(filter_frame)
        filter_layout.setContentsMargins(6, 4, 6, 4)
        filter_label = QLabel("필터:")
        filter_label.setStyleSheet("color: #8b949e; font-size: 10px;")
        filter_layout.addWidget(filter_label)

        self._filter_checks: dict[str, QCheckBox] = {}
        filter_types = [
            ("볼륨",    "VOLUME_SPIKE"),
            ("OI",      "OI_SURGE"),
            ("펀딩",    "FUNDING_EXTREME"),
            ("Delta",   "DELTA_BURST"),
            ("Absorp",  "BULL_ABSORPTION"),
            ("Sweep",   "BULL_SWEEP"),
        ]
        for label, key in filter_types:
            cb = QCheckBox(label)
            cb.setChecked(True)
            cb.setStyleSheet("QCheckBox { color: #8b949e; font-size: 10px; }")
            cb.stateChanged.connect(lambda state, k=key: self._toggle_filter(k, state))
            self._filter_checks[key] = cb
            filter_layout.addWidget(cb)
        filter_layout.addStretch()
        layout.addWidget(filter_frame)

        # ── 테이블 ──
        self._table = QTableWidget()
        self._table.setColumnCount(5)
        self._table.setHorizontalHeaderLabels(["시간", "심볼", "신호 타입", "값", "메시지"])
        self._table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setDefaultSectionSize(90)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.cellDoubleClicked.connect(self._on_row_double_clicked)
        self._table.setStyleSheet("""
            QTableWidget {
                background: #0d1117; color: #c9d1d9;
                border: 1px solid #21262d; gridline-color: #21262d;
                font-size: 11px;
            }
            QTableWidget::item:selected { background: #1f6feb44; }
            QTableWidget::item:alternate { background: #161b22; }
            QHeaderView::section {
                background: #161b22; color: #8b949e;
                border: none; border-bottom: 1px solid #21262d;
                padding: 4px; font-size: 10px;
            }
        """)
        layout.addWidget(self._table)

        # ── 통계 바 ──
        stats_layout = QHBoxLayout()
        self._stats_labels: dict[str, QLabel] = {}
        for key, (color, icon) in SIGNAL_STYLES.items():
            lbl = QLabel(f"{icon} 0")
            lbl.setStyleSheet(f"color: {color}; font-size: 10px;")
            lbl.setToolTip(key)
            self._stats_labels[key] = lbl
            stats_layout.addWidget(lbl)
        stats_layout.addStretch()
        layout.addLayout(stats_layout)

    def _connect_events(self) -> None:
        self._bus.subscribe("scanner_signal", self._on_signal)

    def _start_cleanup_timer(self) -> None:
        """오래된 신호 자동 제거 (5분 이상)."""
        timer = QTimer(self)
        timer.setInterval(60_000)   # 1분마다
        timer.timeout.connect(self._cleanup_old)
        timer.start()

    # ── 신호 수신 ─────────────────────────────────────
    def _on_signal(self, sig: ScannerSignal) -> None:
        """EventBus에서 스캐너 신호 수신."""
        # 필터 체크
        base_type = sig.signal_type.replace("BEAR_", "BULL_")
        if base_type in self._filters:
            return

        self._signals.insert(0, sig)

        # 테이블 맨 위에 행 추가
        self._table.insertRow(0)
        ts_str = time.strftime("%H:%M:%S", time.localtime(sig.ts))
        color, icon = SIGNAL_STYLES.get(sig.signal_type, ("#8b949e", "📡"))

        items = [
            QTableWidgetItem(ts_str),
            QTableWidgetItem(sig.symbol),
            QTableWidgetItem(f"{icon} {sig.signal_type.replace('_', ' ')}"),
            QTableWidgetItem(f"{sig.value:.4f}"),
            QTableWidgetItem(sig.message),
        ]
        for col, item in enumerate(items):
            item.setForeground(QColor(color if col == 2 else "#c9d1d9"))
            item.setData(Qt.ItemDataRole.UserRole, sig)
            self._table.setItem(0, col, item)

        # 최대 200행 유지
        while self._table.rowCount() > 200:
            self._table.removeRow(self._table.rowCount() - 1)
            if self._signals:
                self._signals.pop()

        self._update_stats()

    def _update_stats(self) -> None:
        """통계 카운터 업데이트."""
        counts: dict[str, int] = {}
        for sig in self._signals:
            counts[sig.signal_type] = counts.get(sig.signal_type, 0) + 1

        for key, lbl in self._stats_labels.items():
            bear_key = key.replace("BULL_", "BEAR_")
            cnt = counts.get(key, 0) + counts.get(bear_key, 0)
            icon = SIGNAL_STYLES.get(key, ("#8b949e","📡"))[1]
            lbl.setText(f"{icon} {cnt}")

        self._count_label.setText(f"{len(self._signals)}건")

    def _toggle_filter(self, key: str, state: int) -> None:
        bear_key = key.replace("BULL_", "BEAR_")
        if state == Qt.CheckState.Checked.value:
            self._filters.discard(key)
            self._filters.discard(bear_key)
        else:
            self._filters.add(key)
            self._filters.add(bear_key)

    def _on_row_double_clicked(self, row: int, col: int) -> None:
        item = self._table.item(row, 0)
        if item:
            sig: ScannerSignal = item.data(Qt.ItemDataRole.UserRole)
            if sig:
                self.signal_clicked.emit(sig.symbol, sig.ts)

    def _clear(self) -> None:
        self._signals.clear()
        self._table.setRowCount(0)
        self._update_stats()

    def _cleanup_old(self) -> None:
        """5분 이상 된 신호 제거."""
        cutoff = time.time() - 300
        before = len(self._signals)
        self._signals = [s for s in self._signals if s.ts > cutoff]
        removed = before - len(self._signals)
        if removed > 0:
            # 테이블 재구성
            self._table.setRowCount(0)
            for sig in self._signals:
                self._on_signal(sig)

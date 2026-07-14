"""
ui/multi_chart.py
=================
멀티차트 레이아웃 위젯.
TradingView처럼 여러 심볼/타임프레임을 동시에 볼 수 있다.
- 1×1 (단일)
- 2×1 (좌우)
- 1×2 (상하)
- 2×2 (4분할)
- 1+3 (왼쪽 크게 + 오른쪽 3개)
각 차트는 독립적인 MainChartWidget 인스턴스.
"""
from __future__ import annotations

import logging
from enum import Enum
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QSplitter, QLabel, QComboBox, QPushButton,
    QFrame, QSizePolicy, QButtonGroup, QToolButton,
)

from core.events import EventBus, get_event_bus
from core.models import Candle, Timeframe
from ui.chart_widget import MainChartWidget

logger = logging.getLogger(__name__)


class LayoutMode(Enum):
    SINGLE   = "1×1"
    TWO_H    = "2×1"   # 좌우 2분할
    TWO_V    = "1×2"   # 상하 2분할
    FOUR     = "2×2"   # 4분할
    ONE_PLUS = "1+3"   # 왼쪽 크게 + 우측 3개


class ChartPanel(QFrame):
    """
    단일 차트 패널 (심볼/타임프레임 선택 + 차트).
    활성화(클릭)되면 파란 테두리로 표시.
    """
    activated = Signal(object)   # 활성화된 ChartPanel 전달

    DEFAULT_SYMBOLS = [
        "BTC-USDT-SWAP", "ETH-USDT-SWAP",
        "SOL-USDT-SWAP", "BNB-USDT-SWAP",
    ]
    DEFAULT_TFS = ["1m", "3m", "5m", "15m", "1H", "4H"]

    def __init__(
        self,
        symbol:    str = "BTC-USDT-SWAP",
        timeframe: str = "15m",
        index:     int = 0,
        parent:    Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.index     = index
        self._symbol   = symbol
        self._timeframe = timeframe
        self._active   = False
        self._candles: list[Candle] = []

        self._setup_ui()
        self._set_active(False)

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)

        # ── 패널 헤더 (심볼/타임프레임 선택) ──
        header = QFrame()
        header.setFixedHeight(28)
        header.setStyleSheet("QFrame { background: #161b22; border-bottom: 1px solid #21262d; }")
        h_layout = QHBoxLayout(header)
        h_layout.setContentsMargins(6, 2, 6, 2)

        self._sym_combo = QComboBox()
        self._sym_combo.addItems(self.DEFAULT_SYMBOLS)
        self._sym_combo.setCurrentText(self._symbol)
        self._sym_combo.setStyleSheet("""
            QComboBox { background: #21262d; color: #c9d1d9;
                        border: none; padding: 2px 4px; font-size: 11px; }
            QComboBox::drop-down { border: none; }
        """)
        self._sym_combo.currentTextChanged.connect(self._on_symbol_changed)

        self._tf_combo = QComboBox()
        self._tf_combo.addItems(self.DEFAULT_TFS)
        self._tf_combo.setCurrentText(self._timeframe)
        self._tf_combo.setStyleSheet(self._sym_combo.styleSheet())
        self._tf_combo.setFixedWidth(55)
        self._tf_combo.currentTextChanged.connect(self._on_tf_changed)

        # 인덱스 번호
        self._idx_label = QLabel(f" {self.index + 1} ")
        self._idx_label.setStyleSheet("color: #8b949e; font-size: 10px; background: #30363d; "
                                       "border-radius: 2px; padding: 1px 3px;")

        h_layout.addWidget(self._idx_label)
        h_layout.addWidget(self._sym_combo)
        h_layout.addWidget(self._tf_combo)
        h_layout.addStretch()

        # 차트 위젯
        self._chart = MainChartWidget()
        self._chart.mousePressEvent = lambda e: self.activated.emit(self)

        layout.addWidget(header)
        layout.addWidget(self._chart, stretch=1)

    def _set_active(self, active: bool) -> None:
        self._active = active
        if active:
            self.setStyleSheet("QFrame { border: 2px solid #1f6feb; border-radius: 2px; }")
        else:
            self.setStyleSheet("QFrame { border: 1px solid #30363d; border-radius: 2px; }")

    def _on_symbol_changed(self, sym: str) -> None:
        self._symbol = sym
        get_event_bus().publish_nowait("panel_symbol_changed", {
            "panel": self.index, "symbol": sym, "timeframe": self._timeframe
        })

    def _on_tf_changed(self, tf: str) -> None:
        self._timeframe = tf
        get_event_bus().publish_nowait("panel_symbol_changed", {
            "panel": self.index, "symbol": self._symbol, "timeframe": tf
        })

    def set_candles(self, candles: list[Candle]) -> None:
        self._candles = candles
        self._chart.set_candles(candles)

    @property
    def symbol(self) -> str:
        return self._symbol

    @property
    def timeframe(self) -> str:
        return self._timeframe

    @property
    def chart(self) -> MainChartWidget:
        return self._chart


class MultiChartWidget(QWidget):
    """
    멀티차트 레이아웃 매니저.
    레이아웃 모드를 전환하면 패널 수와 배치가 변경된다.
    """

    active_panel_changed = Signal(int)   # 활성 패널 인덱스

    def __init__(
        self,
        event_bus: Optional[EventBus] = None,
        parent:    Optional[QWidget]  = None,
    ) -> None:
        super().__init__(parent)
        self._bus        = event_bus or get_event_bus()
        self._panels:    list[ChartPanel] = []
        self._active_idx = 0
        self._mode       = LayoutMode.SINGLE
        self._splitter:  Optional[QSplitter] = None

        self._setup_ui()
        self._apply_layout(LayoutMode.SINGLE)
        self._bus.subscribe("panel_symbol_changed", self._on_panel_symbol_changed)

    def _setup_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── 레이아웃 전환 툴바 ──
        toolbar = QFrame()
        toolbar.setFixedHeight(30)
        toolbar.setStyleSheet("QFrame { background: #0d1117; border-bottom: 1px solid #21262d; }")
        t_layout = QHBoxLayout(toolbar)
        t_layout.setContentsMargins(6, 2, 6, 2)
        t_layout.setSpacing(4)

        layout_label = QLabel("레이아웃:")
        layout_label.setStyleSheet("color: #8b949e; font-size: 10px;")
        t_layout.addWidget(layout_label)

        self._layout_group = QButtonGroup(self)
        self._layout_group.setExclusive(True)

        for mode in LayoutMode:
            btn = QToolButton()
            btn.setText(mode.value)
            btn.setCheckable(True)
            btn.setStyleSheet("""
                QToolButton { background: #21262d; color: #8b949e; border: none;
                              padding: 2px 8px; border-radius: 3px; font-size: 10px; }
                QToolButton:checked { background: #1f6feb; color: white; }
                QToolButton:hover   { background: #30363d; }
            """)
            btn.clicked.connect(lambda checked, m=mode: self._apply_layout(m))
            self._layout_group.addButton(btn)
            t_layout.addWidget(btn)
            if mode == LayoutMode.SINGLE:
                btn.setChecked(True)

        t_layout.addStretch()

        # 활성 패널 표시
        self._active_label = QLabel("활성: 패널 1")
        self._active_label.setStyleSheet("color: #58a6ff; font-size: 10px;")
        t_layout.addWidget(self._active_label)

        outer.addWidget(toolbar)

        # ── 차트 영역 (동적으로 채움) ──
        self._chart_area = QWidget()
        self._chart_area.setStyleSheet("QWidget { background: #0d1117; }")
        outer.addWidget(self._chart_area, stretch=1)

    def _apply_layout(self, mode: LayoutMode) -> None:
        """레이아웃 전환 — 기존 패널 정리 후 새 배치."""
        self._mode = mode

        # 기존 레이아웃 정리
        old_layout = self._chart_area.layout()
        if old_layout:
            while old_layout.count():
                item = old_layout.takeAt(0)
                if item.widget():
                    item.widget().setParent(None)
            old_layout.deleteLater()

        # 필요한 패널 수
        n_needed = {
            LayoutMode.SINGLE:   1,
            LayoutMode.TWO_H:    2,
            LayoutMode.TWO_V:    2,
            LayoutMode.FOUR:     4,
            LayoutMode.ONE_PLUS: 4,
        }[mode]

        # 패널 생성/재사용
        while len(self._panels) < n_needed:
            panel = ChartPanel(
                symbol=    ["BTC-USDT-SWAP","ETH-USDT-SWAP","SOL-USDT-SWAP","BNB-USDT-SWAP"][len(self._panels)],
                timeframe= "15m",
                index=     len(self._panels),
            )
            panel.activated.connect(self._on_panel_activated)
            self._panels.append(panel)

        # 레이아웃 배치
        layout = QVBoxLayout(self._chart_area)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        if mode == LayoutMode.SINGLE:
            layout.addWidget(self._panels[0])

        elif mode == LayoutMode.TWO_H:
            splitter = QSplitter(Qt.Orientation.Horizontal)
            splitter.addWidget(self._panels[0])
            splitter.addWidget(self._panels[1])
            splitter.setSizes([1, 1])
            layout.addWidget(splitter)

        elif mode == LayoutMode.TWO_V:
            splitter = QSplitter(Qt.Orientation.Vertical)
            splitter.addWidget(self._panels[0])
            splitter.addWidget(self._panels[1])
            splitter.setSizes([1, 1])
            layout.addWidget(splitter)

        elif mode == LayoutMode.FOUR:
            v_split = QSplitter(Qt.Orientation.Vertical)
            top_split = QSplitter(Qt.Orientation.Horizontal)
            bot_split = QSplitter(Qt.Orientation.Horizontal)
            top_split.addWidget(self._panels[0])
            top_split.addWidget(self._panels[1])
            bot_split.addWidget(self._panels[2])
            bot_split.addWidget(self._panels[3])
            v_split.addWidget(top_split)
            v_split.addWidget(bot_split)
            v_split.setSizes([1, 1])
            layout.addWidget(v_split)

        elif mode == LayoutMode.ONE_PLUS:
            h_split = QSplitter(Qt.Orientation.Horizontal)
            right_split = QSplitter(Qt.Orientation.Vertical)
            right_split.addWidget(self._panels[1])
            right_split.addWidget(self._panels[2])
            right_split.addWidget(self._panels[3])
            h_split.addWidget(self._panels[0])
            h_split.addWidget(right_split)
            h_split.setSizes([2, 1])
            layout.addWidget(h_split)

        # 첫 번째 패널 활성화
        self._set_active(0)
        logger.debug(f"멀티차트 레이아웃: {mode.value}")

    def _on_panel_activated(self, panel: ChartPanel) -> None:
        self._set_active(panel.index)

    def _set_active(self, idx: int) -> None:
        for i, panel in enumerate(self._panels):
            panel._set_active(i == idx)
        self._active_idx = idx
        self._active_label.setText(f"활성: 패널 {idx + 1}")
        self.active_panel_changed.emit(idx)

    def _on_panel_symbol_changed(self, info: dict) -> None:
        """패널 심볼 변경 → 데이터 로드 요청 (main_window에서 처리)."""
        pass

    # ── 외부 API ──────────────────────────────────
    def set_candles(self, candles: list[Candle], panel_idx: int = 0) -> None:
        if 0 <= panel_idx < len(self._panels):
            self._panels[panel_idx].set_candles(candles)

    def get_active_panel(self) -> Optional[ChartPanel]:
        if 0 <= self._active_idx < len(self._panels):
            return self._panels[self._active_idx]
        return None

    @property
    def panels(self) -> list[ChartPanel]:
        return list(self._panels)

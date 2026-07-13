"""
ui/footprint_widget.py
======================
독립 Footprint 차트 위젯 (Bookmap / Exocharts 스타일).
- 가격 레벨별 매수/매도 볼륨 텍스트 + 색상 히트맵
- Delta 바 (하단)
- Imbalance 하이라이트 (4:1 이상)
- Stacked Imbalance 감지
- POC 마커
- 실시간 업데이트 (진행 중인 봉 포함)
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer, QRectF
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QBrush
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QScrollArea, QFrame, QSizePolicy, QSplitter,
    QSpinBox, QPushButton, QCheckBox,
)

from core.models import FootprintBar
from core.events import EventBus, get_event_bus

logger = logging.getLogger(__name__)

# 색상
C_BG           = "#0d1117"
C_BUY_WEAK     = "#0d2b1a"
C_BUY_STRONG   = "#196127"
C_BUY_MAX      = "#2ea043"
C_SELL_WEAK    = "#2d1117"
C_SELL_STRONG  = "#6e1a1a"
C_SELL_MAX     = "#c93c37"
C_IMBALANCE_BG = "#1a3a2a"
C_BEAR_IMBAL   = "#3a1a1a"
C_POC          = "#ff9800"
C_DELTA_POS    = "#3fb950"
C_DELTA_NEG    = "#f85149"
C_TEXT         = "#8b949e"
C_GRID         = "#21262d"


class FootprintCell(pg.GraphicsObject):
    """
    단일 Footprint 셀 (가격 레벨 1개).
    매수/매도 볼륨을 텍스트와 배경 색상으로 표현.
    """
    def __init__(
        self,
        x: float,      # 봉 시간 (X 좌표)
        y: float,      # 가격 레벨 (Y 좌표)
        buy_vol:  float,
        sell_vol: float,
        cell_h:   float,  # 셀 높이 (틱 사이즈)
        cell_w:   float,  # 셀 너비 (봉 너비)
        max_vol:  float,  # 정규화용 최대 볼륨
        is_poc:   bool  = False,
        is_imbalance: str = "",  # "bull" | "bear" | ""
    ) -> None:
        super().__init__()
        self._x = x; self._y = y
        self._bv = buy_vol; self._sv = sell_vol
        self._ch = cell_h; self._cw = cell_w
        self._max = max(max_vol, 1e-9)
        self._poc = is_poc
        self._imb = is_imbalance
        self._picture = None
        self._build()

    def _build(self) -> None:
        self._picture = pg.QtGui.QPicture()
        p = QPainter(self._picture)
        total = self._bv + self._sv
        intensity = min(total / self._max, 1.0)

        # 배경 색상 결정
        if self._imb == "bull":
            bg = QColor(C_IMBALANCE_BG)
            bg.setAlphaF(0.7)
        elif self._imb == "bear":
            bg = QColor(C_BEAR_IMBAL)
            bg.setAlphaF(0.7)
        elif self._bv >= self._sv:
            r, g, b = 13, 43, 26
            r2, g2, b2 = 46, 160, 67
            f = intensity
            bg = QColor(int(r + (r2-r)*f), int(g + (g2-g)*f), int(b + (b2-b)*f), 220)
        else:
            r, g, b = 45, 17, 23
            r2, g2, b2 = 201, 60, 55
            f = intensity
            bg = QColor(int(r + (r2-r)*f), int(g + (g2-g)*f), int(b + (b2-b)*f), 220)

        x0 = self._x - self._cw / 2
        rect = QRectF(x0, self._y - self._ch/2, self._cw, self._ch)

        p.setBrush(QBrush(bg))
        pen_color = QColor(C_POC) if self._poc else QColor(C_GRID)
        pen_color.setAlphaF(0.4)
        p.setPen(QPen(pen_color, 0.5))
        p.drawRect(rect)

        # 볼륨 텍스트 (셀이 충분히 넓을 때만)
        if self._cw > 60:
            p.setFont(QFont("monospace", 7))
            mid_y = self._y
            # 매도 (왼쪽, 빨강)
            p.setPen(QPen(QColor(C_SELL_MAX if self._sv > self._bv else C_TEXT)))
            p.drawText(QRectF(x0 + 2, mid_y - self._ch/2, self._cw/2 - 4, self._ch),
                       Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f"{self._sv:.1f}")
            # 구분선
            p.setPen(QPen(QColor(C_GRID)))
            p.drawLine(pg.QtCore.QPointF(self._x, self._y - self._ch/2),
                       pg.QtCore.QPointF(self._x, self._y + self._ch/2))
            # 매수 (오른쪽, 초록)
            p.setPen(QPen(QColor(C_BUY_MAX if self._bv > self._sv else C_TEXT)))
            p.drawText(QRectF(self._x + 2, mid_y - self._ch/2, self._cw/2 - 4, self._ch),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       f"{self._bv:.1f}")

        p.end()

    def paint(self, p, *args):
        if self._picture:
            self._picture.play(p)

    def boundingRect(self):
        return QRectF(self._x - self._cw/2, self._y - self._ch/2, self._cw, self._ch)


class FootprintWidget(QWidget):
    """
    Footprint 차트 위젯.
    봉 단위로 가격 레벨별 Footprint 셀을 렌더링한다.
    """

    def __init__(
        self,
        tick_size:    float = 0.5,     # 가격 레벨 단위
        max_bars:     int   = 20,      # 화면에 표시할 최대 봉 수
        imbalance_ratio: float = 4.0,
        event_bus:    Optional[EventBus] = None,
        parent:       Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._tick_size       = tick_size
        self._max_bars        = max_bars
        self._imbalance_ratio = imbalance_ratio
        self._bus             = event_bus or get_event_bus()

        self._bars:          list[FootprintBar] = []
        self._current_bar:   Optional[FootprintBar] = None
        self._needs_redraw   = False

        self._setup_ui()
        self._setup_timer()
        self._connect_events()

    def _setup_ui(self) -> None:
        pg.setConfigOptions(antialias=False, useOpenGL=True, background=C_BG)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── 컨트롤 바 ──
        ctrl = QHBoxLayout()
        ctrl_frame = QFrame()
        ctrl_frame.setStyleSheet(f"QFrame {{ background: #161b22; border-bottom: 1px solid {C_GRID}; }}")
        ctrl_frame.setLayout(ctrl)

        title = QLabel("📈 Footprint")
        title.setStyleSheet("color: #c9d1d9; font-weight: bold; padding: 4px 8px;")
        ctrl.addWidget(title)

        # 틱 사이즈
        ctrl.addWidget(QLabel("틱:"))
        self._tick_spin = QSpinBox()
        self._tick_spin.setRange(1, 1000)
        self._tick_spin.setValue(int(self._tick_size * 10))
        self._tick_spin.setStyleSheet("QSpinBox { background: #21262d; color: #c9d1d9; border: 1px solid #30363d; padding: 2px; }")
        self._tick_spin.valueChanged.connect(lambda v: setattr(self, '_tick_size', v/10))
        ctrl.addWidget(self._tick_spin)

        # 임밸런스
        self._imb_check = QCheckBox("Imbalance")
        self._imb_check.setChecked(True)
        self._imb_check.setStyleSheet("QCheckBox { color: #8b949e; }")
        ctrl.addWidget(self._imb_check)

        ctrl.addStretch()

        # 델타 합계
        self._delta_label = QLabel("Delta: 0")
        self._delta_label.setStyleSheet("color: #8b949e; font-size: 11px; padding-right: 8px;")
        ctrl.addWidget(self._delta_label)
        layout.addWidget(ctrl_frame)

        # ── Footprint 플롯 ──
        self._plot = pg.PlotWidget()
        self._plot.setBackground(C_BG)
        self._plot.getAxis("left").setTextPen(C_TEXT)
        self._plot.getAxis("bottom").setTextPen(C_TEXT)
        self._plot.showGrid(x=False, y=True, alpha=0.05)
        self._plot.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        # ── Delta 바 플롯 ──
        self._delta_plot = pg.PlotWidget()
        self._delta_plot.setBackground(C_BG)
        self._delta_plot.setMaximumHeight(60)
        self._delta_plot.setXLink(self._plot)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self._plot)
        splitter.addWidget(self._delta_plot)
        splitter.setSizes([500, 60])
        layout.addWidget(splitter)

    def _setup_timer(self) -> None:
        self._timer = QTimer(self)
        self._timer.setInterval(16)  # 60FPS
        self._timer.timeout.connect(self._tick_render)
        self._timer.start()

    def _connect_events(self) -> None:
        self._bus.subscribe("footprint", self._on_footprint)

    # ── 데이터 업데이트 ───────────────────────────────
    def _on_footprint(self, bar: FootprintBar) -> None:
        if bar.candle.confirmed:
            self._bars.append(bar)
            if len(self._bars) > self._max_bars:
                self._bars = self._bars[-self._max_bars:]
            self._current_bar = None
        else:
            self._current_bar = bar
        self._needs_redraw = True

    def set_bars(self, bars: list[FootprintBar]) -> None:
        """외부에서 직접 봉 설정."""
        self._bars = bars[-self._max_bars:]
        self._needs_redraw = True

    # ── 렌더링 ────────────────────────────────────────
    def _tick_render(self) -> None:
        if self._needs_redraw:
            self._needs_redraw = False
            self._render()

    def _render(self) -> None:
        self._plot.clear()
        self._delta_plot.clear()

        all_bars = self._bars.copy()
        if self._current_bar:
            all_bars.append(self._current_bar)

        if not all_bars:
            return

        # 봉 너비 추정
        times = [b.candle.ts for b in all_bars]
        if len(times) > 1:
            bar_w = (times[-1] - times[0]) / len(times) * 0.85
        else:
            bar_w = 60 * 0.85

        total_delta = 0.0

        for bar in all_bars:
            if not bar.cells:
                continue
            max_vol = max((c.buy_vol + c.sell_vol for c in bar.cells), default=1)
            poc_price = bar.poc

            for cell in bar.cells:
                total_bv = cell.buy_vol; total_sv = cell.sell_vol
                is_imb = ""
                if self._imb_check.isChecked():
                    if total_sv > 0 and total_bv / total_sv >= self._imbalance_ratio:
                        is_imb = "bull"
                    elif total_bv > 0 and total_sv / total_bv >= self._imbalance_ratio:
                        is_imb = "bear"

                fp_cell = FootprintCell(
                    x=          bar.candle.ts,
                    y=          cell.price,
                    buy_vol=    cell.buy_vol,
                    sell_vol=   cell.sell_vol,
                    cell_h=     self._tick_size,
                    cell_w=     bar_w,
                    max_vol=    max_vol,
                    is_poc=     cell.price == poc_price,
                    is_imbalance= is_imb,
                )
                self._plot.addItem(fp_cell)

            total_delta += bar.delta

        # Delta 바
        if all_bars:
            ts_arr     = [b.candle.ts for b in all_bars]
            delta_arr  = [b.delta      for b in all_bars]
            colors     = [QColor(C_DELTA_POS) if d >= 0 else QColor(C_DELTA_NEG) for d in delta_arr]
            bar_item   = pg.BarGraphItem(x=ts_arr, height=delta_arr, width=bar_w * 0.7, brushes=colors)
            self._delta_plot.addItem(bar_item)

            # 0 기준선
            self._delta_plot.addLine(y=0, pen=pg.mkPen(C_GRID, width=1))

        # Delta 합계 레이블
        color = C_DELTA_POS if total_delta >= 0 else C_DELTA_NEG
        self._delta_label.setStyleSheet(f"color: {color}; font-size: 11px; padding-right: 8px; font-weight: bold;")
        self._delta_label.setText(f"Delta: {total_delta:+.2f}")

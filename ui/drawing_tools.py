"""
ui/drawing_tools.py
===================
차트 드로잉 도구.
- 수평선 (지지/저항)
- 추세선
- 피보나치 되돌림
- 박스 (프리미엄/디스카운트 존)
- 텍스트 라벨

PyQtGraph ROI(Region of Interest) 기반으로 드래그/조작 가능.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional

import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QButtonGroup, QLabel, QColorDialog, QToolButton,
    QFrame,
)

logger = logging.getLogger(__name__)


class DrawMode(Enum):
    NONE       = auto()
    HLINE      = auto()   # 수평선
    TRENDLINE  = auto()   # 추세선
    FIBONACCI  = auto()   # 피보나치 되돌림
    BOX        = auto()   # 박스
    TEXT       = auto()   # 텍스트


@dataclass
class DrawingObject:
    """드로잉 오브젝트 메타데이터."""
    mode:   DrawMode
    color:  str
    label:  str = ""
    item:   Optional[object] = None   # pyqtgraph 아이템


class DrawingToolBar(QFrame):
    """드로잉 도구 툴바."""

    mode_changed = Signal(str)   # DrawMode 이름

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._current_mode = DrawMode.NONE
        self._current_color = "#e3b341"
        self._setup_ui()

    def _setup_ui(self) -> None:
        self.setStyleSheet("""
            QFrame { background: #161b22; border: 1px solid #21262d; border-radius: 4px; }
            QToolButton { background: #21262d; color: #c9d1d9; border: none;
                          padding: 4px 8px; border-radius: 3px; font-size: 11px; }
            QToolButton:checked { background: #1f6feb; color: white; }
            QToolButton:hover   { background: #30363d; }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)

        label = QLabel("✏️ 드로잉:")
        label.setStyleSheet("color: #8b949e; font-size: 10px;")
        layout.addWidget(label)

        self._btn_group = QButtonGroup(self)
        self._btn_group.setExclusive(True)

        tools = [
            ("—",  "수평선 (H)",       DrawMode.HLINE),
            ("╱",  "추세선 (T)",       DrawMode.TRENDLINE),
            ("fib", "피보나치 (F)",    DrawMode.FIBONACCI),
            ("□",  "박스 (B)",         DrawMode.BOX),
            ("A",  "텍스트 (X)",       DrawMode.TEXT),
        ]
        for icon, tip, mode in tools:
            btn = QToolButton()
            btn.setText(icon)
            btn.setToolTip(tip)
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked, m=mode: self._set_mode(m))
            self._btn_group.addButton(btn)
            layout.addWidget(btn)

        layout.addSpacing(8)

        # 색상 선택
        self._color_btn = QPushButton()
        self._color_btn.setFixedSize(20, 20)
        self._color_btn.setStyleSheet(f"background: {self._current_color}; border: none; border-radius: 3px;")
        self._color_btn.clicked.connect(self._pick_color)
        layout.addWidget(self._color_btn)

        # 삭제 버튼
        del_btn = QToolButton()
        del_btn.setText("🗑")
        del_btn.setToolTip("선택된 드로잉 삭제 (Del)")
        del_btn.clicked.connect(self._request_delete)
        layout.addWidget(del_btn)

        # 전체 삭제
        clear_btn = QToolButton()
        clear_btn.setText("전체삭제")
        clear_btn.clicked.connect(self._request_clear_all)
        layout.addWidget(clear_btn)

        # ESC = 드로잉 모드 해제
        esc_btn = QToolButton()
        esc_btn.setText("ESC")
        esc_btn.clicked.connect(lambda: self._set_mode(DrawMode.NONE))
        layout.addWidget(esc_btn)

        layout.addStretch()

    def _set_mode(self, mode: DrawMode) -> None:
        self._current_mode = mode
        self.mode_changed.emit(mode.name)

    def _pick_color(self) -> None:
        color = QColorDialog.getColor(QColor(self._current_color), self)
        if color.isValid():
            self._current_color = color.name()
            self._color_btn.setStyleSheet(f"background: {self._current_color}; border: none; border-radius: 3px;")

    def _request_delete(self) -> None:
        self.mode_changed.emit("DELETE")

    def _request_clear_all(self) -> None:
        self.mode_changed.emit("CLEAR_ALL")

    @property
    def current_mode(self) -> DrawMode:
        return self._current_mode

    @property
    def current_color(self) -> str:
        return self._current_color


class DrawingManager:
    """
    차트 위에 드로잉 오브젝트를 추가/관리하는 매니저.
    PyQtGraph PlotWidget에 직접 아이템을 추가한다.
    """

    # 피보나치 레벨
    FIB_LEVELS = [0.0, 0.236, 0.382, 0.5, 0.618, 0.786, 1.0]
    FIB_COLORS = ["#c9d1d9", "#58a6ff", "#3fb950", "#e3b341", "#f85149", "#bc8cff", "#c9d1d9"]

    def __init__(self, plot_widget: pg.PlotWidget) -> None:
        self._plot   = plot_widget
        self._drawings: list[DrawingObject] = []
        self._selected: Optional[DrawingObject] = None
        self._mode    = DrawMode.NONE
        self._color   = "#e3b341"

        # 드로잉 중 임시 상태
        self._start_pos: Optional[tuple[float, float]] = None
        self._temp_item: Optional[object] = None

        # 마우스 이벤트 연결
        self._plot.scene().sigMouseClicked.connect(self._on_click)
        self._plot.scene().sigMouseMoved.connect(self._on_move)

    def set_mode(self, mode: DrawMode, color: str = "#e3b341") -> None:
        self._mode  = mode
        self._color = color
        # 임시 아이템 정리
        if self._temp_item:
            self._plot.removeItem(self._temp_item)
            self._temp_item  = None
        self._start_pos = None

    def _on_click(self, event) -> None:
        if self._mode == DrawMode.NONE:
            return
        if not self._plot.sceneBoundingRect().contains(event.scenePos()):
            return

        pos = self._plot.plotItem.vb.mapSceneToView(event.scenePos())
        x, y = pos.x(), pos.y()

        if self._mode == DrawMode.HLINE:
            self._add_hline(y)
        elif self._mode == DrawMode.TRENDLINE:
            if self._start_pos is None:
                self._start_pos = (x, y)
            else:
                self._add_trendline(self._start_pos, (x, y))
                self._start_pos = None
        elif self._mode == DrawMode.FIBONACCI:
            if self._start_pos is None:
                self._start_pos = (x, y)
            else:
                self._add_fibonacci(self._start_pos, (x, y))
                self._start_pos = None
        elif self._mode == DrawMode.BOX:
            if self._start_pos is None:
                self._start_pos = (x, y)
            else:
                self._add_box(self._start_pos, (x, y))
                self._start_pos = None

    def _on_move(self, pos) -> None:
        """드로잉 중 실시간 미리보기."""
        if not self._start_pos or self._mode == DrawMode.NONE:
            return
        if not self._plot.sceneBoundingRect().contains(pos):
            return

        cur = self._plot.plotItem.vb.mapSceneToView(pos)
        x, y = cur.x(), cur.y()

        # 이전 임시 아이템 제거
        if self._temp_item:
            self._plot.removeItem(self._temp_item)

        sx, sy = self._start_pos
        pen = pg.mkPen(self._color, width=1, style=Qt.PenStyle.DashLine)

        if self._mode == DrawMode.TRENDLINE:
            self._temp_item = self._plot.plot([sx, x], [sy, y], pen=pen)
        elif self._mode in (DrawMode.FIBONACCI, DrawMode.BOX):
            box = pg.RectROI([min(sx,x), min(sy,y)], [abs(x-sx), abs(y-sy)],
                             pen=pen, movable=False, resizable=False)
            self._plot.addItem(box)
            self._temp_item = box

    # ── 드로잉 추가 메서드 ────────────────────────
    def _add_hline(self, y: float) -> None:
        pen  = pg.mkPen(self._color, width=1)
        item = pg.InfiniteLine(
            pos=y, angle=0, movable=True, pen=pen,
            label=f"{y:.2f}",
            labelOpts={"color": self._color, "position": 0.05},
        )
        self._plot.addItem(item)
        obj = DrawingObject(DrawMode.HLINE, self._color, item=item)
        self._drawings.append(obj)

    def _add_trendline(self, start: tuple, end: tuple) -> None:
        pen  = pg.mkPen(self._color, width=1)
        item = self._plot.plot(
            [start[0], end[0]], [start[1], end[1]],
            pen=pen,
        )
        obj = DrawingObject(DrawMode.TRENDLINE, self._color, item=item)
        self._drawings.append(obj)

    def _add_fibonacci(self, start: tuple, end: tuple) -> None:
        """피보나치 되돌림 레벨 그리기."""
        x0, y0 = start
        x1, y1 = end
        price_range = y1 - y0

        for level, color in zip(self.FIB_LEVELS, self.FIB_COLORS):
            fib_price = y0 + price_range * (1 - level)
            pen  = pg.mkPen(color, width=1, style=Qt.PenStyle.DashLine)
            item = self._plot.addLine(
                y=fib_price, pen=pen,
                label=f"  {level*100:.1f}% ({fib_price:.2f})",
            )
            obj = DrawingObject(DrawMode.FIBONACCI, color, label=f"{level*100:.1f}%", item=item)
            self._drawings.append(obj)

    def _add_box(self, start: tuple, end: tuple) -> None:
        """박스 (프리미엄/디스카운트 존 표시용)."""
        x0, y0 = start
        x1, y1 = end
        color = QColor(self._color)
        color.setAlphaF(0.15)
        brush = pg.mkBrush(color)
        pen   = pg.mkPen(self._color, width=1)
        item  = pg.LinearRegionItem(
            values=[min(y0,y1), max(y0,y1)],
            orientation='horizontal',
            brush=brush, pen=pen, movable=True,
        )
        self._plot.addItem(item)
        obj = DrawingObject(DrawMode.BOX, self._color, item=item)
        self._drawings.append(obj)

    def delete_last(self) -> None:
        """마지막 드로잉 삭제."""
        if self._drawings:
            obj = self._drawings.pop()
            if obj.item:
                try: self._plot.removeItem(obj.item)
                except Exception: pass

    def clear_all(self) -> None:
        """전체 드로잉 삭제."""
        for obj in self._drawings:
            if obj.item:
                try: self._plot.removeItem(obj.item)
                except Exception: pass
        self._drawings.clear()

    def get_all(self) -> list[DrawingObject]:
        return list(self._drawings)

"""
ui/smc_widget.py
================
SMC 분석 결과 시각화 패널.

- 시장 구조 (BOS/CHoCH) 레이블
- FVG 구간 하이라이트
- Order Block / Breaker Block 박스
- Equal High / Low 수평선
- Premium / Discount Zone 배경
- 유동성 스윕 마커
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPen, QBrush, QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QCheckBox, QFrame, QScrollArea,
)

from strategy.smc_engine import SMCResult

logger = logging.getLogger(__name__)


# ── 색상 팔레트 ───────────────────────────────────────
COLORS = {
    "bull_fvg":    QColor(46, 160, 67, 40),
    "bear_fvg":    QColor(248, 81, 73, 40),
    "bull_ob":     QColor(46, 160, 67, 60),
    "bear_ob":     QColor(248, 81, 73, 60),
    "breaker":     QColor(188, 140, 255, 50),
    "eqh":         QColor(227, 179, 65, 200),
    "eql":         QColor(88, 166, 255, 200),
    "bull_sweep":  QColor(46, 160, 67, 180),
    "bear_sweep":  QColor(248, 81, 73, 180),
    "premium":     QColor(248, 81, 73, 15),
    "discount":    QColor(46, 160, 67, 15),
    "equilibrium": QColor(227, 179, 65, 100),
    "bos_bull":    QColor(46, 160, 67),
    "bos_bear":    QColor(248, 81, 73),
    "choch":       QColor(188, 140, 255),
}


class SMCOverlay:
    """
    차트 위에 SMC 레벨을 그리는 오버레이 클래스.
    PyQtGraph ViewBox에 그래픽 아이템을 추가한다.
    """

    def __init__(self, plot_item: pg.PlotItem) -> None:
        self._plot = plot_item
        self._items: list = []

    def clear(self) -> None:
        for item in self._items:
            self._plot.removeItem(item)
        self._items.clear()

    def render(self, result: SMCResult, x_start: float, x_end: float) -> None:
        """SMC 결과를 차트에 렌더링."""
        self.clear()
        if not result:
            return

        # 1. Premium / Discount Zone
        self._draw_pd_zone(result, x_start, x_end)

        # 2. FVG 구간
        self._draw_fvg(result)

        # 3. Order Block
        self._draw_ob(result, x_start)

        # 4. Breaker Block
        self._draw_breaker(result, x_start)

        # 5. Equal High / Low
        self._draw_equal_levels(result, x_start, x_end)

        # 6. BOS / CHoCH 레이블
        self._draw_structure_labels(result, x_end)

        # 7. 유동성 스윕 마커
        self._draw_sweeps(result)

    def _draw_pd_zone(self, result, x0, x1):
        pd = result.premium_discount
        if not pd:
            return
        # Premium 배경
        premium_item = pg.LinearRegionItem(
            values=[pd.equilibrium, pd.premium_top],
            orientation="horizontal",
            brush=QBrush(COLORS["premium"]),
            movable=False,
        )
        # Discount 배경
        discount_item = pg.LinearRegionItem(
            values=[pd.discount_bot, pd.equilibrium],
            orientation="horizontal",
            brush=QBrush(COLORS["discount"]),
            movable=False,
        )
        # Equilibrium 선
        eq_line = pg.InfiniteLine(
            pos=pd.equilibrium, angle=0,
            pen=pg.mkPen(COLORS["equilibrium"], width=1, style=Qt.DashLine),
            label=f"EQ {pd.equilibrium:.0f}",
            labelOpts={"color": COLORS["equilibrium"]},
        )
        for item in [premium_item, discount_item, eq_line]:
            self._plot.addItem(item)
            self._items.append(item)

    def _draw_fvg(self, result):
        for fvg in result.fvg_zones:
            color = COLORS["bull_fvg"] if fvg.direction == "bull" else COLORS["bear_fvg"]
            item  = pg.LinearRegionItem(
                values=[fvg.bottom, fvg.top],
                orientation="horizontal",
                brush=QBrush(color), movable=False,
            )
            self._plot.addItem(item)
            self._items.append(item)

    def _draw_ob(self, result, x0):
        for ob in result.order_blocks:
            if ob.broken:
                continue
            color = COLORS["bull_ob"] if ob.direction == "bull" else COLORS["bear_ob"]
            item  = pg.LinearRegionItem(
                values=[ob.low, ob.high],
                orientation="horizontal",
                brush=QBrush(color), movable=False,
            )
            label = pg.TextItem(
                f"{'Bull' if ob.direction=='bull' else 'Bear'} OB",
                color=QColor(color.red(), color.green(), color.blue(), 200),
                anchor=(0, 0.5),
            )
            label.setPos(x0, ob.midpoint)
            for i in [item, label]:
                self._plot.addItem(i)
                self._items.append(i)

    def _draw_breaker(self, result, x0):
        for bb in result.breaker_blocks:
            item = pg.LinearRegionItem(
                values=[bb.low, bb.high],
                orientation="horizontal",
                brush=QBrush(COLORS["breaker"]), movable=False,
            )
            pen_item = pg.InfiniteLine(
                pos=(bb.high + bb.low) / 2, angle=0,
                pen=pg.mkPen(COLORS["breaker"], width=1, style=Qt.DotLine),
            )
            for i in [item, pen_item]:
                self._plot.addItem(i)
                self._items.append(i)

    def _draw_equal_levels(self, result, x0, x1):
        for eqh in result.equal_highs:
            line = pg.InfiniteLine(
                pos=eqh.price, angle=0,
                pen=pg.mkPen(COLORS["eqh"], width=1, style=Qt.DashDotLine),
                label=f"EQH {eqh.price:.0f}",
                labelOpts={"color": COLORS["eqh"], "position": 0.95},
            )
            self._plot.addItem(line)
            self._items.append(line)

        for eql in result.equal_lows:
            line = pg.InfiniteLine(
                pos=eql.price, angle=0,
                pen=pg.mkPen(COLORS["eql"], width=1, style=Qt.DashDotLine),
                label=f"EQL {eql.price:.0f}",
                labelOpts={"color": COLORS["eql"], "position": 0.95},
            )
            self._plot.addItem(line)
            self._items.append(line)

    def _draw_structure_labels(self, result, x_cur):
        if result.last_bos:
            color = COLORS["bos_bull"] if result.bull_ms else COLORS["bos_bear"]
            label = pg.TextItem(
                f"BOS {result.last_bos:.0f}", color=color, anchor=(1, 0.5)
            )
            label.setPos(x_cur, result.last_bos)
            self._plot.addItem(label)
            self._items.append(label)

        if result.last_choch:
            label = pg.TextItem(
                f"CHoCH {result.last_choch:.0f}", color=COLORS["choch"], anchor=(1, 0.5)
            )
            label.setPos(x_cur, result.last_choch)
            self._plot.addItem(label)
            self._items.append(label)

    def _draw_sweeps(self, result):
        for sw in result.liquidity_sweeps[-3:]:
            color = COLORS["bull_sweep"] if sw.direction == "bull_sweep" else COLORS["bear_sweep"]
            marker = pg.ScatterPlotItem(
                [sw.ts], [sw.swept_level],
                symbol="t" if sw.direction == "bull_sweep" else "t1",
                size=10, brush=color, pen=pg.mkPen(None),
            )
            self._plot.addItem(marker)
            self._items.append(marker)


class SMCControlPanel(QWidget):
    """
    SMC 오버레이 컨트롤 패널.
    각 SMC 레이어를 토글하고 결과를 텍스트로 표시한다.
    """

    refresh_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._result: Optional[SMCResult] = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        self.setStyleSheet("background:#0d1117; color:#c9d1d9;")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # 헤더
        header = QHBoxLayout()
        title  = QLabel("🏗 SMC 분석")
        title.setStyleSheet("color:#58a6ff; font-weight:bold; font-size:13px;")
        refresh_btn = QPushButton("↻ 갱신")
        refresh_btn.setFixedWidth(60)
        refresh_btn.setStyleSheet(
            "background:#21262d; border:1px solid #30363d; "
            "border-radius:4px; padding:3px 8px; color:#8b949e;"
        )
        refresh_btn.clicked.connect(self.refresh_requested.emit)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(refresh_btn)
        layout.addLayout(header)

        # 레이어 토글
        self._checks = {}
        layers = [
            ("show_pd",      "Premium/Discount Zone",  True),
            ("show_fvg",     "FVG 구간",               True),
            ("show_ob",      "Order Block",            True),
            ("show_breaker", "Breaker Block",          True),
            ("show_eql",     "EQH / EQL",              True),
            ("show_sweep",   "유동성 스윕",             True),
        ]
        for key, label, default in layers:
            cb = QCheckBox(label)
            cb.setChecked(default)
            cb.setStyleSheet("QCheckBox { color:#8b949e; font-size:11px; }")
            self._checks[key] = cb
            layout.addWidget(cb)

        layout.addWidget(self._separator())

        # 결과 표시 영역
        self._result_label = QLabel("분석 결과 없음")
        self._result_label.setWordWrap(True)
        self._result_label.setStyleSheet(
            "color:#8b949e; font-size:11px; "
            "background:#161b22; border-radius:4px; padding:8px;"
        )
        scroll = QScrollArea()
        scroll.setWidget(self._result_label)
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(200)
        scroll.setStyleSheet("border:none; background:transparent;")
        layout.addWidget(scroll)
        layout.addStretch()

    def _separator(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color:#21262d;")
        return line

    def update_result(self, result: SMCResult) -> None:
        """SMC 결과로 패널 업데이트."""
        self._result = result
        if not result:
            self._result_label.setText("분석 결과 없음")
            return

        lines = []
        ms = "🟢 불리시" if result.bull_ms else "🔴 베어리시"
        lines.append(f"<b>시장 구조:</b> {ms}")

        if result.last_bos:
            lines.append(f"<b>BOS:</b> {result.last_bos:.0f}")
        if result.last_choch:
            lines.append(f"<b>CHoCH:</b> {result.last_choch:.0f} (추세 전환)")
        if result.fvg_zones:
            for z in result.fvg_zones[:2]:
                d = "불리시" if z.direction == "bull" else "베어리시"
                lines.append(f"<b>FVG ({d}):</b> {z.bottom:.0f} ~ {z.top:.0f}")
        if result.order_blocks:
            for ob in result.order_blocks[:2]:
                d = "불리시" if ob.direction == "bull" else "베어리시"
                lines.append(f"<b>OB ({d}):</b> {ob.low:.0f} ~ {ob.high:.0f}")
        if result.equal_highs:
            lines.append(f"<b>EQH:</b> {result.equal_highs[-1].price:.0f}")
        if result.equal_lows:
            lines.append(f"<b>EQL:</b> {result.equal_lows[-1].price:.0f}")
        if result.premium_discount:
            pd = result.premium_discount
            lines.append(f"<b>Zone:</b> {pd.current_zone.upper()} ({pd.equilibrium:.0f})")
        if result.liquidity_sweeps:
            sw = result.liquidity_sweeps[-1]
            lines.append(f"<b>Sweep:</b> {sw.direction} @ {sw.swept_level:.0f}")
        if result.smt_divergences:
            lines.append(f"<b>SMT Div:</b> {result.smt_divergences[-1].kind}")

        lines.append(f"<b>SMC 점수:</b> {result.score:.0f}/100")
        if result.signal:
            sig_color = "#3fb950" if result.signal == "LONG" else "#f85149"
            lines.append(f"<b>신호:</b> <span style='color:{sig_color}'>{result.signal}</span>")

        self._result_label.setText("<br>".join(lines))
        self._result_label.setTextFormat(Qt.RichText)

"""
ui/chart_widget.py
==================
고성능 캔들스틱 차트 위젯.
- PyQtGraph + OpenGL → 60FPS 목표
- Footprint 셀 오버레이 (가격 레벨별 매수/매도 볼륨)
- Volume Profile 우측 히스토그램
- EMA, VWAP 라인
- 세션 POC/VAH/VAL 수평선
- 줌/스크롤/드로잉 지원
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QFont
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QSizePolicy

from core.models import Candle, FootprintBar, VolumeProfile

logger = logging.getLogger(__name__)

# 색상 팔레트
C_BULL       = "#26a641"
C_BEAR       = "#f85149"
C_BG         = "#0d1117"
C_GRID       = "#21262d"
C_TEXT       = "#8b949e"
C_EMA20      = "#e3b341"
C_EMA50      = "#bc8cff"
C_POC        = "#ff9800"
C_VAH        = "#26a641"
C_VAL        = "#f85149"
C_IMBALANCE_BULL = "#1a472a"
C_IMBALANCE_BEAR = "#4a1515"


class CandleItem(pg.GraphicsObject):
    """
    캔들스틱 렌더링 오브젝트.
    picture 캐싱으로 60FPS 유지.
    """
    def __init__(self, candles: list[Candle]) -> None:
        super().__init__()
        self._candles  = candles
        self._picture  = None
        self._bounds   = None
        self.generatePicture()

    def generatePicture(self) -> None:
        self._picture = pg.QtGui.QPicture()
        p = QPainter(self._picture)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)  # 성능 우선

        if not self._candles:
            p.end()
            return

        # 캔들 너비 = 봉 간격의 70%
        times = [c.ts for c in self._candles]
        if len(times) > 1:
            w = (times[-1] - times[0]) / len(times) * 0.7
        else:
            w = 60 * 0.7

        for c in self._candles:
            bull = c.close >= c.open
            color = QColor(C_BULL if bull else C_BEAR)
            p.setPen(pg.mkPen(color))
            p.setBrush(pg.mkBrush(color))

            # 심지
            p.drawLine(
                pg.QtCore.QPointF(c.ts, c.low),
                pg.QtCore.QPointF(c.ts, c.high),
            )
            # 몸통
            body_top = max(c.open, c.close)
            body_bot = min(c.open, c.close)
            body_h   = max(body_top - body_bot, w * 0.01)  # 도지봉 최소 두께
            p.drawRect(pg.QtCore.QRectF(c.ts - w/2, body_bot, w, body_h))

        p.end()
        # 바운딩 박스 계산
        if self._candles:
            all_ts  = [c.ts  for c in self._candles]
            all_hi  = [c.high for c in self._candles]
            all_lo  = [c.low  for c in self._candles]
            self._bounds = pg.QtCore.QRectF(
                min(all_ts) - w, min(all_lo),
                max(all_ts) - min(all_ts) + w * 2,
                max(all_hi) - min(all_lo),
            )

    def paint(self, p, *args):
        if self._picture:
            self._picture.play(p)

    def boundingRect(self):
        if self._bounds:
            return self._bounds
        return pg.QtCore.QRectF()


class FootprintOverlay(pg.GraphicsObject):
    """
    캔들 위에 Footprint 셀 오버레이.
    가격 레벨별 매수(오른쪽)/매도(왼쪽) 볼륨 텍스트 + 색상 배경.
    """
    def __init__(self, bars: list[FootprintBar], max_cells: int = 8) -> None:
        super().__init__()
        self._bars      = bars
        self._max_cells = max_cells
        self._picture   = None
        self._bounds    = pg.QtCore.QRectF()
        self.generatePicture()

    def generatePicture(self) -> None:
        self._picture = pg.QtGui.QPicture()
        p = QPainter(self._picture)
        font = QFont("monospace", 7)
        p.setFont(font)

        if not self._bars:
            p.end()
            return

        # 봉 너비 추정
        times = [b.candle.ts for b in self._bars]
        w = (times[-1] - times[0]) / max(len(times), 1) * 0.45 if len(times) > 1 else 30

        for bar in self._bars:
            cells = bar.cells
            if not cells:
                continue
            # 볼륨 기준 상위 max_cells만 표시
            top_cells = sorted(cells, key=lambda c: c.total, reverse=True)[:self._max_cells]
            max_vol   = max((c.total for c in top_cells), default=1)

            for cell in top_cells:
                y = cell.price
                ratio = cell.total / max_vol

                # 배경 색상 (매수 우세: 초록, 매도 우세: 빨강)
                if cell.buy_vol >= cell.sell_vol:
                    bg = QColor(C_IMBALANCE_BULL)
                    bg.setAlphaF(0.3 + ratio * 0.5)
                else:
                    bg = QColor(C_IMBALANCE_BEAR)
                    bg.setAlphaF(0.3 + ratio * 0.5)

                cell_h = w * 0.4
                p.setBrush(pg.mkBrush(bg))
                p.setPen(pg.mkPen(None))
                p.drawRect(pg.QtCore.QRectF(
                    bar.candle.ts - w, y - cell_h/2,
                    w * 2, cell_h
                ))

        p.end()

    def paint(self, p, *args):
        if self._picture:
            self._picture.play(p)

    def boundingRect(self):
        return self._bounds


class VolumeProfileItem(pg.GraphicsObject):
    """우측 Volume Profile 히스토그램."""

    def __init__(self, profile: Optional[VolumeProfile], price_range: tuple) -> None:
        super().__init__()
        self._profile     = profile
        self._price_range = price_range
        self._picture     = None
        self._bounds      = pg.QtCore.QRectF()
        self.generatePicture()

    def generatePicture(self) -> None:
        self._picture = pg.QtGui.QPicture()
        p = QPainter(self._picture)

        if not self._profile or not self._profile.levels:
            p.end()
            return

        levels   = self._profile.levels
        max_vol  = max(lvl.total for lvl in levels) or 1
        x_origin = self._price_range[1]   # 우측 기준점 (최고가)
        bar_w    = (self._price_range[1] - self._price_range[0]) * 0.15  # 전체 범위의 15%

        for lvl in levels:
            width = (lvl.total / max_vol) * bar_w
            h     = (self._price_range[1] - self._price_range[0]) / len(levels)

            # 매도 (빨강)
            sell_w = width * (lvl.sell_vol / lvl.total) if lvl.total > 0 else 0
            p.setBrush(pg.mkBrush(QColor(C_BEAR).darker(120)))
            p.setPen(pg.mkPen(None))
            p.drawRect(pg.QtCore.QRectF(x_origin, lvl.price - h/2, sell_w, h))

            # 매수 (초록)
            buy_w = width * (lvl.buy_vol / lvl.total) if lvl.total > 0 else 0
            p.setBrush(pg.mkBrush(QColor(C_BULL).darker(120)))
            p.drawRect(pg.QtCore.QRectF(x_origin + sell_w, lvl.price - h/2, buy_w, h))

        # POC 라인
        poc_y = self._profile.poc
        p.setPen(pg.mkPen(C_POC, width=2, style=Qt.PenStyle.DashLine))
        p.drawLine(
            pg.QtCore.QPointF(x_origin, poc_y),
            pg.QtCore.QPointF(x_origin + bar_w, poc_y),
        )

        p.end()

    def paint(self, p, *args):
        if self._picture:
            self._picture.play(p)

    def boundingRect(self):
        return self._bounds


class MainChartWidget(QWidget):
    """
    메인 차트 위젯.
    캔들 + Footprint + Volume Profile + 지표라인을 하나의 위젯에 통합.
    """

    # 시그널
    crosshair_moved = Signal(float, float)   # ts, price

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._candles:  list[Candle]       = []
        self._fp_bars:  list[FootprintBar] = []
        self._profile:  Optional[VolumeProfile] = None

        # 토글 상태
        self.show_footprint = True
        self.show_vp        = True
        self.show_ema       = True
        self.show_poc_lines = True

        self._setup_ui()
        self._setup_timer()

    def _setup_ui(self) -> None:
        pg.setConfigOptions(antialias=False, useOpenGL=True, background=C_BG)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        # ── 메인 차트 ──
        self._chart = pg.PlotWidget()
        self._chart.setBackground(C_BG)
        self._chart.showGrid(x=True, y=True, alpha=0.08)
        self._chart.setLabel("left", "", **{"color": C_TEXT})
        self._chart.getAxis("left").setTextPen(C_TEXT)
        self._chart.getAxis("bottom").setTextPen(C_TEXT)
        self._chart.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        # 크로스헤어
        self._vline = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen(C_TEXT, width=1, style=Qt.PenStyle.DashLine))
        self._hline = pg.InfiniteLine(angle=0,  movable=False, pen=pg.mkPen(C_TEXT, width=1, style=Qt.PenStyle.DashLine))
        self._chart.addItem(self._vline, ignoreBounds=True)
        self._chart.addItem(self._hline, ignoreBounds=True)
        self._chart.scene().sigMouseMoved.connect(self._on_mouse_moved)

        # ── 볼륨 차트 ──
        self._vol_chart = pg.PlotWidget()
        self._vol_chart.setBackground(C_BG)
        self._vol_chart.setMaximumHeight(70)
        self._vol_chart.showGrid(x=False, y=True, alpha=0.05)
        self._vol_chart.getAxis("left").setTextPen(C_TEXT)
        self._vol_chart.setLabel("left", "Vol", **{"color": C_TEXT, "font-size": "9pt"})

        # ── 델타 차트 ──
        self._delta_chart = pg.PlotWidget()
        self._delta_chart.setBackground(C_BG)
        self._delta_chart.setMaximumHeight(55)
        self._delta_chart.showGrid(x=False, y=True, alpha=0.05)
        self._delta_chart.setLabel("left", "Delta", **{"color": C_TEXT, "font-size": "9pt"})

        # X축 동기화
        self._vol_chart.setXLink(self._chart)
        self._delta_chart.setXLink(self._chart)

        layout.addWidget(self._chart, stretch=10)
        layout.addWidget(self._vol_chart, stretch=1)
        layout.addWidget(self._delta_chart, stretch=1)

    def _setup_timer(self) -> None:
        """60FPS 렌더링 타이머."""
        self._render_timer = QTimer(self)
        self._render_timer.setInterval(16)   # ~60FPS
        self._render_timer.timeout.connect(self._tick_render)
        self._render_timer.start()
        self._needs_redraw = False

    # ── 데이터 업데이트 ───────────────────────────────
    def set_candles(self, candles: list[Candle]) -> None:
        self._candles = candles
        self._needs_redraw = True

    def set_footprint(self, bars: list[FootprintBar]) -> None:
        self._fp_bars = bars
        self._needs_redraw = True

    def set_volume_profile(self, profile: VolumeProfile) -> None:
        self._profile = profile
        self._needs_redraw = True

    # ── 렌더링 ────────────────────────────────────────
    def _tick_render(self) -> None:
        if self._needs_redraw:
            self._needs_redraw = False
            self._render()

    def _render(self) -> None:
        t0 = time.perf_counter()
        self._render_candles()
        self._render_indicators()
        self._render_volume()
        self._render_delta()
        elapsed = (time.perf_counter() - t0) * 1000
        if elapsed > 20:
            logger.debug(f"차트 렌더: {elapsed:.1f}ms")

    def _render_candles(self) -> None:
        self._chart.clear()
        self._chart.addItem(self._vline)
        self._chart.addItem(self._hline)

        if not self._candles:
            return

        # 캔들
        candle_item = CandleItem(self._candles)
        self._chart.addItem(candle_item)

        # Footprint 오버레이
        if self.show_footprint and self._fp_bars:
            fp_item = FootprintOverlay(self._fp_bars)
            self._chart.addItem(fp_item)

        # Volume Profile
        if self.show_vp and self._profile:
            prices    = [c.close for c in self._candles]
            vp_item   = VolumeProfileItem(self._profile, (min(prices), max(prices)))
            self._chart.addItem(vp_item)

        # POC / VAH / VAL 수평선
        if self.show_poc_lines and self._profile:
            for price, color, label in [
                (self._profile.poc, C_POC, "POC"),
                (self._profile.vah, C_VAH, "VAH"),
                (self._profile.val, C_VAL, "VAL"),
            ]:
                line = pg.InfiniteLine(
                    pos=price, angle=0, movable=False,
                    pen=pg.mkPen(color, width=1, style=Qt.PenStyle.DashLine),
                    label=label, labelOpts={"color": color, "position": 0.02},
                )
                self._chart.addItem(line)

    def _render_indicators(self) -> None:
        if not self._candles or not self.show_ema:
            return

        from indicators.base_indicators import ema
        times  = np.array([c.ts    for c in self._candles])
        closes = np.array([c.close for c in self._candles])

        if len(closes) >= 20:
            ema20 = ema(closes, 20)
            self._chart.plot(times, ema20, pen=pg.mkPen(C_EMA20, width=1), name="EMA20")
        if len(closes) >= 50:
            ema50 = ema(closes, 50)
            self._chart.plot(times, ema50, pen=pg.mkPen(C_EMA50, width=1), name="EMA50")

    def _render_volume(self) -> None:
        self._vol_chart.clear()
        if not self._candles:
            return
        times = [c.ts    for c in self._candles]
        vols  = [c.volume for c in self._candles]
        colors = [C_BULL if c.close >= c.open else C_BEAR for c in self._candles]

        bars = pg.BarGraphItem(x=times, height=vols, width=0.6, brushes=[QColor(c) for c in colors])
        self._vol_chart.addItem(bars)

    def _render_delta(self) -> None:
        self._delta_chart.clear()
        if not self._fp_bars:
            return
        times  = [b.candle.ts for b in self._fp_bars]
        deltas = [b.delta      for b in self._fp_bars]
        colors = [C_BULL if d >= 0 else C_BEAR for d in deltas]
        bars = pg.BarGraphItem(x=times, height=deltas, width=0.6, brushes=[QColor(c) for c in colors])
        self._delta_chart.addItem(bars)

    # ── 크로스헤어 ────────────────────────────────────
    def _on_mouse_moved(self, pos) -> None:
        if self._chart.sceneBoundingRect().contains(pos):
            mouse_point = self._chart.plotItem.vb.mapSceneToView(pos)
            self._vline.setPos(mouse_point.x())
            self._hline.setPos(mouse_point.y())
            self.crosshair_moved.emit(mouse_point.x(), mouse_point.y())

    # ── 토글 메서드 ───────────────────────────────────
    def toggle_footprint(self, enabled: bool) -> None:
        self.show_footprint = enabled
        self._needs_redraw = True

    def toggle_volume_profile(self, enabled: bool) -> None:
        self.show_vp = enabled
        self._needs_redraw = True

    def toggle_ema(self, enabled: bool) -> None:
        self.show_ema = enabled
        self._needs_redraw = True
    def attach_smc_overlay(self, plot_item=None) -> "SMCOverlay":
        from ui.smc_widget import SMCOverlay
        target = plot_item or self._plot
        self._smc_overlay = SMCOverlay(target)
        return self._smc_overlay

    def render_smc(self, result, candles: list = None) -> None:
        if not hasattr(self, '_smc_overlay') or not self._smc_overlay:
            self.attach_smc_overlay()
        if candles:
            x0 = candles[0].ts
            x1 = candles[-1].ts
        else:
            r = self._plot.viewRange()
            x0, x1 = r[0][0], r[0][1]
        self._smc_overlay.render(result, x0, x1)


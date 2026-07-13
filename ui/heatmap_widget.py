"""
ui/heatmap_widget.py
====================
유동성 히트맵 위젯 (CoinGlass 스타일).
- 시간 × 가격 격자에 오더북 사이즈를 색상 강도로 표현
- 실시간 업데이트 (오더북 스냅샷마다 샘플링)
- 청산 레벨 오버레이
"""
from __future__ import annotations

import logging
from collections import deque
from typing import Optional

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel

from core.models import OrderBook, LiquidationData

logger = logging.getLogger(__name__)


class HeatmapWidget(QWidget):
    """
    오더북 히트맵.
    시간 축(X) × 가격 축(Y)의 2D 배열에 호가 사이즈를 누적 → 색상 강도로 표현.
    """

    def __init__(
        self,
        n_time_slots:  int = 200,   # 시간 샘플 개수
        n_price_slots: int = 100,   # 가격 구간 개수
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._n_time  = n_time_slots
        self._n_price = n_price_slots

        # 히트맵 배열 (시간 × 가격)
        self._matrix       = np.zeros((n_time_slots, n_price_slots), dtype=np.float32)
        self._price_min    = 0.0
        self._price_max    = 0.0
        self._time_samples: deque[float] = deque(maxlen=n_time_slots)

        # 청산 레벨
        self._liquidations: list[LiquidationData] = []

        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        title = QLabel("📊 유동성 히트맵")
        title.setStyleSheet("color: #8b949e; font-size: 11px; padding: 4px;")
        layout.addWidget(title)

        self._plot = pg.PlotWidget()
        self._plot.setBackground("#0d1117")
        self._plot.getAxis("left").setTextPen("#8b949e")
        self._plot.getAxis("bottom").setTextPen("#8b949e")

        # ImageItem (2D 배열을 색상 이미지로 렌더링)
        self._img = pg.ImageItem()
        self._plot.addItem(self._img)

        # 컬러맵: 검정 → 노랑 → 주황 (CoinGlass 스타일)
        colors = [
            (0,   0,   0,   0),       # 투명 (데이터 없음)
            (20,  20,  80,  180),     # 파랑 (낮음)
            (80,  0,   80,  200),     # 보라
            (180, 100, 0,   220),     # 주황
            (255, 200, 0,   255),     # 노랑 (높음)
        ]
        cmap = pg.ColorMap(
            pos=[0.0, 0.25, 0.5, 0.75, 1.0],
            color=colors,
        )
        self._img.setColorMap(cmap)

        layout.addWidget(self._plot)

    def on_orderbook(self, book: OrderBook) -> None:
        """오더북 업데이트 수신 → 히트맵 갱신."""
        if not book.bids or not book.asks:
            return

        # 가격 범위 설정
        all_prices = [lvl.price for lvl in book.bids[:50] + book.asks[:50]]
        if not all_prices:
            return

        self._price_min = min(all_prices) * 0.999
        self._price_max = max(all_prices) * 1.001
        price_range     = self._price_max - self._price_min

        if price_range <= 0:
            return

        # 시간 슬롯 추가
        self._time_samples.append(book.ts)
        t_idx = len(self._time_samples) - 1

        # 히트맵 배열 업데이트
        if t_idx < self._n_time:
            # 슬라이드 (새 시간 슬롯)
            self._matrix = np.roll(self._matrix, -1, axis=0)
            self._matrix[-1] = 0.0
            t_idx = self._n_time - 1

        for lvl in book.bids + book.asks:
            p_idx = int((lvl.price - self._price_min) / price_range * self._n_price)
            if 0 <= p_idx < self._n_price:
                self._matrix[t_idx, p_idx] += lvl.size

        self._render()

    def on_liquidation(self, liq: LiquidationData) -> None:
        """청산 데이터 수신 → 오버레이에 표시."""
        self._liquidations.append(liq)
        if len(self._liquidations) > 500:
            self._liquidations = self._liquidations[-500:]

    def _render(self) -> None:
        """히트맵 이미지 업데이트."""
        # 로그 스케일로 색상 강도 정규화
        data = np.log1p(self._matrix.T)   # 전치: X=시간, Y=가격
        if data.max() > 0:
            data = data / data.max()
        self._img.setImage(data, autoLevels=False, levels=(0, 1))
        self._img.setRect(pg.QtCore.QRectF(0, self._price_min, self._n_time, self._price_max - self._price_min))

"""
ui/ai_summary_widget.py
=======================
AI 차트 자연어 요약 패널 (데스크탑).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from PySide6.QtCore import Qt, Signal, QThread, QObject
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTextEdit, QFrame, QScrollArea,
)

from ai.chart_summary import AIChartSummaryEngine, ChartSummary

logger = logging.getLogger(__name__)

_STYLE = """
QWidget { background: #0d1117; color: #c9d1d9; }
QTextEdit {
    background: #161b22;
    color: #c9d1d9;
    border: 1px solid #21262d;
    border-radius: 6px;
    padding: 10px;
    font-size: 12px;
    font-family: 'Noto Sans KR', 'Malgun Gothic', sans-serif;
}
QPushButton {
    background: #21262d;
    color: #8b949e;
    border: 1px solid #30363d;
    border-radius: 4px;
    padding: 4px 12px;
    font-size: 11px;
}
QPushButton:hover { background: #30363d; color: #c9d1d9; }
"""

SECTION_ICONS = {
    "trend":      "📈",
    "structure":  "🏗",
    "orderflow":  "⚡",
    "key_levels": "🎯",
    "risk":       "⚠️",
    "watchfor":   "👀",
}


class AISummaryWorker(QObject):
    """백그라운드에서 AI 요약 실행."""
    finished = Signal(object)  # ChartSummary
    error    = Signal(str)

    def __init__(self, engine, symbol, candles, ict_result, smc_result, fp_bar):
        super().__init__()
        self._engine     = engine
        self._symbol     = symbol
        self._candles    = candles
        self._ict_result = ict_result
        self._smc_result = smc_result
        self._fp_bar     = fp_bar

    def run(self):
        try:
            result = self._engine.summarize(
                symbol=self._symbol, candles=self._candles,
                ict_result=self._ict_result, smc_result=self._smc_result,
                fp_bar=self._fp_bar,
            )
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


class AISummaryWidget(QWidget):
    """AI 차트 요약 위젯."""

    refresh_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._engine  = AIChartSummaryEngine()
        self._summary: Optional[ChartSummary] = None
        self._thread: Optional[QThread] = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        self.setStyleSheet(_STYLE)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 헤더
        header = QWidget()
        header.setStyleSheet("background:#161b22; border-bottom:1px solid #21262d;")
        h_lay  = QHBoxLayout(header)
        h_lay.setContentsMargins(12, 6, 12, 6)

        title = QLabel("🤖 AI 차트 요약")
        title.setStyleSheet("color:#58a6ff; font-weight:bold; font-size:13px;")

        self._status_lbl = QLabel("분석 대기 중")
        self._status_lbl.setStyleSheet("color:#484f58; font-size:11px;")

        self._refresh_btn = QPushButton("↻ 분석")
        self._refresh_btn.setFixedWidth(70)
        self._refresh_btn.clicked.connect(self.refresh_requested.emit)

        h_lay.addWidget(title)
        h_lay.addWidget(self._status_lbl)
        h_lay.addStretch()
        h_lay.addWidget(self._refresh_btn)
        layout.addWidget(header)

        # 헤드라인
        self._headline = QLabel("—")
        self._headline.setWordWrap(True)
        self._headline.setStyleSheet(
            "color:#c9d1d9; font-size:13px; font-weight:600; "
            "padding:10px 14px; background:#1f2937; "
            "border-bottom:1px solid #21262d;"
        )
        layout.addWidget(self._headline)

        # 섹션 카드들
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("border:none; background:transparent;")
        content = QWidget()
        content.setStyleSheet("background:#0d1117;")
        self._cards_layout = QVBoxLayout(content)
        self._cards_layout.setContentsMargins(12, 12, 12, 12)
        self._cards_layout.setSpacing(8)
        scroll.setWidget(content)
        layout.addWidget(scroll)

        # 초기 카드 생성
        self._section_labels: dict[str, QLabel] = {}
        sections = [
            ("trend",      "📈 추세"),
            ("structure",  "🏗 시장 구조"),
            ("orderflow",  "⚡ 오더플로우"),
            ("key_levels", "🎯 주요 레벨"),
            ("risk",       "⚠️ 리스크"),
            ("watchfor",   "👀 주목할 것"),
        ]
        for key, label in sections:
            card = self._make_card(key, label)
            self._cards_layout.addWidget(card)

        self._cards_layout.addStretch()

    def _make_card(self, key: str, label: str) -> QFrame:
        frame = QFrame()
        frame.setStyleSheet(
            "QFrame { background:#161b22; border-radius:6px; "
            "border:1px solid #21262d; }"
        )
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(4)

        title_lbl = QLabel(label)
        title_lbl.setStyleSheet("color:#58a6ff; font-size:11px; font-weight:600;")

        content_lbl = QLabel("—")
        content_lbl.setWordWrap(True)
        content_lbl.setStyleSheet("color:#8b949e; font-size:12px; line-height:1.5;")

        lay.addWidget(title_lbl)
        lay.addWidget(content_lbl)
        self._section_labels[key] = content_lbl
        return frame

    def update_summary(self, summary: ChartSummary) -> None:
        """요약 결과로 UI 갱신."""
        self._summary = summary
        self._headline.setText(summary.headline)
        self._section_labels["trend"].setText(summary.trend or "—")
        self._section_labels["structure"].setText(summary.structure or "—")
        self._section_labels["orderflow"].setText(summary.orderflow or "—")
        self._section_labels["key_levels"].setText(summary.key_levels or "—")
        self._section_labels["risk"].setText(summary.risk or "—")
        self._section_labels["watchfor"].setText(summary.watchfor or "—")
        self._status_lbl.setText("분석 완료")
        self._status_lbl.setStyleSheet("color:#3fb950; font-size:11px;")

    def set_loading(self, loading: bool) -> None:
        self._refresh_btn.setEnabled(not loading)
        if loading:
            self._status_lbl.setText("AI 분석 중...")
            self._status_lbl.setStyleSheet("color:#e3b341; font-size:11px;")

    def run_analysis(self, symbol, candles, ict_result=None, smc_result=None, fp_bar=None) -> None:
        """백그라운드에서 AI 분석 실행."""
        self.set_loading(True)

        if self._thread and self._thread.isRunning():
            self._thread.quit()

        self._thread  = QThread()
        self._worker  = AISummaryWorker(
            self._engine, symbol, candles, ict_result, smc_result, fp_bar
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self.update_summary)
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(lambda e: logger.error(f"AI 요약 오류: {e}"))
        self._thread.start()

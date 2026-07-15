"""
ui/main_window.py
=================
메인 윈도우 — TradingView 스타일 다크 테마.
Dock 레이아웃으로 차트, 오더북, 포지션, 스캐너 패널을 자유롭게 배치.
60FPS 목표: PyQtGraph 하드웨어 가속 + OpenGL.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal, QThread
from PySide6.QtGui import QColor, QPalette, QFont, QIcon
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QDockWidget,
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QComboBox, QPushButton, QStatusBar, QTabWidget,
    QSplitter, QToolBar, QMessageBox,
)
import pyqtgraph as pg

from core.config import AppConfig, get_config
from core.events import EventBus, get_event_bus
from core.models import Timeframe
from ui.chart_widget import MainChartWidget
from ui.drawing_tools import DrawingToolBar, DrawingManager, DrawMode
from ui.multi_chart import MultiChartWidget, LayoutMode
from ui.settings_dialog import SettingsDialog
from ui.time_sales import TimeSalesWidget
from ui.scanner_widget import ScannerWidget
from ui.footprint_widget import FootprintWidget
from ui.heatmap_widget import HeatmapWidget
from ui.smc_widget import SMCControlPanel, SMCOverlay
from ui.position_widget import PositionWidget
from ui.ai_summary_widget import AISummaryWidget
from ui.smc_widget import SMCControlPanel, SMCOverlay
from ui.position_widget import PositionWidget
from ui.ai_summary_widget import AISummaryWidget

logger = logging.getLogger(__name__)


# ── 다크 테마 팔레트 ─────────────────────────────────────
def apply_dark_theme(app: QApplication) -> None:
    """TradingView 스타일 다크 테마 적용."""
    app.setStyle("Fusion")
    palette = QPalette()

    # 배경
    palette.setColor(QPalette.ColorRole.Window,          QColor("#0d1117"))
    palette.setColor(QPalette.ColorRole.WindowText,      QColor("#c9d1d9"))
    palette.setColor(QPalette.ColorRole.Base,            QColor("#161b22"))
    palette.setColor(QPalette.ColorRole.AlternateBase,   QColor("#21262d"))

    # 텍스트
    palette.setColor(QPalette.ColorRole.Text,            QColor("#c9d1d9"))
    palette.setColor(QPalette.ColorRole.BrightText,      QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.ButtonText,      QColor("#c9d1d9"))

    # 버튼
    palette.setColor(QPalette.ColorRole.Button,          QColor("#21262d"))
    palette.setColor(QPalette.ColorRole.Highlight,       QColor("#1f6feb"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))

    # 비활성
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#484f58"))
    palette.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#484f58"))

    app.setPalette(palette)
    app.setFont(QFont("Noto Sans KR", 10))


# ── 심볼/타임프레임 선택 툴바 ───────────────────────────────
class MarketToolBar(QToolBar):
    """상단 툴바: 심볼 선택, 타임프레임, 연결 상태."""

    symbol_changed    = Signal(str)
    timeframe_changed = Signal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__("Market", parent)
        self.setMovable(False)
        self.setStyleSheet("""
            QToolBar { background: #161b22; border-bottom: 1px solid #21262d; padding: 4px; }
            QComboBox { background: #21262d; color: #c9d1d9; border: 1px solid #30363d;
                        padding: 4px 8px; border-radius: 4px; min-width: 120px; }
            QPushButton { background: #21262d; color: #c9d1d9; border: 1px solid #30363d;
                          padding: 4px 12px; border-radius: 4px; }
            QPushButton:hover { background: #30363d; }
        """)
        self._build()

    def _build(self) -> None:
        # 로고
        logo = QLabel("  💹 CoinHTS  ")
        logo.setStyleSheet("color: #58a6ff; font-weight: bold; font-size: 14px;")
        self.addWidget(logo)
        self.addSeparator()

        # 심볼
        self._sym_combo = QComboBox()
        self._sym_combo.addItems(["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"])
        self._sym_combo.currentTextChanged.connect(self.symbol_changed)
        self.addWidget(QLabel(" 심볼: "))
        self.addWidget(self._sym_combo)
        self.addSeparator()

        # 타임프레임
        self._tf_combo = QComboBox()
        tfs = ["1s", "5s", "15s", "30s", "1m", "3m", "5m", "15m", "1H", "4H"]
        self._tf_combo.addItems(tfs)
        self._tf_combo.setCurrentText("15m")
        self._tf_combo.currentTextChanged.connect(self.timeframe_changed)
        self.addWidget(QLabel(" 타임프레임: "))
        self.addWidget(self._tf_combo)
        self.addSeparator()

        # 연결 상태
        self._status_label = QLabel("● 연결 중...")
        self._status_label.setStyleSheet("color: #e3b341;")
        self.addWidget(self._status_label)
        self.addSeparator()
        # 설정 버튼
        settings_btn = QPushButton("⚙️")
        settings_btn.setToolTip("설정")
        settings_btn.clicked.connect(self._open_settings)
        self.addWidget(settings_btn)
        self._settings_callback = None

    def set_connected(self, ok: bool) -> None:
        if ok:
            self._status_label.setText("● 연결됨")
            self._status_label.setStyleSheet("color: #3fb950;")
        else:
            self._status_label.setText("● 연결 끊김")
            self._status_label.setStyleSheet("color: #f85149;")

    def _open_settings(self) -> None:
        if self._settings_callback:
            self._settings_callback()

    @property
    def current_symbol(self) -> str:
        return self._sym_combo.currentText()

    @property
    def current_timeframe(self) -> str:
        return self._tf_combo.currentText()


# ── 메인 차트 위젯 ────────────────────────────────────────
class ChartWidget(QWidget):
    """
    PyQtGraph 기반 캔들스틱 차트.
    60FPS 목표, OpenGL 가속 지원.
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._setup_ui()
        self._candles: list = []

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # pyqtgraph 설정
        pg.setConfigOptions(
            antialias=True,
            useOpenGL=True,      # GPU 가속
            background="#0d1117",
        )

        # 캔들차트 플롯
        self._plot_widget = pg.PlotWidget()
        self._plot_widget.setBackground("#0d1117")
        self._plot_widget.showGrid(x=True, y=True, alpha=0.1)
        self._plot_widget.getAxis("left").setTextPen("#8b949e")
        self._plot_widget.getAxis("bottom").setTextPen("#8b949e")

        # 볼륨 플롯
        self._vol_widget = pg.PlotWidget()
        self._vol_widget.setBackground("#0d1117")
        self._vol_widget.setMaximumHeight(80)

        # 델타 플롯
        self._delta_widget = pg.PlotWidget()
        self._delta_widget.setBackground("#0d1117")
        self._delta_widget.setMaximumHeight(60)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self._plot_widget)
        splitter.addWidget(self._vol_widget)
        splitter.addWidget(self._delta_widget)
        splitter.setSizes([600, 80, 60])

        layout.addWidget(splitter)

    def update_candles(self, candles: list) -> None:
        """캔들 데이터 업데이트 (60FPS 목표)."""
        if not candles:
            return
        self._candles = candles
        self._draw_candles()

    def _draw_candles(self) -> None:
        """캔들스틱 그리기."""
        self._plot_widget.clear()
        candles = self._candles
        if not candles:
            return

        times  = [c.ts for c in candles]
        opens  = [c.open  for c in candles]
        highs  = [c.high  for c in candles]
        lows   = [c.low   for c in candles]
        closes = [c.close for c in candles]
        vols   = [c.volume for c in candles]

        # 캔들 그리기 (bull: 초록, bear: 빨강)
        for i, (t, o, h, l, cl) in enumerate(zip(times, opens, highs, lows, closes)):
            color = "#26a641" if cl >= o else "#f85149"
            # 심지
            self._plot_widget.plot([t, t], [l, h], pen=pg.mkPen(color, width=1))
            # 몸통
            w = (times[-1] - times[0]) / len(times) * 0.7
            body = pg.QtWidgets.QGraphicsRectItem(t - w/2, min(o, cl), w, abs(o - cl))
            body.setBrush(pg.mkBrush(color))
            body.setPen(pg.mkPen(color))
            self._plot_widget.addItem(body)


# ── 오더북 위젯 ───────────────────────────────────────────
class OrderBookWidget(QWidget):
    """실시간 호가창."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        title = QLabel("호가창")
        title.setStyleSheet("color: #8b949e; font-size: 11px;")
        layout.addWidget(title)

        self._tree = QTreeWidget()
        self._tree.setHeaderLabels(["가격", "수량", "누적"])
        self._tree.setStyleSheet("""
            QTreeWidget { background: #161b22; color: #c9d1d9;
                          border: none; font-size: 11px; }
            QTreeWidget::item:selected { background: #21262d; }
            QHeaderView::section { background: #21262d; color: #8b949e;
                                   border: none; padding: 2px; }
        """)
        layout.addWidget(self._tree)

    def update_book(self, book) -> None:
        """오더북 업데이트."""
        from PySide6.QtWidgets import QTreeWidgetItem
        self._tree.clear()

        # Asks (매도, 위에서 아래로)
        for lvl in reversed(book.asks[:10]):
            item = QTreeWidgetItem([f"{lvl.price:.2f}", f"{lvl.size:.4f}", ""])
            item.setForeground(0, QColor("#f85149"))
            item.setForeground(1, QColor("#f85149"))
            self._tree.addTopLevelItem(item)

        # 스프레드 구분선
        spread_item = QTreeWidgetItem(["── 스프레드 ──", "", ""])
        spread_item.setForeground(0, QColor("#8b949e"))
        self._tree.addTopLevelItem(spread_item)

        # Bids (매수, 위에서 아래로)
        for lvl in book.bids[:10]:
            item = QTreeWidgetItem([f"{lvl.price:.2f}", f"{lvl.size:.4f}", ""])
            item.setForeground(0, QColor("#3fb950"))
            item.setForeground(1, QColor("#3fb950"))
            self._tree.addTopLevelItem(item)


# ── 신호 로그 위젯 ────────────────────────────────────────
class SignalLogWidget(QWidget):
    """전략 신호 로그."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self) -> None:
        from PySide6.QtWidgets import QListWidget
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        title = QLabel("신호 로그")
        title.setStyleSheet("color: #8b949e; font-size: 11px;")
        layout.addWidget(title)

        self._list = QListWidget()
        self._list.setStyleSheet("""
            QListWidget { background: #161b22; color: #c9d1d9;
                          border: none; font-size: 11px; }
        """)
        layout.addWidget(self._list)

    def add_signal(self, sig) -> None:
        """신호 추가."""
        from PySide6.QtWidgets import QListWidgetItem
        direction = sig.direction
        color = "#3fb950" if direction == "LONG" else "#f85149"
        icon  = "🟢" if direction == "LONG" else "🔴"
        text  = (f"{icon} {sig.symbol} {direction} | "
                 f"진입 {sig.entry:.2f} | SL {sig.sl:.2f} | TP {sig.tp:.2f} | "
                 f"RR 1:{sig.rr:.1f} | {sig.score:.0f}점")
        item = QListWidgetItem(text)
        item.setForeground(QColor(color))
        self._list.insertItem(0, item)
        # 최대 100개 유지
        while self._list.count() > 100:
            self._list.takeItem(self._list.count() - 1)


# ── 메인 윈도우 ───────────────────────────────────────────
class MainWindow(QMainWindow):
    """CoinHTS 메인 윈도우."""

    def __init__(self, config: Optional[AppConfig] = None) -> None:
        super().__init__()
        self.config  = config or get_config()
        self.bus     = get_event_bus()
        self._setup_window()
        self._setup_toolbar()
        self._setup_central()
        self._setup_docks()
        self._setup_statusbar()
        self._connect_events()
        self._setup_settings()

    def _setup_window(self) -> None:
        self.setWindowTitle("CoinHTS — Professional Crypto Trading Terminal")
        self.resize(1600, 900)
        self.setStyleSheet("""
            QMainWindow { background: #0d1117; }
            QDockWidget { color: #c9d1d9; font-size: 11px; }
            QDockWidget::title { background: #161b22; padding: 4px;
                                  border-bottom: 1px solid #21262d; }
        """)

    def _setup_toolbar(self) -> None:
        self._toolbar = MarketToolBar(self)
        self.addToolBar(self._toolbar)
        self._toolbar.symbol_changed.connect(self._on_symbol_changed)
        self._toolbar.timeframe_changed.connect(self._on_timeframe_changed)
        # 드로잉 툴바 (두 번째 줄)
        self.addToolBarBreak()
        self._drawing_toolbar = DrawingToolBar(self)
        draw_toolbar_wrapper = self.addToolBar("Drawing")
        draw_toolbar_wrapper.addWidget(self._drawing_toolbar)
        self._drawing_toolbar.mode_changed.connect(self._on_draw_mode_changed)
        self._drawing_manager: DrawingManager | None = None

    def _setup_central(self) -> None:
        """중앙 차트 영역 — 탭으로 차트/Footprint 전환."""
        from PySide6.QtWidgets import QTabWidget
        self._tabs = QTabWidget()
        self._tabs.setStyleSheet("""
            QTabWidget::pane { border: none; background: #0d1117; }
            QTabBar::tab { background: #21262d; color: #8b949e; padding: 6px 16px;
                           border: none; font-size: 11px; }
            QTabBar::tab:selected { background: #0d1117; color: #c9d1d9;
                                    border-bottom: 2px solid #1f6feb; }
        """)
        # 메인 차트
        self._chart = MainChartWidget()
        self._tabs.addTab(self._chart, "📈 차트")
        # 멀티 차트
        self._multi_chart = MultiChartWidget()
        self._tabs.addTab(self._multi_chart, "📊 멀티차트")
        # Footprint
        self._footprint = FootprintWidget(tick_size=0.5)
        self._tabs.addTab(self._footprint, "📊 Footprint")
        # 히트맵
        self._heatmap = HeatmapWidget()
        self._tabs.addTab(self._heatmap, "🌡 히트맵")
        # AI 차트 요약
        self._ai_summary = AISummaryWidget()
        self._ai_summary.refresh_requested.connect(self._run_ai_summary)
        self._tabs.addTab(self._ai_summary, "🤖 AI 요약")
        self.setCentralWidget(self._tabs)

    def _setup_docks(self) -> None:
        """도크 패널들 (드래그로 재배치 가능)."""
        # 오더북 (오른쪽)
        self._book_widget = OrderBookWidget()
        book_dock = QDockWidget("📊 오더북", self)
        book_dock.setWidget(self._book_widget)
        book_dock.setMinimumWidth(220)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, book_dock)

        # 신호 로그 (아래)
        self._signal_log = SignalLogWidget()
        sig_dock = QDockWidget("🚀 신호 로그", self)
        sig_dock.setWidget(self._signal_log)
        sig_dock.setMaximumHeight(200)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, sig_dock)

        # 스캐너 (아래)
        self._scanner_widget = ScannerWidget()
        self._scanner_widget.signal_clicked.connect(self._on_scanner_signal_clicked)
        scan_dock = QDockWidget("🔍 스캐너", self)
        scan_dock.setWidget(self._scanner_widget)
        scan_dock.setMaximumHeight(250)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, scan_dock)

        # Time & Sales (오른쪽)
        self._time_sales = TimeSalesWidget(
            symbol=self._toolbar.current_symbol,
            whale_size=1.0,
        )
        ts_dock = QDockWidget("⏱ Time & Sales", self)
        ts_dock.setWidget(self._time_sales)
        ts_dock.setMinimumWidth(220)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, ts_dock)

        # SMC 패널 (오른쪽)
        self._smc_panel = SMCControlPanel()
        self._smc_panel.refresh_requested.connect(self._run_smc_analysis)
        smc_dock = QDockWidget("🏗 SMC 분석", self)
        smc_dock.setWidget(self._smc_panel)
        smc_dock.setMinimumWidth(220)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, smc_dock)
        self.tabifyDockWidget(book_dock, smc_dock)

        # 포지션 관리 (아래)
        self._position_widget = PositionWidget()
        pos_dock = QDockWidget("💼 포지션 관리", self)
        pos_dock.setWidget(self._position_widget)
        pos_dock.setMinimumHeight(160)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, pos_dock)
        self.tabifyDockWidget(sig_dock, pos_dock)

        # SMC 분석 패널 (오른쪽)
        self._smc_panel = SMCControlPanel()
        self._smc_panel.refresh_requested.connect(self._run_smc_analysis)
        smc_dock = QDockWidget("🏗 SMC 분석", self)
        smc_dock.setWidget(self._smc_panel)
        smc_dock.setMinimumWidth(220)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, smc_dock)
        self.tabifyDockWidget(book_dock, smc_dock)

        # 포지션 관리 (아래)
        self._position_widget = PositionWidget()
        pos_dock = QDockWidget("💼 포지션 관리", self)
        pos_dock.setWidget(self._position_widget)
        pos_dock.setMinimumHeight(160)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, pos_dock)
        self.tabifyDockWidget(sig_dock, pos_dock)

        # SMC 오버레이 (차트에 연결)
        self._smc_overlay: SMCOverlay | None = None

    def _setup_statusbar(self) -> None:
        bar = QStatusBar()
        bar.setStyleSheet("background: #161b22; color: #8b949e; font-size: 11px;")
        self._price_label = QLabel("BTC-USDT-SWAP: --")
        self._oi_label    = QLabel("OI: --")
        self._funding_label = QLabel("Funding: --")
        bar.addWidget(self._price_label)
        bar.addWidget(QLabel(" | "))
        bar.addWidget(self._oi_label)
        bar.addWidget(QLabel(" | "))
        bar.addWidget(self._funding_label)
        self.setStatusBar(bar)

    def _setup_settings(self) -> None:
        """설정 다이얼로그 연결."""
        self._toolbar._settings_callback = self._open_settings
        self._ict_params = __import__(
            'strategy.ict_engine', fromlist=['ICTParams']
        ).ICTParams()

    def _open_settings(self) -> None:
        dlg = SettingsDialog(self.config, self._ict_params, self)
        dlg.settings_applied.connect(self._on_settings_applied)
        dlg.exec()

    def _on_settings_applied(self) -> None:
        logger.info("설정 적용됨")
        self.bus.publish_nowait("settings_changed", self.config)

    def _connect_events(self) -> None:
        """EventBus 이벤트 연결."""
        self.bus.subscribe("tick",       self._on_tick)
        self.bus.subscribe("orderbook",  self._on_orderbook)
        self.bus.subscribe("connected",  lambda _: self._toolbar.set_connected(True))
        self.bus.subscribe("disconnected", lambda _: self._toolbar.set_connected(False))
        self.bus.subscribe("strategy_signal", self._on_signal)

    # ── 이벤트 핸들러 ─────────────────────────────────────
    def _on_tick(self, tick) -> None:
        if tick.symbol == self._toolbar.current_symbol:
            color = "#3fb950" if tick.side.value == "buy" else "#f85149"
            self._price_label.setStyleSheet(f"color: {color};")
            self._price_label.setText(f"{tick.symbol}: {tick.price:.2f}")
            if hasattr(self, "_position_widget"):
                self._position_widget.update_price(tick.symbol, tick.price)
            # 포지션 위젯 가격 업데이트
            if hasattr(self, '_position_widget'):
                self._position_widget.update_price(tick.symbol, tick.price)

    def _run_smc_analysis(self) -> None:
        sym = self._toolbar.current_symbol
        try:
            logger.info(f"[UI] SMC 분석 요청: {sym}")
        except Exception as e:
            logger.error(f"[UI] SMC 분석 오류: {e}")

    def _run_ai_summary(self) -> None:
        """AI 차트 요약 실행."""
        sym = self._toolbar.current_symbol
        if hasattr(self, '_ai_summary'):
            self._ai_summary.run_analysis(sym, [], None, None, None)
            logger.info(f"[UI] AI 요약 요청: {sym}")

    def update_smc_result(self, result, candles=None) -> None:
        """외부에서 SMC 결과 업데이트 (앱 오케스트레이터 연결용)."""
        if hasattr(self, '_smc_panel'):
            self._smc_panel.update_result(result)
        if hasattr(self, '_smc_overlay') and self._smc_overlay and candles:
            ts_list = [c.ts for c in candles]
            x0 = ts_list[0]  if ts_list else 0
            x1 = ts_list[-1] if ts_list else 0
            self._smc_overlay.render(result, x0, x1)

    def set_trader(self, trader) -> None:
        """자동매매 엔진 연결."""
        if hasattr(self, '_position_widget'):
            self._position_widget.set_trader(trader)

    def _on_orderbook(self, book) -> None:
        if book.symbol == self._toolbar.current_symbol:
            self._book_widget.update_book(book)
            self._heatmap.on_orderbook(book)

    def _on_signal(self, sig) -> None:
        self._signal_log.add_signal(sig)

    def _on_draw_mode_changed(self, mode_name: str) -> None:
        """드로잉 모드 변경."""
        if mode_name == "DELETE":
            if self._drawing_manager: self._drawing_manager.delete_last()
            return
        if mode_name == "CLEAR_ALL":
            if self._drawing_manager: self._drawing_manager.clear_all()
            return
        if self._drawing_manager:
            mode = DrawMode[mode_name] if mode_name in DrawMode.__members__ else DrawMode.NONE
            self._drawing_manager.set_mode(mode, self._drawing_toolbar.current_color)

    def _on_scanner_signal_clicked(self, symbol: str, ts: float) -> None:
        """스캐너 신호 더블클릭 → 해당 심볼로 차트 전환."""
        idx = self._toolbar._sym_combo.findText(symbol)
        if idx >= 0:
            self._toolbar._sym_combo.setCurrentIndex(idx)

    def _on_symbol_changed(self, sym: str) -> None:
        logger.info(f"심볼 변경: {sym}")
        if hasattr(self, '_time_sales'):
            self._time_sales.set_symbol(sym)
        self.bus.publish_nowait("symbol_changed", sym)

    def _on_timeframe_changed(self, tf: str) -> None:
        logger.info(f"타임프레임 변경: {tf}")
        self.bus.publish_nowait("timeframe_changed", tf)

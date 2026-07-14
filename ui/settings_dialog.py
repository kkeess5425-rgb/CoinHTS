"""
ui/settings_dialog.py
=====================
설정 다이얼로그.
탭 구조:
  1. 거래소 (API 키, 시크릿, 패스프레이즈)
  2. 알림 (Telegram, Discord)
  3. 전략 (ICT 파라미터)
  4. UI (테마, FPS, 폰트)
  5. 데이터베이스 (SQLite 경로, PostgreSQL)
"""
from __future__ import annotations

import logging
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QIntValidator, QDoubleValidator
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget,
    QWidget, QLabel, QLineEdit, QPushButton,
    QCheckBox, QSpinBox, QDoubleSpinBox, QComboBox,
    QGroupBox, QFormLayout, QDialogButtonBox,
    QFileDialog, QMessageBox, QFrame,
)

from core.config import AppConfig, get_config
from strategy.ict_engine import ICTParams

logger = logging.getLogger(__name__)

STYLE_LABEL = "color: #c9d1d9; font-size: 11px;"
STYLE_INPUT = """
    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
        background: #21262d; color: #c9d1d9;
        border: 1px solid #30363d; border-radius: 4px;
        padding: 4px 8px; font-size: 11px;
    }
    QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {
        border-color: #1f6feb;
    }
"""
STYLE_GROUP = """
    QGroupBox {
        color: #8b949e; font-size: 11px;
        border: 1px solid #30363d; border-radius: 4px;
        margin-top: 8px; padding-top: 8px;
    }
    QGroupBox::title { subcontrol-origin: margin; left: 8px; }
"""


def make_input(placeholder: str = "", password: bool = False) -> QLineEdit:
    w = QLineEdit()
    w.setPlaceholderText(placeholder)
    if password:
        w.setEchoMode(QLineEdit.EchoMode.Password)
    w.setStyleSheet(STYLE_INPUT)
    return w


class ExchangeTab(QWidget):
    """거래소 API 설정 탭."""

    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self._config = config
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)

        # OKX
        okx_group = QGroupBox("OKX")
        okx_group.setStyleSheet(STYLE_GROUP)
        okx_form = QFormLayout(okx_group)

        self._api_key = make_input("API Key", password=True)
        self._api_key.setText(self._config.exchange.api_key)
        self._api_secret = make_input("API Secret", password=True)
        self._api_secret.setText(self._config.exchange.api_secret)
        self._passphrase = make_input("Passphrase", password=True)
        self._passphrase.setText(self._config.exchange.passphrase)
        self._testnet = QCheckBox("Testnet 사용")
        self._testnet.setChecked(self._config.exchange.testnet)
        self._testnet.setStyleSheet("QCheckBox { color: #c9d1d9; }")

        for label, widget in [
            ("API Key:",      self._api_key),
            ("API Secret:",   self._api_secret),
            ("Passphrase:",   self._passphrase),
            ("Testnet:",      self._testnet),
        ]:
            lbl = QLabel(label)
            lbl.setStyleSheet(STYLE_LABEL)
            okx_form.addRow(lbl, widget)

        layout.addWidget(okx_group)

        # 심볼
        sym_group = QGroupBox("기본 심볼")
        sym_group.setStyleSheet(STYLE_GROUP)
        sym_layout = QVBoxLayout(sym_group)
        self._sym_input = make_input("BTC-USDT-SWAP, ETH-USDT-SWAP")
        self._sym_input.setText(", ".join(self._config.default_symbols))
        sym_layout.addWidget(QLabel("심볼 목록 (쉼표로 구분):"))
        sym_layout.addWidget(self._sym_input)
        layout.addWidget(sym_group)

        layout.addStretch()

        # 연결 테스트
        test_btn = QPushButton("🔗 연결 테스트")
        test_btn.setStyleSheet("""
            QPushButton { background: #238636; color: white; border: none;
                          padding: 6px 16px; border-radius: 4px; }
            QPushButton:hover { background: #2ea043; }
        """)
        test_btn.clicked.connect(self._test_connection)
        layout.addWidget(test_btn)

    def _test_connection(self) -> None:
        QMessageBox.information(self, "연결 테스트", "OKX 연결 테스트는 앱 재시작 후 로그에서 확인하세요.")

    def apply(self) -> None:
        self._config.exchange.api_key    = self._api_key.text().strip()
        self._config.exchange.api_secret = self._api_secret.text().strip()
        self._config.exchange.passphrase = self._passphrase.text().strip()
        self._config.exchange.testnet    = self._testnet.isChecked()
        syms = [s.strip() for s in self._sym_input.text().split(",") if s.strip()]
        if syms:
            self._config.default_symbols = syms


class AlertTab(QWidget):
    """알림 설정 탭."""

    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self._config = config
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)

        # Telegram
        tg_group = QGroupBox("Telegram")
        tg_group.setStyleSheet(STYLE_GROUP)
        tg_form = QFormLayout(tg_group)
        self._tg_token = make_input("bot 토큰", password=True)
        self._tg_token.setText(self._config.alert.telegram_token)
        self._tg_chat  = make_input("Chat ID")
        self._tg_chat.setText(self._config.alert.telegram_chat_id)
        for label, widget in [("Bot Token:", self._tg_token), ("Chat ID:", self._tg_chat)]:
            lbl = QLabel(label); lbl.setStyleSheet(STYLE_LABEL)
            tg_form.addRow(lbl, widget)
        layout.addWidget(tg_group)

        # Discord
        dc_group = QGroupBox("Discord")
        dc_group.setStyleSheet(STYLE_GROUP)
        dc_layout = QVBoxLayout(dc_group)
        self._dc_webhook = make_input("Webhook URL")
        self._dc_webhook.setText(self._config.alert.discord_webhook)
        dc_layout.addWidget(QLabel("Webhook URL:"))
        dc_layout.addWidget(self._dc_webhook)
        layout.addWidget(dc_group)

        layout.addStretch()

    def apply(self) -> None:
        self._config.alert.telegram_token   = self._tg_token.text().strip()
        self._config.alert.telegram_chat_id = self._tg_chat.text().strip()
        self._config.alert.discord_webhook  = self._dc_webhook.text().strip()


class StrategyTab(QWidget):
    """ICT 전략 파라미터 설정 탭."""

    def __init__(self, params: ICTParams) -> None:
        super().__init__()
        self._params = params
        self._widgets: dict[str, QWidget] = {}
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)

        defs = [
            ("일반",         [
                ("min_rr",                "최소 RR",               "double", 0.5,  10.0,  0.5),
                ("min_risk_pct",          "최소 위험 % (SL 거리)",  "double", 0.01, 1.0,   0.01),
                ("min_confluence",        "최소 컨플루언스",         "int",    0,    4,     1),
            ]),
            ("유동성 스윕",  [
                ("sweep_window",          "스윕 윈도우 (봉)",       "int",    1,   30,    1),
                ("liq_lookback",          "유동성 탐색 (봉)",       "int",    5,   100,   5),
                ("sweep_confirm_dist",    "스윕 확인 거리 (ATR×)", "double", 0.0, 2.0,   0.1),
            ]),
            ("FVG",          [
                ("fvg_min_pct",           "FVG 최소 크기 (%)",     "double", 0.01, 1.0,   0.01),
                ("fvg_lookback",          "FVG 탐색 (봉)",         "int",    5,   100,   5),
                ("fvg_max_fill",          "FVG 최대 채움 비율",     "double", 0.1, 1.0,   0.1),
            ]),
            ("Displacement", [
                ("require_displacement",  "Displacement 필수",     "bool",   None, None,  None),
                ("displacement_atr_mult", "Displacement ATR 배수", "double", 0.1,  3.0,   0.1),
                ("displacement_max_bars", "Displacement 최대 봉",  "int",    1,    20,    1),
            ]),
            ("SL/TP",        [
                ("sl_buffer_atr",         "SL 버퍼 (ATR×)",       "double", 0.0,  2.0,   0.1),
                ("ote_fib_min",           "OTE 피보 하단",         "double", 0.0,  1.0,   0.01),
                ("ote_fib_max",           "OTE 피보 상단",         "double", 0.0,  1.0,   0.01),
            ]),
        ]

        for group_name, fields in defs:
            group = QGroupBox(group_name)
            group.setStyleSheet(STYLE_GROUP)
            form  = QFormLayout(group)
            for attr, label, ftype, mn, mx, step in fields:
                lbl = QLabel(label); lbl.setStyleSheet(STYLE_LABEL)
                val = getattr(self._params, attr)
                if ftype == "bool":
                    w = QCheckBox(); w.setChecked(bool(val))
                    w.setStyleSheet("QCheckBox { color: #c9d1d9; }")
                elif ftype == "int":
                    w = QSpinBox(); w.setRange(int(mn), int(mx)); w.setValue(int(val))
                    w.setStyleSheet(STYLE_INPUT)
                else:
                    w = QDoubleSpinBox(); w.setRange(mn, mx)
                    w.setSingleStep(step); w.setValue(float(val)); w.setDecimals(3)
                    w.setStyleSheet(STYLE_INPUT)
                self._widgets[attr] = w
                form.addRow(lbl, w)
            layout.addWidget(group)
        layout.addStretch()

    def apply(self) -> None:
        for attr, w in self._widgets.items():
            if isinstance(w, QCheckBox):
                setattr(self._params, attr, w.isChecked())
            elif isinstance(w, QSpinBox):
                setattr(self._params, attr, w.value())
            elif isinstance(w, QDoubleSpinBox):
                setattr(self._params, attr, w.value())


class UITab(QWidget):
    """UI 설정 탭."""

    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self._config = config
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)

        grp = QGroupBox("디스플레이")
        grp.setStyleSheet(STYLE_GROUP)
        form = QFormLayout(grp)

        self._fps = QSpinBox(); self._fps.setRange(10, 144); self._fps.setValue(self._config.ui.fps)
        self._fps.setStyleSheet(STYLE_INPUT)
        self._font = QSpinBox(); self._font.setRange(8, 18); self._font.setValue(self._config.ui.font_size)
        self._font.setStyleSheet(STYLE_INPUT)
        self._bull = make_input(); self._bull.setText(self._config.ui.bull_color)
        self._bear = make_input(); self._bear.setText(self._config.ui.bear_color)

        for label, widget in [
            ("목표 FPS:", self._fps), ("폰트 크기:", self._font),
            ("상승 색상:", self._bull), ("하락 색상:", self._bear),
        ]:
            lbl = QLabel(label); lbl.setStyleSheet(STYLE_LABEL)
            form.addRow(lbl, widget)

        layout.addWidget(grp)
        layout.addStretch()

    def apply(self) -> None:
        self._config.ui.fps        = self._fps.value()
        self._config.ui.font_size  = self._font.value()
        self._config.ui.bull_color = self._bull.text()
        self._config.ui.bear_color = self._bear.text()


class DatabaseTab(QWidget):
    """데이터베이스 설정 탭."""

    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self._config = config
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)

        # SQLite
        sqlite_grp = QGroupBox("SQLite (기본)")
        sqlite_grp.setStyleSheet(STYLE_GROUP)
        s_layout = QHBoxLayout(sqlite_grp)
        self._sqlite_path = make_input("data/coinhts.db")
        self._sqlite_path.setText(self._config.database.sqlite_path)
        browse_btn = QPushButton("찾기")
        browse_btn.setStyleSheet("QPushButton { background: #21262d; color: #c9d1d9; border: 1px solid #30363d; padding: 4px 8px; border-radius: 3px; }")
        browse_btn.clicked.connect(self._browse_sqlite)
        s_layout.addWidget(self._sqlite_path)
        s_layout.addWidget(browse_btn)
        layout.addWidget(sqlite_grp)

        # PostgreSQL
        pg_grp = QGroupBox("PostgreSQL (선택)")
        pg_grp.setStyleSheet(STYLE_GROUP)
        pg_layout = QVBoxLayout(pg_grp)
        self._use_pg = QCheckBox("PostgreSQL 사용")
        self._use_pg.setChecked(self._config.database.use_postgresql)
        self._use_pg.setStyleSheet("QCheckBox { color: #c9d1d9; }")
        self._pg_dsn = make_input("postgresql://user:pass@localhost:5432/coinhts")
        self._pg_dsn.setText(self._config.database.pg_dsn)
        self._pg_dsn.setEnabled(self._config.database.use_postgresql)
        self._use_pg.stateChanged.connect(lambda s: self._pg_dsn.setEnabled(bool(s)))
        pg_layout.addWidget(self._use_pg)
        pg_layout.addWidget(QLabel("DSN:"))
        pg_layout.addWidget(self._pg_dsn)
        layout.addWidget(pg_grp)

        layout.addStretch()

    def _browse_sqlite(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "SQLite DB 경로", "", "SQLite (*.db)")
        if path:
            self._sqlite_path.setText(path)

    def apply(self) -> None:
        self._config.database.sqlite_path    = self._sqlite_path.text()
        self._config.database.use_postgresql = self._use_pg.isChecked()
        self._config.database.pg_dsn         = self._pg_dsn.text()


class SettingsDialog(QDialog):
    """
    전체 설정 다이얼로그.
    OK 클릭 시 설정을 메모리에 적용하고 config.json에 저장.
    """
    settings_applied = Signal()

    def __init__(
        self,
        config:  Optional[AppConfig] = None,
        params:  Optional[ICTParams] = None,
        parent:  Optional[QWidget]   = None,
    ) -> None:
        super().__init__(parent)
        self._config = config or get_config()
        self._params = params or ICTParams()

        self.setWindowTitle("⚙️ CoinHTS 설정")
        self.setMinimumSize(520, 600)
        self.setStyleSheet("""
            QDialog { background: #0d1117; }
            QTabWidget::pane { border: 1px solid #30363d; background: #0d1117; }
            QTabBar::tab { background: #161b22; color: #8b949e; padding: 6px 16px;
                           border: 1px solid #30363d; border-bottom: none; }
            QTabBar::tab:selected { background: #0d1117; color: #c9d1d9; }
            QScrollArea { border: none; background: transparent; }
        """)

        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        self._tabs = QTabWidget()
        self._exchange_tab = ExchangeTab(self._config)
        self._alert_tab    = AlertTab(self._config)
        self._strategy_tab = StrategyTab(self._params)
        self._ui_tab       = UITab(self._config)
        self._db_tab       = DatabaseTab(self._config)

        self._tabs.addTab(self._exchange_tab, "🔑 거래소")
        self._tabs.addTab(self._alert_tab,    "🔔 알림")
        self._tabs.addTab(self._strategy_tab, "📊 전략 (ICT)")
        self._tabs.addTab(self._ui_tab,       "🎨 UI")
        self._tabs.addTab(self._db_tab,       "💾 데이터베이스")
        layout.addWidget(self._tabs)

        # OK / 취소
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.setStyleSheet("""
            QPushButton { background: #21262d; color: #c9d1d9; border: 1px solid #30363d;
                          padding: 6px 20px; border-radius: 4px; }
            QPushButton:hover { background: #30363d; }
            QPushButton[text="OK"] { background: #238636; border-color: #238636; }
        """)
        buttons.accepted.connect(self._apply)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _apply(self) -> None:
        self._exchange_tab.apply()
        self._alert_tab.apply()
        self._strategy_tab.apply()
        self._ui_tab.apply()
        self._db_tab.apply()
        try:
            self._config.save()
        except Exception as e:
            logger.warning(f"설정 저장 실패: {e}")
        self.settings_applied.emit()
        self.accept()
        logger.info("설정 적용 완료")

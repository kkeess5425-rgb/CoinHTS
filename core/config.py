"""
core/config.py
==============
앱 전역 설정. 환경변수 또는 config.json에서 로드.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class ExchangeConfig:
    api_key:    str = ""
    api_secret: str = ""
    passphrase: str = ""   # OKX 전용
    testnet:    bool = False


@dataclass
class UIConfig:
    theme:       str = "dark"
    fps:         int = 60
    font_size:   int = 12
    chart_bg:    str = "#0d1117"
    chart_grid:  str = "#21262d"
    bull_color:  str = "#26a641"
    bear_color:  str = "#f85149"


@dataclass
class AlertConfig:
    telegram_token:   str = ""
    telegram_chat_id: str = ""
    discord_webhook:  str = ""


@dataclass
class DatabaseConfig:
    sqlite_path:    str = "data/coinhts.db"
    use_postgresql: bool = False
    pg_dsn:         str = ""


@dataclass
class AppConfig:
    exchange:  ExchangeConfig  = field(default_factory=ExchangeConfig)
    ui:        UIConfig        = field(default_factory=UIConfig)
    alert:     AlertConfig     = field(default_factory=AlertConfig)
    database:  DatabaseConfig  = field(default_factory=DatabaseConfig)

    # 기본 심볼 목록
    default_symbols: list[str] = field(default_factory=lambda: [
        "BTC-USDT-SWAP", "ETH-USDT-SWAP"
    ])

    # 성능 설정
    tick_buffer_size:   int = 100_000   # 메모리 내 틱 버퍼
    candle_history:     int = 2000      # 로드할 최대 봉 수
    orderbook_depth:    int = 400       # 호가창 깊이

    @classmethod
    def load(cls, path: str = "config.json") -> "AppConfig":
        """JSON 파일에서 설정 로드. 없으면 기본값 사용."""
        p = Path(path)
        if not p.exists():
            return cls()
        with open(p) as f:
            data = json.load(f)
        cfg = cls()
        # 환경변수 우선 (보안)
        cfg.exchange.api_key    = os.environ.get("OKX_API_KEY",    data.get("api_key", ""))
        cfg.exchange.api_secret = os.environ.get("OKX_API_SECRET", data.get("api_secret", ""))
        cfg.exchange.passphrase = os.environ.get("OKX_PASSPHRASE", data.get("passphrase", ""))
        cfg.alert.telegram_token   = data.get("telegram_token", "")
        cfg.alert.telegram_chat_id = data.get("telegram_chat_id", "")
        return cfg

    def save(self, path: str = "config.json") -> None:
        """현재 설정을 JSON으로 저장 (API 키 제외)."""
        data = {
            "telegram_token":   self.alert.telegram_token,
            "telegram_chat_id": self.alert.telegram_chat_id,
            "discord_webhook":  self.alert.discord_webhook,
            "default_symbols":  self.default_symbols,
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)


# 전역 싱글턴
_config: AppConfig | None = None


def get_config() -> AppConfig:
    global _config
    if _config is None:
        _config = AppConfig.load()
    return _config

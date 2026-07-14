"""
plugins/plugin_system.py
========================
플러그인 시스템.
외부 전략 스크립트를 동적으로 로드해서 실행한다.
각 플러그인은 BasePlugin 인터페이스를 구현한다.
"""
from __future__ import annotations

import importlib.util
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

from core.events import EventBus, get_event_bus
from core.models import Candle, FootprintBar, StrategySignal

logger = logging.getLogger(__name__)


# ── 플러그인 인터페이스 ───────────────────────────────
class BasePlugin(ABC):
    """모든 플러그인이 구현해야 하는 기본 인터페이스."""

    def __init__(self, event_bus: Optional[EventBus] = None) -> None:
        self.bus = event_bus or get_event_bus()

    @property
    @abstractmethod
    def name(self) -> str:
        """플러그인 이름."""
        ...

    @property
    @abstractmethod
    def version(self) -> str:
        """플러그인 버전."""
        ...

    @property
    def description(self) -> str:
        return ""

    def on_candle(self, candle: Candle) -> None:
        """캔들 수신 콜백 (선택 구현)."""
        pass

    def on_footprint(self, bar: FootprintBar) -> None:
        """Footprint 봉 수신 콜백 (선택 구현)."""
        pass

    def on_signal(self, signal: StrategySignal) -> None:
        """신호 수신 콜백 (선택 구현)."""
        pass

    async def on_startup(self) -> None:
        """앱 시작 시 호출."""
        pass

    async def on_shutdown(self) -> None:
        """앱 종료 시 호출."""
        pass


@dataclass
class PluginInfo:
    """플러그인 메타데이터."""
    name:        str
    version:     str
    description: str
    enabled:     bool = True
    module_path: str  = ""
    instance:    Optional[BasePlugin] = None


class PluginManager:
    """
    플러그인 관리자.
    지정된 디렉토리에서 플러그인 파일을 스캔해서 동적으로 로드한다.
    """

    def __init__(
        self,
        plugin_dir:  str = "plugins/user",
        event_bus:   Optional[EventBus] = None,
    ) -> None:
        self._plugin_dir = plugin_dir
        self._bus        = event_bus or get_event_bus()
        self._plugins:   list[PluginInfo] = []

        # EventBus 연결
        self._bus.subscribe("candle",          self._dispatch_candle)
        self._bus.subscribe("footprint",       self._dispatch_footprint)
        self._bus.subscribe("strategy_signal", self._dispatch_signal)

    # ── 로드 / 언로드 ────────────────────────────────
    def load_all(self) -> int:
        """플러그인 디렉토리에서 전체 로드."""
        if not os.path.exists(self._plugin_dir):
            os.makedirs(self._plugin_dir, exist_ok=True)
            self._create_example_plugin()
            return 0

        count = 0
        for fname in os.listdir(self._plugin_dir):
            if fname.endswith(".py") and not fname.startswith("_"):
                path = os.path.join(self._plugin_dir, fname)
                if self.load_file(path):
                    count += 1
        logger.info(f"[Plugin] {count}개 플러그인 로드됨")
        return count

    def load_file(self, path: str) -> bool:
        """단일 플러그인 파일 로드."""
        try:
            spec   = importlib.util.spec_from_file_location("plugin", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            # BasePlugin 서브클래스 찾기
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (isinstance(attr, type) and issubclass(attr, BasePlugin)
                        and attr is not BasePlugin):
                    instance = attr(event_bus=self._bus)
                    info = PluginInfo(
                        name=        instance.name,
                        version=     instance.version,
                        description= instance.description,
                        module_path= path,
                        instance=    instance,
                    )
                    self._plugins.append(info)
                    logger.info(f"[Plugin] 로드: {instance.name} v{instance.version}")
                    return True
        except Exception as e:
            logger.error(f"[Plugin] 로드 실패 {path}: {e}")
        return False

    def unload(self, name: str) -> bool:
        """플러그인 언로드."""
        for info in self._plugins:
            if info.name == name:
                self._plugins.remove(info)
                logger.info(f"[Plugin] 언로드: {name}")
                return True
        return False

    def enable(self, name: str, enabled: bool) -> None:
        for info in self._plugins:
            if info.name == name:
                info.enabled = enabled

    # ── 디스패치 ─────────────────────────────────────
    def _dispatch_candle(self, candle: Candle) -> None:
        for info in self._plugins:
            if info.enabled and info.instance:
                try: info.instance.on_candle(candle)
                except Exception as e: logger.warning(f"[Plugin] {info.name} on_candle 오류: {e}")

    def _dispatch_footprint(self, bar: FootprintBar) -> None:
        for info in self._plugins:
            if info.enabled and info.instance:
                try: info.instance.on_footprint(bar)
                except Exception as e: logger.warning(f"[Plugin] {info.name} on_footprint 오류: {e}")

    def _dispatch_signal(self, sig: StrategySignal) -> None:
        for info in self._plugins:
            if info.enabled and info.instance:
                try: info.instance.on_signal(sig)
                except Exception as e: logger.warning(f"[Plugin] {info.name} on_signal 오류: {e}")

    async def startup_all(self) -> None:
        for info in self._plugins:
            if info.enabled and info.instance:
                try: await info.instance.on_startup()
                except Exception as e: logger.warning(f"[Plugin] {info.name} startup 오류: {e}")

    async def shutdown_all(self) -> None:
        for info in self._plugins:
            if info.instance:
                try: await info.instance.on_shutdown()
                except Exception as e: pass

    # ── 상태 조회 ─────────────────────────────────────
    def get_list(self) -> list[dict]:
        return [
            {"name": p.name, "version": p.version,
             "description": p.description, "enabled": p.enabled}
            for p in self._plugins
        ]

    def _create_example_plugin(self) -> None:
        """예제 플러그인 파일 생성."""
        example = '''"""example_plugin.py — CoinHTS 예제 플러그인."""
from plugins.plugin_system import BasePlugin
from core.models import Candle, StrategySignal

class ExamplePlugin(BasePlugin):
    """
    예제 플러그인.
    신호 발생 시 콘솔에 출력한다.
    """

    @property
    def name(self) -> str:
        return "ExamplePlugin"

    @property
    def version(self) -> str:
        return "1.0.0"

    @property
    def description(self) -> str:
        return "신호 발생 시 콘솔 출력"

    def on_signal(self, signal: StrategySignal) -> None:
        print(f"[ExamplePlugin] {signal.symbol} {signal.direction} "
              f"진입={signal.entry:.2f} 점수={signal.score:.0f}")
'''
        path = os.path.join(self._plugin_dir, "example_plugin.py")
        os.makedirs(self._plugin_dir, exist_ok=True)
        with open(path, "w") as f:
            f.write(example)
        logger.info(f"[Plugin] 예제 플러그인 생성: {path}")

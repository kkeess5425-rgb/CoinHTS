"""
main.py
=======
CoinHTS 앱 진입점.
CoinHTSApp(오케스트레이터) + PySide6 UI를 qasync로 통합.
"""
from __future__ import annotations

import asyncio
import logging
import sys

from PySide6.QtWidgets import QApplication
import qasync

from core.app import CoinHTSApp
from core.config import get_config
from core.events import get_event_bus
from ui.main_window import MainWindow, apply_dark_theme

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


async def async_main(window: MainWindow) -> None:
    """비동기 메인: CoinHTSApp 시작 + 초기 차트 데이터 적용."""
    config = get_config()
    hts    = CoinHTSApp(config)

    # 초기 캔들 로드 후 차트 표시
    initial_candles = await hts.load_initial_data()
    first_sym = config.default_symbols[0] if config.default_symbols else None
    if first_sym and first_sym in initial_candles:
        window._chart.set_candles(initial_candles[first_sym])
        logger.info(f"초기 차트 {first_sym}: {len(initial_candles[first_sym])}봉")

    # 심볼 변경 이벤트 처리
    async def on_symbol_changed(sym: str) -> None:
        if sym in hts._candle_cache and hts._candle_cache[sym]:
            window._chart.set_candles(hts._candle_cache[sym])
        else:
            candles = await hts.exchange.get_candles_paged(sym, __import__('core.models', fromlist=['Timeframe']).Timeframe.M15, 300)
            if candles:
                window._chart.set_candles(candles)
                hts._candle_cache[sym] = candles

    get_event_bus().subscribe("symbol_changed", on_symbol_changed)

    # 앱 시작 (WebSocket + 분석 루프 등)
    await hts.start()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    apply_dark_theme(app)

    loop   = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    config = get_config()
    window = MainWindow(config)
    window.show()

    with loop:
        loop.create_task(async_main(window))
        loop.run_forever()

"""
main.py
=======
CoinHTS 앱 진입점.
PySide6 이벤트 루프 + asyncio를 qasync로 통합한다.
"""
from __future__ import annotations

import asyncio
import logging
import sys

from PySide6.QtWidgets import QApplication
import qasync

from core.config import get_config
from core.events import get_event_bus
from exchange.okx import OKXExchange
from websocket.okx_feed import OKXWebSocketFeed
from ui.main_window import MainWindow, apply_dark_theme

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def load_initial_candles(exchange: OKXExchange, window: MainWindow, config) -> None:
    """초기 캔들 데이터 로드 → 차트에 표시."""
    from core.models import Timeframe
    for sym in config.default_symbols[:1]:   # 첫 번째 심볼만 초기 로드
        try:
            candles = await exchange.get_candles_paged(sym, Timeframe.M15, total=500)
            if candles:
                window._chart.update_candles(candles)
                logger.info(f"{sym} 캔들 {len(candles)}봉 로드 완료")
        except Exception as e:
            logger.warning(f"초기 캔들 로드 실패: {e}")


async def start_market_data(config, window: MainWindow) -> None:
    """시장 데이터 백그라운드 태스크 시작."""
    exchange = OKXExchange(
        api_key=    config.exchange.api_key,
        api_secret= config.exchange.api_secret,
        passphrase= config.exchange.passphrase,
    )

    # 초기 데이터 로드
    await load_initial_candles(exchange, window, config)

    # WebSocket 피드 시작
    feed = OKXWebSocketFeed(
        symbols=   config.default_symbols,
        depth=     50,   # 무료 배포 환경에서는 50단계 (로컬에서는 400)
    )
    await feed.start()


async def main() -> None:
    """메인 비동기 진입점."""
    config = get_config()
    app    = QApplication.instance() or QApplication(sys.argv)
    apply_dark_theme(app)

    window = MainWindow(config)
    window.show()

    # 시장 데이터를 백그라운드 태스크로 시작
    asyncio.create_task(start_market_data(config, window))

    logger.info("CoinHTS 시작됨")


if __name__ == "__main__":
    app = QApplication(sys.argv)
    apply_dark_theme(app)

    loop = qasync.QEventLoop(app)
    asyncio.set_event_loop(loop)

    config = get_config()
    window = MainWindow(config)
    window.show()

    with loop:
        loop.create_task(start_market_data(config, window))
        loop.run_forever()

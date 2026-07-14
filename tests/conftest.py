"""
tests/conftest.py
=================
pytest 공통 픽스처 및 설정.
"""
import asyncio
import numpy as np
import pytest
from core.models import Candle, Tick, Side, Timeframe
from core.events import EventBus


@pytest.fixture
def event_loop():
    """asyncio 이벤트 루프 (비동기 테스트용)."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def bus():
    """독립 EventBus 인스턴스."""
    return EventBus()


@pytest.fixture
def btc_candles_100():
    """BTC 100봉 합성 캔들."""
    np.random.seed(42)
    prices = 65000.0 + np.cumsum(np.random.normal(0, 50, 100))
    return [
        Candle(
            ts=1700000000.0 + i * 900,
            open=prices[i],
            high=prices[i] + abs(np.random.normal(30, 10)),
            low=prices[i]  - abs(np.random.normal(30, 10)),
            close=prices[i],
            volume=abs(np.random.normal(500, 100)),
            symbol="BTC-USDT-SWAP",
            timeframe=Timeframe.M15,
        )
        for i in range(100)
    ]


@pytest.fixture
def btc_candles_300():
    """BTC 300봉 합성 캔들."""
    np.random.seed(0)
    prices = 65000.0 + np.cumsum(np.random.normal(0, 50, 300))
    return [
        Candle(
            ts=1700000000.0 + i * 900,
            open=prices[i],
            high=prices[i] + abs(np.random.normal(30, 10)),
            low=prices[i]  - abs(np.random.normal(30, 10)),
            close=prices[i],
            volume=abs(np.random.normal(500, 100)),
            symbol="BTC-USDT-SWAP",
            timeframe=Timeframe.M15,
        )
        for i in range(300)
    ]


@pytest.fixture
def tick_stream_1000():
    """1000개 합성 틱 스트림."""
    np.random.seed(7)
    price = 65000.0
    ticks = []
    for i in range(1000):
        price += np.random.normal(0, 5)
        ticks.append(Tick(
            ts=    1700000000.0 + i * 0.1,
            price= price,
            size=  abs(np.random.normal(0.1, 0.05)),
            side=  Side.BUY if np.random.random() > 0.5 else Side.SELL,
            symbol="BTC-USDT-SWAP",
        ))
    return ticks

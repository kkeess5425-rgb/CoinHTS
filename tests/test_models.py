"""tests/test_models.py — 데이터 모델 단위 테스트"""
import pytest
from core.models import (
    Tick, Candle, Side, Timeframe, FootprintCell,
    FootprintBar, OrderBook, BookLevel, StrategySignal, Exchange
)


class TestTick:
    def test_basic(self):
        t = Tick(ts=1700000000.0, price=65000.0, size=0.1, side=Side.BUY, symbol="BTC-USDT-SWAP")
        assert t.price == 65000.0
        assert t.side == Side.BUY

    def test_side_values(self):
        assert Side.BUY.value  == "buy"
        assert Side.SELL.value == "sell"


class TestCandle:
    def test_basic(self):
        c = Candle(ts=1700000000.0, open=64900.0, high=65100.0,
                   low=64800.0, close=65000.0, volume=100.0,
                   symbol="BTC-USDT-SWAP", timeframe=Timeframe.M15)
        assert c.high > c.low
        assert c.timeframe == Timeframe.M15

    def test_timeframe_seconds(self):
        assert Timeframe.M1.seconds  == 60
        assert Timeframe.M15.seconds == 900
        assert Timeframe.H1.seconds  == 3600

    def test_timeframe_to_okx(self):
        assert Timeframe.M15.to_okx() == "15m"
        assert Timeframe.H1.to_okx()  == "1H"


class TestFootprintCell:
    def test_delta(self):
        cell = FootprintCell(price=65000.0, buy_vol=10.0, sell_vol=6.0)
        assert cell.delta == pytest.approx(4.0)
        assert cell.total == pytest.approx(16.0)

    def test_imbalance_ratio(self):
        cell = FootprintCell(price=65000.0, buy_vol=8.0, sell_vol=2.0)
        assert cell.imbalance_ratio == pytest.approx(4.0)

    def test_imbalance_ratio_no_sell(self):
        cell = FootprintCell(price=65000.0, buy_vol=5.0, sell_vol=0.0)
        assert cell.imbalance_ratio == float('inf')


class TestStrategySignal:
    def test_rr(self):
        sig = StrategySignal(
            symbol="BTC-USDT-SWAP", ts=1700000000.0,
            direction="LONG", score=85.0,
            entry=65000.0, sl=64500.0, tp=66000.0, reasons=[],
        )
        assert sig.rr == pytest.approx(2.0)

    def test_rr_zero_risk(self):
        sig = StrategySignal(
            symbol="BTC-USDT-SWAP", ts=1700000000.0,
            direction="LONG", score=85.0,
            entry=65000.0, sl=65000.0, tp=66000.0, reasons=[],
        )
        assert sig.rr == 0.0


class TestOrderBook:
    def test_basic(self):
        book = OrderBook(
            symbol="BTC-USDT-SWAP", ts=1700000000.0,
            bids=[BookLevel(64990.0, 1.5), BookLevel(64980.0, 2.0)],
            asks=[BookLevel(65000.0, 1.0), BookLevel(65010.0, 0.5)],
        )
        assert book.bids[0].price > book.bids[1].price   # 내림차순
        assert book.asks[0].price < book.asks[1].price   # 오름차순

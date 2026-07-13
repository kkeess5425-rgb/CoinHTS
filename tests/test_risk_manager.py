"""tests/test_risk_manager.py — Risk Manager 단위 테스트"""
import pytest
from risk.risk_manager import RiskManager, RiskParams, TradePosition


@pytest.fixture
def manager():
    return RiskManager(RiskParams(
        account_size=10_000.0,
        risk_per_trade=1.0,
        max_daily_loss=3.0,
        max_open_trades=3,
        min_rr=2.0,
    ))


class TestRiskManager:
    def test_valid_signal(self, manager):
        ok, reason = manager.validate_signal("LONG", 65000, 64500, 66000)
        assert ok is True
        assert reason == "OK"

    def test_rr_too_low(self, manager):
        # RR = (65500-65000)/(65000-64500) = 1.0 < 2.0
        ok, reason = manager.validate_signal("LONG", 65000, 64500, 65500)
        assert ok is False
        assert "RR" in reason

    def test_zero_risk(self, manager):
        ok, reason = manager.validate_signal("LONG", 65000, 65000, 66000)
        assert ok is False

    def test_position_size_basic(self, manager):
        # 1% 리스크 = 100 USDT, SL 500포인트 → size = 100/500 = 0.2
        size = manager.calc_position_size(65000, 64500)
        assert size == pytest.approx(0.2, rel=1e-4)

    def test_position_size_zero_on_no_risk(self, manager):
        size = manager.calc_position_size(65000, 65000)
        assert size == 0.0

    def test_daily_loss_limit(self, manager):
        # 3% 손실 = 300 USDT 강제 로스
        manager._daily_loss = 300.0
        ok, reason = manager.validate_signal("LONG", 65000, 64500, 66000)
        assert ok is False
        assert "일일" in reason

    def test_max_open_trades(self, manager):
        for i in range(3):
            pos = TradePosition(
                symbol="BTC-USDT-SWAP", direction="LONG",
                entry=65000, sl=64500, tp=66000, size=0.1,
                entry_ts=1700000000.0 + i,
            )
            manager.open_position(pos)
        ok, reason = manager.validate_signal("LONG", 65000, 64500, 66000)
        assert ok is False
        assert "최대" in reason

    def test_trailing_stop_long(self, manager):
        pos = TradePosition(
            symbol="BTC-USDT-SWAP", direction="LONG",
            entry=65000, sl=64000, tp=67000, size=0.1,
            entry_ts=1700000000.0, peak_price=65000,
        )
        manager.update_trailing_stop(pos, current_price=66000, atr_cur=100)
        assert pos.sl > 64000   # SL이 올라가야 함

    def test_trailing_stop_short(self, manager):
        pos = TradePosition(
            symbol="BTC-USDT-SWAP", direction="SHORT",
            entry=65000, sl=66000, tp=63000, size=0.1,
            entry_ts=1700000000.0, peak_price=65000,
        )
        manager.update_trailing_stop(pos, current_price=64000, atr_cur=100)
        assert pos.sl < 66000   # SL이 내려가야 함

    def test_close_long_profit(self, manager):
        pos = TradePosition(
            symbol="BTC-USDT-SWAP", direction="LONG",
            entry=65000, sl=64500, tp=66000, size=0.2,
            entry_ts=1700000000.0,
        )
        manager.open_position(pos)
        pnl = manager.close_position(pos, exit_price=66000)
        assert pnl == pytest.approx((66000 - 65000) * 0.2)
        assert len(manager.open_trades) == 0

    def test_close_short_loss(self, manager):
        pos = TradePosition(
            symbol="BTC-USDT-SWAP", direction="SHORT",
            entry=65000, sl=65500, tp=63000, size=0.2,
            entry_ts=1700000000.0,
        )
        manager.open_position(pos)
        pnl = manager.close_position(pos, exit_price=65500)   # SL 도달
        assert pnl < 0
        assert manager.daily_loss_pct > 0

    def test_get_stats(self, manager):
        stats = manager.get_stats()
        assert "open_trades"    in stats
        assert "daily_loss"     in stats
        assert "daily_loss_pct" in stats
        assert "account_size"   in stats

    def test_reset_daily(self, manager):
        manager._daily_loss = 150.0
        manager.reset_daily()
        assert manager.daily_loss_pct == 0.0

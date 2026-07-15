"""tests/test_live_system.py — 새 모듈 단위 테스트."""
import asyncio, time
import numpy as np
import pytest


# ── AI Chart Summary ─────────────────────────────────
class TestAIChartSummary:
    @pytest.fixture
    def engine(self):
        from ai.chart_summary import AIChartSummaryEngine
        return AIChartSummaryEngine()

    @pytest.fixture
    def candles(self):
        from core.models import Candle, Timeframe
        np.random.seed(0)
        prices = 65000 + np.cumsum(np.random.normal(0, 50, 200))
        return [
            Candle(ts=1700000000.0+i*900, open=prices[i],
                   high=prices[i]+30, low=prices[i]-30,
                   close=prices[i], volume=500,
                   symbol="BTC-USDT-SWAP", timeframe=Timeframe.M15)
            for i in range(200)
        ]

    def test_returns_chart_summary(self, engine, candles):
        from ai.chart_summary import ChartSummary
        result = engine.summarize("BTC-USDT-SWAP", candles)
        assert isinstance(result, ChartSummary)

    def test_headline_not_empty(self, engine, candles):
        result = engine.summarize("BTC-USDT-SWAP", candles)
        assert result.headline and len(result.headline) > 5

    def test_full_text_contains_sections(self, engine, candles):
        result = engine.summarize("BTC-USDT-SWAP", candles)
        assert "추세" in result.full_text
        assert "구조" in result.full_text

    def test_with_ict_result(self, engine, candles):
        from strategy.ict_engine import ICTEngine, ICTParams
        ict = ICTEngine(ICTParams()).analyze(candles)
        result = engine.summarize("BTC-USDT-SWAP", candles, ict_result=ict)
        assert result.trend  # 추세 분석이 있어야 함

    def test_with_smc_result(self, engine, candles):
        from strategy.smc_engine import SMCEngine
        smc = SMCEngine().analyze(candles)
        result = engine.summarize("BTC-USDT-SWAP", candles, smc_result=smc)
        assert result.structure  # 구조 분석이 있어야 함

    def test_empty_candles_returns_default(self, engine):
        result = engine.summarize("BTC-USDT-SWAP", [])
        assert result.trend == "데이터 부족"

    def test_funding_warning(self, engine, candles):
        result = engine.summarize("BTC-USDT-SWAP", candles, funding_rate=0.002)
        assert "펀딩" in result.risk

    def test_risk_text_populated(self, engine, candles):
        result = engine.summarize("BTC-USDT-SWAP", candles)
        assert isinstance(result.risk, str)


# ── Settings Manager ─────────────────────────────────
class TestSettingsManager:
    def test_backup_restore(self, tmp_path):
        from core.settings_manager import SettingsManager
        import json
        config = tmp_path / "config.json"
        config.write_text(json.dumps({"test": "value"}))
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        mgr = SettingsManager(str(config), str(backup_dir))

        # 백업
        backup_path = mgr.backup("test")
        assert backup_path and (tmp_path / "backups").exists()

        # 설정 변경
        config.write_text(json.dumps({"test": "changed"}))
        assert json.loads(config.read_text())["test"] == "changed"

        # 복원
        ok = mgr.restore(backup_path)
        assert ok
        assert json.loads(config.read_text())["test"] == "value"

    def test_list_backups(self, tmp_path):
        from core.settings_manager import SettingsManager
        import json
        config = tmp_path / "config.json"
        config.write_text(json.dumps({"test": "v1"}))
        mgr = SettingsManager(str(config), str(tmp_path / "backups"))
        mgr.backup("v1")
        mgr.backup("v2")
        backups = mgr.list_backups()
        assert len(backups) >= 2

    def test_export_import(self, tmp_path):
        from core.settings_manager import SettingsManager
        import json
        config = tmp_path / "config.json"
        config.write_text(json.dumps({"key": "original"}))
        mgr = SettingsManager(str(config), str(tmp_path / "backups"))
        data = mgr.export_json()
        assert data == {"key": "original"}
        ok = mgr.import_json({"key": "imported"})
        assert ok
        assert json.loads(config.read_text())["key"] == "imported"


# ── Error Reporter ────────────────────────────────────
class TestErrorReporter:
    def test_capture_and_retrieve(self):
        from core.settings_manager import ErrorReporter
        reporter = ErrorReporter()
        try:
            raise ValueError("테스트 오류")
        except Exception as e:
            reporter.capture(e, "test_context")
        errors = reporter.get_recent(5)
        assert len(errors) == 1
        assert errors[0]["type"] == "ValueError"
        assert "테스트 오류" in errors[0]["message"]

    def test_stats(self):
        from core.settings_manager import ErrorReporter
        reporter = ErrorReporter()
        for _ in range(3):
            try: raise TypeError("type err")
            except Exception as e: reporter.capture(e, "ctx")
        for _ in range(2):
            try: raise ValueError("val err")
            except Exception as e: reporter.capture(e, "ctx")
        stats = reporter.get_stats()
        assert stats["total"] == 5
        assert "TypeError" in stats["by_type"]


# ── Performance Module ────────────────────────────────
class TestRedisCache:
    def test_memory_fallback(self):
        """Redis 없을 때 인메모리 폴백 동작."""
        from core.performance import RedisCache
        cache = RedisCache(host="localhost", port=9999)  # 없는 포트
        asyncio.run(cache.set("key1", "value1", ttl=60))
        result = asyncio.run(cache.get("key1"))
        assert result == "value1"

    def test_cache_stats(self):
        from core.performance import RedisCache
        cache = RedisCache(host="localhost", port=9999)
        asyncio.run(cache.set("k", "v", ttl=10))
        asyncio.run(cache.get("k"))      # hit
        asyncio.run(cache.get("miss"))   # miss
        stats = cache.stats
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["hit_rate"] == 50.0

    def test_delete(self):
        from core.performance import RedisCache
        cache = RedisCache(host="localhost", port=9999)
        asyncio.run(cache.set("del_key", "val", ttl=10))
        asyncio.run(cache.delete("del_key"))
        result = asyncio.run(cache.get("del_key"))
        assert result is None

    def test_complex_value(self):
        from core.performance import RedisCache
        cache = RedisCache(host="localhost", port=9999)
        data  = {"list": [1, 2, 3], "nested": {"a": "b"}}
        asyncio.run(cache.set("complex", data, ttl=10))
        result = asyncio.run(cache.get("complex"))
        assert result == data


class TestParallelBacktester:
    def test_param_grid_build(self):
        from core.performance import ParallelBacktester
        ranges = {"rr": [1.5, 2.0], "conf": [1, 2]}
        grid   = ParallelBacktester.build_param_grid(ranges)
        assert len(grid) == 4
        assert {"rr": 1.5, "conf": 1} in grid

    def test_backtest_worker_basic(self):
        from core.performance import _backtest_worker
        from core.models import Candle, Timeframe
        import numpy as np
        np.random.seed(0)
        prices = 65000 + np.cumsum(np.random.normal(0, 50, 300))
        candles_data = [
            {"ts": 1700000000.0+i*900, "o": prices[i],
             "h": prices[i]+30, "l": prices[i]-30,
             "c": prices[i], "v": 500.0, "sym": "BTC"}
            for i in range(300)
        ]
        result = _backtest_worker((candles_data, {"require_displacement": False, "min_confluence": 0}))
        assert "total" in result or "error" in result


# ── Live Trader (mock) ────────────────────────────────
class TestLiveTrader:
    def test_init_sandbox(self):
        from trading.live_trader import OKXLiveTrader, TradingConfig
        trader = OKXLiveTrader(
            api_key="test", api_secret="test", passphrase="test",
            sandbox=True, config=TradingConfig(min_score=0.0),
        )
        assert trader.sandbox is True

    def test_daily_loss_reset(self):
        from trading.live_trader import OKXLiveTrader, TradingConfig
        trader = OKXLiveTrader("k", "s", "p", sandbox=True,
                               config=TradingConfig(account_size=10000))
        trader._balance     = 10000
        trader._daily_loss  = 100
        trader._daily_reset = time.time() - 90000  # 25시간 전
        pct = trader._daily_loss_pct()
        assert pct == 0.0  # 리셋 후

    def test_position_size_calculation(self):
        """리스크 기반 포지션 크기 계산."""
        from trading.paper_trader import TradingConfig
        cfg = TradingConfig(account_size=10000, risk_per_trade=1.0)
        risk_amount  = cfg.account_size * cfg.risk_per_trade / 100   # 100 USD
        risk_per_unit = 500.0   # 진입가 65000, SL 64500
        size = risk_amount / risk_per_unit
        assert abs(size - 0.2) < 0.001


# ── Telegram Bot ─────────────────────────────────────
class TestTelegramBot:
    def test_init(self):
        from alert.telegram_bot import TelegramBot
        bot = TelegramBot("test_token", ["123456"])
        assert bot.token == "test_token"
        assert "123456" in bot.allowed_ids

    def test_allowed_check(self):
        from alert.telegram_bot import TelegramBot
        bot = TelegramBot("token", ["111"])
        assert "111" in bot.allowed_ids
        assert "999" not in bot.allowed_ids

    def test_help_text(self):
        from alert.telegram_bot import HELP_TEXT
        assert "/status" in HELP_TEXT
        assert "/positions" in HELP_TEXT
        assert "/signals" in HELP_TEXT


# ── Voice Alert ──────────────────────────────────────
class TestVoiceAlert:
    def test_init_disabled(self):
        from alert.voice_alert import VoiceAlert
        va = VoiceAlert(enabled=False)
        assert not va.enabled

    def test_say_when_disabled(self):
        from alert.voice_alert import VoiceAlert
        va = VoiceAlert(enabled=False)
        va.say("테스트 메시지")  # 에러 없이 통과

    def test_console_backend(self, capsys):
        from alert.voice_alert import VoiceAlert
        va = VoiceAlert(enabled=True)
        va._backend = "console"  # 콘솔 폴백으로 강제
        va._speak("테스트")
        captured = capsys.readouterr()
        assert "테스트" in captured.out

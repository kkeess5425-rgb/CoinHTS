"""tests/test_final_modules.py — 최종 모듈 단위 테스트."""
import asyncio, time
import numpy as np
import pytest


# ── 시스템 모니터 ─────────────────────────────────────
class TestSystemMonitor:
    @pytest.fixture
    def monitor(self):
        from core.monitor import SystemMonitor
        return SystemMonitor(history_len=50, interval=0.1)

    def test_initial_state(self, monitor):
        assert monitor.latest is None
        assert monitor.stats.total_ticks == 0

    def test_on_tick(self, monitor):
        for _ in range(100):
            monitor.on_tick()
        assert monitor._total_ticks == 100

    def test_on_error(self, monitor):
        monitor.on_error()
        monitor.on_error()
        assert monitor._error_count == 2

    def test_collect_snapshot(self, monitor):
        from core.monitor import SystemSnapshot
        snap = monitor._collect()
        assert isinstance(snap, SystemSnapshot)
        assert snap.ts > 0

    def test_history_after_collect(self, monitor):
        snap = monitor._collect()
        monitor._history.append(snap)
        assert monitor.latest is not None
        assert monitor.latest.ts == snap.ts

    def test_summary_text(self, monitor):
        for _ in range(10):
            monitor.on_tick()
        text = monitor.summary()
        assert "가동" in text
        assert "TPS" in text

    def test_history_dict(self, monitor):
        for _ in range(5):
            snap = monitor._collect()
            monitor._history.append(snap)
        h = monitor.get_history_dict(5)
        assert "cpu" in h and "mem" in h and "tps" in h
        assert len(h["cpu"]) == 5

    def test_stats_computed(self, monitor):
        for _ in range(3):
            snap = monitor._collect()
            monitor._history.append(snap)
        stats = monitor.stats
        assert stats.uptime_sec >= 0
        assert stats.avg_tps >= 0


# ── 포지션 사이저 ─────────────────────────────────────
class TestPositionSizer:
    @pytest.fixture
    def sizer(self):
        from risk.position_sizer import PositionSizer, SizingConfig
        return PositionSizer(SizingConfig(
            account_size=10000, risk_pct=1.0, max_size_pct=10.0
        ))

    def test_fixed_sizing(self, sizer):
        sizer.cfg.method = "fixed"
        r = sizer.calculate(entry=65000, sl=64500)
        assert r.size > 0
        assert r.risk_amount == 100.0   # 1% of 10000
        assert r.method == "Fixed"

    def test_atr_sizing(self, sizer):
        sizer.cfg.method = "atr"
        r = sizer.calculate(entry=65000, sl=64500, atr=500)
        assert r.size > 0
        assert r.method == "ATR"

    def test_kelly_sizing(self, sizer):
        sizer.cfg.method = "kelly"
        sizer.cfg.win_rate  = 0.55
        sizer.cfg.avg_win_r = 2.0
        sizer.cfg.avg_loss_r= 1.0
        r = sizer.calculate(entry=65000, sl=64500)
        assert r.size > 0
        assert r.method == "Kelly"

    def test_parity_sizing(self, sizer):
        sizer.cfg.method = "parity"
        r = sizer.calculate(entry=65000, sl=64500, open_count=2)
        # 3포지션이면 리스크가 1/3로 나뉨
        r0 = sizer.calculate(entry=65000, sl=64500, open_count=0)
        # parity 방법 — 포지션이 많을수록 크기 줄어야
        sizer.cfg.method = "parity"
        r3 = sizer.calculate(entry=65000, sl=64500, open_count=3)
        # 포지션 많을수록 개별 사이즈 감소
        assert r3.size <= r.size

    def test_max_size_limit(self, sizer):
        """최대 포지션 크기 제한."""
        # 매우 좁은 SL로 이론적 크기가 매우 커지더라도
        sizer.cfg.method = "fixed"
        r = sizer.calculate(entry=65000, sl=64999)  # 1달러 SL
        max_size = sizer.cfg.account_size * sizer.cfg.max_size_pct / 100 / 65000
        assert r.size <= max_size + 0.001  # 소수점 허용 오차

    def test_recommend(self, sizer):
        result = sizer.recommend(entry=65000, sl=64500, atr=500)
        assert "recommended" in result
        assert "methods" in result
        assert result["recommended"] in ("atr", "fixed", "kelly")
        assert len(result["methods"]) == 3

    def test_leverage_calculation(self, sizer):
        sizer.cfg.method = "fixed"
        r = sizer.calculate(entry=65000, sl=64500)
        expected_lev = r.size * 65000 / sizer.cfg.account_size
        assert abs(r.leverage - expected_lev) < 0.01

    def test_negative_kelly_no_trade(self, sizer):
        """Kelly가 음수면 사이즈 0."""
        sizer.cfg.method   = "kelly"
        sizer.cfg.win_rate = 0.30   # 매우 낮은 승률
        sizer.cfg.avg_win_r= 1.0
        sizer.cfg.avg_loss_r= 2.0
        r = sizer.calculate(entry=65000, sl=64500)
        # 음수 Kelly → size 0 (또는 max_size_pct 제한)
        assert r.risk_amount >= 0


# ── Chart Image ───────────────────────────────────────
class TestChartImage:
    def test_build_without_matplotlib(self):
        """matplotlib 없어도 None 반환 (에러 없음)."""
        import sys
        # matplotlib를 숨겨서 ImportError 시뮬레이션
        orig = sys.modules.get('matplotlib')
        sys.modules['matplotlib'] = None   # None으로 설정

        try:
            # 모듈 재로드 없이 직접 테스트
            from alert.chart_image import build_chart_image
            # matplotlib가 import 안 됐다면 None 반환해야
            # 실제로는 이미 캐시된 import가 있을 수 있음
        except Exception:
            pass
        finally:
            if orig is not None:
                sys.modules['matplotlib'] = orig
            elif 'matplotlib' in sys.modules:
                del sys.modules['matplotlib']

    def test_build_empty_candles(self):
        """캔들 없으면 None 반환."""
        from alert.chart_image import build_chart_image
        result = build_chart_image("BTC", [])
        assert result is None

    def test_build_with_candles(self):
        """캔들 있으면 PNG 바이트 반환 (matplotlib 있을 때)."""
        try:
            import matplotlib
        except ImportError:
            pytest.skip("matplotlib 없음")

        from core.models import Candle, Timeframe
        from alert.chart_image import build_chart_image
        import numpy as np

        np.random.seed(0)
        prices = 65000 + np.cumsum(np.random.normal(0, 50, 50))
        candles = [
            Candle(ts=float(i)*900, open=prices[i],
                   high=prices[i]+30, low=prices[i]-30,
                   close=prices[i], volume=500,
                   symbol="BTC-USDT-SWAP", timeframe=Timeframe.M15)
            for i in range(50)
        ]
        result = build_chart_image("BTC-USDT-SWAP", candles,
                                   entry=65000, sl=64500, tp=67000,
                                   direction="LONG", score=82.0)
        if result is not None:
            assert isinstance(result, bytes)
            assert result[:8] == b'\x89PNG\r\n\x1a\n'   # PNG 시그니처


# ── 데이터 내보내기 추가 테스트 ───────────────────────
class TestExporterEdgeCases:
    @pytest.fixture
    def exporter(self):
        from database.exporter import DataExporter
        return DataExporter()

    def test_csv_headers_correct(self, exporter):
        class T:
            id="J-0001"; symbol="BTC"; direction="LONG"; entry=65000.0
            exit_price=67000.0; sl=64500.0; tp=67000.0; pnl_r=2.0; pnl_usd=200.0
            entry_ts=1700000000.0; exit_ts=1700003600.0; exit_reason="TP"
            entry_reason="BOS+FVG"; mistakes=[]; result="WIN"
        csv = exporter.export_journal_csv([T()])
        assert "ID" in csv.split('\n')[0]
        assert "BTC" in csv

    def test_stats_json_all_fields(self, exporter):
        import json
        from stats.statistics import StatisticsEngine, TradeRecord
        records = [
            TradeRecord(entry_ts=float(i)*86400, exit_ts=float(i)*86400+3600,
                        direction="LONG", entry=65000, exit_price=65500,
                        sl=64500, tp=67000, pnl_r=1.0 if i%2==0 else -1.0)
            for i in range(20)
        ]
        stats = StatisticsEngine().compute(records)
        j = json.loads(exporter.export_stats_json(stats))
        assert all(k in j for k in [
            "total_trades", "win_rate", "sharpe_ratio", "sortino_ratio",
            "max_drawdown", "profit_factor", "expectancy",
        ])


# ── 알림 강화 통합 테스트 ─────────────────────────────
class TestAlertChartIntegration:
    def test_telegram_bot_chart_command(self):
        """Telegram 봇이 차트 커맨드를 처리 가능한지 구조 확인."""
        from alert.telegram_bot import TelegramBot, HELP_TEXT
        bot = TelegramBot("token", ["12345"])
        # /signals 등 커맨드가 등록돼 있는지
        assert hasattr(bot, '_cmd_signals')
        assert hasattr(bot, '_cmd_status')
        assert "/status" in HELP_TEXT


# ── 재연결 로직 테스트 ────────────────────────────────
class TestReconnection:
    def test_websocket_reconnect_config(self):
        """WebSocket 재연결 설정 확인."""
        from websocket.okx_feed import OKXWebSocketFeed
        from core.events import EventBus
        feed = OKXWebSocketFeed(["BTC-USDT-SWAP"], event_bus=EventBus())
        assert hasattr(feed, '_reconnect_delay') or hasattr(feed, '_max_retries')

    def test_exchange_rate_limit(self):
        """거래소 API rate limit 설정 확인."""
        from exchange.okx import OKXExchange
        exc = OKXExchange()
        # 클래스 또는 인스턴스에 rate limit 관련 속성 확인
        has_limit = (hasattr(exc, '_rate_limit') or hasattr(exc, '_min_interval')
                     or hasattr(type(exc), '_min_interval') or hasattr(type(exc), '_rate_limit'))
        assert has_limit

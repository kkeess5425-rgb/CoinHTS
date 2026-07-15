"""
alert/telegram_bot.py
=====================
Telegram 인터랙티브 봇.

커맨드:
  /status    — 현재 앱 상태 (연결, 포지션, 잔고)
  /positions — 열린 포지션 목록
  /signals   — 최근 신호 목록
  /scanner   — 스캐너 신호
  /whale     — 고래 트래커 상태
  /news      — 경제 캘린더 (24시간 이내)
  /journal   — 매매일지 요약
  /help      — 도움말
  /stop      — 자동매매 일시 중지
  /start_trading — 자동매매 재개
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional, Callable, Any

import aiohttp

logger = logging.getLogger(__name__)

HELP_TEXT = r"""
🤖 *CoinHTS 봇 커맨드*

/status — 앱 상태 요약
/positions — 열린 포지션
/signals — 최근 전략 신호
/scanner — 스캐너 알림
/whale — 고래 트래커
/news — 경제 캘린더
/journal — 매매일지 요약
/stop — 자동매매 중지
/start\_trading — 자동매매 재개
/help — 이 도움말
"""


class TelegramBot:
    """
    Telegram Long Polling 봇.
    커맨드를 수신하고 앱 상태를 반환한다.
    """

    API = "https://api.telegram.org/bot{token}/{method}"

    def __init__(
        self,
        token:       str,
        allowed_ids: list[str],         # 허용된 chat_id 목록
        app_getter:  Optional[Callable[[], Any]] = None,   # CoinHTSApp 참조
    ) -> None:
        self.token       = token
        self.allowed_ids = [str(i) for i in allowed_ids]
        self.get_app     = app_getter
        self._session:   Optional[aiohttp.ClientSession] = None
        self._offset:    int = 0
        self._running:   bool = False

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        return self._session

    async def _api(self, method: str, data: dict = None) -> dict:
        url = self.API.format(token=self.token, method=method)
        session = await self._get_session()
        async with session.post(url, json=data or {}) as resp:
            return await resp.json()

    async def send_message(self, chat_id: str, text: str, parse_mode: str = "Markdown") -> None:
        await self._api("sendMessage", {
            "chat_id": chat_id, "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        })

    async def _get_updates(self) -> list[dict]:
        r = await self._api("getUpdates", {
            "offset":  self._offset,
            "timeout": 20,
            "limit":   10,
        })
        updates = r.get("result", [])
        if updates:
            self._offset = updates[-1]["update_id"] + 1
        return updates

    async def start(self) -> None:
        """Long Polling 시작."""
        self._running = True
        logger.info("[TelegramBot] 시작됨")
        while self._running:
            try:
                updates = await self._get_updates()
                for update in updates:
                    await self._handle_update(update)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[TelegramBot] 오류: {e}")
                await asyncio.sleep(5)

    async def stop(self) -> None:
        self._running = False
        if self._session and not self._session.closed:
            await self._session.close()

    async def _handle_update(self, update: dict) -> None:
        msg = update.get("message", {})
        if not msg:
            return
        chat_id = str(msg.get("chat", {}).get("id", ""))
        text    = msg.get("text", "").strip()

        if not text.startswith("/"):
            return
        if chat_id not in self.allowed_ids:
            logger.warning(f"[TelegramBot] 허용되지 않은 ID: {chat_id}")
            await self.send_message(chat_id, "❌ 허가되지 않은 사용자입니다.")
            return

        cmd = text.split()[0].lower().replace("@", "").split("@")[0]
        await self._dispatch(cmd, chat_id)

    async def _dispatch(self, cmd: str, chat_id: str) -> None:
        handlers = {
            "/help":          self._cmd_help,
            "/status":        self._cmd_status,
            "/positions":     self._cmd_positions,
            "/signals":       self._cmd_signals,
            "/scanner":       self._cmd_scanner,
            "/whale":         self._cmd_whale,
            "/news":          self._cmd_news,
            "/journal":       self._cmd_journal,
            "/stop":          self._cmd_stop_trading,
            "/start_trading": self._cmd_start_trading,
        }
        handler = handlers.get(cmd)
        if handler:
            await handler(chat_id)
        else:
            await self.send_message(chat_id, f"알 수 없는 커맨드: `{cmd}`\n/help 를 사용하세요.")

    # ── 커맨드 핸들러 ─────────────────────────────────
    async def _cmd_help(self, chat_id: str) -> None:
        await self.send_message(chat_id, HELP_TEXT)

    async def _cmd_status(self, chat_id: str) -> None:
        app = self.get_app() if self.get_app else None
        if not app:
            await self.send_message(chat_id, "⚠️ 앱 미연결")
            return
        status = app.get_status()
        pt     = status.get("paper_trader", {})
        lines  = [
            "📊 *CoinHTS 상태*",
            f"● 실행 중: {'✅' if status.get('running') else '❌'}",
            f"● 심볼: {', '.join(status.get('symbols', []))}",
            f"● 잔고: ${pt.get('balance', 0):,.2f}",
            f"● 열린 포지션: {pt.get('open_pos', 0)}개",
            f"● 청산 포지션: {pt.get('closed_pos', 0)}개",
            f"● 일일 손실: {pt.get('daily_loss_pct', 0):.2f}%",
            f"● 고래 심리: {status.get('whale_sentiment', {}).get('signal', '-')}",
            f"● 플러그인: {len(status.get('plugins', []))}개",
        ]
        await self.send_message(chat_id, "\n".join(lines))

    async def _cmd_positions(self, chat_id: str) -> None:
        app = self.get_app() if self.get_app else None
        if not app:
            await self.send_message(chat_id, "⚠️ 앱 미연결"); return
        positions = app.paper_trader.open_positions
        if not positions:
            await self.send_message(chat_id, "📭 열린 포지션 없음"); return
        lines = ["📊 *열린 포지션*"]
        for pos in positions:
            ts = time.strftime("%H:%M", time.localtime(pos.entry_ts))
            lines.append(
                f"● {pos.symbol.replace('-USDT-SWAP','')} "
                f"{'🟢 LONG' if pos.direction=='LONG' else '🔴 SHORT'} "
                f"진입={pos.entry:.0f} SL={pos.sl:.0f} TP={pos.tp:.0f} ({ts})"
            )
        await self.send_message(chat_id, "\n".join(lines))

    async def _cmd_signals(self, chat_id: str) -> None:
        app = self.get_app() if self.get_app else None
        if not app:
            await self.send_message(chat_id, "⚠️ 앱 미연결"); return
        # 최근 신호 (journal에서 가져옴)
        entries = app.journal.entries[-5:]
        if not entries:
            await self.send_message(chat_id, "📭 최근 신호 없음"); return
        lines = ["🚀 *최근 신호*"]
        for e in reversed(entries):
            ts = time.strftime("%m/%d %H:%M", time.localtime(e.entry_ts))
            r  = e.pnl_r
            lines.append(
                f"● {e.symbol.replace('-USDT-SWAP','')} {e.direction} "
                f"{'✅' if r>0 else '❌' if r<0 else '⏳'} {r:+.2f}R ({ts})"
            )
        await self.send_message(chat_id, "\n".join(lines))

    async def _cmd_scanner(self, chat_id: str) -> None:
        app = self.get_app() if self.get_app else None
        if not app:
            await self.send_message(chat_id, "⚠️ 앱 미연결"); return
        signals = list(app.advanced_scanner.signals)[-5:]
        if not signals:
            await self.send_message(chat_id, "📭 스캐너 신호 없음"); return
        lines = ["🔍 *스캐너 신호*"]
        for sig in reversed(signals):
            ts = time.strftime("%H:%M", time.localtime(sig.ts))
            lines.append(f"● [{sig.signal_type}] {sig.symbol}: {sig.message} ({ts})")
        await self.send_message(chat_id, "\n".join(lines))

    async def _cmd_whale(self, chat_id: str) -> None:
        app = self.get_app() if self.get_app else None
        if not app:
            await self.send_message(chat_id, "⚠️ 앱 미연결"); return
        sentiment = app.whale_tracker.get_market_sentiment()
        signal    = sentiment.get("signal", "-")
        netflow   = sentiment.get("exchange_netflow", 0)
        stable    = sentiment.get("stablecoin_net", 0)
        lines = [
            "🐋 *고래 트래커*",
            f"● 심리: {'🟢 불리시' if signal=='bullish' else '🔴 베어리시'}",
            f"● 거래소 순유입: {netflow:+,.0f} BTC",
            f"● 스테이블코인: {stable:+.0f}M USD",
        ]
        await self.send_message(chat_id, "\n".join(lines))

    async def _cmd_news(self, chat_id: str) -> None:
        app = self.get_app() if self.get_app else None
        if not app:
            await self.send_message(chat_id, "⚠️ 앱 미연결"); return
        events = app.news_aggregator.get_upcoming_events(24)
        if not events:
            await self.send_message(chat_id, "📭 24시간 내 주요 이벤트 없음"); return
        lines = ["📰 *경제 캘린더 (24h)*"]
        for e in events[:5]:
            ts = time.strftime("%m/%d %H:%M UTC", time.gmtime(e.ts))
            icon = "🔴" if e.impact=="high" else "🟡" if e.impact=="medium" else "⚪"
            lines.append(f"{icon} {e.title} ({e.currency}) — {ts}")
            if e.forecast:
                lines.append(f"   예측: {e.forecast} | 이전: {e.previous or '-'}")
        await self.send_message(chat_id, "\n".join(lines))

    async def _cmd_journal(self, chat_id: str) -> None:
        app = self.get_app() if self.get_app else None
        if not app:
            await self.send_message(chat_id, "⚠️ 앱 미연결"); return
        report = app.journal.generate_report(10)
        # Markdown 특수문자 이스케이프
        await self.send_message(chat_id, f"```\n{report}\n```", parse_mode="Markdown")

    async def _cmd_stop_trading(self, chat_id: str) -> None:
        app = self.get_app() if self.get_app else None
        if app:
            app.paper_trader.cfg.min_score = 999.0   # 사실상 비활성화
            await self.send_message(chat_id, "⏸ 자동매매 중지됨 (진입 최소 점수 → 999)")
        else:
            await self.send_message(chat_id, "⚠️ 앱 미연결")

    async def _cmd_start_trading(self, chat_id: str) -> None:
        app = self.get_app() if self.get_app else None
        if app:
            app.paper_trader.cfg.min_score = 70.0
            await self.send_message(chat_id, "▶️ 자동매매 재개됨 (최소 점수: 70)")
        else:
            await self.send_message(chat_id, "⚠️ 앱 미연결")

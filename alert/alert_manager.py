"""
alert/alert_manager.py
=======================
통합 알림 관리자.
전략 신호, 스캐너 신호, 리스크 이벤트를 수신해서
Telegram / Discord / Desktop 알림으로 전달한다.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import aiohttp

from core.models import ScannerSignal, StrategySignal
from core.events import EventBus, get_event_bus

logger = logging.getLogger(__name__)


class AlertManager:
    """
    통합 알림 관리자.
    중복 알림 방지, 우선순위 필터링 포함.
    """

    def __init__(
        self,
        telegram_token:   str = "",
        telegram_chat_id: str = "",
        discord_webhook:  str = "",
        event_bus:        Optional[EventBus] = None,
        min_score:        float = 60.0,   # 이 점수 이상 신호만 알림
    ) -> None:
        self._tg_token   = telegram_token
        self._tg_chat    = telegram_chat_id
        self._discord_wh = discord_webhook
        self._min_score  = min_score
        self._bus        = event_bus or get_event_bus()
        self._session:   Optional[aiohttp.ClientSession] = None

        # 중복 방지 (같은 신호 60초 내 재발송 금지)
        self._sent: dict[str, float] = {}
        self._cooldown = 60.0

        # 이벤트 구독
        self._bus.subscribe("strategy_signal", self._on_strategy_signal)
        self._bus.subscribe("scanner_signal",  self._on_scanner_signal)

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            )
        return self._session

    # ── 이벤트 핸들러 ────────────────────────────────
    async def _on_strategy_signal(self, sig: StrategySignal) -> None:
        if sig.score < self._min_score:
            return
        key = f"strategy:{sig.symbol}:{sig.direction}:{sig.entry:.0f}"
        if not self._check_cooldown(key):
            return

        icon  = "🟢" if sig.direction == "LONG" else "🔴"
        text  = (
            f"{icon} <b>{sig.symbol} {sig.direction}</b>\n"
            f"📊 AI 점수: {sig.score:.0f}/100점\n"
            f"💰 진입: <code>{sig.entry:.2f}</code>\n"
            f"🛑 SL: <code>{sig.sl:.2f}</code>\n"
            f"🎯 TP: <code>{sig.tp:.2f}</code> (RR 1:{sig.rr:.1f})\n"
            f"📋 {chr(10).join(sig.reasons[:3])}"
        )
        await self._send_telegram(text)
        await self._send_discord(f"{icon} **{sig.symbol} {sig.direction}** | 점수 {sig.score:.0f}점 | "
                                 f"진입 {sig.entry:.2f} | SL {sig.sl:.2f} | TP {sig.tp:.2f}")

    async def _on_scanner_signal(self, sig: ScannerSignal) -> None:
        key = f"scanner:{sig.symbol}:{sig.signal_type}"
        if not self._check_cooldown(key):
            return

        icons = {
            "VOLUME_SPIKE":     "📊",
            "OI_SURGE":         "📈",
            "FUNDING_EXTREME":  "⚠️",
            "DELTA_BURST":      "⚡",
            "BULL_ABSORPTION":  "🐂",
            "BEAR_ABSORPTION":  "🐻",
            "BULL_SWEEP":       "🎯",
            "BEAR_SWEEP":       "🎯",
        }
        icon = icons.get(sig.signal_type, "📡")
        text = f"{icon} <b>[스캐너] {sig.symbol}</b>\n{sig.message}"
        await self._send_telegram(text)

    # ── 전송 메서드 ───────────────────────────────────
    async def _send_telegram(self, text: str) -> bool:
        if not self._tg_token or not self._tg_chat:
            return False
        url = f"https://api.telegram.org/bot{self._tg_token}/sendMessage"
        try:
            session = await self._get_session()
            async with session.post(url, json={
                "chat_id":    self._tg_chat,
                "text":       text,
                "parse_mode": "HTML",
            }) as resp:
                ok = resp.status == 200
                if not ok:
                    logger.warning(f"Telegram 전송 실패: {resp.status}")
                return ok
        except Exception as e:
            logger.warning(f"Telegram 오류: {e}")
            return False

    async def _send_discord(self, content: str) -> bool:
        if not self._discord_wh:
            return False
        try:
            session = await self._get_session()
            async with session.post(self._discord_wh, json={"content": content}) as resp:
                return resp.status in (200, 204)
        except Exception as e:
            logger.warning(f"Discord 오류: {e}")
            return False

    async def send_custom(self, text: str) -> None:
        """커스텀 메시지 직접 발송."""
        await self._send_telegram(text)
        await self._send_discord(text)

    def _check_cooldown(self, key: str) -> bool:
        import time
        now = time.time()
        if now - self._sent.get(key, 0) < self._cooldown:
            return False
        self._sent[key] = now
        return True

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

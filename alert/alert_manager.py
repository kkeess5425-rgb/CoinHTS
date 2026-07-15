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
from alert.chart_image import build_chart_image, send_chart_to_telegram, ChartImageConfig
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

    async def send_signal_with_chart(
        self,
        signal,
        candles:    list = None,
        smc_result  = None,
    ) -> None:
        """신호 + 차트 이미지를 Telegram으로 전송."""
        if not self.telegram_token or not self.telegram_chat_id:
            return

        # 텍스트 알림 먼저
        await self.on_strategy_signal(signal)

        # 차트 이미지 생성 및 전송
        if candles and self._cfg.get("send_chart_image", False):
            try:
                image = build_chart_image(
                    symbol=    signal.symbol,
                    candles=   candles,
                    entry=     signal.entry,
                    sl=        signal.sl,
                    tp=        signal.tp,
                    tp2=       getattr(signal, "tp2", None),
                    direction= signal.direction,
                    score=     signal.score,
                    smc_result=smc_result,
                )
                if image:
                    caption = (
                        f"*{signal.symbol}* {signal.direction} 신호\n"
                        f"점수: {signal.score:.0f}/100\n"
                        f"진입: {signal.entry:.0f} | SL: {signal.sl:.0f} | TP: {signal.tp:.0f}"
                    )
                    await send_chart_to_telegram(
                        self.telegram_token, self.telegram_chat_id,
                        image, caption,
                    )
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(f"차트 이미지 전송 오류: {e}")

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

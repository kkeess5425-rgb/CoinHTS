"""
alert/voice_alert.py
====================
TTS 음성 알림.
신호 발생 시 "비트코인 15분봉 롱 신호, 점수 82점" 등을 음성으로 안내한다.

백엔드 우선순위:
  1. pyttsx3 (오프라인, 가장 빠름)
  2. gTTS + pygame (품질 좋음, 인터넷 필요)
  3. 콘솔 출력 (폴백)
"""
from __future__ import annotations

import asyncio
import logging
import os
import queue
import threading
from typing import Optional

from core.events import EventBus, get_event_bus
from core.models import StrategySignal, ScannerSignal

logger = logging.getLogger(__name__)


class VoiceAlert:
    """
    TTS 음성 알림 엔진.
    신호를 큐에 쌓고 백그라운드 스레드에서 순차 재생한다.
    """

    def __init__(
        self,
        enabled:   bool = True,
        language:  str  = "ko",     # "ko" | "en"
        min_score: float = 70.0,
        event_bus: Optional[EventBus] = None,
    ) -> None:
        self.enabled   = enabled
        self.language  = language
        self.min_score = min_score
        self._bus      = event_bus or get_event_bus()
        self._queue:   queue.Queue  = queue.Queue(maxsize=10)
        self._backend: Optional[str] = None
        self._engine   = None
        self._thread:  Optional[threading.Thread] = None

        if enabled:
            self._init_backend()
            self._start_thread()
            self._bus.subscribe("strategy_signal", self._on_signal)
            self._bus.subscribe("scanner_signal",  self._on_scanner)

    def _init_backend(self) -> None:
        """사용 가능한 TTS 백엔드 초기화."""
        # 1. pyttsx3
        try:
            import pyttsx3
            engine = pyttsx3.init()
            # 한국어 목소리 탐색
            voices = engine.getProperty('voices')
            for v in (voices or []):
                if 'ko' in (v.id or '').lower() or 'korean' in (v.name or '').lower():
                    engine.setProperty('voice', v.id)
                    break
            engine.setProperty('rate', 160)
            engine.setProperty('volume', 0.9)
            self._engine  = engine
            self._backend = "pyttsx3"
            logger.info("[Voice] pyttsx3 백엔드 초기화")
            return
        except Exception:
            pass

        # 2. gTTS
        try:
            from gtts import gTTS
            import pygame
            pygame.mixer.init()
            self._backend = "gtts"
            logger.info("[Voice] gTTS 백엔드 초기화")
            return
        except Exception:
            pass

        # 3. 폴백
        self._backend = "console"
        logger.info("[Voice] 음성 백엔드 없음 → 콘솔 출력")

    def _start_thread(self) -> None:
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def _worker(self) -> None:
        """백그라운드 TTS 재생 스레드."""
        while True:
            try:
                text = self._queue.get(timeout=1)
                if text is None:
                    break
                self._speak(text)
                self._queue.task_done()
            except queue.Empty:
                continue

    def _speak(self, text: str) -> None:
        """텍스트를 음성으로 출력."""
        try:
            if self._backend == "pyttsx3" and self._engine:
                self._engine.say(text)
                self._engine.runAndWait()

            elif self._backend == "gtts":
                import io
                from gtts import gTTS
                import pygame
                tts = gTTS(text=text, lang=self.language, slow=False)
                buf = io.BytesIO()
                tts.write_to_fp(buf)
                buf.seek(0)
                pygame.mixer.music.load(buf)
                pygame.mixer.music.play()
                while pygame.mixer.music.get_busy():
                    import time; time.sleep(0.1)

            else:
                # 콘솔 폴백
                print(f"🔊 [{self._backend}] {text}")

        except Exception as e:
            logger.debug(f"[Voice] TTS 오류: {e}")

    def say(self, text: str) -> None:
        """텍스트를 큐에 추가."""
        if not self.enabled:
            return
        try:
            self._queue.put_nowait(text)
        except queue.Full:
            logger.debug("[Voice] 음성 큐 가득 참")

    # ── 이벤트 핸들러 ─────────────────────────────────
    async def _on_signal(self, sig: StrategySignal) -> None:
        if sig.score < self.min_score:
            return
        sym   = sig.symbol.replace("-USDT-SWAP", "").replace("-USDT", "")
        dir_kr = "롱" if sig.direction == "LONG" else "숏"
        text   = f"{sym} {dir_kr} 신호, 점수 {sig.score:.0f}점"
        self.say(text)

    async def _on_scanner(self, sig: ScannerSignal) -> None:
        type_map = {
            "VOLUME_SPIKE":    "거래량 급증",
            "OI_SURGE":        "미결제약정 급증",
            "CVD_BULL_DIV":    "CVD 불리시 다이버전스",
            "CVD_BEAR_DIV":    "CVD 베어리시 다이버전스",
            "SMC_SWEEP":       "유동성 스윕",
            "FP_ABSORPTION":   "Absorption 신호",
        }
        label = type_map.get(sig.signal_type)
        if label:
            sym = sig.symbol.replace("-USDT-SWAP", "")
            self.say(f"{sym} {label}")

    def stop(self) -> None:
        self._queue.put(None)

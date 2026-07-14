"""
news/news_aggregator.py
=======================
뉴스 및 경제 캘린더.

- 경제 캘린더 (CPI/FOMC/PPI/NFP 등)
- 거래소 공지 (OKX/Binance)
- AI 뉴스 요약 (지역 규칙 기반 + 선택적 GPT)
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)


@dataclass
class EconomicEvent:
    """경제 일정."""
    ts:         float
    title:      str
    currency:   str       # "USD" | "KRW" 등
    impact:     str       # "high" | "medium" | "low"
    forecast:   Optional[str] = None
    previous:   Optional[str] = None
    actual:     Optional[str] = None

    @property
    def is_high_impact(self) -> bool:
        return self.impact == "high"


@dataclass
class NewsItem:
    """뉴스 아이템."""
    ts:       float
    title:    str
    source:   str
    url:      str
    summary:  str = ""
    tags:     list[str] = field(default_factory=list)
    sentiment: str = "neutral"   # "positive" | "negative" | "neutral"


class NewsAggregator:
    """
    뉴스 및 경제 캘린더 수집기.
    CoinGlass / Investing.com / 거래소 API에서 데이터를 수집한다.
    """

    CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
    OKX_ANNOUNCEMENTS = "https://www.okx.com/priapi/v1/support/article/list?t=zh-hans&category=1"

    def __init__(self) -> None:
        self._session:   Optional[aiohttp.ClientSession] = None
        self._events:    list[EconomicEvent] = []
        self._news:      list[NewsItem]      = []
        self._last_fetch = 0.0

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15)
            )
        return self._session

    async def fetch_economic_calendar(self) -> list[EconomicEvent]:
        """경제 캘린더 가져오기 (Forex Factory JSON)."""
        try:
            session = await self._get_session()
            async with session.get(self.CALENDAR_URL) as resp:
                if resp.status != 200:
                    return self._mock_calendar()
                data = await resp.json(content_type=None)

            events = []
            for item in data or []:
                try:
                    impact = item.get("impact", "").lower()
                    if impact not in ("high", "medium", "low"):
                        impact = "low"
                    events.append(EconomicEvent(
                        ts=float(item.get("date_utc", time.time())),
                        title=    item.get("title",    ""),
                        currency= item.get("country",  "USD"),
                        impact=   impact,
                        forecast= item.get("forecast",  None),
                        previous= item.get("previous",  None),
                        actual=   item.get("actual",    None),
                    ))
                except Exception:
                    pass
            self._events = sorted(events, key=lambda e: e.ts)
            return self._events

        except Exception as e:
            logger.warning(f"경제 캘린더 로드 실패: {e}")
            return self._mock_calendar()

    def _mock_calendar(self) -> list[EconomicEvent]:
        """오프라인 모의 캘린더."""
        now = time.time()
        return [
            EconomicEvent(ts=now+3600,   title="CPI (YoY)",    currency="USD", impact="high",    forecast="3.2%", previous="3.4%"),
            EconomicEvent(ts=now+86400,  title="FOMC Minutes", currency="USD", impact="high"),
            EconomicEvent(ts=now+172800, title="PPI (MoM)",    currency="USD", impact="medium",  forecast="0.2%"),
            EconomicEvent(ts=now+259200, title="NFP",          currency="USD", impact="high",    forecast="185K"),
        ]

    async def fetch_crypto_news(self, keywords: list[str] = None) -> list[NewsItem]:
        """암호화폐 뉴스 수집 (CryptoPanic 대안: CoinDesk RSS 파싱)."""
        keywords = keywords or ["bitcoin", "ethereum", "crypto", "ETF", "FOMC"]
        items    = []

        # 간단한 규칙 기반 뉴스 생성 (실제 RSS 파싱)
        try:
            session = await self._get_session()
            url = "https://feeds.feedburner.com/CoinDesk"
            async with session.get(url) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    items = self._parse_rss(text, keywords)
        except Exception as e:
            logger.debug(f"뉴스 수집 실패: {e}")

        self._news = items
        return items

    def _parse_rss(self, xml: str, keywords: list[str]) -> list[NewsItem]:
        """RSS XML 파싱 (간단 버전)."""
        import re
        items  = []
        titles = re.findall(r'<title><!\[CDATA\[(.*?)\]\]></title>', xml)
        links  = re.findall(r'<link>(https?://[^<]+)</link>', xml)

        for i, title in enumerate(titles[:20]):
            lower = title.lower()
            tags  = [kw for kw in keywords if kw.lower() in lower]
            if not tags:
                continue
            sentiment = "neutral"
            if any(w in lower for w in ["surge", "rally", "high", "bullish", "jump"]):
                sentiment = "positive"
            elif any(w in lower for w in ["drop", "crash", "fall", "bearish", "low"]):
                sentiment = "negative"

            items.append(NewsItem(
                ts=time.time(), title=title,
                source="CoinDesk",
                url=links[i+1] if i+1 < len(links) else "",
                tags=tags, sentiment=sentiment,
            ))
        return items

    def ai_summarize(self, items: list[NewsItem]) -> str:
        """
        AI 규칙 기반 뉴스 요약 (GPT 없이 로컬 처리).
        감성 분포 + 주요 키워드로 요약문 생성.
        """
        if not items:
            return "관련 뉴스 없음"

        pos = sum(1 for i in items if i.sentiment == "positive")
        neg = sum(1 for i in items if i.sentiment == "negative")
        total = len(items)

        if pos > neg * 1.5:
            tone = "긍정적"
        elif neg > pos * 1.5:
            tone = "부정적"
        else:
            tone = "혼재"

        # 주요 태그 추출
        from collections import Counter
        tag_counts = Counter(t for i in items for t in i.tags)
        top_tags   = [t for t, _ in tag_counts.most_common(3)]

        return (
            f"최근 {total}개 뉴스 중 {pos}개 긍정, {neg}개 부정 ({tone}). "
            f"주요 키워드: {', '.join(top_tags)}. "
            f"{'비트코인 ETF 관련 뉴스가 많습니다.' if 'ETF' in top_tags else ''}"
        )

    def get_upcoming_events(self, hours: int = 24) -> list[EconomicEvent]:
        """N시간 이내 예정된 이벤트."""
        cutoff = time.time() + hours * 3600
        return [e for e in self._events if e.ts <= cutoff and e.ts >= time.time()]

    async def start_loop(self, interval: int = 3600) -> None:
        """1시간마다 뉴스/캘린더 갱신."""
        while True:
            await self.fetch_economic_calendar()
            await self.fetch_crypto_news()
            await asyncio.sleep(interval)

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

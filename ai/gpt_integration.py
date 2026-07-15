"""
ai/gpt_integration.py
=====================
GPT API 선택적 연동.
API 키가 없으면 기존 규칙 기반 분석으로 폴백한다.

사용 방법:
  export OPENAI_API_KEY="sk-..."
  → GPT 분석 활성화

  API 키 없으면 → ai/chart_summary.py 규칙 기반 요약 사용
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class GPTAnalysisResult:
    """GPT 분석 결과."""
    narrative:    str        # 자연어 시장 설명
    entry_eval:   str        # 진입 평가
    risk_summary: str        # 리스크 요약
    model:        str        # 사용된 모델
    tokens_used:  int = 0


class GPTIntegration:
    """
    OpenAI GPT 선택적 연동.
    API 키가 있으면 GPT, 없으면 로컬 요약 사용.
    """

    MODEL = "gpt-4o-mini"    # 빠르고 저렴한 모델 기본 사용

    def __init__(self, api_key: Optional[str] = None) -> None:
        self._api_key  = api_key or os.environ.get("OPENAI_API_KEY", "")
        self._client   = None
        self._available = False

        if self._api_key:
            try:
                import openai
                self._client    = openai.AsyncOpenAI(api_key=self._api_key)
                self._available = True
                logger.info(f"[GPT] OpenAI 연결됨 ({self.MODEL})")
            except ImportError:
                logger.info("[GPT] openai 패키지 없음 → pip install openai")
        else:
            logger.info("[GPT] API 키 없음 → 로컬 분석 사용")

    @property
    def available(self) -> bool:
        return self._available

    async def analyze_market(
        self,
        symbol:       str,
        summary_text: str,     # chart_summary.py의 full_text
        direction:    str = "LONG",
        score:        float = 0.0,
    ) -> GPTAnalysisResult:
        """
        GPT로 시장 분석 강화.
        summary_text를 컨텍스트로 GPT에 전달해서 심화 분석을 요청한다.
        """
        if not self._available:
            return self._fallback_analysis(symbol, summary_text, direction, score)

        prompt = f"""당신은 전문 암호화폐 트레이더입니다.
다음 {symbol} 시장 분석 데이터를 바탕으로 트레이더에게 도움이 되는 분석을 제공하세요.

=== 시장 분석 데이터 ===
{summary_text}

=== 현재 신호 ===
방향: {direction}
AI 점수: {score:.0f}/100

다음 3가지를 한국어로 간결하게 답변하세요:

1. 📊 시장 내러티브 (2~3문장): 현재 시장 상황을 스마트 머니 관점에서 설명
2. 🎯 진입 평가 (1~2문장): 이 시점에 {direction} 진입이 적절한지 평가
3. ⚠️ 주요 리스크 (1~2문장): 이 진입에서 주의해야 할 리스크"""

        try:
            response = await self._client.chat.completions.create(
                model=self.MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=400,
                temperature=0.3,
            )
            text   = response.choices[0].message.content.strip()
            tokens = response.usage.total_tokens if response.usage else 0

            # 파싱
            narrative = entry_eval = risk_summary = ""
            sections = text.split("\n\n")
            for i, s in enumerate(sections):
                if "📊" in s or "시장" in s:
                    narrative    = s.replace("1.", "").replace("📊 시장 내러티브:", "").strip()
                elif "🎯" in s or "진입" in s:
                    entry_eval   = s.replace("2.", "").replace("🎯 진입 평가:", "").strip()
                elif "⚠️" in s or "리스크" in s:
                    risk_summary = s.replace("3.", "").replace("⚠️ 주요 리스크:", "").strip()

            if not narrative:
                narrative = text[:200]

            return GPTAnalysisResult(
                narrative=    narrative    or text,
                entry_eval=   entry_eval   or f"{direction} 방향 진입 검토 중",
                risk_summary= risk_summary or "표준 리스크 관리 준수 권장",
                model=        self.MODEL,
                tokens_used=  tokens,
            )

        except Exception as e:
            logger.error(f"[GPT] 분석 오류: {e}")
            return self._fallback_analysis(symbol, summary_text, direction, score)

    async def summarize_news(self, news_items: list[dict]) -> str:
        """뉴스 AI 요약 (GPT 버전)."""
        if not self._available or not news_items:
            return "GPT 미연결 또는 뉴스 없음"

        headlines = "\n".join(f"- {n.get('title', '')}" for n in news_items[:10])
        prompt    = f"다음 암호화폐 뉴스 헤드라인을 한국어로 2~3문장으로 요약하세요:\n{headlines}"

        try:
            response = await self._client.chat.completions.create(
                model=self.MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=150, temperature=0.3,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"[GPT] 뉴스 요약 오류: {e}")
            return "GPT 요약 실패"

    def _fallback_analysis(
        self, symbol: str, summary: str,
        direction: str, score: float,
    ) -> GPTAnalysisResult:
        """GPT 없을 때 규칙 기반 폴백."""
        qual = "강한" if score >= 80 else "보통" if score >= 60 else "약한"
        return GPTAnalysisResult(
            narrative=    f"{symbol}: {summary[:100]}..." if len(summary) > 100 else summary,
            entry_eval=   f"{direction} 방향으로 {qual} 진입 근거 (점수 {score:.0f}/100)",
            risk_summary= "표준 리스크 관리 원칙 적용 권장",
            model=        "local_fallback",
        )

"""
ai/trade_journal.py
===================
AI 매매일지 자동 생성 + 실수 감지.

- 매매일지: 진입/청산 이유 + 결과 자동 기록
- 실수 감지: 추격매수, 과도한 레버리지, 뉴스 직전 진입 경고
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class MistakeWarning:
    """실수 감지 경고."""
    kind:     str    # "chasing" | "overleverage" | "news_entry" | "revenge" | "fomo"
    message:  str
    severity: str    # "high" | "medium" | "low"


@dataclass
class JournalEntry:
    """단일 매매일지 항목."""
    id:           str
    symbol:       str
    direction:    str
    entry:        float
    exit_price:   float
    sl:           float
    tp:           float
    pnl_r:        float
    pnl_usd:      float
    entry_ts:     float
    exit_ts:      float
    entry_reason: str    # AI 생성 진입 이유
    exit_reason:  str    # 청산 이유 (TP/SL/Manual)
    mistakes:     list[MistakeWarning] = field(default_factory=list)
    notes:        str    = ""
    score_at_entry: float = 0.0
    tags:         list[str] = field(default_factory=list)

    @property
    def hold_minutes(self) -> float:
        return (self.exit_ts - self.entry_ts) / 60

    @property
    def result(self) -> str:
        if self.pnl_r > 0: return "WIN"
        if self.pnl_r < 0: return "LOSS"
        return "BE"


class TradeJournalAI:
    """
    AI 기반 매매일지 자동 생성 및 실수 감지.
    """

    def __init__(self) -> None:
        self._entries:    list[JournalEntry] = []
        self._last_trades: list[dict]        = []  # 복수 트레이드 패턴 감지용
        self._entry_id    = 0

    def create_entry(
        self,
        symbol:       str,
        direction:    str,
        entry:        float,
        exit_price:   float,
        sl:           float,
        tp:           float,
        pnl_r:        float,
        pnl_usd:      float,
        entry_ts:     float,
        exit_ts:      float,
        exit_reason:  str,
        score:        float     = 0.0,
        smc_info:     Optional[dict] = None,
        ict_info:     Optional[dict] = None,
    ) -> JournalEntry:
        """매매일지 항목 생성."""
        self._entry_id += 1
        entry_reason = self._generate_entry_reason(direction, smc_info, ict_info, score)
        mistakes     = self._detect_mistakes(
            entry, entry_ts, score, pnl_r, sl, tp, direction
        )

        je = JournalEntry(
            id=           f"J-{self._entry_id:04d}",
            symbol=       symbol,
            direction=    direction,
            entry=        entry,
            exit_price=   exit_price,
            sl=           sl,
            tp=           tp,
            pnl_r=        pnl_r,
            pnl_usd=      pnl_usd,
            entry_ts=     entry_ts,
            exit_ts=      exit_ts,
            entry_reason= entry_reason,
            exit_reason=  exit_reason,
            mistakes=     mistakes,
            score_at_entry=score,
        )

        self._entries.append(je)
        self._last_trades.append({"pnl_r": pnl_r, "ts": exit_ts, "dir": direction})
        if len(self._last_trades) > 20:
            self._last_trades = self._last_trades[-20:]

        if mistakes:
            for m in mistakes:
                logger.warning(f"[Journal] 실수 감지 ({m.kind}): {m.message}")

        return je

    def _generate_entry_reason(
        self,
        direction:str,
        smc:      Optional[dict],
        ict:      Optional[dict],
        score:    float,
    ) -> str:
        """진입 이유 자동 생성."""
        parts = []
        dir_kr = "롱" if direction == "LONG" else "숏"

        if ict:
            if ict.get("bull_ms") == (direction == "LONG"):
                parts.append("시장 구조 일치")
            if ict.get("displacement"):
                parts.append("Displacement 확인")
            if ict.get("bull_sweep_active") or ict.get("bear_sweep_active"):
                parts.append("유동성 스윕 완료")

        if smc:
            if smc.get("fvg_zones"):
                parts.append("FVG 컨플루언스")
            if smc.get("order_blocks"):
                parts.append("Order Block 반응")
            if smc.get("liquidity_sweeps"):
                parts.append("Liquidity Sweep 반전")

        reason = ", ".join(parts) if parts else "기술적 조건 충족"
        return f"{dir_kr} 진입: {reason} (AI 점수 {score:.0f}/100)"

    def _detect_mistakes(
        self,
        entry:     float,
        entry_ts:  float,
        score:     float,
        pnl_r:     float,
        sl:        float,
        tp:        float,
        direction: str,
    ) -> list[MistakeWarning]:
        """실수 패턴 감지."""
        warnings = []

        # 1. 추격매수 감지 (낮은 점수로 진입)
        if score < 50:
            warnings.append(MistakeWarning(
                kind="chasing",
                message=f"낮은 AI 점수({score:.0f}/100)로 진입 — 추격 매수 가능성",
                severity="high",
            ))

        # 2. 과도한 RR 비율 (너무 작은 RR)
        risk  = abs(entry - sl)
        reward = abs(entry - tp)
        rr = reward / max(risk, 1e-9)
        if rr < 1.5:
            warnings.append(MistakeWarning(
                kind="bad_rr",
                message=f"RR 비율 부족 ({rr:.1f}:1) — 최소 1.5:1 권장",
                severity="medium",
            ))

        # 3. 연속 손절 후 복수매매
        recent_losses = sum(1 for t in self._last_trades[-5:] if t["pnl_r"] < 0)
        if recent_losses >= 3:
            warnings.append(MistakeWarning(
                kind="revenge",
                message=f"최근 {recent_losses}연속 손절 — 복수매매 위험",
                severity="high",
            ))

        # 4. FOMO 감지 (하루에 너무 많은 트레이드)
        today = entry_ts - 86400
        today_trades = sum(1 for t in self._last_trades if t["ts"] > today)
        if today_trades > 10:
            warnings.append(MistakeWarning(
                kind="fomo",
                message=f"오늘 {today_trades}번 트레이드 — 과매매 주의",
                severity="medium",
            ))

        # 5. SL이 너무 넓음 (ATR 대비)
        if risk > abs(entry) * 0.03:
            warnings.append(MistakeWarning(
                kind="wide_sl",
                message=f"SL이 진입가 대비 {risk/entry*100:.1f}% — 과도한 리스크",
                severity="medium",
            ))

        return warnings

    def generate_report(self, n_recent: int = 20) -> str:
        """최근 N건 매매일지 텍스트 리포트."""
        entries = self._entries[-n_recent:]
        if not entries:
            return "매매 내역 없음"

        lines = [f"📋 매매일지 리포트 ({len(entries)}건)\n" + "="*40]
        wins  = sum(1 for e in entries if e.pnl_r > 0)
        total_r = sum(e.pnl_r for e in entries)

        lines.append(f"승률: {wins}/{len(entries)} ({wins/len(entries)*100:.0f}%)")
        lines.append(f"누적R: {total_r:+.2f}R\n")

        for e in entries[-5:]:
            emoji = "✅" if e.pnl_r > 0 else "❌" if e.pnl_r < 0 else "⚖️"
            ts    = time.strftime("%m/%d %H:%M", time.localtime(e.entry_ts))
            lines.append(
                f"{emoji} {e.id} {e.symbol} {e.direction} | {ts}\n"
                f"   진입: {e.entry:.2f} → 청산: {e.exit_price:.2f} ({e.exit_reason})\n"
                f"   PnL: {e.pnl_r:+.2f}R ({e.pnl_usd:+.1f} USD) | {e.hold_minutes:.0f}분\n"
                f"   이유: {e.entry_reason}"
            )
            if e.mistakes:
                for m in e.mistakes:
                    lines.append(f"   ⚠️ [{m.kind}] {m.message}")

        return "\n".join(lines)

    def get_mistake_stats(self) -> dict:
        """실수 유형별 통계."""
        stats: dict[str, int] = {}
        for e in self._entries:
            for m in e.mistakes:
                stats[m.kind] = stats.get(m.kind, 0) + 1
        return dict(sorted(stats.items(), key=lambda x: x[1], reverse=True))

    @property
    def entries(self) -> list[JournalEntry]:
        return self._entries

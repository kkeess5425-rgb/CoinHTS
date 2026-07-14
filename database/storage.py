"""
database/storage.py
===================
고성능 틱/캔들/신호 저장소.
- SQLite (로컬, 빠른 읽기/쓰기)
- aiosqlite 비동기 I/O
- 배치 삽입으로 I/O 오버헤드 최소화
- Polars DataFrame으로 대용량 조회 최적화
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from pathlib import Path
from typing import Optional

import aiosqlite
import polars as pl

from core.models import Candle, ScannerSignal, StrategySignal, Tick, Timeframe

logger = logging.getLogger(__name__)

# 배치 삽입 간격 (초)
FLUSH_INTERVAL = 2.0
# 배치 크기
BATCH_SIZE     = 500


class DataStorage:
    """
    SQLite 기반 시장 데이터 저장소.
    틱/캔들/신호를 비동기 배치 삽입으로 저장하고
    Polars로 빠르게 조회한다.
    """

    def __init__(self, db_path: str = "data/coinhts.db") -> None:
        self._db_path = db_path
        self._conn:    Optional[aiosqlite.Connection] = None
        self._tick_buffer:   deque[tuple] = deque()
        self._candle_buffer: deque[tuple] = deque()
        self._signal_buffer: deque[tuple] = deque()
        self._flush_task:    Optional[asyncio.Task] = None

    async def initialize(self) -> None:
        """DB 초기화 및 테이블 생성."""
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._db_path)

        # WAL 모드 (동시 읽기/쓰기 성능 향상)
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA synchronous=NORMAL")
        await self._conn.execute("PRAGMA cache_size=10000")
        await self._conn.execute("PRAGMA temp_store=MEMORY")

        await self._create_tables()
        self._flush_task = asyncio.create_task(self._auto_flush())
        logger.info(f"DataStorage 초기화: {self._db_path}")

    async def _create_tables(self) -> None:
        """스키마 생성."""
        await self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS ticks (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol  TEXT    NOT NULL,
                ts      REAL    NOT NULL,
                price   REAL    NOT NULL,
                size    REAL    NOT NULL,
                side    TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ticks_symbol_ts ON ticks(symbol, ts);

            CREATE TABLE IF NOT EXISTS candles (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol    TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                ts        REAL NOT NULL,
                open      REAL NOT NULL,
                high      REAL NOT NULL,
                low       REAL NOT NULL,
                close     REAL NOT NULL,
                volume    REAL NOT NULL,
                UNIQUE(symbol, timeframe, ts)
            );
            CREATE INDEX IF NOT EXISTS idx_candles_symbol_tf_ts ON candles(symbol, timeframe, ts);

            CREATE TABLE IF NOT EXISTS strategy_signals (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol    TEXT NOT NULL,
                ts        REAL NOT NULL,
                direction TEXT NOT NULL,
                score     REAL NOT NULL,
                entry     REAL NOT NULL,
                sl        REAL NOT NULL,
                tp        REAL NOT NULL,
                rr        REAL NOT NULL,
                reasons   TEXT
            );

            CREATE TABLE IF NOT EXISTS scanner_signals (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol      TEXT NOT NULL,
                ts          REAL NOT NULL,
                signal_type TEXT NOT NULL,
                value       REAL NOT NULL,
                message     TEXT
            );
        """)
        await self._conn.commit()

    # ── 데이터 추가 (버퍼) ───────────────────────────
    def add_tick(self, tick: Tick) -> None:
        """틱 버퍼에 추가 (즉시 DB 쓰지 않음)."""
        self._tick_buffer.append((tick.symbol, tick.ts, tick.price, tick.size, tick.side.value))

    def add_candle(self, candle: Candle) -> None:
        """캔들 버퍼에 추가."""
        self._candle_buffer.append((
            candle.symbol, candle.timeframe.value, candle.ts,
            candle.open, candle.high, candle.low, candle.close, candle.volume,
        ))

    def add_strategy_signal(self, sig: StrategySignal) -> None:
        """전략 신호 버퍼에 추가."""
        self._signal_buffer.append((
            sig.symbol, sig.ts, sig.direction, sig.score,
            sig.entry, sig.sl, sig.tp, sig.rr,
            "\n".join(sig.reasons),
        ))

    # ── 자동 플러시 ──────────────────────────────────
    async def _auto_flush(self) -> None:
        """FLUSH_INTERVAL마다 버퍼 일괄 DB 삽입."""
        while True:
            await asyncio.sleep(FLUSH_INTERVAL)
            await self.flush()

    async def flush(self) -> None:
        """버퍼 → DB 일괄 삽입."""
        if self._conn is None:
            return
        try:
            # 틱
            if self._tick_buffer:
                batch = [self._tick_buffer.popleft() for _ in range(min(BATCH_SIZE, len(self._tick_buffer)))]
                await self._conn.executemany(
                    "INSERT INTO ticks(symbol,ts,price,size,side) VALUES(?,?,?,?,?)", batch
                )
            # 캔들
            if self._candle_buffer:
                batch = [self._candle_buffer.popleft() for _ in range(min(BATCH_SIZE, len(self._candle_buffer)))]
                await self._conn.executemany(
                    "INSERT OR REPLACE INTO candles(symbol,timeframe,ts,open,high,low,close,volume) VALUES(?,?,?,?,?,?,?,?)", batch
                )
            # 신호
            if self._signal_buffer:
                batch = [self._signal_buffer.popleft() for _ in range(min(100, len(self._signal_buffer)))]
                await self._conn.executemany(
                    "INSERT INTO strategy_signals(symbol,ts,direction,score,entry,sl,tp,rr,reasons) VALUES(?,?,?,?,?,?,?,?,?)", batch
                )
            await self._conn.commit()
        except Exception as e:
            logger.error(f"DB flush 오류: {e}")

    # ── 조회 (Polars 기반) ────────────────────────────
    async def get_candles(
        self, symbol: str, timeframe: Timeframe,
        limit: int = 1000, since: Optional[float] = None
    ) -> pl.DataFrame:
        """캔들 조회 → Polars DataFrame."""
        if self._conn is None:
            return pl.DataFrame()

        query = "SELECT ts,open,high,low,close,volume FROM candles WHERE symbol=? AND timeframe=?"
        params: list = [symbol, timeframe.value]
        if since:
            query += " AND ts >= ?"
            params.append(since)
        query += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)

        async with self._conn.execute(query, params) as cursor:
            rows = await cursor.fetchall()

        if not rows:
            return pl.DataFrame()

        df = pl.DataFrame({
            "ts":     [r[0] for r in rows],
            "open":   [r[1] for r in rows],
            "high":   [r[2] for r in rows],
            "low":    [r[3] for r in rows],
            "close":  [r[4] for r in rows],
            "volume": [r[5] for r in rows],
        })
        return df.sort("ts")

    async def get_ticks(
        self, symbol: str,
        since: float, until: Optional[float] = None,
        limit: int = 100_000,
    ) -> pl.DataFrame:
        """틱 조회 → Polars DataFrame (Replay용)."""
        if self._conn is None:
            return pl.DataFrame()

        query = "SELECT ts,price,size,side FROM ticks WHERE symbol=? AND ts>=?"
        params: list = [symbol, since]
        if until:
            query += " AND ts<=?"
            params.append(until)
        query += " ORDER BY ts LIMIT ?"
        params.append(limit)

        async with self._conn.execute(query, params) as cursor:
            rows = await cursor.fetchall()

        if not rows:
            return pl.DataFrame()

        return pl.DataFrame({
            "ts":    [r[0] for r in rows],
            "price": [r[1] for r in rows],
            "size":  [r[2] for r in rows],
            "side":  [r[3] for r in rows],
        })

    async def get_statistics(self, symbol: str) -> dict:
        """저장된 데이터 통계."""
        if self._conn is None:
            return {}
        async with self._conn.execute(
            "SELECT COUNT(*), MIN(ts), MAX(ts) FROM ticks WHERE symbol=?", [symbol]
        ) as cursor:
            row = await cursor.fetchone()
        return {
            "tick_count": row[0],
            "oldest_ts":  row[1],
            "newest_ts":  row[2],
        }

    async def close(self) -> None:
        if self._flush_task:
            self._flush_task.cancel()
        await self.flush()
        if self._conn:
            await self._conn.close()


# ── PostgreSQL 지원 추가 ──────────────────────────────
class PostgreSQLStorage:
    """
    PostgreSQL 기반 시장 데이터 저장소.
    asyncpg 사용. TimescaleDB 호환.
    pip install asyncpg 필요.

    사용:
        pg = PostgreSQLStorage("postgresql://user:pass@host:5432/coinhts")
        await pg.initialize()
    """

    def __init__(self, dsn: str) -> None:
        self._dsn   = dsn
        self._pool  = None
        self._tick_buffer:   deque = deque()
        self._candle_buffer: deque = deque()
        self._flush_task = None

    async def initialize(self) -> None:
        try:
            import asyncpg
            self._pool = await asyncpg.create_pool(
                self._dsn, min_size=2, max_size=10,
            )
            await self._create_tables()
            self._flush_task = asyncio.create_task(self._auto_flush())
            logger.info("PostgreSQL 스토리지 초기화 완료")
        except ImportError:
            raise RuntimeError("asyncpg 미설치. pip install asyncpg")

    async def _create_tables(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS ticks (
                    id     BIGSERIAL PRIMARY KEY,
                    symbol TEXT   NOT NULL,
                    ts     DOUBLE PRECISION NOT NULL,
                    price  DOUBLE PRECISION NOT NULL,
                    size   DOUBLE PRECISION NOT NULL,
                    side   TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_pg_ticks ON ticks(symbol, ts DESC);

                CREATE TABLE IF NOT EXISTS candles (
                    id        BIGSERIAL PRIMARY KEY,
                    symbol    TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    ts        DOUBLE PRECISION NOT NULL,
                    open      DOUBLE PRECISION NOT NULL,
                    high      DOUBLE PRECISION NOT NULL,
                    low       DOUBLE PRECISION NOT NULL,
                    close     DOUBLE PRECISION NOT NULL,
                    volume    DOUBLE PRECISION NOT NULL,
                    UNIQUE(symbol, timeframe, ts)
                );
                CREATE INDEX IF NOT EXISTS idx_pg_candles ON candles(symbol, timeframe, ts DESC);

                CREATE TABLE IF NOT EXISTS strategy_signals (
                    id        BIGSERIAL PRIMARY KEY,
                    symbol    TEXT NOT NULL,
                    ts        DOUBLE PRECISION NOT NULL,
                    direction TEXT NOT NULL,
                    score     DOUBLE PRECISION NOT NULL,
                    entry     DOUBLE PRECISION NOT NULL,
                    sl        DOUBLE PRECISION NOT NULL,
                    tp        DOUBLE PRECISION NOT NULL,
                    rr        DOUBLE PRECISION NOT NULL,
                    reasons   TEXT
                );
            """)

    def add_tick(self, tick) -> None:
        self._tick_buffer.append(
            (tick.symbol, tick.ts, tick.price, tick.size, tick.side.value)
        )

    def add_candle(self, candle) -> None:
        self._candle_buffer.append((
            candle.symbol, candle.timeframe.value, candle.ts,
            candle.open, candle.high, candle.low, candle.close, candle.volume,
        ))

    async def _auto_flush(self) -> None:
        while True:
            await asyncio.sleep(FLUSH_INTERVAL)
            await self.flush()

    async def flush(self) -> None:
        if not self._pool:
            return
        try:
            async with self._pool.acquire() as conn:
                if self._tick_buffer:
                    batch = [self._tick_buffer.popleft()
                             for _ in range(min(BATCH_SIZE, len(self._tick_buffer)))]
                    await conn.executemany(
                        "INSERT INTO ticks(symbol,ts,price,size,side) VALUES($1,$2,$3,$4,$5)",
                        batch,
                    )
                if self._candle_buffer:
                    batch = [self._candle_buffer.popleft()
                             for _ in range(min(BATCH_SIZE, len(self._candle_buffer)))]
                    await conn.executemany(
                        """INSERT INTO candles(symbol,timeframe,ts,open,high,low,close,volume)
                           VALUES($1,$2,$3,$4,$5,$6,$7,$8)
                           ON CONFLICT(symbol,timeframe,ts) DO NOTHING""",
                        batch,
                    )
        except Exception as e:
            logger.error(f"PostgreSQL flush 오류: {e}")

    async def get_candles(self, symbol: str, timeframe, limit: int = 1000) -> pl.DataFrame:
        if not self._pool:
            return pl.DataFrame()
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT ts,open,high,low,close,volume FROM candles
                   WHERE symbol=$1 AND timeframe=$2
                   ORDER BY ts DESC LIMIT $3""",
                symbol, timeframe.value, limit,
            )
        if not rows:
            return pl.DataFrame()
        return pl.DataFrame({
            "ts":     [r["ts"]     for r in rows],
            "open":   [r["open"]   for r in rows],
            "high":   [r["high"]   for r in rows],
            "low":    [r["low"]    for r in rows],
            "close":  [r["close"]  for r in rows],
            "volume": [r["volume"] for r in rows],
        }).sort("ts")

    async def close(self) -> None:
        if self._flush_task:
            self._flush_task.cancel()
        await self.flush()
        if self._pool:
            await self._pool.close()

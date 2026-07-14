"""
core/performance.py
===================
성능 최적화 모듈.

- Redis 캐시       : 캔들/OI/펀딩 TTL 캐싱
- 멀티프로세스 백테스트: ProcessPoolExecutor로 병렬 실행
- 비동기 배치 처리  : 틱 배치 압축 저장
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import pickle
import time
from concurrent.futures import ProcessPoolExecutor
from functools import lru_cache
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


# ── Redis 캐시 ────────────────────────────────────────
class RedisCache:
    """
    Redis 캐시 래퍼.
    Redis가 없으면 자동으로 인메모리 딕셔너리로 폴백.
    """

    def __init__(self, host: str = "localhost", port: int = 6379, db: int = 0) -> None:
        self._redis = None
        self._memory: dict[str, tuple[Any, float]] = {}   # (value, expire_ts)
        self._hits = 0; self._misses = 0

        try:
            import redis.asyncio as aioredis
            self._redis = aioredis.Redis(host=host, port=port, db=db,
                                         socket_connect_timeout=2, decode_responses=False)
            logger.info("[Cache] Redis 연결됨")
        except Exception:
            logger.info("[Cache] Redis 없음 → 인메모리 캐시 사용")

    async def get(self, key: str) -> Optional[Any]:
        if self._redis:
            try:
                raw = await self._redis.get(key)
                if raw:
                    self._hits += 1
                    return pickle.loads(raw)
            except Exception:
                pass
        # 인메모리 폴백
        entry = self._memory.get(key)
        if entry and (entry[1] == 0 or time.time() < entry[1]):
            self._hits += 1
            return entry[0]
        self._misses += 1
        return None

    async def set(self, key: str, value: Any, ttl: int = 60) -> None:
        if self._redis:
            try:
                await self._redis.setex(key, ttl, pickle.dumps(value))
                return
            except Exception:
                pass
        self._memory[key] = (value, time.time() + ttl if ttl else 0)

    async def delete(self, key: str) -> None:
        if self._redis:
            try: await self._redis.delete(key); return
            except Exception: pass
        self._memory.pop(key, None)

    async def clear_pattern(self, pattern: str) -> None:
        """패턴 매칭 키 전체 삭제 (예: 'candles:BTC*')."""
        if self._redis:
            try:
                keys = await self._redis.keys(pattern)
                if keys: await self._redis.delete(*keys)
                return
            except Exception: pass
        prefix = pattern.rstrip("*")
        for k in list(self._memory.keys()):
            if k.startswith(prefix):
                del self._memory[k]

    @property
    def stats(self) -> dict:
        total = self._hits + self._misses
        return {
            "hits":     self._hits,
            "misses":   self._misses,
            "hit_rate": round(self._hits / max(total, 1) * 100, 1),
            "backend":  "redis" if self._redis else "memory",
        }

    async def close(self) -> None:
        if self._redis:
            await self._redis.close()


# ── 캐시 데코레이터 ───────────────────────────────────
def cached(ttl: int = 60):
    """비동기 함수용 Redis 캐시 데코레이터."""
    def decorator(func):
        async def wrapper(self_or_cache, *args, **kwargs):
            # 캐시 인스턴스 탐색
            cache: Optional[RedisCache] = None
            if hasattr(self_or_cache, '_cache'):
                cache = self_or_cache._cache
            if not cache:
                return await func(self_or_cache, *args, **kwargs)

            key = f"{func.__name__}:{hashlib.md5(str(args).encode()).hexdigest()}"
            cached_val = await cache.get(key)
            if cached_val is not None:
                return cached_val

            result = await func(self_or_cache, *args, **kwargs)
            await cache.set(key, result, ttl)
            return result
        return wrapper
    return decorator


# ── 멀티프로세스 백테스트 ─────────────────────────────
def _backtest_worker(args: tuple) -> dict:
    """
    별도 프로세스에서 실행되는 백테스트 워커.
    (picklable 함수만 ProcessPoolExecutor에서 사용 가능)
    """
    candles_data, params = args
    try:
        import sys, os
        sys.path.insert(0, os.getcwd())

        import numpy as np
        from core.models import Candle, Timeframe
        from strategy.ict_engine import ICTEngine, ICTParams

        # 캔들 복원
        candles = [
            Candle(ts=d['ts'], open=d['o'], high=d['h'], low=d['l'],
                   close=d['c'], volume=d['v'],
                   symbol=d.get('sym','BTC'), timeframe=Timeframe.M15)
            for d in candles_data
        ]

        p = ICTParams(**params)
        engine = ICTEngine(p)

        trades, open_pos = [], []
        warmup = 100
        wins = losses = 0
        cum_r = 0.0

        for i in range(warmup, len(candles)):
            window = candles[:i+1]
            bh = window[-1].high; bl = window[-1].low; bt = window[-1].ts

            still_open = []
            for pos in open_pos:
                hit_sl = (pos['dir']=='LONG' and bl<=pos['sl']) or (pos['dir']=='SHORT' and bh>=pos['sl'])
                hit_tp = (pos['dir']=='LONG' and bh>=pos['tp']) or (pos['dir']=='SHORT' and bl<=pos['tp'])
                if hit_sl or hit_tp:
                    r = pos.get('rr', 2.0) if hit_tp else -1.0
                    cum_r += r
                    wins  += 1 if hit_tp else 0
                    losses+= 0 if hit_tp else 1
                    trades.append({**pos, 'result': 'WIN' if hit_tp else 'LOSS', 'r': r, 'exit_ts': bt})
                else:
                    still_open.append(pos)
            open_pos = still_open

            try:
                res = engine.analyze(window)
            except Exception:
                continue
            if not res.signal or any(pos['dir']==res.signal for pos in open_pos):
                continue
            if res.entry and res.sl and res.tp:
                open_pos.append({'dir': res.signal, 'entry': res.entry,
                                 'sl': res.sl, 'tp': res.tp,
                                 'rr': res.rr or 2.0, 'ts': bt})

        closed = [t for t in trades if t.get('result') in ('WIN','LOSS')]
        wr = wins / max(len(closed), 1) * 100
        return {
            'params':    params,
            'total':     len(trades),
            'wins':      wins,
            'losses':    losses,
            'win_rate':  round(wr, 1),
            'cum_r':     round(cum_r, 2),
            'avg_r':     round(cum_r / max(len(closed), 1), 3),
        }
    except Exception as e:
        return {'error': str(e), 'params': params}


class ParallelBacktester:
    """
    멀티프로세스 병렬 백테스트.
    여러 파라미터 세트를 동시에 테스트해서 최적 파라미터를 찾는다.
    """

    def __init__(self, max_workers: int = 4) -> None:
        self._max_workers = max_workers

    async def run_grid_search(
        self,
        candles: list,
        param_grid: list[dict],
        progress_cb: Optional[Callable[[int, int], None]] = None,
    ) -> list[dict]:
        """
        그리드 서치 — 파라미터 조합을 병렬로 백테스트.
        candles: list[Candle]
        param_grid: [{"risk_reward_ratio": 1.5, "min_confluence": 1}, ...]
        """
        # 캔들을 직렬화 가능한 dict로 변환
        candles_data = [
            {'ts': c.ts, 'o': c.open, 'h': c.high, 'l': c.low,
             'c': c.close, 'v': c.volume, 'sym': c.symbol}
            for c in candles
        ]

        args_list = [(candles_data, params) for params in param_grid]
        results   = []
        loop      = asyncio.get_event_loop()

        with ProcessPoolExecutor(max_workers=self._max_workers) as executor:
            futures = [
                loop.run_in_executor(executor, _backtest_worker, args)
                for args in args_list
            ]
            for i, future in enumerate(asyncio.as_completed(futures)):
                result = await future
                results.append(result)
                if progress_cb:
                    progress_cb(i + 1, len(args_list))

        # 승률 × 누적R 기준 정렬
        valid = [r for r in results if 'error' not in r]
        valid.sort(key=lambda x: x.get('cum_r', -999), reverse=True)
        return valid

    @staticmethod
    def build_param_grid(ranges: dict) -> list[dict]:
        """파라미터 그리드 생성."""
        import itertools
        keys   = list(ranges.keys())
        values = list(ranges.values())
        grid   = []
        for combo in itertools.product(*values):
            grid.append(dict(zip(keys, combo)))
        return grid


# ── 배치 압축 저장 ────────────────────────────────────
class BatchWriter:
    """
    틱/신호를 배치로 모아서 DB에 한 번에 저장.
    초당 수십만 건 처리를 위한 버퍼링.
    """

    def __init__(
        self,
        flush_interval: float = 2.0,    # 초
        max_batch_size: int   = 1000,
    ) -> None:
        self._interval  = flush_interval
        self._max_size  = max_batch_size
        self._buffer:   list = []
        self._flush_fn: Optional[Callable] = None
        self._task:     Optional[asyncio.Task] = None

    def set_flush_fn(self, fn: Callable) -> None:
        self._flush_fn = fn

    def add(self, item: Any) -> None:
        self._buffer.append(item)
        if len(self._buffer) >= self._max_size:
            asyncio.create_task(self._flush())

    async def _flush(self) -> None:
        if not self._buffer or not self._flush_fn:
            return
        batch = self._buffer[:]
        self._buffer.clear()
        try:
            await self._flush_fn(batch)
        except Exception as e:
            logger.error(f"[BatchWriter] flush 오류: {e}")

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            await self._flush()

    async def stop(self) -> None:
        if self._task: self._task.cancel()
        await self._flush()

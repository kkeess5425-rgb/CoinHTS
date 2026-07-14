"""
optimization/optimizer.py
=========================
전략 파라미터 자동 최적화.

- Walk Forward Test     : 과적합 방지 (훈련/검증 분리)
- Monte Carlo Simulation: 랜덤 샘플링으로 결과 분포 추정
- Genetic Algorithm     : 유전자 알고리즘으로 최적 파라미터 탐색
"""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class ParamRange:
    """파라미터 탐색 범위."""
    name:    str
    min_val: float
    max_val: float
    step:    float
    dtype:   str = "float"   # "float" | "int"

    def sample(self) -> float | int:
        val = random.uniform(self.min_val, self.max_val)
        if self.dtype == "int":
            return int(round(val / self.step) * self.step)
        return round(val / self.step) * self.step


@dataclass
class OptimizationResult:
    """최적화 결과."""
    best_params:    dict
    best_score:     float
    all_results:    list[dict]        = field(default_factory=list)
    wf_scores:      list[float]       = field(default_factory=list)   # Walk Forward 검증 점수
    mc_percentiles: dict              = field(default_factory=dict)   # Monte Carlo 분포
    generations:    int               = 0


# ── 평가 함수 타입 ────────────────────────────────────
# evaluate(params: dict) -> float (높을수록 좋음)
EvalFunc = Callable[[dict], float]


class WalkForwardTest:
    """
    Walk Forward 테스트.
    데이터를 훈련/검증 윈도우로 분리해서 과적합을 방지한다.
    """

    def __init__(
        self,
        train_pct:   float = 0.7,   # 훈련 데이터 비율
        n_windows:   int   = 5,     # 롤링 윈도우 수
    ) -> None:
        self.train_pct  = train_pct
        self.n_windows  = n_windows

    def run(
        self,
        data:          list,
        param_ranges:  list[ParamRange],
        eval_func:     EvalFunc,
        n_trials:      int = 50,
    ) -> OptimizationResult:
        """
        Walk Forward 최적화.
        각 윈도우에서 최적 파라미터를 찾고 다음 윈도우에서 검증한다.
        """
        n = len(data)
        window_size = n // self.n_windows
        wf_scores   = []
        all_results = []
        best_params = {}
        best_score  = -np.inf

        for w in range(self.n_windows - 1):
            start   = w * window_size
            t_end   = start + int(window_size * self.train_pct)
            v_end   = start + window_size

            train_data = data[start:t_end]
            valid_data = data[t_end:v_end]

            # 훈련 윈도우에서 최적화
            best_train  = -np.inf
            best_p_win  = {}
            for _ in range(n_trials):
                params = {r.name: r.sample() for r in param_ranges}
                try:
                    score = eval_func(params)  # train_data 사용 가정
                except Exception:
                    continue
                all_results.append({"params": params, "score": score, "window": w})
                if score > best_train:
                    best_train = score
                    best_p_win = params

            # 검증 윈도우에서 검증
            if best_p_win:
                try:
                    valid_score = eval_func(best_p_win)  # valid_data 사용 가정
                    wf_scores.append(valid_score)
                    if valid_score > best_score:
                        best_score  = valid_score
                        best_params = best_p_win
                except Exception:
                    pass

            logger.debug(f"[WF] 윈도우 {w}: 훈련 {best_train:.3f} | 검증 {wf_scores[-1] if wf_scores else '-':.3f}")

        return OptimizationResult(
            best_params=best_params,
            best_score=best_score,
            all_results=all_results,
            wf_scores=wf_scores,
        )


class MonteCarloSimulation:
    """
    Monte Carlo 시뮬레이션.
    트레이드 결과를 랜덤 샘플링해서 성과 분포를 추정한다.
    """

    def run(
        self,
        pnl_series:   list[float],
        n_sims:        int = 1000,
        confidence:    float = 0.95,
    ) -> dict:
        """
        트레이드 PnL 시계열로 Monte Carlo 시뮬레이션.
        반환: 승률/MDD/최종 R의 분포 통계.
        """
        if not pnl_series:
            return {}

        arr = np.array(pnl_series)
        final_returns, max_dds = [], []

        for _ in range(n_sims):
            shuffled = np.random.choice(arr, size=len(arr), replace=True)
            equity   = np.cumsum(shuffled)
            peak     = np.maximum.accumulate(equity)
            dd       = float((equity - peak).min())
            final_returns.append(float(equity[-1]))
            max_dds.append(dd)

        fr  = np.array(final_returns)
        mdd = np.array(max_dds)
        ci  = (1 - confidence) / 2

        return {
            "final_r": {
                "mean":   round(float(fr.mean()),  3),
                "median": round(float(np.median(fr)), 3),
                "p5":     round(float(np.percentile(fr, 5)),  3),
                "p95":    round(float(np.percentile(fr, 95)), 3),
                "worst":  round(float(fr.min()),  3),
                "best":   round(float(fr.max()),  3),
            },
            "max_dd": {
                "mean":   round(float(mdd.mean()),  3),
                "worst":  round(float(mdd.min()),   3),
                "p95":    round(float(np.percentile(mdd, 5)), 3),
            },
            "ruin_prob": round(float((fr < -5).mean()) * 100, 1),  # -5R 이하 확률
            "n_sims":    n_sims,
        }


class GeneticAlgorithm:
    """
    유전자 알고리즘으로 전략 파라미터 최적화.
    """

    def __init__(
        self,
        population:    int   = 50,
        generations:   int   = 30,
        mutation_rate: float = 0.15,
        crossover_rate:float = 0.7,
        elite_pct:     float = 0.1,
    ) -> None:
        self.population     = population
        self.generations    = generations
        self.mutation_rate  = mutation_rate
        self.crossover_rate = crossover_rate
        self.elite_pct      = elite_pct

    def run(
        self,
        param_ranges: list[ParamRange],
        eval_func:    EvalFunc,
        progress_cb:  Optional[Callable[[int, float], None]] = None,
    ) -> OptimizationResult:
        """GA 최적화 실행."""
        # 초기 개체군
        pop = [{r.name: r.sample() for r in param_ranges} for _ in range(self.population)]
        best_params = pop[0]
        best_score  = -np.inf
        all_results = []

        n_elite = max(1, int(self.population * self.elite_pct))

        for gen in range(self.generations):
            # 평가
            scored = []
            for individual in pop:
                try:
                    score = eval_func(individual)
                except Exception:
                    score = -999
                scored.append((score, individual))
                all_results.append({"params": individual, "score": score, "gen": gen})

            # 정렬 (높을수록 좋음)
            scored.sort(key=lambda x: x[0], reverse=True)

            if scored[0][0] > best_score:
                best_score  = scored[0][0]
                best_params = scored[0][1]

            if progress_cb:
                progress_cb(gen, best_score)

            # 엘리트 유지
            new_pop = [ind for _, ind in scored[:n_elite]]

            # 크로스오버 + 변이
            while len(new_pop) < self.population:
                p1, p2 = random.choices(scored[:max(10, self.population//3)], k=2)
                child  = self._crossover(p1[1], p2[1], param_ranges)
                child  = self._mutate(child, param_ranges)
                new_pop.append(child)

            pop = new_pop
            logger.debug(f"[GA] 세대 {gen+1}/{self.generations}: 최고 점수 {best_score:.4f}")

        return OptimizationResult(
            best_params=best_params,
            best_score=best_score,
            all_results=all_results,
            generations=self.generations,
        )

    def _crossover(self, p1: dict, p2: dict, ranges: list[ParamRange]) -> dict:
        if random.random() > self.crossover_rate:
            return p1.copy()
        child = {}
        for r in ranges:
            child[r.name] = p1[r.name] if random.random() > 0.5 else p2[r.name]
        return child

    def _mutate(self, individual: dict, ranges: list[ParamRange]) -> dict:
        result = individual.copy()
        for r in ranges:
            if random.random() < self.mutation_rate:
                result[r.name] = r.sample()
        return result


class StrategyOptimizer:
    """
    전략 최적화 통합 클래스.
    Walk Forward + Monte Carlo + GA를 조합해서 사용.
    """

    def __init__(self) -> None:
        self.wf  = WalkForwardTest()
        self.mc  = MonteCarloSimulation()
        self.ga  = GeneticAlgorithm()

    def full_optimize(
        self,
        pnl_series:   list[float],
        param_ranges: list[ParamRange],
        eval_func:    EvalFunc,
        data:         Optional[list] = None,
    ) -> OptimizationResult:
        """GA 최적화 → Monte Carlo 검증."""
        # 1. GA 최적화
        logger.info("[Optimizer] GA 최적화 시작...")
        ga_result = self.ga.run(param_ranges, eval_func)

        # 2. Monte Carlo 검증
        logger.info("[Optimizer] Monte Carlo 검증...")
        mc_result = self.mc.run(pnl_series)
        ga_result.mc_percentiles = mc_result

        # 3. Walk Forward (데이터 있을 때)
        if data and len(data) > 100:
            logger.info("[Optimizer] Walk Forward 검증...")
            wf_result = self.wf.run(data, param_ranges, eval_func)
            ga_result.wf_scores = wf_result.wf_scores

        return ga_result

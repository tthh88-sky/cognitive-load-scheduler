"""Turns completed tasks into calibration signal: examples for the
estimator's prompt, a duration multiplier that corrects the model's
timing, and summary insights for the UI. Pure stdlib + dataclasses, no
FastAPI/SQLAlchemy/app.llm imports.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from statistics import median
from typing import Optional

FIB = [1, 2, 3, 5, 8, 13]


@dataclass
class Outcome:
    title: str
    load: int  # final cognitive_load
    estimated_minutes: int  # final estimated_duration_minutes
    ai_load: Optional[int]
    ai_duration_minutes: Optional[int]  # raw model duration, before the multiplier
    actual_minutes: Optional[int]
    hindsight_load: Optional[int]
    load_source: str
    completed_at: datetime


@dataclass
class Metric:
    value: Optional[float]
    samples: int


@dataclass
class Insights:
    median_duration_error: Metric
    ai_match_rate: Metric
    ai_match_within_one_step: Metric
    own_match_rate: Metric


def hindsight_load(load: int, hindsight: str) -> int:
    if load not in FIB:
        load = min(FIB, key=lambda f: abs(f - load))
    idx = FIB.index(load)
    if hindsight == "easier":
        idx = max(0, idx - 1)
    elif hindsight == "harder":
        idx = min(len(FIB) - 1, idx + 1)
    return FIB[idx]


def _bucket(load: int) -> str:
    return "light" if load <= 3 else "deep"


def duration_multiplier(outcomes: list[Outcome], load: int | None = None) -> float:
    samples = [
        o
        for o in outcomes
        if o.actual_minutes is not None and o.ai_duration_minutes is not None
    ]
    samples = sorted(samples, key=lambda o: o.completed_at, reverse=True)[:20]

    if load is not None:
        bucket = _bucket(load)
        bucketed = [o for o in samples if _bucket(o.load) == bucket]
        if len(bucketed) >= 5:
            samples = bucketed

    if len(samples) < 5:
        return 1.0

    ratios = [o.actual_minutes / o.ai_duration_minutes for o in samples]
    m = median(ratios)
    return round(max(0.5, min(2.0, m)), 2)


_WORD_RE = re.compile(r"[a-z0-9]{3,}")


def _tokens(title: str) -> set[str]:
    return set(_WORD_RE.findall(title.lower()))


def select_examples(outcomes: list[Outcome], title: str, k: int = 8) -> list[Outcome]:
    candidates = [
        o for o in outcomes if o.actual_minutes is not None or o.hindsight_load is not None
    ]
    candidates.sort(key=lambda o: o.completed_at, reverse=True)
    if len(candidates) <= k:
        return candidates

    target_tokens = _tokens(title)
    scored = [
        (len(target_tokens & _tokens(o.title)), i, o) for i, o in enumerate(candidates)
    ]
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    return [o for _score, _i, o in scored[:k]]


def insights(outcomes: list[Outcome]) -> Insights:
    latest = sorted(outcomes, key=lambda o: o.completed_at, reverse=True)[:20]

    duration_samples = [o for o in latest if o.actual_minutes is not None]
    if duration_samples:
        errors = [
            (o.actual_minutes - o.estimated_minutes) / o.estimated_minutes
            for o in duration_samples
        ]
        median_error = Metric(value=median(errors), samples=len(errors))
    else:
        median_error = Metric(value=None, samples=0)

    ai_samples = [
        o for o in latest if o.ai_load is not None and o.hindsight_load is not None
    ]
    if ai_samples:
        exact = sum(1 for o in ai_samples if o.ai_load == o.hindsight_load)
        within_one = sum(
            1
            for o in ai_samples
            if abs(_fib_index(o.ai_load) - _fib_index(o.hindsight_load)) <= 1
        )
        ai_match = Metric(value=exact / len(ai_samples), samples=len(ai_samples))
        ai_within = Metric(value=within_one / len(ai_samples), samples=len(ai_samples))
    else:
        ai_match = Metric(value=None, samples=0)
        ai_within = Metric(value=None, samples=0)

    own_samples = [
        o
        for o in latest
        if o.load_source in ("manual", "ai_edited") and o.hindsight_load is not None
    ]
    if own_samples:
        exact = sum(1 for o in own_samples if o.load == o.hindsight_load)
        own_match = Metric(value=exact / len(own_samples), samples=len(own_samples))
    else:
        own_match = Metric(value=None, samples=0)

    return Insights(
        median_duration_error=median_error,
        ai_match_rate=ai_match,
        ai_match_within_one_step=ai_within,
        own_match_rate=own_match,
    )


def _fib_index(load: int) -> int:
    if load in FIB:
        return FIB.index(load)
    return FIB.index(min(FIB, key=lambda f: abs(f - load)))

#!/usr/bin/env python
"""Compares Ollama models on a labeled estimation set. Needs Ollama running;
not part of pytest. The Ollama runbook (out of scope for this repo) uses
this script to pick the final OLLAMA_MODEL.

    python scripts/eval_estimator.py --models qwen3.8:27b,other:tag --csv golden.csv --about-me "Backend dev"
    python scripts/eval_estimator.py --models qwen3.8:27b --from-db
"""

from __future__ import annotations

import argparse
import csv
import statistics
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.calibration import FIB, Outcome, hindsight_load  # noqa: E402
from app.llm.config import LLMConfig  # noqa: E402
from app.llm.estimator import estimate  # noqa: E402
from app.llm.provider import LLMUnavailable, OllamaProvider  # noqa: E402


@dataclass
class GoldenRow:
    title: str
    description: str
    true_load: int
    actual_minutes: int | None


def _load_csv(path: str) -> list[GoldenRow]:
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows.append(
                GoldenRow(
                    title=row["title"],
                    description=row.get("description", ""),
                    true_load=int(row["true_load"]),
                    actual_minutes=int(row["actual_minutes"]) if row.get("actual_minutes") else None,
                )
            )
    return rows


def _load_from_db() -> list[GoldenRow]:
    from app import models
    from app.database import SessionLocal
    from app.models import TaskStatus

    db = SessionLocal()
    try:
        tasks = (
            db.query(models.Task)
            .filter(models.Task.status == TaskStatus.DONE, models.Task.hindsight_load.isnot(None))
            .all()
        )
        return [
            GoldenRow(
                title=t.title,
                description=t.description or "",
                true_load=t.hindsight_load,
                actual_minutes=t.actual_minutes,
            )
            for t in tasks
        ]
    finally:
        db.close()


def _fib_distance(a: int, b: int) -> int:
    def idx(load):
        return FIB.index(load) if load in FIB else min(range(len(FIB)), key=lambda i: abs(FIB[i] - load))

    return abs(idx(a) - idx(b))


def _run_model(model: str, rows: list[GoldenRow], about_me: str) -> dict:
    cfg = LLMConfig.from_env()
    cfg = LLMConfig(
        provider=cfg.provider, host=cfg.host, model=model, allow_cloud=cfg.allow_cloud,
        timeout_s=cfg.timeout_s, keep_alive=cfg.keep_alive, num_ctx=cfg.num_ctx,
    )
    provider = OllamaProvider(cfg)

    # One untimed warm-up call.
    try:
        estimate(provider, "warm up", "", "", about_me, [])
    except LLMUnavailable:
        pass

    exact = 0
    within_one = 0
    n = 0
    duration_errors = []
    latencies = []
    final_invalid = 0

    for row in rows:
        n += 1
        start = time.perf_counter()
        try:
            suggestion = estimate(provider, row.title, row.description, "", about_me, [])
        except LLMUnavailable:
            final_invalid += 1
            continue
        latencies.append(time.perf_counter() - start)

        if suggestion.load == row.true_load:
            exact += 1
        if _fib_distance(suggestion.load, row.true_load) <= 1:
            within_one += 1
        if row.actual_minutes:
            duration_errors.append(abs(suggestion.duration_minutes - row.actual_minutes) / row.actual_minutes)

    latencies.sort()

    def pct(x):
        return round(x * 100, 1) if n else 0.0

    def p(latency_list, q):
        if not latency_list:
            return None
        idx = min(len(latency_list) - 1, int(len(latency_list) * q))
        return round(latency_list[idx], 2)

    return {
        "model": model,
        "n": n,
        "exact_match_pct": pct(exact / n) if n else 0.0,
        "within_one_step_pct": pct(within_one / n) if n else 0.0,
        "median_duration_error_pct": round(statistics.median(duration_errors) * 100, 1) if duration_errors else None,
        "first_try_valid_pct": pct(1 - provider.invalid_outputs / n) if n else 0.0,
        "final_valid_pct": pct((n - final_invalid) / n) if n else 0.0,
        "p50_latency_s": p(latencies, 0.5),
        "p95_latency_s": p(latencies, 0.95),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", required=True, help="Comma-separated Ollama model tags")
    parser.add_argument("--csv", help="Path to a golden CSV (title,description,true_load,actual_minutes)")
    parser.add_argument("--from-db", action="store_true", help="Build rows from done tasks in scheduler.db")
    parser.add_argument("--about-me", default="", help="Profile text passed to the estimator")
    args = parser.parse_args()

    if not args.csv and not args.from_db:
        parser.error("one of --csv or --from-db is required")

    rows = _load_csv(args.csv) if args.csv else _load_from_db()
    if not rows:
        print("No rows to evaluate.")
        return

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    for m in models:
        if m.split(":")[-1].endswith("cloud"):
            parser.error(f"cloud model refused: {m}")

    results = [_run_model(m, rows, args.about_me) for m in models]

    headers = [
        "model", "n", "exact_match_pct", "within_one_step_pct", "median_duration_error_pct",
        "first_try_valid_pct", "final_valid_pct", "p50_latency_s", "p95_latency_s",
    ]
    widths = {h: max(len(h), *(len(str(r[h])) for r in results)) for h in headers}
    print(" | ".join(h.ljust(widths[h]) for h in headers))
    print("-+-".join("-" * widths[h] for h in headers))
    for r in results:
        print(" | ".join(str(r[h]).ljust(widths[h]) for h in headers))

    out_path = Path(__file__).parent / f"eval_results_{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(results)
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()

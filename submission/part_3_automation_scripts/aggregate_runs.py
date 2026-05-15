#!/usr/bin/env python3
"""
aggregate_runs.py RUN_DIR [RUN_DIR ...]

Compute mean + std of per-job execution time and makespan across N run
directories (typically 3 for the report). Each RUN_DIR must contain a
results.json produced by run_experiment.sh.

Also reports the memcached SLO violation ratio (fraction of mcperf rows
whose p95 > 1 ms) during the batch window of each run, plus the mean
across runs. The batch window is
[min(container.startedAt), max(container.finishedAt)] over all batch
containers, as required by the assignment.

Prints the table in the order the CCA Part-3 report expects.
"""
from __future__ import annotations

import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

from slo_violations import SLO_US, job_window, parse_mcperf

TFMT = "%Y-%m-%dT%H:%M:%SZ"
JOB_ORDER = [
    "parsec-barnes",
    "parsec-blackscholes",
    "parsec-canneal",
    "parsec-freqmine",
    "parsec-radix",
    "parsec-streamcluster",
    "parsec-vips",
]


def parse_iso(ts: str) -> float:
    return datetime.strptime(ts, TFMT).replace(tzinfo=timezone.utc).timestamp()


def per_run(results_json: Path) -> tuple[dict[str, float], float]:
    data = json.loads(results_json.read_text())
    run_times: dict[str, float] = {}
    starts: list[float] = []
    ends: list[float] = []
    for item in data.get("items", []):
        cs = item["status"].get("containerStatuses", [{}])[0]
        name = cs.get("name", "?")
        if name == "memcached":
            continue
        term = cs.get("state", {}).get("terminated")
        if not term:
            continue
        s = parse_iso(term["startedAt"])
        e = parse_iso(term["finishedAt"])
        run_times[name] = e - s
        starts.append(s)
        ends.append(e)
    makespan = (max(ends) - min(starts)) if starts and ends else 0.0
    return run_times, makespan


def per_run_slo(run_dir: Path) -> tuple[int, int, float] | None:
    """Return (violations, rows_in_window, ratio) for one run, or None if the
    run has no mcperf log or no rows inside the batch window."""
    mcperf_txt = run_dir / "mcperf.txt"
    results_json = run_dir / "results.json"
    if not mcperf_txt.exists() or not results_json.exists():
        return None
    try:
        w_start, w_end = job_window(results_json)
    except RuntimeError:
        return None
    rows = parse_mcperf(mcperf_txt)
    in_window = [p for (s, e, p) in rows if s >= w_start and e <= w_end]
    if not in_window:
        return None
    violations = sum(1 for p in in_window if p > SLO_US)
    return violations, len(in_window), violations / len(in_window)


def fmt(mean: float, std: float) -> str:
    return f"{mean:>10.1f}  {std:>8.2f}"


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: aggregate_runs.py <run_dir> [run_dir ...]", file=sys.stderr)
        sys.exit(1)

    run_dirs = [Path(p) for p in sys.argv[1:]]
    per_run_times: list[dict[str, float]] = []
    makespans: list[float] = []
    for d in run_dirs:
        rj = d / "results.json"
        if not rj.exists():
            print(f"skipping {d}: no results.json", file=sys.stderr)
            continue
        rt, ms = per_run(rj)
        per_run_times.append(rt)
        makespans.append(ms)

    if not per_run_times:
        print("no runs to aggregate", file=sys.stderr)
        sys.exit(1)

    print(f"aggregated across {len(per_run_times)} run(s): "
          f"{[p.name for p in run_dirs]}")
    print()
    print(f"{'job name':<20}{'mean time [s]':>15}{'std [s]':>12}")
    print("-" * 47)
    for job in JOB_ORDER:
        vals = [rt[job] for rt in per_run_times if job in rt]
        if not vals:
            print(f"{job:<20}{'MISSING':>15}{'':>12}")
            continue
        mean = statistics.mean(vals)
        std = statistics.stdev(vals) if len(vals) > 1 else 0.0
        short = job.replace("parsec-", "")
        print(f"{short:<20}{mean:>15.1f}{std:>12.2f}")
    print("-" * 47)
    ms_mean = statistics.mean(makespans)
    ms_std = statistics.stdev(makespans) if len(makespans) > 1 else 0.0
    print(f"{'total time':<20}{ms_mean:>15.1f}{ms_std:>12.2f}")

    print()
    print("memcached SLO violation ratio (p95 > 1 ms, within batch window):")
    print(f"{'run':<12}{'violations':>12}{'rows':>8}{'ratio':>10}")
    print("-" * 42)
    slo_rows: list[tuple[str, int, int, float]] = []
    for d in run_dirs:
        res = per_run_slo(d)
        if res is None:
            print(f"{d.name:<12}{'N/A':>12}{'':>8}{'':>10}")
            continue
        v, t, r = res
        slo_rows.append((d.name, v, t, r))
        print(f"{d.name:<12}{v:>12}{t:>8}{r:>10.4f}")
    print("-" * 42)
    if slo_rows:
        ratios = [r for (_, _, _, r) in slo_rows]
        r_mean = statistics.mean(ratios)
        r_std = statistics.stdev(ratios) if len(ratios) > 1 else 0.0
        total_v = sum(v for (_, v, _, _) in slo_rows)
        total_t = sum(t for (_, _, t, _) in slo_rows)
        pooled = total_v / total_t if total_t else 0.0
        print(f"{'mean':<12}{'':>12}{'':>8}{r_mean:>10.4f}  (std {r_std:.4f})")
        print(f"{'pooled':<12}{total_v:>12}{total_t:>8}{pooled:>10.4f}")


if __name__ == "__main__":
    main()

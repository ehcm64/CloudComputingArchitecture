#!/usr/bin/env python3
"""
slo_violations.py RESULTS_JSON MCPERF_TXT

Compute the memcached SLO violation ratio during the batch window.

Window: [min(container.startedAt), max(container.finishedAt)] from the pod JSON.
Violation: a row where p95 latency > 1000us (1ms).
Ratio: violations / total rows inside the window.

mcperf augmented format has ts_start / ts_end columns (unix ms since epoch).
The header line starts with '#type'; data rows start with 'read'.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

TFMT = "%Y-%m-%dT%H:%M:%SZ"
SLO_US = 1000.0  # 1 ms = 1000 us


def parse_iso_utc(ts: str) -> float:
    return datetime.strptime(ts, TFMT).replace(tzinfo=timezone.utc).timestamp()


def job_window(results_json: Path) -> tuple[float, float]:
    data = json.loads(results_json.read_text())
    starts, ends = [], []
    for item in data.get("items", []):
        cs = item["status"].get("containerStatuses", [{}])[0]
        if cs.get("name") == "memcached":
            continue
        term = cs.get("state", {}).get("terminated")
        if not term:
            continue
        starts.append(parse_iso_utc(term["startedAt"]))
        ends.append(parse_iso_utc(term["finishedAt"]))
    if not starts:
        raise RuntimeError("no terminated batch containers in results.json")
    return min(starts), max(ends)


def parse_mcperf(mcperf_txt: Path) -> list[tuple[float, float, float]]:
    """Return list of (ts_start_sec, ts_end_sec, p95_us)."""
    rows = []
    headers: list[str] | None = None
    for line in mcperf_txt.read_text().splitlines():
        tokens = line.split()
        if not tokens:
            continue
        if tokens[0].startswith("#"):
            headers = [t.lstrip("#") for t in tokens]
            continue
        if tokens[0] != "read" or headers is None:
            continue
        try:
            idx_p95 = headers.index("p95")
            idx_ts_start = headers.index("ts_start")
            idx_ts_end = headers.index("ts_end")
        except ValueError:
            continue
        try:
            p95 = float(tokens[idx_p95])
            ts_start = float(tokens[idx_ts_start])
            ts_end = float(tokens[idx_ts_end])
        except (ValueError, IndexError):
            continue
        if ts_start > 1e12:
            ts_start /= 1000.0
        if ts_end > 1e12:
            ts_end /= 1000.0
        rows.append((ts_start, ts_end, p95))
    return rows


def main() -> None:
    if len(sys.argv) < 3:
        print("usage: slo_violations.py <results.json> <mcperf.txt>",
              file=sys.stderr)
        sys.exit(1)

    w_start, w_end = job_window(Path(sys.argv[1]))
    rows = parse_mcperf(Path(sys.argv[2]))

    in_window = [(s, e, p) for (s, e, p) in rows if s >= w_start and e <= w_end]
    violations = [p for (_, _, p) in in_window if p > SLO_US]

    print(f"window start (UTC epoch s): {w_start:.0f}")
    print(f"window end   (UTC epoch s): {w_end:.0f}")
    print(f"window duration          : {w_end - w_start:.1f} s")
    print(f"mcperf rows (total)      : {len(rows)}")
    print(f"mcperf rows (in window)  : {len(in_window)}")
    print(f"violations (p95 > 1ms)   : {len(violations)}")
    if in_window:
        ratio = len(violations) / len(in_window)
        print(f"SLO violation ratio      : {ratio:.4f}")
        p95_vals = [p for (_, _, p) in in_window]
        print(f"p95 min/avg/max (us)     : "
              f"{min(p95_vals):.0f} / "
              f"{sum(p95_vals)/len(p95_vals):.0f} / "
              f"{max(p95_vals):.0f}")
    else:
        print("SLO violation ratio      : N/A (no mcperf rows inside window)")


if __name__ == "__main__":
    main()

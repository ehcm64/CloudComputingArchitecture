#!/usr/bin/env python3
"""
summarize_run.py RESULTS_JSON
Print a per-pod breakdown of scheduling wait time vs running time.

Timeline of a k8s pod:
    creationTimestamp -> container.startedAt -> container.finishedAt
          submitted             actually ran           finished
    |------ wait ------|------ run ------|

wait = scheduling latency + image pull + container init
run  = actual job execution
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

TFMT = "%Y-%m-%dT%H:%M:%SZ"


def parse(ts: str) -> datetime:
    return datetime.strptime(ts, TFMT)


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: summarize_run.py <results.json>", file=sys.stderr)
        sys.exit(1)

    data = json.loads(Path(sys.argv[1]).read_text())

    rows = []
    for item in data.get("items", []):
        cs = item["status"].get("containerStatuses", [{}])[0]
        name = cs.get("name", "?")
        if name == "memcached":
            continue
        node = item.get("spec", {}).get("nodeName") or "?"
        term = cs.get("state", {}).get("terminated")
        created = item["metadata"].get("creationTimestamp")
        if not term or not created:
            rows.append((name, node, None, None, None, None, "INCOMPLETE"))
            continue
        t_create = parse(created)
        t_start = parse(term["startedAt"])
        t_end = parse(term["finishedAt"])
        wait = (t_start - t_create).total_seconds()
        run = (t_end - t_start).total_seconds()
        rows.append((name, node, t_create, t_start, t_end, wait, run))

    rows.sort(key=lambda r: (r[2] is None, r[2]))

    header = (f"{'job':<24}{'node':<40}"
              f"{'wait (s)':>12}{'run (s)':>12}{'total (s)':>14}")
    print(header)
    print("-" * len(header))

    min_create = None
    max_end = None
    for name, node, t_create, t_start, t_end, wait, run in rows:
        if isinstance(run, str):
            print(f"{name:<24}{node:<40}{'—':>12}{'—':>12}{run:>14}")
            continue
        total = wait + run
        print(f"{name:<24}{node:<40}{wait:>12.1f}{run:>12.1f}{total:>14.1f}")
        min_create = t_create if min_create is None else min(min_create, t_create)
        max_end = t_end if max_end is None else max(max_end, t_end)

    if min_create and max_end:
        print("-" * len(header))
        makespan = (max_end - min_create).total_seconds()
        print(f"{'MAKESPAN (wall)':<24}{'':<40}{'':>12}{'':>12}{makespan:>14.1f}")


if __name__ == "__main__":
    main()

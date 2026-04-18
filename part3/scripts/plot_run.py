#!/usr/bin/env python3
"""
plot_run.py RUN_DIR [OUT_PNG]

Two-panel figure for one experiment run:

  Top panel : memcached p95 over time. One bar per mcperf row, width =
              ts_end - ts_start, height = p95 (us). Horizontal dashed line
              at 1000 us marks the SLO. Vertical lines annotate each batch
              job's start with "<name> (<node>)".

  Bottom    : per-core Gantt. One row per core of node-a (0..7) and node-b
              (0..3). A colored bar shows which job occupied that core from
              its startedAt to finishedAt. Pinned jobs (taskset -c X-Y) use
              their explicit range; unpinned jobs are first-fit packed into
              ceil(cpu_request) contiguous cores within their time window.

x = 0 is aligned to the earliest container startedAt in the run.

Colors are chosen to roughly match the PARSEC palette in the CCA LaTeX
template (main.tex). Adjust JOB_COLORS if your template differs.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import Patch

TFMT = "%Y-%m-%dT%H:%M:%SZ"
SLO_US = 1000.0

# Colours taken verbatim from main.tex \definecolor{...} directives.
JOB_COLORS = {
    "parsec-barnes":        "#AACCCA",
    "parsec-blackscholes":  "#CCA000",
    "parsec-canneal":       "#CCCCAA",
    "parsec-freqmine":      "#0CCA00",
    "parsec-radix":         "#00CCA0",
    "parsec-streamcluster": "#CCACCA",
    "parsec-vips":          "#CC0A00",
    "memcached":            "#8888AA",
}

NODE_CORES = {
    "node-a": 8,
    "node-b": 4,
}


def parse_iso(ts: str) -> float:
    return datetime.strptime(ts, TFMT).replace(tzinfo=timezone.utc).timestamp()


def parse_cpu_req(s: str) -> int:
    """Return cpu request in millicores. Accepts '500m', '1', '1.5', '3500m'."""
    if s is None:
        return 0
    s = str(s).strip()
    if s.endswith("m"):
        return int(float(s[:-1]))
    return int(float(s) * 1000)


def parse_taskset(args_str: str) -> list[int] | None:
    """Extract pinned cores from 'taskset -c X-Y' or 'taskset -c N'. Return None if unpinned."""
    m = re.search(r"taskset\s+-c\s+(\d+)(?:-(\d+))?", args_str)
    if not m:
        return None
    lo = int(m.group(1))
    hi = int(m.group(2)) if m.group(2) else lo
    return list(range(lo, hi + 1))


def node_tag(node_name: str) -> str:
    if "node-a" in node_name:
        return "node-a"
    if "node-b" in node_name:
        return "node-b"
    return node_name


def extract_pods(data: dict) -> list[dict]:
    """One entry per batch pod with the fields we need."""
    pods = []
    for item in data.get("items", []):
        cs = item["status"].get("containerStatuses", [{}])[0]
        name = cs.get("name", "?")
        if name == "memcached":
            continue
        term = cs.get("state", {}).get("terminated")
        if not term:
            continue
        spec = item.get("spec", {})
        container = spec.get("containers", [{}])[0]
        args = " ".join(container.get("args", []))
        req = container.get("resources", {}).get("requests", {}).get("cpu")
        pods.append({
            "name": name,
            "node": node_tag(spec.get("nodeName", "?")),
            "start": parse_iso(term["startedAt"]),
            "end": parse_iso(term["finishedAt"]),
            "cpu_millis": parse_cpu_req(req) if req else 1000,
            "pinned": parse_taskset(args),
        })
    return pods


def parse_mcperf(path: Path) -> list[tuple[float, float, float]]:
    rows = []
    headers: list[str] | None = None
    for line in path.read_text().splitlines():
        toks = line.split()
        if not toks:
            continue
        if toks[0].startswith("#"):
            headers = [t.lstrip("#") for t in toks]
            continue
        if toks[0] != "read" or headers is None:
            continue
        try:
            p = float(toks[headers.index("p95")])
            ts_s = float(toks[headers.index("ts_start")])
            ts_e = float(toks[headers.index("ts_end")])
        except (ValueError, IndexError):
            continue
        if ts_s > 1e12:
            ts_s /= 1000.0
        if ts_e > 1e12:
            ts_e /= 1000.0
        rows.append((ts_s, ts_e, p))
    return rows


def allocate_cores(pods: list[dict], node: str) -> list[tuple[str, int, float, float]]:
    """Assign cores to each pod on this node. Pinned jobs win; unpinned jobs
    first-fit-pack. Returns list of (name, core_idx, start, end)."""
    total = NODE_CORES[node]
    core_busy: list[list[tuple[float, float]]] = [[] for _ in range(total)]
    events = []

    node_pods = [p for p in pods if p["node"] == node]
    node_pods.sort(key=lambda p: (p["pinned"] is None, p["start"]))

    for p in node_pods:
        if p["pinned"] is not None:
            cores = [c for c in p["pinned"] if c < total]
        else:
            n = max(1, -(-p["cpu_millis"] // 1000))  # ceil
            cores = []
            for base in range(total - n + 1):
                if all(_free(core_busy[base + i], p["start"], p["end"])
                       for i in range(n)):
                    cores = list(range(base, base + n))
                    break
            if not cores:
                free = [c for c in range(total)
                        if _free(core_busy[c], p["start"], p["end"])]
                cores = free[:n] if free else list(range(n))
        for c in cores:
            events.append((p["name"], c, p["start"], p["end"]))
            core_busy[c].append((p["start"], p["end"]))
    return events


def _free(busy: list[tuple[float, float]], s: float, e: float) -> bool:
    return all(e <= bs or s >= be for (bs, be) in busy)


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: plot_run.py <run_dir> [out.png]", file=sys.stderr)
        sys.exit(1)

    run_dir = Path(sys.argv[1])
    out_png = Path(sys.argv[2]) if len(sys.argv) > 2 else run_dir / "plot.png"

    data = json.loads((run_dir / "results.json").read_text())
    pods = extract_pods(data)
    mc_rows = parse_mcperf(run_dir / "mcperf.txt")

    if not pods:
        print("no batch pods found", file=sys.stderr)
        sys.exit(1)

    t0 = min(p["start"] for p in pods)
    t_end = max(p["end"] for p in pods)
    duration = t_end - t0

    fig, (ax_top, ax_gantt) = plt.subplots(
        2, 1, figsize=(14, 9),
        gridspec_kw={"height_ratios": [1, 2]}, sharex=True)

    # Top panel: p95 bars
    for ts_s, ts_e, p95 in mc_rows:
        x0 = ts_s - t0
        w = max(ts_e - ts_s, 0.5)
        ax_top.bar(x0, p95, width=w, align="edge",
                   color="#4477AA", edgecolor="black", linewidth=0.4)
    ax_top.axhline(SLO_US, color="red", linestyle="--", linewidth=1,
                   label=f"SLO ({SLO_US:.0f} us)")
    ax_top.set_ylabel("memcached p95 (us)")
    ax_top.set_title(f"{run_dir.name}: memcached p95 latency and core occupancy")
    ax_top.legend(loc="upper right")
    ax_top.grid(True, alpha=0.3)

    # Vertical lines at each job start
    for p in sorted(pods, key=lambda p: p["start"]):
        x = p["start"] - t0
        ax_top.axvline(x, color="grey", linestyle=":", linewidth=0.7, alpha=0.6)
        label = f"{p['name'].replace('parsec-', '')} ({p['node']})"
        ax_top.text(x, ax_top.get_ylim()[1] * 0.95, label,
                    rotation=90, fontsize=7, va="top", ha="right",
                    color="black", alpha=0.8)

    # Bottom panel: Gantt
    y_labels = []
    y_idx = 0
    core_events_all = []
    for node in ("node-a", "node-b"):
        events = allocate_cores(pods, node)
        for core in range(NODE_CORES[node]):
            for name, c, s, e in events:
                if c != core:
                    continue
                color = JOB_COLORS.get(name, "#888888")
                ax_gantt.barh(y_idx, e - s, left=s - t0, height=0.8,
                              color=color, edgecolor="black", linewidth=0.3)
                core_events_all.append((name, node, core, s - t0, e - t0))
            y_labels.append(f"{node} c{core}")
            y_idx += 1

    ax_gantt.set_yticks(range(len(y_labels)))
    ax_gantt.set_yticklabels(y_labels, fontsize=8)
    ax_gantt.invert_yaxis()
    ax_gantt.set_xlabel("time since first container start (s)")
    ax_gantt.set_xlim(0, duration * 1.02)
    ax_gantt.grid(True, axis="x", alpha=0.3)

    jobs_present = sorted({p["name"] for p in pods})
    legend_handles = [
        Patch(facecolor=JOB_COLORS.get(j, "#888"),
              edgecolor="black", label=j.replace("parsec-", ""))
        for j in jobs_present
    ]
    ax_gantt.legend(handles=legend_handles, loc="upper right",
                    fontsize=8, ncol=4)

    plt.tight_layout()
    plt.savefig(out_png, dpi=150)
    print(f"saved {out_png}")


if __name__ == "__main__":
    main()

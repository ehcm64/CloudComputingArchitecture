#!/usr/bin/env python3
"""
plot_policy_3_2_runs.py [RUN_DIR ...] [--out OUT_DIR]

For each RUN_DIR (expected layout: results.json + mcperf.txt inside),
produce one PNG bar plot of memcached p95 latency over time, with
horizontal bracket annotations showing when each PARSEC/SPLASH batch
job started and ended, plus which node it ran on.

Defaults to the three Part 3.2 policy runs:
    results/run11_policy_3_2  results/run12_policy_3_2  results/run13_policy_3_2
saved as plot_policy_3_2_runN.png in --out (default: submission dir).

The bar style matches scripts/plot_run.py: one bar per mcperf measurement
row, width = ts_end - ts_start, height = p95 (us), dashed red 1000 us SLO.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import Patch

TFMT = "%Y-%m-%dT%H:%M:%SZ"
SLO_US = 1000.0

# Same palette as plot_run.py (derived from main.tex \definecolor directives).
JOB_COLORS = {
    "parsec-barnes":        "#AACCCA",
    "parsec-blackscholes":  "#CCA000",
    "parsec-canneal":       "#CCCCAA",
    "parsec-freqmine":      "#0CCA00",
    "parsec-radix":         "#00CCA0",
    "parsec-streamcluster": "#CCACCA",
    "parsec-vips":          "#CC0A00",
}


def parse_iso(ts: str) -> float:
    return datetime.strptime(ts, TFMT).replace(tzinfo=timezone.utc).timestamp()


def node_tag(node_name: str) -> str:
    if "node-a" in node_name:
        return "node-a"
    if "node-b" in node_name:
        return "node-b"
    return node_name


def extract_pods(data: dict) -> list[dict]:
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
        pods.append({
            "name": name,
            "node": node_tag(spec.get("nodeName", "?")),
            "start": parse_iso(term["startedAt"]),
            "end":   parse_iso(term["finishedAt"]),
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
            p    = float(toks[headers.index("p95")])
            ts_s = float(toks[headers.index("ts_start")])
            ts_e = float(toks[headers.index("ts_end")])
        except (ValueError, IndexError):
            continue
        if ts_s > 1e12: ts_s /= 1000.0
        if ts_e > 1e12: ts_e /= 1000.0
        rows.append((ts_s, ts_e, p))
    return rows


def assign_lanes(pods: list[dict]) -> dict[str, int]:
    """Assign each pod to a vertical lane so their annotation spans don't
    visually overlap. Greedy first-fit on start/end intervals."""
    pods_sorted = sorted(pods, key=lambda p: p["start"])
    lanes: list[float] = []  # lanes[i] = last end time in that lane
    lane_of: dict[str, int] = {}
    for p in pods_sorted:
        placed = False
        for i, end in enumerate(lanes):
            if p["start"] >= end:
                lanes[i] = p["end"]
                lane_of[p["name"]] = i
                placed = True
                break
        if not placed:
            lane_of[p["name"]] = len(lanes)
            lanes.append(p["end"])
    return lane_of


def plot_one(run_dir: Path, out_png: Path) -> None:
    data   = json.loads((run_dir / "results.json").read_text())
    pods   = extract_pods(data)
    mc     = parse_mcperf(run_dir / "mcperf.txt")

    if not pods:
        print(f"[{run_dir.name}] no batch pods found", file=sys.stderr)
        return

    t0     = min(p["start"] for p in pods)
    t_end  = max(p["end"]   for p in pods)

    fig, ax = plt.subplots(figsize=(14, 6))

    # ---- p95 bars (in-window samples only) -----------------------------------
    drawn = 0
    for ts_s, ts_e, p95 in mc:
        if ts_e < t0 or ts_s > t_end:
            continue
        x0 = ts_s - t0
        w  = max(ts_e - ts_s, 0.5)
        ax.bar(x0, p95, width=w, align="edge",
               color="#4477AA", edgecolor="black", linewidth=0.4)
        drawn += 1
    ax.axhline(SLO_US, color="red", linestyle="--", linewidth=1,
               label=f"SLO ({SLO_US:.0f} us)")

    # Pick a y-axis that keeps the SLO visible even when p95 is well below it.
    in_win = [p for s, e, p in mc if e >= t0 and s <= t_end]
    max_p95 = max(in_win) if in_win else 1000
    y_top   = max(SLO_US * 1.35, max_p95 * 1.35, 800)
    ax.set_ylim(0, y_top)

    ax.set_ylabel("memcached p95 latency (us)", fontsize=11)
    ax.set_xlabel("time since first container start (s)", fontsize=11)
    ax.set_title(f"{run_dir.name}: memcached p95 vs batch-job timeline",
                 fontsize=12)
    ax.grid(True, axis="y", alpha=0.3)

    # ---- job spans above the bars --------------------------------------------
    # Put spans in the upper 25% of the plot, stacked in lanes so they don't
    # occlude each other. Lane 0 sits just below y_top.
    lane_of = assign_lanes(pods)
    n_lanes = max(lane_of.values()) + 1 if lane_of else 1
    span_bottom = y_top * 0.75
    lane_h = (y_top - span_bottom) / max(n_lanes, 1)

    duration = t_end - t0

    for p in sorted(pods, key=lambda p: p["start"]):
        x0 = p["start"] - t0
        x1 = p["end"]   - t0
        lane = lane_of[p["name"]]
        y    = y_top - (lane + 0.5) * lane_h
        color = JOB_COLORS.get(p["name"], "#888")
        # Horizontal span: thick line with start/end ticks.
        ax.hlines(y, x0, x1, colors=color, linewidth=6, alpha=0.85,
                  zorder=3)
        ax.vlines([x0, x1], y - lane_h*0.25, y + lane_h*0.25,
                  colors=color, linewidth=1.2, zorder=3)
        # Dashed vertical guides down to x-axis.
        ax.axvline(x0, color=color, linestyle=":", linewidth=0.6, alpha=0.4,
                   zorder=1)
        ax.axvline(x1, color=color, linestyle=":", linewidth=0.6, alpha=0.4,
                   zorder=1)
        # Label centered on the span.
        label = f"{p['name'].replace('parsec-', '')} ({p['node']})"
        x_mid = (x0 + x1) / 2
        ax.text(x_mid, y + lane_h * 0.15, label,
                ha="center", va="bottom", fontsize=8, color="black",
                zorder=4)
        # Numeric start/end on the ends, tiny.
        ax.text(x0, y - lane_h * 0.30, f"{x0:.0f}s",
                ha="center", va="top", fontsize=6.5, color=color, zorder=4)
        ax.text(x1, y - lane_h * 0.30, f"{x1:.0f}s",
                ha="center", va="top", fontsize=6.5, color=color, zorder=4)

    ax.set_xlim(-duration * 0.01, duration * 1.02)

    # Legend: SLO + job colours. Placed BELOW the axes so it never occludes
    # the span annotations in the upper 25% of the plot.
    legend_handles = [
        Patch(facecolor=JOB_COLORS[j], edgecolor="black",
              label=j.replace("parsec-", ""))
        for j in sorted(JOB_COLORS) if any(p["name"] == j for p in pods)
    ]
    legend_handles.append(
        plt.Line2D([], [], color="red", linestyle="--",
                   label=f"SLO {SLO_US:.0f} us")
    )
    ax.legend(handles=legend_handles, loc="upper center",
              bbox_to_anchor=(0.5, -0.14), fontsize=9,
              ncol=len(legend_handles), frameon=False)

    plt.tight_layout()
    fig.subplots_adjust(bottom=0.18)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out_png, dpi=150)
    plt.close(fig)
    print(f"saved {out_png}  ({drawn} mcperf rows in window, "
          f"{len(pods)} pods, makespan {t_end - t0:.0f}s)")


def main() -> None:
    ap = argparse.ArgumentParser()
    default_repo = Path(__file__).resolve().parents[1]
    ap.add_argument("run_dirs", nargs="*", type=Path,
                    help="default: results/run{11,12,13}_policy_3_2")
    ap.add_argument("--out", type=Path,
                    default=default_repo / "submission" /
                            "part_3_2_results_group_105" / "plots",
                    help="output directory")
    args = ap.parse_args()

    if not args.run_dirs:
        args.run_dirs = [
            default_repo / "results" / "run11_policy_3_2",
            default_repo / "results" / "run12_policy_3_2",
            default_repo / "results" / "run13_policy_3_2",
        ]

    for rd in args.run_dirs:
        out = args.out / f"{rd.name}_p95.png"
        plot_one(rd, out)


if __name__ == "__main__":
    main()

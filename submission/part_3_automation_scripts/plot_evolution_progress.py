#!/usr/bin/env python3
"""
plot_evolution_progress.py [CHECKPOINT_DIR] [--out OUT_DIR]

Reads every program JSON inside CHECKPOINT_DIR/programs/, extracts the
per-iteration metrics (max_p95_us from artifacts_json, violations from
metrics), and produces two line plots showing how the policy evolved
over iterations of OpenEvolve:

  * max p95 memcached latency per iteration (with SLO line at 1000 us)
  * SLO violations per iteration

X-axis uses `iteration_found` (the OpenEvolve iteration that produced
each program). Default CHECKPOINT_DIR points at the Part 3.2 submission
checkpoint (run_4's latest).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt

SLO_US = 1000.0


def load_programs(checkpoint: Path) -> list[dict]:
    rows = []
    for f in sorted((checkpoint / "programs").glob("*.json")):
        j = json.loads(f.read_text())
        m = j.get("metrics", {}) or {}
        arts_raw = j.get("artifacts_json")
        arts = {}
        if isinstance(arts_raw, str) and arts_raw:
            try:
                arts = json.loads(arts_raw)
            except Exception:
                arts = {}
        elif isinstance(arts_raw, dict):
            arts = arts_raw
        try:
            max_p95 = float(arts.get("max_p95_us")) if arts.get("max_p95_us") not in (None, "", "nan") else None
        except ValueError:
            max_p95 = None
        rows.append({
            "iter": j.get("iteration_found") or 0,
            "score": m.get("combined_score"),
            "makespan": m.get("makespan_s"),
            "violations": m.get("violations"),
            "max_p95": max_p95,
            "timeout": bool(m.get("timeout")),
        })
    rows.sort(key=lambda r: r["iter"] or 0)
    return rows


def plot_p95(rows: list[dict], out_png: Path) -> None:
    xs, ys = [], []
    miss_iters = []
    for r in rows:
        if r["max_p95"] is None or r["max_p95"] != r["max_p95"]:  # NaN guard
            miss_iters.append(r["iter"])
            continue
        xs.append(r["iter"])
        ys.append(r["max_p95"])

    fig, ax = plt.subplots(figsize=(11, 5))
    ax.plot(xs, ys, marker="o", linewidth=1.4, color="#1f4e8f",
            label="max p95 (us) per iteration")
    ax.axhline(SLO_US, color="red", linestyle="--", linewidth=1,
               label=f"SLO {SLO_US:.0f} us")
    for it in miss_iters:
        ax.axvline(it, color="grey", linestyle=":", linewidth=0.4, alpha=0.35)
    ax.set_xlabel("OpenEvolve iteration")
    ax.set_ylabel("max p95 memcached latency (us)")
    ax.set_title("Evolution of memcached p95 latency across iterations")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="upper right")
    plt.tight_layout()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out_png, dpi=150)
    plt.close(fig)
    print(f"saved {out_png}  ({len(xs)} iterations with p95; "
          f"{len(miss_iters)} without)")


def plot_violations(rows: list[dict], out_png: Path) -> None:
    xs, ys = [], []
    for r in rows:
        v = r["violations"]
        if v is None:
            continue
        xs.append(r["iter"])
        ys.append(max(0, int(v)))  # clamp the -1 sentinel from _fail() to 0

    fig, ax = plt.subplots(figsize=(11, 4))
    ax.plot(xs, ys, marker="s", linewidth=1.4, color="#c0392b",
            label="SLO violations per iteration")
    ax.set_xlabel("OpenEvolve iteration")
    ax.set_ylabel("mcperf rows with p95 > 1 ms")
    ax.set_title("SLO violations across iterations")
    ax.grid(True, alpha=0.3)
    ax.set_ylim(bottom=-0.5)
    ax.legend(loc="upper right")
    plt.tight_layout()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out_png, dpi=150)
    plt.close(fig)
    print(f"saved {out_png}  ({len(xs)} iterations; "
          f"{sum(1 for y in ys if y > 0)} had violations)")


def main() -> None:
    ap = argparse.ArgumentParser()
    default_repo = Path(__file__).resolve().parents[1]
    default_ck = (default_repo / "submission" /
                  "part_3_2_results_group_105" /
                  "part_3_openevolve" / "checkpoint_latest")
    ap.add_argument("checkpoint", nargs="?", type=Path, default=default_ck)
    ap.add_argument("--out", type=Path,
                    default=default_repo / "submission" /
                            "part_3_2_results_group_105" / "plots")
    args = ap.parse_args()

    rows = load_programs(args.checkpoint)
    if not rows:
        raise SystemExit(f"no programs found under {args.checkpoint}")
    plot_p95(rows, args.out / "evolution_p95.png")
    plot_violations(rows, args.out / "evolution_violations.png")


if __name__ == "__main__":
    main()

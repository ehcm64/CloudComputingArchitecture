#!/usr/bin/env python3
"""
make_interference_heatmap.py
Builds a single figure that combines data from Part 1 (memcached under ibench)
and Part 2a (PARSEC jobs under ibench) into a normalized-slowdown heatmap.

Cell value = (metric under interference X) / (metric alone), where:
  - memcached row: p95 latency at matched QPS (Part 1 csvs)
  - PARSEC rows:   `real` runtime in seconds (Part 2a timing files)

A value of 1.0 means "interference had no effect" → light cell.
Higher values → deeper red → stronger sensitivity to that resource axis.
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
PART1_DIR = REPO_ROOT / "part1" / "data"
PART2A_DIR = REPO_ROOT / "part2" / "data" / "part2a"
OUT_PATH = REPO_ROOT / "part3" / "interference_heatmap.png"

IBENCHES = ["cpu", "l1d", "l1i", "l2", "llc", "membw"]
PARSEC_JOBS = [
    "streamcluster",
    "freqmine",
    "canneal",
    "barnes",
    "vips",
    "blackscholes",
    "radix",
]

TIME_RE = re.compile(r"real\s+(\d+)m([\d.]+)s")


def parse_real_seconds(path: Path) -> float:
    """Extract the `real` time from a PARSEC timing file and return seconds."""
    text = path.read_text()
    m = TIME_RE.search(text)
    if m is None:
        raise ValueError(f"no `real` line in {path}")
    return int(m.group(1)) * 60 + float(m.group(2))


def parsec_slowdowns() -> dict[str, dict[str, float]]:
    """For each job: slowdown[job][ibench] = time_with_ibench / time_alone."""
    out: dict[str, dict[str, float]] = {}
    for job in PARSEC_JOBS:
        base = parse_real_seconds(PART2A_DIR / job / f"{job}.txt")
        out[job] = {}
        for ib in IBENCHES:
            t = parse_real_seconds(PART2A_DIR / job / f"{job}-{ib}.txt")
            out[job][ib] = t / base
    return out


def memcached_p95_at_peak(csv_path: Path) -> float:
    """
    Summary statistic: mean p95 across the top-half rows by achieved_qps.
    This captures steady-state behavior at each ibench's saturation point
    (which differs — e.g. l1d saturates at ~1100 QPS, l1i at ~1500).
    """
    rows = []
    with csv_path.open() as f:
        for row in csv.DictReader(f):
            rows.append((float(row["achieved_qps"]), float(row["p95_latency"])))
    if not rows:
        raise ValueError(f"no rows in {csv_path}")
    rows.sort(key=lambda r: r[0], reverse=True)
    top = rows[: max(1, len(rows) // 2)]
    return float(np.mean([p for _, p in top]))


def memcached_slowdowns() -> dict[str, float]:
    base = memcached_p95_at_peak(PART1_DIR / "result_none.csv")
    return {
        ib: memcached_p95_at_peak(PART1_DIR / f"result_{ib}.csv") / base
        for ib in IBENCHES
    }


def build_matrix() -> tuple[np.ndarray, list[str]]:
    parsec = parsec_slowdowns()
    mc = memcached_slowdowns()

    row_labels = ["memcached (p95)"] + PARSEC_JOBS
    matrix = np.zeros((len(row_labels), len(IBENCHES)))

    matrix[0] = [mc[ib] for ib in IBENCHES]
    for i, job in enumerate(PARSEC_JOBS, start=1):
        matrix[i] = [parsec[job][ib] for ib in IBENCHES]

    return matrix, row_labels


def plot(matrix: np.ndarray, row_labels: list[str]) -> None:
    display = np.maximum(matrix, 1.0)
    vmax = max(float(np.percentile(display, 95)), 1.5)

    fig, ax = plt.subplots(figsize=(8, 6))
    im = ax.imshow(display, aspect="auto", cmap="Reds", vmin=1.0, vmax=vmax)

    ax.set_xticks(range(len(IBENCHES)))
    ax.set_xticklabels([f"ibench-{x}" for x in IBENCHES], rotation=30, ha="right")
    ax.set_yticks(range(len(row_labels)))
    ax.set_yticklabels(row_labels)

    for (i, j), val in np.ndenumerate(matrix):
        shown = max(val, 1.0)
        color = "white" if shown > (1.0 + vmax) / 2 else "black"
        ax.text(j, i, f"{val:.2f}", ha="center", va="center",
                color=color, fontsize=9)

    ax.axhline(0.5, color="black", linewidth=1.5)

    cbar = fig.colorbar(im, ax=ax, shrink=0.8)
    cbar.set_label("Slowdown ratio  (1.0 = no interference; sub-1 clipped as noise)")

    ax.set_title(
        "Interference sensitivity — memcached (Part 1) & PARSEC (Part 2a)\n"
        "Normalized slowdown under each ibench vs no-interference baseline"
    )
    plt.tight_layout()
    plt.savefig(OUT_PATH, dpi=150, bbox_inches="tight")
    print(f"wrote {OUT_PATH}")


def main() -> None:
    matrix, row_labels = build_matrix()
    print("Slowdown matrix (rows = workloads, cols = ibenches):")
    header = "".join(f"{ib:>10}" for ib in IBENCHES)
    print(f"{'':>20}{header}")
    for label, row in zip(row_labels, matrix):
        cells = "".join(f"{v:>10.2f}" for v in row)
        print(f"{label:>20}{cells}")
    plot(matrix, row_labels)


if __name__ == "__main__":
    main()

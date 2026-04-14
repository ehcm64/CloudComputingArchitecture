#!/usr/bin/env python3
"""
Plot 95th percentile latency vs achieved QPS for 7 memcached benchmark configurations.
Each configuration has 5 runs; points are averaged across runs at matching target QPS levels,
with error bars showing standard deviation in both dimensions.

Data source: raw run_*.txt files in each config subdirectory.
Format: whitespace-delimited with columns:
  #type avg std min p5 p10 p50 p67 p75 p80 p85 p90 p95 p99 p999 p9999 QPS target
"""

import os
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

RESULTS_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIGS = {
    "none":  "No interference",
    "cpu":   "CPU",
    "l1d":   "L1d cache",
    "l1i":   "L1i cache",
    "l2":    "L2 cache",
    "llc":   "LLC",
    "membw": "Mem BW",
}

STYLES = [
    dict(color="#1f77b4", marker="o"),
    dict(color="#ff7f0e", marker="s"),
    dict(color="#2ca02c", marker="^"),
    dict(color="#d62728", marker="D"),
    dict(color="#9467bd", marker="v"),
    dict(color="#8c564b", marker="P"),
    dict(color="#e377c2", marker="X"),
]

COLUMNS = [
    "type", "avg", "std", "min", "p5", "p10", "p50", "p67", "p75",
    "p80", "p85", "p90", "p95", "p99", "p999", "p9999", "QPS", "target",
]


def parse_run_file(path):
    """Parse a single run_*.txt file, returning only the 'read' data rows."""
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("read"):
                parts = line.split()
                rows.append(parts)
    df = pd.DataFrame(rows, columns=COLUMNS)
    # Convert numeric columns
    for col in COLUMNS[1:]:
        df[col] = df[col].astype(float)
    return df


fig, ax = plt.subplots(figsize=(10, 6))

num_runs = 5  # all configs have 5 runs

for (key, label), style in zip(CONFIGS.items(), STYLES):
    config_dir = os.path.join(RESULTS_DIR, key)
    all_runs = []
    for r in range(1, num_runs + 1):
        run_df = parse_run_file(os.path.join(config_dir, f"run_{r}.txt"))
        run_df["run"] = r
        all_runs.append(run_df)
    df = pd.concat(all_runs, ignore_index=True)

    # Align by row index within each run (same target QPS sequence)
    df["idx"] = df.groupby("run").cumcount()

    grouped = df.groupby("idx").agg(
        qps_mean=("QPS", "mean"),
        qps_std=("QPS", "std"),
        p95_mean=("p95", "mean"),
        p95_std=("p95", "std"),
    ).reset_index()

    # p95 is in microseconds -> convert to milliseconds
    ax.errorbar(
        grouped["qps_mean"],
        grouped["p95_mean"] / 1000,
        xerr=grouped["qps_std"],
        yerr=grouped["p95_std"] / 1000,
        label=label,
        capsize=3,
        linewidth=1.5,
        markersize=5,
        **style,
    )

ax.set_xlabel("Achieved QPS (queries per second)", fontsize=12)
ax.set_ylabel("95th Percentile Latency (ms)", fontsize=12)
ax.set_title(
    f"Memcached: 95th Percentile Latency vs QPS\n(averaged across {num_runs} runs, error bars = 1 std dev)",
    fontsize=13,
)
ax.set_xlim(0, 80_000)
ax.set_ylim(0, 6)
ax.legend(loc="upper left", fontsize=10)
ax.grid(True, alpha=0.3)

plt.tight_layout()
plt.savefig(os.path.join(RESULTS_DIR, "qps_latency.png"), dpi=200)
print(f"Saved to {os.path.join(RESULTS_DIR, 'qps_latency.png')}")
plt.show()

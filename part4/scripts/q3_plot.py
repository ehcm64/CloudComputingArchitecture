#!/usr/bin/env python3
"""
q3_plot.py [DATA_DIR] [OUT_DIR]

Plots a 3-panel figure for each run:
1. Core allocation (Gantt)
2. QPS (left y) and p95 latency (right y)
3. CPU utilization per core (from cpu_X.csv, falling back to core allocation if missing)
"""
import sys
import matplotlib.pyplot as plt
from matplotlib.patches import Patch
import matplotlib.dates as mdates
from pathlib import Path
from datetime import datetime, timezone

TFMT = "%Y-%m-%dT%H:%M:%S.%f"

# Matching CCA LaTeX colors
JOB_COLORS = {
    "barnes":        "#AACCCA",
    "blackscholes":  "#CCA000",
    "canneal":       "#CCCCAA",
    "freqmine":      "#0CCA00",
    "radix":         "#00CCA0",
    "streamcluster": "#CCACCA",
    "vips":          "#CC0A00",
    "memcached":     "#8888AA",
    "scheduler":     "#000000",
}

def parse_iso(ts: str) -> float:
    try:
        return datetime.strptime(ts, TFMT).replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()

def parse_jobs(path: Path):
    events = []
    lines = path.read_text().splitlines()
    for line in lines:
        parts = line.split()
        if not parts:
            continue
        ts = parse_iso(parts[0])
        event = parts[1]
        job = parts[2]
        cores = []
        if len(parts) > 3 and parts[3].startswith("[") and parts[3].endswith("]"):
            core_str = parts[3][1:-1]
            if core_str:
                cores = [int(c) for c in core_str.split(",")]
        events.append({"ts": ts, "event": event, "job": job, "cores": cores})
    return events

def parse_mcperf(path: Path):
    lines = path.read_text().splitlines()
    start_ts = None
    data = []
    headers = None
    for line in lines:
        if line.startswith("Timestamp start:"):
            start_ts = float(line.split(":")[1].strip()) / 1000.0
        elif line.startswith("#"):
            headers = line.strip().split()
        elif line.startswith("read") and headers:
            toks = line.split()
            try:
                p95 = float(toks[headers.index("p95")])
                qps = float(toks[headers.index("QPS")])
                data.append({"p95": p95, "QPS": qps})
            except (ValueError, IndexError):
                pass
    return start_ts, data

def parse_cpu(path: Path):
    if not path.exists():
        return None
    data = []
    lines = path.read_text().splitlines()
    if not lines:
        return None
    # Skip header
    for line in lines[1:]:
        parts = line.split(",")
        if len(parts) >= 5:
            ts = parse_iso(parts[0])
            cores = [float(c) for c in parts[1:5]]
            data.append({"ts": ts, "cores": cores})
    return data

def build_gantt(events):
    # Returns a list of (job, core, start_time, end_time)
    job_states = {} # job -> {"start": ts, "cores": []}
    gantt = []

    for ev in events:
        ts = ev["ts"]
        job = ev["job"]
        event = ev["event"]

        if job == "scheduler":
            continue

        if event == "start":
            job_states[job] = {"start": ts, "cores": ev.get("cores", [])}
        
        elif event == "update_cores":
            if job in job_states:
                # finish the current segments
                old_start = job_states[job]["start"]
                old_cores = job_states[job]["cores"]
                for c in old_cores:
                    gantt.append((job, c, old_start, ts))
                # update state
                job_states[job]["start"] = ts
                job_states[job]["cores"] = ev.get("cores", [])
        
        elif event == "end":
            if job in job_states:
                old_start = job_states[job]["start"]
                old_cores = job_states[job]["cores"]
                for c in old_cores:
                    gantt.append((job, c, old_start, ts))
                del job_states[job]

    return gantt

def plot_run(run_id, jobs_file, mcperf_file, cpu_file, out_png):
    events = parse_jobs(jobs_file)
    gantt = build_gantt(events)
    mc_start, mc_data = parse_mcperf(mcperf_file)
    cpu_data = parse_cpu(cpu_file)

    if not events:
        return

    # Find total time window based on scheduler start/end
    t0 = events[0]["ts"]
    t_end = events[-1]["ts"]
    duration = t_end - t0

    fig, (ax_gantt, ax_mc, ax_cpu) = plt.subplots(3, 1, figsize=(14, 12), sharex=True,
                                                  gridspec_kw={"height_ratios": [1, 1, 1]})

    # 1. Gantt Chart
    for job, core, start, end in gantt:
        color = JOB_COLORS.get(job, "#888888")
        ax_gantt.barh(core, end - start, left=start - t0, height=0.8,
                      color=color, edgecolor="black", linewidth=0.3)

    ax_gantt.set_yticks([0, 1, 2, 3])
    ax_gantt.set_yticklabels(["Core 0", "Core 1", "Core 2", "Core 3"])
    ax_gantt.invert_yaxis()
    ax_gantt.grid(True, axis="x", alpha=0.3)
    ax_gantt.set_title(f"Run {run_id}: Core Allocations")

    # Legend for Gantt
    jobs_present = sorted(list(set([g[0] for g in gantt])))
    handles = [Patch(facecolor=JOB_COLORS.get(j, "#888"), edgecolor="black", label=j) for j in jobs_present]
    ax_gantt.legend(handles=handles, loc="upper right", fontsize=9, ncol=len(jobs_present))

    # 2. QPS and Latency
    ax_mc2 = ax_mc.twinx()
    
    times = []
    qps = []
    p95 = []
    if mc_start is not None and mc_data:
        for i, d in enumerate(mc_data):
            interval_start = mc_start + i * 15.0
            interval_end = mc_start + (i + 1) * 15.0
            # Align with t0
            t_mid = (interval_start + interval_end) / 2.0 - t0
            times.append(t_mid)
            qps.append(d["QPS"])
            p95.append(d["p95"])
    
    # Plot QPS (left axis)
    l1 = ax_mc.plot(times, qps, color="blue", alpha=0.7, label="QPS", marker=".")
    ax_mc.set_ylabel("QPS", color="blue")
    ax_mc.tick_params(axis="y", labelcolor="blue")
    
    # Plot p95 (right axis)
    l2 = ax_mc2.plot(times, p95, color="red", alpha=0.7, label="p95 Latency", marker="x")
    ax_mc2.set_ylabel("p95 (us)", color="red")
    ax_mc2.tick_params(axis="y", labelcolor="red")
    
    # SLO Line
    l3 = ax_mc2.axhline(800.0, color="orange", linestyle="--", label="SLO (800us)")

    # Combine legends
    lines = l1 + l2 + [l3]
    labels = [l.get_label() for l in lines]
    ax_mc.legend(lines, labels, loc="upper right")
    
    ax_mc.grid(True, alpha=0.3)
    ax_mc.set_title("Memcached Performance")

    # 3. CPU Utilization
    if cpu_data:
        # We have actual CPU usage
        cpu_times = [d["ts"] - t0 for d in cpu_data]
        colors = ["purple", "green", "orange", "brown"]
        for c in range(4):
            usages = [d["cores"][c] for d in cpu_data]
            ax_cpu.plot(cpu_times, usages, color=colors[c], label=f"Core {c}", alpha=0.8)
    else:
        # Fallback to allocations
        for job, core, start, end in gantt:
            color = JOB_COLORS.get(job, "#888888")
            ax_cpu.barh(core, end - start, left=start - t0, height=0.8,
                          color=color, edgecolor="black", linewidth=0.3)
        ax_cpu.set_yticks([0, 1, 2, 3])
        ax_cpu.invert_yaxis()
        ax_cpu.text(0.5, 0.5, "NO CPU LOG FOUND. PLOTTING ALLOCATIONS AS PROXY.", 
                    horizontalalignment='center', verticalalignment='center', transform=ax_cpu.transAxes,
                    fontsize=14, color="gray", alpha=0.5, weight='bold')

    ax_cpu.set_xlabel("Time since scheduler start (s)")
    if cpu_data:
        ax_cpu.set_ylabel("CPU Utilization (%)")
        ax_cpu.set_ylim(0, 105)
        ax_cpu.legend(loc="upper right")
    else:
        ax_cpu.set_ylabel("Cores")
    
    ax_cpu.grid(True, alpha=0.3)
    ax_cpu.set_title("Per-Core CPU Utilization")

    ax_cpu.set_xlim(-10, duration + 10)

    plt.tight_layout()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out_png, dpi=150)
    plt.close()
    print(f"Saved plot to {out_png}")

def main():
    data_dir = Path("part4/data/q3") if len(sys.argv) < 2 else Path(sys.argv[1])
    out_dir = Path("part4/plots") if len(sys.argv) < 3 else Path(sys.argv[2])
    
    if not data_dir.exists():
        print(f"Directory {data_dir} not found.")
        sys.exit(1)

    for jobs_file in sorted(data_dir.glob("jobs_*.txt")):
        run_id = jobs_file.stem[5:]
        mcperf_file = data_dir / f"mcperf_{run_id}.txt"
        
        cpu_file = data_dir / f"cpu_{run_id}.csv"
        
        if mcperf_file.exists():
            out_png = out_dir / f"run_{run_id}.png"
            plot_run(run_id, jobs_file, mcperf_file, cpu_file, out_png)

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
q4_calculate.py [DATA_DIR]

Parses jobs_X.txt and mcperf_X.txt to calculate execution times, makespan, and 
SLO violation ratios across multiple runs.
"""
import sys
import math
from pathlib import Path
from datetime import datetime, timezone

TFMT = "%Y-%m-%dT%H:%M:%S.%f"

def parse_iso(ts: str) -> float:
    try:
        return datetime.strptime(ts, TFMT).replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        # Fallback if no microseconds
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

def calculate_runtimes(events):
    job_runtimes = {}
    job_state = {} # job_name -> {"start_ts": ts, "cores": []}

    first_batch_start = None
    last_batch_end = None

    for ev in events:
        ts = ev["ts"]
        job = ev["job"]
        event = ev["event"]

        if job == "scheduler" or job == "memcached":
            continue

        if event == "start":
            if first_batch_start is None:
                first_batch_start = ts
            job_state[job] = {"start_ts": ts, "cores": ev.get("cores", [])}
            job_runtimes[job] = 0.0
        
        elif event == "update_cores":
            if job in job_state:
                # Add time if it was running (had cores)
                if len(job_state[job]["cores"]) > 0:
                    job_runtimes[job] += ts - job_state[job]["start_ts"]
                job_state[job]["start_ts"] = ts
                job_state[job]["cores"] = ev.get("cores", [])
        
        elif event == "end":
            if job in job_state:
                if len(job_state[job]["cores"]) > 0:
                    job_runtimes[job] += ts - job_state[job]["start_ts"]
                del job_state[job]
                last_batch_end = ts

    makespan = 0.0
    if first_batch_start and last_batch_end:
        makespan = last_batch_end - first_batch_start

    return job_runtimes, makespan, first_batch_start, last_batch_end

def parse_mcperf(path: Path):
    lines = path.read_text().splitlines()
    start_ts = None
    rows = []
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
                rows.append(p95)
            except (ValueError, IndexError):
                pass
    return start_ts, rows

def mean_std(values):
    if not values:
        return 0.0, 0.0
    mean = sum(values) / len(values)
    var = sum((x - mean) ** 2 for x in values) / len(values)
    return mean, math.sqrt(var)

def main():
    data_dir = Path("part4/data/q4") if len(sys.argv) < 2 else Path(sys.argv[1])
    
    if not data_dir.exists():
        print(f"Directory {data_dir} not found.")
        sys.exit(1)

    all_job_times = {} # job -> [time1, time2, ...]
    all_makespans = []
    all_slo_ratios = []

    # Find runs by looking for jobs_X.txt
    runs = []
    for jobs_file in data_dir.glob("jobs_*.txt"):
        run_id = jobs_file.stem[5:]
        mcperf_file = data_dir / f"mcperf_{run_id}.txt"
        if mcperf_file.exists():
            runs.append((jobs_file, mcperf_file))
    
    if not runs:
        print("No paired jobs_X.txt and mcperf_X.txt found.")
        sys.exit(1)

    runs.sort(key=lambda x: x[0].name)

    for jobs_file, mcperf_file in runs:
        events = parse_jobs(jobs_file)
        runtimes, makespan, batch_start, batch_end = calculate_runtimes(events)
        
        for j, t in runtimes.items():
            all_job_times.setdefault(j, []).append(t)
        if makespan > 0:
            all_makespans.append(makespan)

        # Parse mcperf and calculate SLO
        mc_start, mc_p95 = parse_mcperf(mcperf_file)
        if mc_start and batch_start and batch_end:
            violations = 0
            total = 0
            for i, p95 in enumerate(mc_p95):
                interval_start = mc_start + i * 15.0
                interval_end = mc_start + (i + 1) * 15.0
                
                # Check if this interval overlaps with the batch processing window
                # Assuming "time from when the first batch-job starts to when the last stops"
                if interval_end > batch_start and interval_start < batch_end:
                    total += 1
                    if p95 > 800.0:
                        violations += 1
            ratio = violations / total if total > 0 else 0
            all_slo_ratios.append(ratio)
        else:
            all_slo_ratios.append(0.0)

    # Print Table
    print(f"{'job name':<18} {'mean time [s]':<15} {'std [s]':<15}")
    print("-" * 50)
    
    # Sort jobs as in the prompt
    target_jobs = ["barnes", "blackscholes", "canneal", "freqmine", "radix", "streamcluster", "vips"]
    
    for job in target_jobs:
        if job in all_job_times:
            m, s = mean_std(all_job_times[job])
            print(f"{job:<18} {m:<15.2f} {s:<15.2f}")
        else:
            print(f"{job:<18} {'-':<15} {'-':<15}")
    
    # Makespan
    m, s = mean_std(all_makespans)
    print(f"{'total time':<18} {m:<15.2f} {s:<15.2f}")
    
    print("\nSLO Violation Ratios (p95 > 0.8ms during batch execution):")
    for i, ratio in enumerate(all_slo_ratios):
        print(f"Run {i+1}: {ratio:.2%} ({ratio:.4f})")

if __name__ == "__main__":
    main()

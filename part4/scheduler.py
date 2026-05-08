#!/usr/bin/env python3
import subprocess
import time
import csv
import psutil
from scheduler_logger import SchedulerLogger, Job

PARSEC_JOBS = [
    ##slow
    "parsec_freqmine",
    "parsec_streamcluster",
    ##medium
    "parsec_blackscholes",
    "parsec_canneal",
    ##fast
    "parsec_radix",
    "parsec_barnes",
    "parsec_vips",
]

JOB_IMAGE = {
    "parsec_radix":         "anakli/cca:splash2x_radix",
    "parsec_streamcluster": "anakli/cca:parsec_streamcluster",
    "parsec_vips":          "anakli/cca:parsec_vips",
    "parsec_barnes":        "anakli/cca:splash2x_barnes",
    "parsec_blackscholes":  "anakli/cca:parsec_blackscholes",
    "parsec_canneal":       "anakli/cca:parsec_canneal",
    "parsec_freqmine":      "anakli/cca:parsec_freqmine",
}


JOB_TO_LOGGER = {
    "parsec_blackscholes":  Job.BLACKSCHOLES,
    "parsec_canneal":       Job.CANNEAL,
    "parsec_freqmine":      Job.FREQMINE,
    "parsec_radix":         Job.RADIX,
    "parsec_streamcluster": Job.STREAMCLUSTER,
    "parsec_vips":          Job.VIPS,
    "parsec_barnes":        Job.BARNES,
}

JOB_THREADS = {  ## we just give 2 well use 2 cores
    "parsec_blackscholes":  2,
    "parsec_canneal":       2,
    "parsec_freqmine":      2,
    "parsec_radix":         2,
    "parsec_streamcluster": 2,
    "parsec_vips":          2,
    "parsec_barnes":        2,
}


state = {
    "memcached_cores": [0, 1, 2], # start with 3 cores maybe first load is high
    "parsec_cores": [3],
    "current_parsec": None,         
    "job_queue": list(PARSEC_JOBS), 
    "completed_jobs": [],
}


CPU_HIGH = 140.0
# CPU_LOW = 70.0
# TIME = 5

def get_memcached_pid():
    try:
        result = subprocess.check_output(["pgrep", "-o", "memcached"]).decode().strip()
        if not result:
            print("ERROR: memcached not running")
            exit(1)
        return result
    except subprocess.CalledProcessError:
        print("ERROR: memcached not running")
        exit(1)

def set_cpu_affinity(pid, cores):
    """Pin ALL threads of a process to specific CPU cores."""
    core_list = ",".join(str(c) for c in cores)
    subprocess.check_call(
        ["sudo", "taskset", "-a", "-cp", core_list, pid]
    )

def get_memcached_cpu(pid: str) -> float:
    """
    Returns CPU usage (%) of memcached process.
    """
    try:
        p = psutil.Process(int(pid))
        p.cpu_percent(interval=None)
        cpu = p.cpu_percent(interval=0.1)

        return float(cpu)

    except psutil.NoSuchProcess:
        print("ERROR: memcached process not found")
        return -1.0


def start_parsec(job_name, image, cores, threads):

    core_str = ",".join(str(c) for c in cores)

    cmd = [
        "sudo", "docker", "run",
        "--cpuset-cpus=" + core_str,
        "-d",
        "--rm",
        "--name", job_name,
        image,
        "./run", "-a", "run",
        "-S", "parsec",
        "-p", job_name.replace("parsec_", ""),
        "-i", "native",
        "-n", str(threads)
    ]

    print(f"[START] {job_name} on cores {core_str}")
    subprocess.check_call(cmd)


def set_job_cores(container_name, cores):
    core_str = ",".join(str(c) for c in cores)

    subprocess.check_call([
        "sudo", "docker", "update",
        "--cpuset-cpus=" + core_str,
        container_name
    ])


def is_job_running(container_name):
    result = subprocess.run(
        ["sudo", "docker", "inspect", "-f", "{{.State.Running}}", container_name],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        return False
    return result.stdout.strip() == "true"


def policy2(cpu, state, memcached_pid, logger):
    job   = state["current_parsec"]
    queue = state["job_queue"]

    # ── 1. Adjust memcached cores based on CPU pressure ───────────────────
    n_mem_cores = len(state["memcached_cores"])

    if cpu > CPU_HIGH and n_mem_cores != 3:
        # Give memcached one more core, shrink batch allocation
        new_mem_cores   = list([0, 1, 2])
        new_parsec_cores = list([3])
        print(f"[DECISION] CPU {cpu:.1f}% new memcached cores: {new_mem_cores}")
        set_cpu_affinity(memcached_pid, new_mem_cores)
        logger.update_cores(Job.MEMCACHED, new_mem_cores)
        state["memcached_cores"] = new_mem_cores
        state["parsec_cores"] = new_parsec_cores

        if job:
            set_job_cores(job, new_parsec_cores)
            logger.update_cores(JOB_TO_LOGGER[job], new_parsec_cores)


    elif cpu < CPU_HIGH and n_mem_cores != 2:
        # Give memcached one more core, shrink batch allocation
        new_mem_cores   = list([0, 1])   
        new_parsec_cores = list([2, 3]) 
        print(f"[DECISION] CPU {cpu:.1f}% new memcached cores: {new_mem_cores}")
        set_cpu_affinity(memcached_pid, new_mem_cores)
        logger.update_cores(Job.MEMCACHED, new_mem_cores)
        state["memcached_cores"] = new_mem_cores
        state["parsec_cores"]     = new_parsec_cores
        
        if job:
            set_job_cores(job, new_parsec_cores)
            logger.update_cores(JOB_TO_LOGGER[job], new_parsec_cores)
    

    # check job is finished
    if job and not is_job_running(job):
        print(f"[DECISION] {job} finished")
        logger.job_end(JOB_TO_LOGGER[job])
        state["completed_jobs"].append(job)
        state["current_parsec"] = None
        job = None

    # check next job
    if job is None and queue:
        next_job = queue.pop(0)
        n_threads = 2; 
        cores = state["parsec_cores"][:n_threads]
        print(f"[DECISION] Starting {next_job} on cores {cores}")
        start_parsec(next_job, JOB_IMAGE[next_job], cores, threads=n_threads)
        logger.job_start(JOB_TO_LOGGER[next_job], cores, n_threads)
        state["current_parsec"] = next_job

def take_decision(cpu, state, memcached_pid, logger):
    policy2(cpu, state, memcached_pid, logger)
    print(f"Cpu utilizzata = {cpu}")


def main():
    logger = SchedulerLogger()

    memcached_pid = get_memcached_pid()

    memcached_cores = [0, 1, 2]  ### CHANGED FOR TESTS
    set_cpu_affinity(memcached_pid, memcached_cores)

    logger.job_start(Job.MEMCACHED, memcached_cores, initial_threads=2)
  
    try:
        while True:
            cpu = get_memcached_cpu(memcached_pid)
            take_decision(cpu, state, memcached_pid, logger)
            time.sleep(2)

    except KeyboardInterrupt:
        print("Stopping scheduler...!!!!")

    finally:
        logger.job_end(Job.MEMCACHED)
        logger.end()
        print(f"Log written to: {logger.get_file_name()}")

if __name__ == "__main__":
    main()
"""OpenEvolve evaluator

Given an evolved scheduler.yaml, this function:
  1. renders each inline job definition into a real Kubernetes Job YAML,
  2. executes the `schedule:` DSL against the live cluster,
  3. measures makespan and memcached SLO violations,
  4. returns a single `combined_score` to OpenEvolve (higher = better).

Assumptions (set up once by the user before evolution starts):
  - The Part 3 kops cluster is up and `kubectl` is configured for it.
  - Memcached is already running on node-b-4core (yaml/memcached.yaml).
  - A continuous mcperf loop writes to MCPERF_REMOTE_PATH on client-measure.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import yaml

from openevolve.evaluation_result import EvaluationResult

# Allow importing Part 3.1 helpers without duplicating their logic.
# This file lives at part3/openevolve/run_N/evaluator.py, so parents[2] = part3/.
_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
from slo_violations import parse_mcperf, SLO_US  # noqa: E402

# --- Fixed project knobs -----------------------------------------------------

EXPECTED_JOBS = {
    "parsec-barnes",
    "parsec-blackscholes",
    "parsec-canneal",
    "parsec-freqmine",
    "parsec-radix",
    "parsec-streamcluster",
    "parsec-vips",
}
ALLOWED_NODES = {"node-a-8core", "node-b-4core"}
ALLOWED_SUITES = {"parsec", "splash2x"}
FORBIDDEN_PLACEMENTS = {("parsec-radix", "node-b-4core")}

WAIT_TIMEOUT_S = 2000            # per kubectl wait (never normally hit; we abort first)
OVERALL_TIMEOUT_S = 30 * 60      # hard cap on one evaluation
# Soft deadline must be under OpenEvolve's `evaluator.timeout` in config.yaml.
# When we cross this, we abort the join loop, assemble whatever stuck_jobs / excess
# we can see, and return a FINITE combined_score. This guarantees the LLM always
# sees a real score + artifacts — the wrapper timeout is never allowed to fire.
SOFT_DEADLINE_S = 500
ZONE = os.environ.get("CCA_ZONE", "europe-west1-b")
SSH_KEY = os.environ.get(
    "CCA_SSH_KEY", str(Path.home() / ".ssh" / "cloud-computing")
)
MCPERF_REMOTE_PATH = os.environ.get(
    "CCA_MCPERF_PATH", "/home/ubuntu/mcperf_continuous.txt"
)
INFEASIBLE_SCORE = -1_000_000.0
SLO_PENALTY = 10_000.0

# Solo per-job baseline runtime (seconds) from Part 3 baseline_times.txt.
# Used to compute per-job `excess_s = max(0, observed - baseline)`, which is
# summed into combined_score so the LLM gets a gradient that says WHICH jobs
# were slowed by interference/starvation, not just "schedule was slow".
# Measurements were taken with memcached idle, so node-b entries are slightly
# optimistic under 30K-QPS load — accept the bias (it nudges toward node-a).
BASELINE_S = {
    ("parsec-streamcluster", "node-a-8core"): 124.0,
    ("parsec-streamcluster", "node-b-4core"): 209.0,
    ("parsec-freqmine",      "node-a-8core"): 110.0,
    ("parsec-freqmine",      "node-b-4core"): 213.0,
    ("parsec-canneal",       "node-a-8core"):  98.0,
    ("parsec-canneal",       "node-b-4core"): 118.0,
    ("parsec-barnes",        "node-a-8core"):  32.0,
    ("parsec-barnes",        "node-b-4core"):  55.0,
    ("parsec-vips",          "node-a-8core"):  28.0,
    ("parsec-vips",          "node-b-4core"):  54.0,
    ("parsec-blackscholes",  "node-a-8core"):  46.0,
    ("parsec-blackscholes",  "node-b-4core"):  39.0,
    ("parsec-radix",         "node-a-8core"):  11.0,
    # radix on node-b = OOM (forbidden placement, rejected in _validate)
}

_TEMPLATE = (Path(__file__).resolve().parent / "job_template.yaml").read_text()
_TFMT = "%Y-%m-%dT%H:%M:%SZ"
# Cross-iteration state: previous iteration's per-job run_s, used to compute
# the Δrun (s) column so the LLM sees which pod regressed/sped up.
_STATE_PATH = Path(tempfile.gettempdir()) / "oe_run4_last.json"


# --- Small helpers -----------------------------------------------------------

def _log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[evaluator {ts}] {msg}", file=sys.stderr, flush=True)


_CONFIG_DUMPED = False


def _dump_config_once() -> None:
    global _CONFIG_DUMPED
    if _CONFIG_DUMPED:
        return
    _CONFIG_DUMPED = True
    cfg_path = Path(__file__).resolve().parent / "config.yaml"
    try:
        cfg = yaml.safe_load(cfg_path.read_text())
    except Exception as e:
        _log(f"could not read config.yaml: {e}")
        return
    llm = cfg.get("llm", {}) or {}
    prompt = cfg.get("prompt", {}) or {}
    db = cfg.get("database", {}) or {}
    ev = cfg.get("evaluator", {}) or {}
    _log("=== OpenEvolve config (from config.yaml) ===")
    _log(f"  max_iterations       = {cfg.get('max_iterations')}")
    _log(f"  checkpoint_interval  = {cfg.get('checkpoint_interval')}")
    _log(f"  diff_based_evolution = {cfg.get('diff_based_evolution')}")
    _log(f"  max_code_length      = {cfg.get('max_code_length')}")
    _log(f"  llm.api_base         = {llm.get('api_base')}")
    _log(f"  llm.primary_model    = {llm.get('primary_model')}")
    _log(f"  llm.top_p            = {llm.get('top_p')}")
    _log(f"  llm.temperature      = {llm.get('temperature')}")
    _log(f"  prompt.template_dir  = {prompt.get('template_dir')}")
    _log(f"  prompt.system_message= {prompt.get('system_message')}")
    _log(f"  database.population  = {db.get('population_size')}")
    _log(f"  database.num_islands = {db.get('num_islands')}")
    _log(f"  evaluator.parallel   = {ev.get('parallel_evaluations')}")
    _log(f"  evaluator.cascade    = {ev.get('cascade_evaluation')}")
    _log("=== evaluator environment ===")
    _log(f"  CCA_ZONE             = {ZONE}")
    _log(f"  CCA_SSH_KEY          = {SSH_KEY}")
    _log(f"  CCA_MCPERF_PATH      = {MCPERF_REMOTE_PATH}")
    _log(f"  OVERALL_TIMEOUT_S    = {OVERALL_TIMEOUT_S}")
    _log(f"  WAIT_TIMEOUT_S       = {WAIT_TIMEOUT_S}")
    _log("=============================================")


def _run(cmd: list[str], timeout: float | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, check=False, capture_output=True, text=True, timeout=timeout
    )


def _parse_iso_utc(ts: str) -> float:
    return datetime.strptime(ts, _TFMT).replace(tzinfo=timezone.utc).timestamp()


def _fail(reason: str, extra: dict | None = None) -> EvaluationResult:
    metrics = {
        "combined_score": INFEASIBLE_SCORE,
        "makespan_s": 0.0,
        "violations": -1.0,
        "slo_rows_in_window": 0.0,
        "total_excess_s": 0.0,
    }
    artifacts = {"error": reason}
    if extra:
        artifacts.update({k: str(v) for k, v in extra.items()})
    return EvaluationResult(metrics=metrics, artifacts=artifacts)


# --- Validation --------------------------------------------------------------

def _validate(spec: dict) -> tuple[dict[str, dict], list[tuple[str, str]]]:
    """Return (jobs, parsed_actions). Raise ValueError on any rule violation."""
    if not isinstance(spec, dict):
        raise ValueError("top-level YAML is not a mapping")

    jobs = spec.get("jobs")
    schedule = spec.get("schedule")
    if not isinstance(jobs, dict):
        raise ValueError("`jobs` must be a mapping")
    if not isinstance(schedule, list):
        raise ValueError("`schedule` must be a list")

    if set(jobs.keys()) != EXPECTED_JOBS:
        missing = EXPECTED_JOBS - set(jobs.keys())
        extra = set(jobs.keys()) - EXPECTED_JOBS
        raise ValueError(f"jobs keys mismatch: missing={missing} extra={extra}")

    for name, j in jobs.items():
        node = j.get("node")
        if node not in ALLOWED_NODES:
            raise ValueError(f"{name}: node `{node}` not in {ALLOWED_NODES}")
        if (name, node) in FORBIDDEN_PLACEMENTS:
            raise ValueError(f"{name} may not run on {node}")
        if j.get("suite") not in ALLOWED_SUITES:
            raise ValueError(f"{name}: suite `{j.get('suite')}` invalid")
        try:
            threads = int(j["threads"])
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"{name}: threads must be an int")
        if threads < 1 or threads > 16:
            raise ValueError(f"{name}: threads {threads} out of range [1,16]")
        for k in ("cpu_request", "cpu_limit", "memory_request", "memory_limit",
                  "image", "benchmark"):
            if not isinstance(j.get(k), str) or not j[k]:
                raise ValueError(f"{name}: `{k}` must be a non-empty string")
        if not isinstance(j.get("cpuset", ""), str):
            raise ValueError(f"{name}: `cpuset` must be a string")

    actions: list[tuple[str, str]] = []
    submitted: list[str] = []
    for i, step in enumerate(schedule):
        if not isinstance(step, str):
            raise ValueError(f"schedule[{i}] must be a string, got {type(step)}")
        toks = step.strip().split()
        if len(toks) != 2:
            raise ValueError(f"schedule[{i}] `{step}` must have exactly 2 tokens")
        verb, arg = toks
        if verb == "submit":
            if arg not in jobs:
                raise ValueError(f"schedule[{i}] submits unknown job `{arg}`")
            if arg in submitted:
                raise ValueError(f"schedule[{i}] submits `{arg}` twice")
            submitted.append(arg)
            actions.append(("submit", arg))
        elif verb == "sleep":
            try:
                s = float(arg)
            except ValueError:
                raise ValueError(f"schedule[{i}] sleep arg `{arg}` not a number")
            if s < 0 or s > 600:
                raise ValueError(f"schedule[{i}] sleep {s}s out of range [0,600]")
            actions.append(("sleep", arg))
        else:
            raise ValueError(f"schedule[{i}] unknown verb `{verb}`")

    if set(submitted) != EXPECTED_JOBS:
        raise ValueError(
            f"schedule must submit every job exactly once; got {submitted}"
        )

    return jobs, actions


# --- Rendering + cluster interaction -----------------------------------------

def _render_job(name: str, j: dict) -> str:
    cpuset = (j.get("cpuset") or "").strip()
    prefix = f"taskset -c {cpuset} " if cpuset else ""
    return _TEMPLATE.format_map({
        "name": name,
        "image": j["image"],
        "prefix": prefix,
        "suite": j["suite"],
        "benchmark": j["benchmark"],
        "threads": int(j["threads"]),
        "cpu_request": j["cpu_request"],
        "cpu_limit": j["cpu_limit"],
        "memory_request": j["memory_request"],
        "memory_limit": j["memory_limit"],
        "node": j["node"],
    })


def _cleanup_jobs() -> None:
    _run(["kubectl", "delete", "jobs", "--all",
          "--ignore-not-found=true", "--wait=true"], timeout=120)


def _apply_job(yaml_text: str) -> None:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False
    ) as f:
        f.write(yaml_text)
        path = f.name
    try:
        r = _run(["kubectl", "apply", "-f", path], timeout=60)
        if r.returncode != 0:
            raise RuntimeError(
                f"kubectl apply failed: {r.stderr.strip() or r.stdout.strip()}"
            )
    finally:
        os.unlink(path)


def _wait_job(name: str, remaining_s: float) -> None:
    timeout = max(1.0, min(WAIT_TIMEOUT_S, remaining_s))
    r = _run(
        [
            "kubectl", "wait", f"--for=condition=complete",
            f"job/{name}", f"--timeout={int(timeout)}s",
        ],
        timeout=timeout + 30,
    )
    if r.returncode != 0:
        # Distinguish between timeout/pending and actual failure (e.g., OOM).
        fr = _run(
            ["kubectl", "wait", "--for=condition=failed",
             f"job/{name}", "--timeout=1s"],
            timeout=10,
        )
        if fr.returncode == 0:
            raise RuntimeError(f"job {name} failed")
        raise RuntimeError(
            f"kubectl wait on {name} did not complete: "
            f"{r.stderr.strip() or r.stdout.strip()}"
        )


def _get_pods_json() -> dict:
    r = _run(["kubectl", "get", "pods", "-o", "json"], timeout=60)
    if r.returncode != 0:
        raise RuntimeError(f"kubectl get pods failed: {r.stderr}")
    return json.loads(r.stdout)


def _job_window(pods: dict) -> tuple[float, float]:
    starts, ends = [], []
    for item in pods.get("items", []):
        cs_list = item["status"].get("containerStatuses") or []
        if not cs_list:
            continue
        cs = cs_list[0]
        if cs.get("name") == "memcached":
            continue
        term = cs.get("state", {}).get("terminated")
        if not term:
            continue
        starts.append(_parse_iso_utc(term["startedAt"]))
        ends.append(_parse_iso_utc(term["finishedAt"]))
    if not starts:
        raise RuntimeError("no terminated batch containers found")
    return min(starts), max(ends)


def _per_job_summary(
    pods: dict,
    makespan_s: float | None = None,
    prev_runs: dict[str, float] | None = None,
) -> tuple[str, list[str], dict[str, float]]:
    """Build a text table of per-pod timing matching the Part 3.1 summarize_run.py format.

    Columns: job | node | wait (s) | run (s) | total (s) | [Δrun (s)]
    A trailing 'MAKESPAN (wall)' row shows the wall-clock span used for scoring.
    If `prev_runs` is provided, adds a Δrun (s) column with the delta vs the
    previous iteration's run_s for the same job (signed, "+" for regressions).

    Returns (table_text, problem_jobs, current_runs) where current_runs is
    a dict {job_name: run_s} suitable for persisting as next iteration's prev_runs.
    """
    rows: list[dict] = []
    problems: list[str] = []
    current_runs: dict[str, float] = {}

    for item in pods.get("items", []):
        meta = item.get("metadata", {})
        name = meta.get("name", "?")
        labels = meta.get("labels", {}) or {}
        job_name = labels.get("job-name") or labels.get("name") or name
        if job_name == "some-memcached" or "memcached" in name:
            continue
        created = meta.get("creationTimestamp")
        node = item.get("spec", {}).get("nodeName", "?")
        cs_list = item.get("status", {}).get("containerStatuses") or []
        if not cs_list:
            rows.append({"job": job_name, "node": node,
                         "wait": "-", "run": "-", "total": "-",
                         "run_raw": None, "reason": "no-container-status"})
            problems.append(job_name)
            continue
        cs = cs_list[0]
        state = cs.get("state", {}) or {}
        if "terminated" in state:
            term = state["terminated"]
            reason = term.get("reason", "")
            exit_code = term.get("exitCode", "?")
            try:
                s = _parse_iso_utc(term["startedAt"])
                f = _parse_iso_utc(term["finishedAt"])
                c = _parse_iso_utc(created) if created else s
                wait_v = s - c
                run_v = f - s
                current_runs[job_name] = run_v
                wait = f"{wait_v:.1f}"
                run = f"{run_v:.1f}"
                total = f"{wait_v + run_v:.1f}"
                run_raw = run_v
            except Exception:
                wait = run = total = "-"
                run_raw = None
            if exit_code != 0 or reason not in ("Completed", ""):
                problems.append(f"{job_name}({reason or 'exit='+str(exit_code)})")
            rows.append({"job": job_name, "node": node, "wait": wait,
                         "run": run, "total": total, "run_raw": run_raw,
                         "reason": reason or "Completed"})
        elif "waiting" in state:
            w = state["waiting"]
            rows.append({"job": job_name, "node": node,
                         "wait": "-", "run": "-", "total": "-",
                         "run_raw": None,
                         "reason": f"Waiting({w.get('reason', '?')})"})
            problems.append(f"{job_name}(waiting:{w.get('reason', '?')})")
        elif "running" in state:
            rows.append({"job": job_name, "node": node,
                         "wait": "-", "run": "-", "total": "-",
                         "run_raw": None, "reason": "Running"})
            problems.append(f"{job_name}(still-running)")

    if not rows:
        return "(no batch pods found)", problems, current_runs

    show_delta = prev_runs is not None and any(r["run_raw"] is not None for r in rows)
    if show_delta:
        header = (f"{'job':<24}{'node':<20}{'wait (s)':>10}{'run (s)':>10}"
                  f"{'total (s)':>12}{'Δrun (s)':>10}")
    else:
        header = (f"{'job':<24}{'node':<20}{'wait (s)':>10}{'run (s)':>10}"
                  f"{'total (s)':>12}")
    sep = "-" * len(header)
    lines = [header, sep]
    for r in sorted(rows, key=lambda r: r["job"]):
        line = (f"{r['job']:<24}{r['node']:<20}"
                f"{r['wait']:>10}{r['run']:>10}{r['total']:>12}")
        if show_delta:
            if r["run_raw"] is not None and prev_runs and r["job"] in prev_runs:
                delta = r["run_raw"] - prev_runs[r["job"]]
                sign = "+" if delta >= 0 else ""
                line += f"{sign}{delta:>.1f}".rjust(10)
            else:
                line += f"{'-':>10}"
        lines.append(line)
    lines.append(sep)
    if makespan_s is not None:
        mk_line = f"{'MAKESPAN (wall)':<76}{makespan_s:>10.1f}"
        lines.append(mk_line)
    return "\n".join(lines), problems, current_runs


def _observed_s_per_job(pods: dict, now_s: float) -> dict[str, dict]:
    """Return {job_name: {'node': ..., 'observed_s': ..., 'state': 'Completed'|'Running'|'Pending'}}.
    `observed_s` is the pod's actual run duration if Terminated, or elapsed-since-creation
    if still Pending/Running (so Pending counts as wasted time too)."""
    out: dict[str, dict] = {}
    for item in pods.get("items", []):
        meta = item.get("metadata", {}) or {}
        name = meta.get("name", "?")
        if "memcached" in name:
            continue
        labels = meta.get("labels", {}) or {}
        job_name = labels.get("job-name") or labels.get("name") or name
        if job_name not in EXPECTED_JOBS:
            continue
        node = item.get("spec", {}).get("nodeName", "?")
        created = meta.get("creationTimestamp")
        cs_list = item.get("status", {}).get("containerStatuses") or []
        cs = cs_list[0] if cs_list else {}
        state = (cs.get("state", {}) or {})
        if "terminated" in state:
            t = state["terminated"]
            try:
                s = _parse_iso_utc(t["startedAt"])
                f = _parse_iso_utc(t["finishedAt"])
                observed = f - s
            except Exception:
                observed = 0.0
            st = "Completed" if t.get("reason", "") in ("Completed", "") and t.get("exitCode", 0) == 0 else f"Failed({t.get('reason','?')})"
        elif "running" in state:
            try:
                s = _parse_iso_utc(state["running"]["startedAt"])
                observed = now_s - s
            except Exception:
                observed = 0.0
            st = "Running"
        else:
            # Pending (ContainerCreating, ImagePullBackOff, …). Count time since submit
            # so the LLM sees "this pod never got CPU" as wasted time.
            try:
                c = _parse_iso_utc(created) if created else now_s
                observed = max(0.0, now_s - c)
            except Exception:
                observed = 0.0
            st = "Pending"
        out[job_name] = {"node": node, "observed_s": observed, "state": st}
    return out


def _compute_excess(per_job: dict[str, dict]) -> tuple[dict[str, float], float]:
    """Per-job excess_s = max(0, observed - baseline(job, node)).
    Returns (per_job_excess, total_excess). Jobs missing from BASELINE_S
    (e.g. radix on node-b, which is forbidden anyway) are skipped silently."""
    excess: dict[str, float] = {}
    for job, info in per_job.items():
        base = BASELINE_S.get((job, info.get("node", "")))
        if base is None:
            excess[job] = 0.0
            continue
        excess[job] = max(0.0, info["observed_s"] - base)
    return excess, sum(excess.values())


def _slowness_table(per_job: dict[str, dict], excess: dict[str, float]) -> str:
    """Per-pod slowness breakdown: observed / baseline / ratio / excess / state."""
    if not per_job:
        return "(no batch pods found)"
    header = (f"{'job':<24}{'node':<20}{'observed':>10}"
              f"{'baseline':>10}{'ratio':>8}{'excess':>10}  state")
    sep = "-" * len(header)
    lines = [header, sep]
    for job in sorted(per_job.keys()):
        info = per_job[job]
        node = info.get("node", "?")
        obs = info["observed_s"]
        base = BASELINE_S.get((job, node))
        base_s = f"{base:.0f}" if base else "-"
        ratio = f"{obs/base:.2f}x" if base else "-"
        exc = excess.get(job, 0.0)
        lines.append(f"{job:<24}{node:<20}{obs:>10.0f}"
                     f"{base_s:>10}{ratio:>8}{exc:>10.0f}  {info['state']}")
    return "\n".join(lines)


def _load_prev_runs() -> dict[str, float]:
    try:
        return {k: float(v) for k, v in json.loads(_STATE_PATH.read_text()).items()}
    except Exception:
        return {}


def _save_prev_runs(runs: dict[str, float]) -> None:
    try:
        _STATE_PATH.write_text(json.dumps(runs))
    except Exception as e:
        _log(f"could not persist prev_runs: {e}")


def _oom_pods(pods: dict) -> list[str]:
    """Return one-line descriptions of pods whose container was OOMKilled.

    Checks both the current `terminated` state and the previous `lastState`,
    so a pod that was OOMKilled and retried (or ended up in a non-0 exit) is
    still surfaced.
    """
    out: list[str] = []
    for item in pods.get("items", []):
        meta = item.get("metadata", {}) or {}
        name = meta.get("name", "?")
        if "memcached" in name:
            continue
        labels = meta.get("labels", {}) or {}
        job_name = labels.get("job-name") or labels.get("name") or name
        for cs in item.get("status", {}).get("containerStatuses") or []:
            for probe in (
                cs.get("state", {}).get("terminated"),
                cs.get("lastState", {}).get("terminated"),
            ):
                if probe and probe.get("reason") == "OOMKilled":
                    try:
                        s = _parse_iso_utc(probe["startedAt"])
                        f = _parse_iso_utc(probe["finishedAt"])
                        runtime = f"{f - s:.1f}s"
                    except Exception:
                        runtime = "?"
                    out.append(f"{job_name} (OOMKilled after {runtime})")
                    break
    return out


def _client_measure_node_name() -> str:
    r = _run(["kubectl", "get", "nodes", "-o", "json"], timeout=30)
    if r.returncode != 0:
        raise RuntimeError(f"kubectl get nodes failed: {r.stderr}")
    for item in json.loads(r.stdout).get("items", []):
        labels = item["metadata"].get("labels", {})
        if labels.get("cca-project-nodetype") == "client-measure":
            return item["metadata"]["name"]
    raise RuntimeError("no node labelled client-measure found")


def _fetch_mcperf(dest: Path) -> None:
    node = _client_measure_node_name()
    r = _run(
        [
            "gcloud", "compute", "scp",
            "--ssh-key-file", SSH_KEY,
            "--zone", ZONE,
            f"ubuntu@{node}:{MCPERF_REMOTE_PATH}",
            str(dest),
        ],
        timeout=120,
    )
    if r.returncode != 0:
        raise RuntimeError(
            f"gcloud scp of mcperf file failed: {r.stderr.strip()}"
        )


# --- Main entry point --------------------------------------------------------

def evaluate(program_path: str) -> EvaluationResult:
    _dump_config_once()
    start_wall = time.time()
    _log(f"starting evaluation of {program_path}")
    try:
        spec = yaml.safe_load(Path(program_path).read_text())
        jobs, actions = _validate(spec)
        _log(f"parsed and validated: {len(jobs)} jobs, {len(actions)} actions")
    except Exception as e:  # YAML parse or validation error
        _log(f"validation FAILED: {e}")
        return _fail(f"invalid scheduler.yaml: {e}")

    try:
        _log("cleaning up previous jobs")
        _cleanup_jobs()
    except Exception as e:
        _log(f"cleanup FAILED: {e}")
        return _fail(f"pre-run cleanup failed: {e}")

    submitted: list[str] = []
    soft_deadline_hit = False
    try:
        _log(f"executing schedule ({len(actions)} actions)")
        for i, (verb, arg) in enumerate(actions, 1):
            remaining = OVERALL_TIMEOUT_S - (time.time() - start_wall)
            if remaining <= 0:
                raise TimeoutError("overall evaluation timeout reached")
            _log(f"  [{i}/{len(actions)}] {verb} {arg} "
                 f"(elapsed {int(time.time() - start_wall)}s)")
            if verb == "submit":
                _apply_job(_render_job(arg, jobs[arg]))
                submitted.append(arg)
            elif verb == "sleep":
                time.sleep(min(float(arg), max(1.0, remaining - 5)))

        _log(f"joining {len(submitted)} submitted job(s)")
        for name in submitted:
            elapsed = time.time() - start_wall
            budget = SOFT_DEADLINE_S - elapsed
            if budget <= 0:
                soft_deadline_hit = True
                _log(f"  SOFT DEADLINE hit (elapsed {elapsed:.0f}s) - skipping remaining waits")
                break
            _log(f"  waiting on {name} (soft-budget {int(budget)}s remaining)")
            try:
                _wait_job(name, budget)
            except Exception as e:
                # Don't raise — mark soft-deadline hit and let scoring proceed
                # with whatever pod state we have. This is the critical change vs run_3:
                # a stuck/failed job produces a finite combined_score + rich artifacts,
                # never a wrapper-timeout placeholder.
                _log(f"  {name} did not complete within budget ({e}); aborting join loop")
                soft_deadline_hit = True
                break
        if not soft_deadline_hit:
            _log("all jobs complete")

    except Exception as e:
        _log(f"schedule submission/sleep FAILED: {e}")
        _cleanup_jobs()
        return _fail(f"schedule execution failed: {e}",
                     {"traceback": traceback.format_exc()})

    # --- Pod state & per-job excess (always computed, even on soft-deadline) --
    try:
        _log("collecting pod metadata")
        pods = _get_pods_json()
    except Exception as e:
        _log(f"pod metadata FAILED: {e}")
        _cleanup_jobs()
        return _fail(f"pod metadata failed: {e}",
                     {"traceback": traceback.format_exc()})

    now_s = time.time()
    per_job_obs = _observed_s_per_job(pods, now_s)
    excess, total_excess = _compute_excess(per_job_obs)
    slowness_block = _slowness_table(per_job_obs, excess)
    stuck_jobs = [
        f"{j} ({info['state']}, observed={info['observed_s']:.0f}s, "
        f"baseline={BASELINE_S.get((j, info['node']), float('nan')):.0f}s)"
        for j, info in sorted(per_job_obs.items())
        if info["state"] != "Completed"
    ]
    ooms = _oom_pods(pods)

    # --- Soft-deadline branch: skip mcperf, return finite score with excess --
    # On this path we do NOT trust the mcperf window (jobs still running → window
    # is ill-defined) and we do NOT call _fail(), so OpenEvolve gets a finite
    # combined_score plus rich artifacts instead of a wrapper-timeout placeholder.
    if soft_deadline_hit:
        elapsed_s = time.time() - start_wall
        combined = -(elapsed_s + total_excess)
        note = (f"soft-deadline hit after {elapsed_s:.0f}s; "
                f"{len(stuck_jobs)} job(s) did not complete; "
                f"total_excess_s={total_excess:.0f}")
        _log(f"cleaning up and returning score={combined:.1f} ({note})")
        _cleanup_jobs()
        per_pod_block = slowness_block + "\n(mcperf not sampled: soft deadline)"
        artifacts = {
            "note": note,
            "min_p95_us": "nan",
            "mean_p95_us": "nan",
            "max_p95_us": "nan",
            "slo_headroom_us": "nan",
            "window_start": "nan",
            "window_end": "nan",
            "mcperf_rows_total": "0",
            "per_pod_summary": per_pod_block,
            "slowness_breakdown": slowness_block,
            "total_excess_s": f"{total_excess:.1f}",
            "stuck_jobs": "; ".join(stuck_jobs) if stuck_jobs else "none",
            "ooms": "; ".join(ooms) if ooms else "none",
        }
        return EvaluationResult(
            metrics={
                "combined_score": float(combined),
                "makespan_s": float(elapsed_s),
                "violations": 0.0,
                "slo_rows_in_window": 0.0,
                "total_excess_s": float(total_excess),
            },
            artifacts=artifacts,
        )

    # --- Normal path: window, mcperf, scoring ---------------------------------
    try:
        w_start, w_end = _job_window(pods)
        makespan = w_end - w_start
        prev_runs = _load_prev_runs()
        per_job_table, problem_jobs, current_runs = _per_job_summary(
            pods, makespan_s=makespan, prev_runs=prev_runs or None
        )
        if current_runs:
            _save_prev_runs(current_runs)
        _log(f"makespan = {makespan:.1f}s "
             f"(window {w_start:.0f} -> {w_end:.0f})")
        if problem_jobs:
            _log(f"WARNING: problem jobs: {problem_jobs}")

        mcperf_dst = Path(tempfile.gettempdir()) / (
            f"mcperf_{int(start_wall)}.txt"
        )
        _log(f"fetching mcperf log from client-measure -> {mcperf_dst}")
        _fetch_mcperf(mcperf_dst)
        rows = parse_mcperf(mcperf_dst)
        in_window = [
            (s, e, p) for (s, e, p) in rows if s >= w_start and e <= w_end
        ]
        violations = sum(1 for (_, _, p) in in_window if p > SLO_US)
        _log(f"mcperf rows: {len(rows)} total, {len(in_window)} in window, "
             f"{violations} SLO violations")
    except Exception as e:
        _log(f"metric collection FAILED: {e}")
        _cleanup_jobs()
        return _fail(f"metric collection failed: {e}",
                     {"traceback": traceback.format_exc()})

    if not in_window:
        combined = INFEASIBLE_SCORE + 1.0
        note = "no mcperf rows inside batch window (mcperf running?)"
        min_p95 = mean_p95 = max_p95 = float("nan")
    elif violations > 0:
        combined = -(makespan + SLO_PENALTY + total_excess)
        note = (f"SLO violated: {violations}/{len(in_window)} rows > 1ms; "
                f"total_excess_s={total_excess:.0f}")
        ps = [p for (_, _, p) in in_window]
        min_p95 = min(ps)
        mean_p95 = sum(ps) / len(ps)
        max_p95 = max(ps)
    else:
        combined = -(makespan + total_excess)
        note = f"ok (total_excess_s={total_excess:.0f})"
        ps = [p for (_, _, p) in in_window]
        min_p95 = min(ps)
        mean_p95 = sum(ps) / len(ps)
        max_p95 = max(ps)

    # Headroom below SLO (1000 us). Positive = room to push harder on node-b;
    # small/negative = you are at the cliff. Computed from max_p95 so a single
    # worst sample drives the signal (same thing that voids a run).
    slo_headroom_us = (SLO_US - max_p95) if in_window else float("nan")

    _log(
        f"cleaning up and returning score={combined:.1f} ({note}); "
        f"p95 min/avg/max={min_p95:.0f}/{mean_p95:.0f}/{max_p95:.0f}us "
        f"headroom={slo_headroom_us:.0f}us ooms={len(ooms)} "
        f"total_excess={total_excess:.0f}s"
    )
    _cleanup_jobs()

    if in_window:
        p95_line = (f"p95 min/avg/max (us) : "
                    f"{min_p95:.0f} / {mean_p95:.0f} / {max_p95:.0f}")
    else:
        p95_line = "p95 min/avg/max (us) : n/a"
    per_pod_block = per_job_table + "\n" + p95_line

    artifacts = {
        "note": note,
        "min_p95_us": f"{min_p95:.1f}",
        "mean_p95_us": f"{mean_p95:.1f}",
        "max_p95_us": f"{max_p95:.1f}",
        "slo_headroom_us": f"{slo_headroom_us:.1f}",
        "window_start": f"{w_start:.3f}",
        "window_end": f"{w_end:.3f}",
        "mcperf_rows_total": str(len(rows)),
        "per_pod_summary": per_pod_block,
        "slowness_breakdown": slowness_block,
        "total_excess_s": f"{total_excess:.1f}",
        "stuck_jobs": "; ".join(stuck_jobs) if stuck_jobs else "none",
        "ooms": "; ".join(ooms) if ooms else "none",
    }

    return EvaluationResult(
        metrics={
            "combined_score": float(combined),
            "makespan_s": float(makespan),
            "violations": float(violations),
            "slo_rows_in_window": float(len(in_window)),
            "total_excess_s": float(total_excess),
        },
        artifacts=artifacts,
    )


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("program", nargs="?", default="scheduler.yaml")
    args = ap.parse_args()
    result = evaluate(args.program)
    print(json.dumps({"metrics": result.metrics,
                      "artifacts": result.artifacts}, indent=2))

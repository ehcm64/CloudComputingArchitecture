"""OpenEvolve evaluator for Part 3.2 of the CCA project.

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
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
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

WAIT_TIMEOUT_S = 2000            # per kubectl wait
OVERALL_TIMEOUT_S = 30 * 60      # hard cap on one evaluation
ZONE = os.environ.get("CCA_ZONE", "europe-west1-b")
SSH_KEY = os.environ.get(
    "CCA_SSH_KEY", str(Path.home() / ".ssh" / "cloud-computing")
)
MCPERF_REMOTE_PATH = os.environ.get(
    "CCA_MCPERF_PATH", "/home/ubuntu/mcperf_continuous.txt"
)
INFEASIBLE_SCORE = -1_000_000.0
SLO_PENALTY = 10_000.0

_TEMPLATE = (Path(__file__).resolve().parent / "job_template.yaml").read_text()
_TFMT = "%Y-%m-%dT%H:%M:%SZ"


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
        elif verb == "wait":
            if arg not in jobs:
                raise ValueError(f"schedule[{i}] waits on unknown job `{arg}`")
            actions.append(("wait", arg))
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


def _per_job_summary(pods: dict) -> tuple[str, list[str]]:
    """Build a text table of per-job timing / status. Flag OOMs and failures.

    Returns (table_text, problem_jobs) where problem_jobs is a list of names
    whose container did not exit 0 (OOMKilled, Error, Pending, etc.).
    """
    rows: list[dict] = []
    problems: list[str] = []
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
            rows.append({"job": job_name, "node": node, "wait": "-",
                         "run": "-", "reason": "no-container-status",
                         "exit": "-"})
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
                wait = f"{s - c:.1f}"
                run = f"{f - s:.1f}"
            except Exception:
                wait = run = "-"
            if exit_code != 0 or reason not in ("Completed", ""):
                problems.append(f"{job_name}({reason or 'exit='+str(exit_code)})")
            rows.append({"job": job_name, "node": node, "wait": wait,
                         "run": run, "reason": reason or "Completed",
                         "exit": str(exit_code)})
        elif "waiting" in state:
            w = state["waiting"]
            rows.append({"job": job_name, "node": node, "wait": "-",
                         "run": "-",
                         "reason": f"Waiting({w.get('reason', '?')})",
                         "exit": "-"})
            problems.append(f"{job_name}(waiting:{w.get('reason', '?')})")
        elif "running" in state:
            rows.append({"job": job_name, "node": node, "wait": "-",
                         "run": "-", "reason": "Running", "exit": "-"})
            problems.append(f"{job_name}(still-running)")

    if not rows:
        return "(no batch pods found)", problems

    header = f"{'job':<24} {'node':<22} {'wait_s':>7} {'run_s':>7} {'reason':<16} {'exit':>4}"
    lines = [header, "-" * len(header)]
    for r in sorted(rows, key=lambda r: r["job"]):
        lines.append(
            f"{r['job']:<24} {r['node']:<22} {r['wait']:>7} {r['run']:>7} "
            f"{r['reason']:<16} {r['exit']:>4}"
        )
    return "\n".join(lines), problems


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
            elif verb == "wait":
                _wait_job(arg, remaining)

        _log(f"joining {len(submitted)} submitted job(s)")
        for name in submitted:
            remaining = OVERALL_TIMEOUT_S - (time.time() - start_wall)
            if remaining <= 0:
                raise TimeoutError("overall evaluation timeout while joining")
            _log(f"  waiting on {name} (remaining {int(remaining)}s)")
            _wait_job(name, remaining)
        _log("all jobs complete")

    except Exception as e:
        _log(f"schedule execution FAILED: {e}")
        # Best-effort: capture pod state so the LLM sees which job broke.
        per_job = ""
        problems: list[str] = []
        try:
            pods = _get_pods_json()
            per_job, problems = _per_job_summary(pods)
        except Exception:
            pass
        _cleanup_jobs()
        extra = {"traceback": traceback.format_exc()}
        if per_job:
            extra["per_job_table"] = per_job
        if problems:
            extra["problem_jobs"] = ", ".join(problems)
        return _fail(f"schedule execution failed: {e}", extra)

    # Metrics.
    try:
        _log("collecting pod metadata")
        pods = _get_pods_json()
        w_start, w_end = _job_window(pods)
        makespan = w_end - w_start
        per_job_table, problem_jobs = _per_job_summary(pods)
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
        mean_p95 = max_p95 = float("nan")
    elif violations > 0:
        combined = -(makespan + SLO_PENALTY)
        note = f"SLO violated: {violations}/{len(in_window)} rows > 1ms"
        ps = [p for (_, _, p) in in_window]
        mean_p95 = sum(ps) / len(ps)
        max_p95 = max(ps)
    else:
        combined = -makespan
        note = "ok"
        ps = [p for (_, _, p) in in_window]
        mean_p95 = sum(ps) / len(ps)
        max_p95 = max(ps)

    ooms = _oom_pods(pods)
    _log(
        f"cleaning up and returning score={combined:.1f} ({note}); "
        f"mean_p95={mean_p95:.0f}us max_p95={max_p95:.0f}us ooms={len(ooms)}"
    )
    _cleanup_jobs()

    artifacts = {
        "note": note,
        "mean_p95_us": f"{mean_p95:.1f}",
        "max_p95_us": f"{max_p95:.1f}",
        "window_start": f"{w_start:.3f}",
        "window_end": f"{w_end:.3f}",
        "mcperf_rows_total": str(len(rows)),
        "per_pod_summary": per_job_table,
        "ooms": "; ".join(ooms) if ooms else "none",
    }

    return EvaluationResult(
        metrics={
            "combined_score": float(combined),
            "makespan_s": float(makespan),
            "violations": float(violations),
            "slo_rows_in_window": float(len(in_window)),
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

#!/usr/bin/env bash
# run_experiment_policy_3_2.sh RUN_NUMBER
#
# Runs the Part 3.2 OpenEvolve-optimized scheduling policy (best program from
# run_4, iteration 3: combined_score=-189, makespan 189 s, 0 SLO violations,
# total_excess_s=0 — i.e. every pod ran at or below its solo-node baseline).
#
# Schedule (DSL form, from best_program.yaml):
#   submit parsec-canneal
#   submit parsec-blackscholes
#   submit parsec-radix
#   submit parsec-vips
#   sleep 5
#   submit parsec-freqmine
#   submit parsec-streamcluster
#   sleep 1
#   submit parsec-barnes
#
# vs Part 3.1 (scripts/run_experiment.sh): radix no longer runs alone on
# node-a; it overlaps with the three node-b jobs. The `kubectl wait
# job/parsec-radix` barrier is replaced by a 5 s sleep — radix finishes inside
# that window on node-a (baseline 11 s, but cpu_request=6 keeps the node
# exclusive) before freqmine + streamcluster take over.
set -euo pipefail

if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <run-number>" >&2
    exit 1
fi

RUN="$1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
YAML_DIR="$REPO_ROOT/yaml"
OUT_DIR="$REPO_ROOT/results/run${RUN}_policy_3_2"

mkdir -p "$OUT_DIR" "$OUT_DIR/yaml"
echo "[run $RUN] policy 3.2 results -> $OUT_DIR"

# Snapshot the YAMLs actually submitted in this run so the config is
# reproducible later even if we edit requests/limits between runs.
cp "$YAML_DIR"/*.yaml "$OUT_DIR/yaml/"

# Discover IPs/nodes needed for mcperf + the post-run SCP pullback.
MEASURE_NODE=$(kubectl get nodes -o name | grep client-measure | sed 's|^node/||' | head -n1)
MEMCACHED_IP=$(kubectl get pod some-memcached -o jsonpath='{.status.podIP}')
AGENT_A_NODE=$(kubectl get nodes -o name | grep client-agent-a | sed 's|^node/||' | head -n1)
AGENT_B_NODE=$(kubectl get nodes -o name | grep client-agent-b | sed 's|^node/||' | head -n1)
AGENT_A_IP=$(kubectl get node "$AGENT_A_NODE" -o jsonpath='{.status.addresses[?(@.type=="InternalIP")].address}')
AGENT_B_IP=$(kubectl get node "$AGENT_B_NODE" -o jsonpath='{.status.addresses[?(@.type=="InternalIP")].address}')
MCPERF_REMOTE="mcperf_run${RUN}_policy_3_2.txt"

cat <<EOF

================================================================
[run $RUN] PASTE THIS INTO THE client-measure PANE NOW:

./mcperf -s ${MEMCACHED_IP} -a ${AGENT_A_IP} -a ${AGENT_B_IP} \\
  --noload -T 6 -C 4 -D 4 -Q 1000 -c 4 -t 10 --scan 30000:30500:5 \\
  | tee ~/${MCPERF_REMOTE}

================================================================
EOF
read -r -p "[run $RUN] press Enter once mcperf is running... " _

echo "[run $RUN] clearing previous batch jobs..."
kubectl delete jobs --all --ignore-not-found=true >/dev/null

JOB_NAMES=(
    "parsec-canneal"
    "parsec-blackscholes"
    "parsec-radix"
    "parsec-vips"
    "parsec-freqmine"
    "parsec-streamcluster"
    "parsec-barnes"
)

# Batch 1: canneal, blackscholes, radix, vips submitted back-to-back.
#   - canneal/blackscholes/vips land on node-b (run in parallel with memcached).
#   - radix lands on node-a alone (cpu_request=6 keeps the rest queued).
for jobname in parsec-canneal parsec-blackscholes parsec-radix parsec-vips; do
    echo "[run $RUN][batch 1] apply $jobname"
    kubectl apply -f "$YAML_DIR/${jobname}.yaml"
done

# Give radix room to finish on node-a before the heavy two arrive.
sleep 5

# Batch 2: freqmine + streamcluster co-scheduled on node-a.
echo "[run $RUN][batch 2] apply parsec-freqmine"
kubectl apply -f "$YAML_DIR/parsec-freqmine.yaml"
echo "[run $RUN][batch 2] apply parsec-streamcluster"
kubectl apply -f "$YAML_DIR/parsec-streamcluster.yaml"

sleep 1

# Batch 3: barnes queues behind freqmine/streamcluster on node-a.
echo "[run $RUN][batch 3] apply parsec-barnes"
kubectl apply -f "$YAML_DIR/parsec-barnes.yaml"

echo "[run $RUN] all jobs submitted — waiting for completion..."
for jobname in "${JOB_NAMES[@]}"; do
    kubectl wait --for=condition=complete "job/$jobname" --timeout=2000s
    echo "[run $RUN] done   $jobname"
done

echo "[run $RUN] all batch jobs complete. collecting pod JSON..."
kubectl get pods -o json > "$OUT_DIR/results.json"

echo "[run $RUN] per-job execution times:"
python3 "$REPO_ROOT/get_time.py" "$OUT_DIR/results.json" | tee "$OUT_DIR/times.txt"

echo
echo "[run $RUN] per-job wait vs run breakdown:"
python3 "$SCRIPT_DIR/summarize_run.py" "$OUT_DIR/results.json" \
    | tee "$OUT_DIR/wait_vs_run.txt"

echo
echo "[run $RUN] pulling mcperf output back from client-measure..."
gcloud compute scp --ssh-key-file ~/.ssh/cloud-computing \
    --zone europe-west1-b \
    "ubuntu@${MEASURE_NODE}:~/${MCPERF_REMOTE}" \
    "$OUT_DIR/mcperf.txt"

echo
echo "[run $RUN] SLO violation ratio (p95 > 1ms) during job window:"
python3 "$SCRIPT_DIR/slo_violations.py" \
    "$OUT_DIR/results.json" "$OUT_DIR/mcperf.txt" \
    | tee "$OUT_DIR/slo.txt"

echo "[run $RUN] done."

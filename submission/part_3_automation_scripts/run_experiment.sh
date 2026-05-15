#!/usr/bin/env bash
# run_experiment.sh RUN_NUMBER
set -euo pipefail

if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <run-number>" >&2
    exit 1
fi

RUN="$1"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
YAML_DIR="$REPO_ROOT/yaml"
OUT_DIR="$REPO_ROOT/results/run$RUN"

mkdir -p "$OUT_DIR" "$OUT_DIR/yaml"
echo "[run $RUN] results -> $OUT_DIR"

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
MCPERF_REMOTE="mcperf_run${RUN}.txt"

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

# Three-phase submission:
NODE_B_YAMLS=(
    "$YAML_DIR/parsec-canneal.yaml"
    "$YAML_DIR/parsec-blackscholes.yaml"
    "$YAML_DIR/parsec-vips.yaml"
)
PHASE2_YAMLS=(
    "$YAML_DIR/parsec-barnes.yaml"
)

JOB_NAMES=("parsec-radix" "parsec-freqmine" "parsec-streamcluster")
for yaml in "${PHASE2_YAMLS[@]}" "${NODE_B_YAMLS[@]}"; do
    JOB_NAMES+=("$(basename "$yaml" .yaml)")
done

# Kick off node-b jobs first — they'll run in parallel with everything on node-a.
for yaml in "${NODE_B_YAMLS[@]}"; do
    jobname="$(basename "$yaml" .yaml)"
    echo "[run $RUN][node-b] apply $jobname"
    kubectl apply -f "$yaml"
done

# Phase 0: radix alone on node-a.
echo "[run $RUN][phase 0] apply parsec-radix (alone on node-a)"
kubectl apply -f "$YAML_DIR/parsec-radix.yaml"
kubectl wait --for=condition=complete job/parsec-radix --timeout=600s
echo "[run $RUN][phase 0] parsec-radix complete"

# Phase 1: freqmine + streamcluster co-scheduled on node-a.
echo "[run $RUN][phase 1] apply parsec-freqmine"
kubectl apply -f "$YAML_DIR/parsec-freqmine.yaml"
sleep 1
echo "[run $RUN][phase 1] apply parsec-streamcluster"
kubectl apply -f "$YAML_DIR/parsec-streamcluster.yaml"
sleep 1

# Phase 2: barnes + vips queue behind the big two.
for yaml in "${PHASE2_YAMLS[@]}"; do
    jobname="$(basename "$yaml" .yaml)"
    echo "[run $RUN][phase 2] apply $jobname"
    kubectl apply -f "$yaml"
done

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

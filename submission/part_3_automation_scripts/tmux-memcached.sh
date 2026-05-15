#!/usr/bin/env bash
# tmux-memcached.sh
# One-shot setup before running experiments:
#   1. deploy memcached (if not already running) and wait for it to be Ready
#   2. split the current tmux window:
#        top = original pane (kept for running run_experiment.sh)
#        bottom row = 3 side-by-side SSH sessions into client-agent-a / -b / measure
#   3. print the mcperf commands with the discovered IPs baked in,
#      ready to copy-paste into each of the bottom panes.
set -euo pipefail

if [[ -z "${TMUX:-}" ]]; then
    echo "error: run this from inside a tmux session" >&2
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
YAML_DIR="$REPO_ROOT/yaml"

# 1. memcached
if ! kubectl get pod some-memcached >/dev/null 2>&1; then
    echo "deploying memcached..."
    kubectl apply -f "$YAML_DIR/memcached.yaml"
fi
echo "waiting for memcached pod Ready..."
kubectl wait --for=condition=Ready pod/some-memcached --timeout=300s

# 2. discover client VMs
agent_a_node=$(kubectl get nodes -o name | grep client-agent-a | sed 's|^node/||' | head -n1)
agent_b_node=$(kubectl get nodes -o name | grep client-agent-b | sed 's|^node/||' | head -n1)
measure_node=$(kubectl get nodes -o name | grep client-measure | sed 's|^node/||' | head -n1)

if [[ -z "${agent_a_node}" || -z "${agent_b_node}" || -z "${measure_node}" ]]; then
    echo "error: could not discover all three client nodes via kubectl" >&2
    exit 1
fi

memcached_ip=$(kubectl get pod some-memcached -o jsonpath='{.status.podIP}')
agent_a_ip=$(kubectl get node "${agent_a_node}" -o jsonpath='{.status.addresses[?(@.type=="InternalIP")].address}')
agent_b_ip=$(kubectl get node "${agent_b_node}" -o jsonpath='{.status.addresses[?(@.type=="InternalIP")].address}')

SSH="gcloud compute ssh --ssh-key-file ~/.ssh/cloud-computing --zone europe-west1-b ubuntu@"

# 3. split panes
orig_pane=$(tmux display -p '#{pane_id}')

tmux split-window -v -p 60 -c "#{pane_current_path}"
tmux send-keys "${SSH}${agent_a_node}" Enter

# Goal for the bottom row: agent-a 25% | agent-b 25% | measure 50%.
#
# 1. Split current (agent-a, 100%) so the new right pane is 75% of it:
#    → agent-a = 25%, new empty pane = 75%. Active = new.
# 2. Put agent-b in this 75% pane, then split it so new right is 67% of 75%:
#    → agent-b keeps 33% of 75% ≈ 25% of row, measure gets 67% of 75% ≈ 50%.
tmux split-window -h -p 75 -c "#{pane_current_path}"
tmux send-keys "${SSH}${agent_b_node}" Enter

tmux split-window -h -p 67 -c "#{pane_current_path}"
tmux send-keys "${SSH}${measure_node}" Enter

tmux select-pane -t "${orig_pane}"

# 4. print copy-paste commands
cat <<EOF

memcached ready, SSH panes opened. IPs discovered:
  MEMCACHED = ${memcached_ip}
  AGENT_A   = ${agent_a_ip}
  AGENT_B   = ${agent_b_ip}

Paste into each pane once SSH is connected:

# agent-a pane
./mcperf -T 2 -A

# agent-b pane
./mcperf -T 4 -A

# measure pane (first time only per cluster — loads keyspace)
./mcperf -s ${memcached_ip} --loadonly

# measure pane (start constant 30K QPS load; tee to a per-run file)
./mcperf -s ${memcached_ip} -a ${agent_a_ip} -a ${agent_b_ip} \\
  --noload -T 6 -C 4 -D 4 -Q 1000 -c 4 -t 10 --scan 30000:30500:5 \\
  | tee ~/mcperf_run1.txt

EOF

#!/usr/bin/env bash
# tmux-teardown.sh
# Teardown counterpart to tmux-memcached.sh:
#   1. delete memcached pod
#   2. close every tmux pane in the current window EXCEPT the one you run this from
#      (tmux sends SIGHUP to the pane's shell, which terminates the SSH session)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
YAML_DIR="$REPO_ROOT/yaml"

# 1. memcached
if kubectl get pod some-memcached >/dev/null 2>&1; then
    echo "deleting memcached..."
    kubectl delete -f "$YAML_DIR/memcached.yaml" --ignore-not-found=true
else
    echo "memcached not running, skipping"
fi

# 2. tmux panes
if [[ -n "${TMUX:-}" ]]; then
    echo "closing sibling tmux panes in this window..."
    tmux kill-pane -a
else
    echo "not inside tmux — skipping pane cleanup"
fi

echo "teardown complete."

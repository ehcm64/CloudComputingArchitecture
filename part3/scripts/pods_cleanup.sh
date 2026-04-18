#!/usr/bin/env bash
# pods_cleanup.sh
# Delete non-Running pods AND Jobs that are no longer active.
#
# memcached (some-memcached) is Running, so it is never touched.
# Jobs with at least one active (Running/Pending) pod are left alone.
set -euo pipefail

kubectl delete jobs --all

# 1. delete non-Running pods
pods=$(kubectl get pods --field-selector=status.phase!=Running -o name)
if [[ -n "$pods" ]]; then
    echo "deleting pods:"
    echo "$pods"
    # shellcheck disable=SC2086
    kubectl delete $pods --wait=false
else
    echo "no non-running pods to delete"
fi

# 2. delete inactive Jobs (status.active is empty or 0 — no running pods)
inactive_jobs=$(kubectl get jobs \
    -o jsonpath='{range .items[*]}{.metadata.name} {.status.active}{"\n"}{end}' \
    | awk '$2=="" || $2=="0" {print "job/" $1}')

if [[ -n "$inactive_jobs" ]]; then
    echo "deleting jobs:"
    echo "$inactive_jobs"
    # shellcheck disable=SC2086
    kubectl delete $inactive_jobs --wait=false
else
    echo "no inactive jobs to delete"
fi

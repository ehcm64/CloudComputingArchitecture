#!/usr/bin/env bash
set -euo pipefail

ZONE="europe-west1-b"
SSH_KEY="$HOME/.ssh/cloud-computing"

echo "Looking for client-measure VM..."

MEASURE_VM=$(gcloud compute instances list \
  --filter="name~'client-measure'" \
  --format="value(name)" \
  | head -n1)

if [[ -z "$MEASURE_VM" ]]; then
  echo "client-measure VM not found"
  exit 1
fi


gcloud compute ssh \
  --ssh-key-file "${SSH_KEY}" \
  --zone "${ZONE}" \
  ubuntu@"${MEASURE_VM}"

echo "Found VM: $MEASURE_VM"
echo ""
echo " Run commands:"
echo "   tail -f ~/mcperf_results.txt"
echo ""
echo "Connecting..."
echo ""
#!/usr/bin/env bash
set -euo pipefail

ZONE="europe-west1-b"
SSH_KEY="$HOME/.ssh/cloud-computing"

MEMCACHED_VM=$(gcloud compute instances list \
  --filter="name~'memcache-server'" \
  --format="value(name)" \
  | head -n1)

if [[ -z "$MEMCACHED_VM" ]]; then
  echo "memcache-server VM not found"
  exit 1
fi

echo "OK Connecting to: $MEMCACHED_VM"

gcloud compute ssh \
  --ssh-key-file "${SSH_KEY}" \
  --zone "${ZONE}" \
  ubuntu@"${MEMCACHED_VM}"


echo ""
echo "GOOD commands:"
echo "   tail -f log*.txt"
echo ""
echo "Connecting..."
echo ""
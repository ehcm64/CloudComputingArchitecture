#!/usr/bin/env bash
set -euo pipefail

ZONE="europe-west1-b"
SSH_KEY="$HOME/.ssh/cloud-computing"
SSH="gcloud compute ssh --ssh-key-file ${SSH_KEY} --zone ${ZONE}"

echo "Discovering memcache-server VM..."
MEMCACHED_VM=$(gcloud compute instances list \
  --filter="name~'memcache-server'" \
  --format="value(name)")

if [[ -z "$MEMCACHED_VM" ]]; then
  echo "memcache-server VM not found"
  exit 1
fi
echo "OK Found memcached VM: $MEMCACHED_VM"

echo "Getting memcached internal IP..."
MEMCACHED_INTERNAL_IP=$(gcloud compute instances describe "$MEMCACHED_VM" \
  --zone "$ZONE" \
  --format="value(networkInterfaces[0].networkIP)")
echo "OK Internal IP: $MEMCACHED_INTERNAL_IP"

echo "Installing and configuring memcached on $MEMCACHED_VM..."
$SSH ubuntu@"$MEMCACHED_VM" <<EOF
set -e

sudo apt update
sudo apt install -y memcached libmemcached-tools

# Update memory limit from 64 to 1024 MB
sudo sed -i 's/^-m 64$/-m 1024/' /etc/memcached.conf

# Bind to internal IP instead of localhost
sudo sed -i 's/^-l 127.0.0.1$/-l ${MEMCACHED_INTERNAL_IP}/' /etc/memcached.conf

# Set number of threads to 3  !!!
# You can adjust this later
if grep -q '^-t' /etc/memcached.conf; then
  sudo sed -i 's/^-t.*/-t 3/' /etc/memcached.conf
else
  echo '-t 3' | sudo tee -a /etc/memcached.conf
fi

sudo systemctl restart memcached
sleep 2
sudo systemctl status memcached --no-pager

echo "--- Verifying config ---"
grep -E '^-m|^-l|^-t' /etc/memcached.conf
EOF

echo "OK Installing Python dependencies on $MEMCACHED_VM..."
gcloud compute ssh --ssh-key-file "${SSH_KEY}" --zone "${ZONE}" ubuntu@"$MEMCACHED_VM" -- "sudo apt install -y python3-psutil"
echo "OK Python dependencies installed"

echo ""
echo "OK Memcached setup complete"
echo "   VM:          $MEMCACHED_VM"
echo "   Internal IP: $MEMCACHED_INTERNAL_IP"
echo ""



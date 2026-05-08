#!/usr/bin/env bash
set -euo pipefail

ZONE="europe-west1-b"
SSH_KEY="$HOME/.ssh/cloud-computing"

SSH="gcloud compute ssh --ssh-key-file ${SSH_KEY} --zone ${ZONE}"
SCP="gcloud compute scp --ssh-key-file ${SSH_KEY} --zone ${ZONE}"

echo "🔍 Discovering memcache-server VM..."

MEMCACHED_VM=$(gcloud compute instances list \
  --filter="name~'memcache-server'" \
  --format="value(name)")

if [[ -z "$MEMCACHED_VM" ]]; then
  echo "ERR memcache-server VM not found"
  exit 1
fi

echo "OK Found memcached VM: $MEMCACHED_VM"

echo
echo "Copying scheduler files to VM..."

$SCP scheduler.py scheduler_logger.py \
  ubuntu@"$MEMCACHED_VM":~/

echo
echo "OK Starting scheduler on memcached VM..."
$SSH ubuntu@"$MEMCACHED_VM" <<'EOF'

set -e

echo "Killing existing scheduler..."
sudo pkill -f "python3 scheduler.py" || true

# Install docker if not present
if ! command -v docker &> /dev/null; then
    echo "Installing Docker..."
    sudo apt-get update -q
    sudo apt-get install -y docker.io
    sudo systemctl start docker
    sudo systemctl enable docker
fi

echo "OK Docker ready: $(docker --version)"

chmod +x scheduler.py

echo "Setup complete"
echo ""
echo "-> Now run manually:"
echo "   sudo python3 scheduler.py"
echo ""
EOF

$SSH ubuntu@"$MEMCACHED_VM"
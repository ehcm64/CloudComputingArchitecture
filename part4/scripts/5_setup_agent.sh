# # # #!/usr/bin/env bash

# # # source "$(dirname "$0")/../0_config.sh"

# # # set -euo pipefail
# # # ZONE="europe-west1-b"
# # # SSH_KEY="$HOME/.ssh/cloud-computing"
# # # SSH="gcloud compute ssh --ssh-key-file ${SSH_KEY} --zone ${ZONE}"

# # # echo "📦 Installing mcperf on agent..."
# # # $SSH ubuntu@"$AGENT_VM" -- '
# # # set -e
# # # sudo apt-get update -q
# # # sudo apt-get install -y libevent-dev libzmq3-dev git make g++
# # # sudo sed -i "s/^Types: deb$/Types: deb deb-src/" /etc/apt/sources.list.d/ubuntu.sources
# # # sudo apt-get update -q
# # # sudo apt-get build-dep -y memcached
# # # cd ~
# # # if [ ! -d memcache-perf-dynamic ]; then
# # #   git clone https://github.com/eth-easl/memcache-perf-dynamic.git
# # # fi
# # # cd memcache-perf-dynamic
# # # make
# # # echo "✅ mcperf built"
# # # '

# # # echo ""
# # # echo "🚀 Starting mcperf agent..."
# # # $SSH ubuntu@"$AGENT_VM" -- \
# # #   "pkill mcperf || true && nohup ~/memcache-perf-dynamic/mcperf -T 8 -A > ~/mcperf_agent.log 2>&1 &"

# # # sleep 2
# # # echo "✅ Agent is running"

# # # echo ""
# # # echo "🔍 Verifying..."
# # # $SSH ubuntu@"$AGENT_VM" -- "ps aux | grep mcperf | grep -v grep"


# # #!/usr/bin/env bash
# # set -euo pipefail

# # ZONE="europe-west1-b"
# # SSH_KEY="$HOME/.ssh/cloud-computing"
# # SSH="gcloud compute ssh --ssh-key-file ${SSH_KEY} --zone ${ZONE}"

# # echo "🔍 Auto-discovering VMs..."
# # AGENT_VM=$(kubectl get nodes -o wide | grep "client-agent" | awk '{print $1}')
# # AGENT_INTERNAL_IP=$(kubectl get nodes -o wide | grep "client-agent" | awk '{print $6}')
# # echo "✅ AGENT_VM=$AGENT_VM"
# # echo "✅ AGENT_INTERNAL_IP=$AGENT_INTERNAL_IP"

# # echo ""
# # echo "📦 Installing mcperf on agent..."
# # $SSH ubuntu@"$AGENT_VM" -- '
# # set -e
# # sudo apt-get update -q
# # sudo apt-get install -y libevent-dev libzmq3-dev git make g++
# # sudo sed -i "s/^Types: deb$/Types: deb deb-src/" /etc/apt/sources.list.d/ubuntu.sources
# # sudo apt-get update -q
# # sudo apt-get build-dep -y memcached
# # cd ~
# # if [ ! -d memcache-perf-dynamic ]; then
# #   git clone https://github.com/eth-easl/memcache-perf-dynamic.git
# # fi
# # cd memcache-perf-dynamic
# # make
# # echo "✅ mcperf built"
# # '

# # echo ""
# # echo "🚀 Starting mcperf agent..."
# # $SSH ubuntu@"$AGENT_VM" -- \
# #   "pkill mcperf || true && nohup ~/memcache-perf-dynamic/mcperf -T 8 -A > ~/mcperf_agent.log 2>&1 &"

# # sleep 2
# # echo "✅ Agent is running"

# # echo ""
# # echo "🔍 Verifying..."
# # $SSH ubuntu@"$AGENT_VM" -- "ps aux | grep mcperf | grep -v grep"


# # #!/usr/bin/env bash
# # set -euo pipefail

# # ZONE="europe-west1-b"
# # SSH_KEY="$HOME/.ssh/cloud-computing"
# # SSH="gcloud compute ssh --ssh-key-file ${SSH_KEY} --zone ${ZONE}"

# # echo "🔍 Auto-discovering VMs..."
# # AGENT_VM=$(kubectl get nodes -o wide | grep "client-agent" | awk '{print $1}')
# # AGENT_INTERNAL_IP=$(kubectl get nodes -o wide | grep "client-agent" | awk '{print $6}')
# # echo "✅ AGENT_VM=$AGENT_VM"
# # echo "✅ AGENT_INTERNAL_IP=$AGENT_INTERNAL_IP"

# # echo ""
# # echo "🚀 Starting mcperf agent..."
# # $SSH ubuntu@"$AGENT_VM" -- \
# #   "pkill mcperf || true; sleep 1; nohup ~/memcache-perf-dynamic/mcperf -T 8 -A > ~/mcperf_agent.log 2>&1 < /dev/null &; sleep 2; echo done"

# # echo "✅ Agent started"

# # echo ""
# # echo "🔍 Verifying..."
# # $SSH ubuntu@"$AGENT_VM" -- "ps aux | grep '[m]cperf'"


# #!/usr/bin/env bash
# set -euo pipefail

# ZONE="europe-west1-b"
# SSH_KEY="$HOME/.ssh/cloud-computing"
# SSH="gcloud compute ssh --ssh-key-file ${SSH_KEY} --zone ${ZONE}"

# echo "🔍 Auto-discovering VMs..."
# AGENT_VM=$(kubectl get nodes -o wide | grep "client-agent" | awk '{print $1}')
# AGENT_INTERNAL_IP=$(kubectl get nodes -o wide | grep "client-agent" | awk '{print $6}')
# echo "✅ AGENT_VM=$AGENT_VM"
# echo "✅ AGENT_INTERNAL_IP=$AGENT_INTERNAL_IP"

# echo ""
# echo "🚀 Starting mcperf agent..."
# $SSH ubuntu@"$AGENT_VM" -- \
#   "pkill mcperf || true; sleep 1; nohup ~/memcache-perf-dynamic/mcperf -T 8 -A > ~/mcperf_agent.log 2>&1 < /dev/null & sleep 2; echo done"

# echo "✅ Agent started"

# echo ""
# echo "🔍 Verifying..."
# $SSH ubuntu@"$AGENT_VM"


#!/usr/bin/env bash
set -euo pipefail
ZONE="europe-west1-b"
SSH_KEY="$HOME/.ssh/cloud-computing"
SSH="gcloud compute ssh --ssh-key-file ${SSH_KEY} --zone ${ZONE}"

echo "🔍 Auto-discovering VMs..."
AGENT_VM=$(kubectl get nodes -o wide | grep "client-agent" | awk '{print $1}')
AGENT_INTERNAL_IP=$(kubectl get nodes -o wide | grep "client-agent" | awk '{print $6}')

echo "OK AGENT_VM=$AGENT_VM"
echo "OK AGENT_INTERNAL_IP=$AGENT_INTERNAL_IP"
echo ""

echo "Installing mcperf on agent (this may take a while)..."
$SSH ubuntu@"$AGENT_VM" -- '
set -e
sudo apt-get update -q
sudo apt-get install -y libevent-dev libzmq3-dev git make g++
sudo sed -i "s/^Types: deb$/Types: deb deb-src/" /etc/apt/sources.list.d/ubuntu.sources
sudo apt-get update -q
sudo apt-get build-dep -y memcached
cd ~
if [ ! -d memcache-perf-dynamic ]; then
  git clone https://github.com/eth-easl/memcache-perf-dynamic.git
fi
cd memcache-perf-dynamic
make
echo "OK mcperf built"
'


$SSH ubuntu@"$AGENT_VM"
echo ""
echo "OK Agent setup complete!"
echo ""
echo "════════════════════════════════════════════════════════"
echo "Run:"
echo ""
echo "   ~/memcache-perf-dynamic/mcperf -T 8 -A"
echo "════════════════════════════════════════════════════════"
echo ""
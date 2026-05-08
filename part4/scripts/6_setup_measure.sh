
#!/usr/bin/env bash
set -euo pipefail

ZONE="europe-west1-b"
SSH_KEY="$HOME/.ssh/cloud-computing"
SSH="gcloud compute ssh --ssh-key-file ${SSH_KEY} --zone ${ZONE}"

echo "Auto-discovering VMs..."

MEASURE_VM=$(kubectl get nodes -o wide | grep "client-measure" | awk '{print $1}')
MEMCACHED_INTERNAL_IP=$(kubectl get nodes -o wide | grep "memcache-server" | awk '{print $6}')
AGENT_INTERNAL_IP=$(kubectl get nodes -o wide | grep "client-agent" | awk '{print $6}')

echo "MEASURE_VM=$MEASURE_VM"
echo "MEMCACHED_INTERNAL_IP=$MEMCACHED_INTERNAL_IP"
echo "AGENT_INTERNAL_IP=$AGENT_INTERNAL_IP"

echo ""
echo "Installing mcperf on measure VM..."

$SSH ubuntu@"$MEASURE_VM" -- "
set -e

sudo apt-get update -q
sudo apt-get install -y libevent-dev libzmq3-dev git make g++

sudo sed -i 's/^Types: deb$/Types: deb deb-src/' /etc/apt/sources.list.d/ubuntu.sources
sudo apt-get update -q
sudo apt-get build-dep -y memcached

cd ~

if [ ! -d memcache-perf-dynamic ]; then
  git clone https://github.com/eth-easl/memcache-perf-dynamic.git
fi

cd memcache-perf-dynamic
make

echo 'OK mcperf built successfully'
"

echo ""
echo "Loading memcached database..."

$SSH ubuntu@"$MEASURE_VM" -- "
~/memcache-perf-dynamic/mcperf \
  -s ${MEMCACHED_INTERNAL_IP} \
  --loadonly
"

echo ""
echo "OK Database loaded"

echo ""
echo "Opening interactive shell on MEASURE VM..."
echo ""

$SSH ubuntu@"$MEASURE_VM" -- "
echo ''
echo '~/memcache-perf-dynamic/mcperf -s ${MEMCACHED_INTERNAL_IP} --loadonly'
echo '~/memcache-perf-dynamic/mcperf \\'
echo '  -s ${MEMCACHED_INTERNAL_IP} \\'
echo '  -a ${AGENT_INTERNAL_IP} \\'
echo '  --noload -T 8 -C 8 -D 4 -Q 1000 -c 8 -t 1800 \\'
echo '  --qps_interval 15 --qps_min 5000 --qps_max 110000 \\'
echo '  > ~/mcperf_results.txt 2>&1'
echo ' remove the last line not to redirect!'

echo 'View logs:'
echo 'tail -f ~/mcperf_results.txt'
echo '===================================================='
echo ''
bash
"
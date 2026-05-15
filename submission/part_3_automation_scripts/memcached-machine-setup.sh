#!/usr/bin/env bash

# DO NOT RUN ON YOUR MACHINE


sudo sed -i 's/^Types: deb$/Types: deb deb-src/' /etc/apt/sources.list.d/ubuntu.sources
sudo apt-get update
sudo apt-get install libevent-dev libzmq3-dev git make g++ --yes
sudo apt-get build-dep memcached --yes
git clone https://github.com/eth-easl/memcache-perf-dynamic.git
cd memcache-perf-dynamic
make


######
while true; do
./mcperf -s 100.96.2.3 -a 10.0.16.8 -a 10.0.16.6 \
  --noload -T 6 -C 4 -D 4 -Q 1000 -c 4 -t 10 --scan 30000:30500:5 \
  | tee ~/mcperf_continuous.txt
done

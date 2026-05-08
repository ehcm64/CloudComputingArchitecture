#!/usr/bin/env bash

set -e

export KOPS_STATE_STORE=gs://cca-eth-2026-group-105-ethzid/
export PROJECT=cca-eth-2026-group-105
kops create -f part4.yaml

kops update cluster --name part4.k8s.local --yes --admin

kops validate cluster --wait 10m

kops export kubeconfig --name part4.k8s.local --admin

kubectl get nodes -o wide

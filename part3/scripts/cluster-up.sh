#!/usr/bin/env bash

export KPS_STATE_STORE=gs://cca-eth-2026-group-105-ethzid/
export PROJECT=cca-eth-2026-group-105

kops create -f part3.yaml

kops update cluster --name part3.k8s.local --yes

kops validate cluster --wait 10m
kops export kubeconfig --name part3.k8s.local --admin

kubectl label nodes $(kubectl get nodes -o name | grep node-a-8core   | sed 's|^node/||') cca-project-nodetype=node-a-8core   --overwrite
kubectl label nodes $(kubectl get nodes -o name | grep node-b-4core   | sed 's|^node/||') cca-project-nodetype=node-b-4core   --overwrite
kubectl label nodes $(kubectl get nodes -o name | grep client-agent-a | sed 's|^node/||') cca-project-nodetype=client-agent-a --overwrite
kubectl label nodes $(kubectl get nodes -o name | grep client-agent-b | sed 's|^node/||') cca-project-nodetype=client-agent-b --overwrite
kubectl label nodes $(kubectl get nodes -o name | grep client-measure | sed 's|^node/||') cca-project-nodetype=client-measure --overwrite

kubectl get nodes -o wide

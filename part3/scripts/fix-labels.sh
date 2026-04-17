#!/usr/bin/env bash
set -e

kubectl label nodes $(kubectl get nodes -o name | grep node-a-8core   | sed 's|^node/||') cca-project-nodetype=node-a-8core   --overwrite
kubectl label nodes $(kubectl get nodes -o name | grep node-b-4core   | sed 's|^node/||') cca-project-nodetype=node-b-4core   --overwrite
kubectl label nodes $(kubectl get nodes -o name | grep client-agent-a | sed 's|^node/||') cca-project-nodetype=client-agent-a --overwrite
kubectl label nodes $(kubectl get nodes -o name | grep client-agent-b | sed 's|^node/||') cca-project-nodetype=client-agent-b --overwrite
kubectl label nodes $(kubectl get nodes -o name | grep client-measure | sed 's|^node/||') cca-project-nodetype=client-measure --overwrite

kubectl get nodes -L cca-project-nodetype

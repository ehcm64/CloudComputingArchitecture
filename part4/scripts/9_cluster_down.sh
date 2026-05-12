export KOPS_STATE_STORE=gs://cca-eth-2026-group-105-ethzid/
export PROJECT=cca-eth-2026-group-105

kops delete cluster --name part4.k8s.local --yes

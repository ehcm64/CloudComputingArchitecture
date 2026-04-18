#!/usr/bin/env bash

kubectl get pods -o json > results.json
python3 get_time.py results.json

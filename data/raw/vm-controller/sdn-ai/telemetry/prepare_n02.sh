#!/usr/bin/env bash
set -euo pipefail

cd "$HOME/sdn-ai/telemetry"

SESSION="N02"
DIR="$HOME/sdn-ai/dataset/normal/$SESSION"

mkdir -p "$DIR"

echo "=== VERIFY ==="
python3 -m py_compile telemetry_collector_v3.py
command -v tcpdump >/dev/null

echo "=== OUTPUT ==="
echo "Dataset: $DIR"
echo "Samples: 500"
echo "Interval: 2 seconds"
echo "Expected duration: approximately 1000 seconds"

echo "PREPARE_N02_OK"

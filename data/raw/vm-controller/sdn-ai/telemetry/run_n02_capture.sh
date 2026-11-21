#!/usr/bin/env bash
set -euo pipefail

cd "$HOME/sdn-ai/telemetry"

SESSION="N02"
SAMPLES=500
INTERVAL=2
CAPTURE_SECONDS=1050

mkdir -p "$HOME/sdn-ai/dataset/normal"

echo "Starting OpenFlow capture..."
sudo -v

sudo timeout --signal=INT "${CAPTURE_SECONDS}" \
  tcpdump -i ens4 -nn -s 0 \
  -w "/tmp/openflow-${SESSION}.pcap" \
  'tcp port 6653' &

CAP_PID=$!

sleep 3

echo "Starting telemetry..."
SDN_SAMPLES="$SAMPLES" \
SDN_INTERVAL="$INTERVAL" \
python3 telemetry_collector_v2.py

echo "Waiting for capture to finish..."
wait "$CAP_PID" || {
  status=$?
  if [ "$status" -ne 124 ]; then
    exit "$status"
  fi
}

echo "Capture saved: /tmp/openflow-${SESSION}.pcap"
echo "Telemetry collection finished."

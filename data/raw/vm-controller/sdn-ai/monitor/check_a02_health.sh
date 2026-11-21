#!/usr/bin/env bash
set -euo pipefail

echo "=== OPENFLOW CONNECTIONS ==="
OF_COUNT=$(ss -Htn state established \
  '( sport = :6653 )' | wc -l)
echo "OF_COUNT=$OF_COUNT"

echo "=== MEMORY ==="
awk '/MemAvailable:/ {
  printf "MEM_AVAILABLE_MB=%.0f\n", $2/1024
}' /proc/meminfo

echo "=== CONTROLLER PROCESS ==="
pgrep -af 'org.apache.karaf.main.Main' || true

echo "=== HTTP LOCAL ==="
curl -s -o /dev/null \
  -w 'RESTCONF_HTTP=%{http_code}\n' \
  --connect-timeout 2 --max-time 3 \
  http://127.0.0.1:8181/rests/data/opendaylight-inventory:nodes

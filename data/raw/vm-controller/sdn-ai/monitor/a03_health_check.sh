#!/usr/bin/env bash
set -euo pipefail

OF=$(ss -Htn state established '( sport = :6653 )' | wc -l)

MEM=$(awk '/MemAvailable:/ {
    printf "%.0f", $2/1024
}' /proc/meminfo)

HTTP=$(curl -sS \
    --max-time 5 \
    -o /dev/null \
    -w '%{http_code}' \
    http://127.0.0.1:8181/rests/data/ \
    2>/dev/null || true)

echo "OF_CONNECTIONS=$OF"
echo "MEM_AVAILABLE_MB=$MEM"
echo "RESTCONF_HTTP=$HTTP"

if [ "$OF" -lt 4 ]; then
    echo "HEALTH=FAIL REASON=OPENFLOW"
    exit 1
fi

if [ "$MEM" -lt 2048 ]; then
    echo "HEALTH=FAIL REASON=LOW_MEMORY"
    exit 1
fi

if [ "$HTTP" != "401" ] && [ "$HTTP" != "200" ]; then
    echo "HEALTH=FAIL REASON=RESTCONF"
    exit 1
fi

echo "HEALTH=PASS"

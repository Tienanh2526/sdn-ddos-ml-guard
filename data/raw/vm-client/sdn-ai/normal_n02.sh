#!/usr/bin/env bash
set -u

DURATION="${1:-1000}"
END=$((SECONDS + DURATION))

HTTP_OK=0
HTTP_FAIL=0
PING_OK=0
PING_FAIL=0

echo "N02_START=$(date -u +%FT%TZ)"

while (( SECONDS < END )); do
    STATUS=$(curl --silent --show-error \
      --noproxy '*' \
      --interface 192.168.200.11 \
      --connect-timeout 3 \
      --max-time 5 \
      -o /dev/null \
      -w '%{http_code}' \
      http://192.168.200.12:8080/ 2>/dev/null)

    if [[ "$STATUS" == "200" ]]; then
        ((HTTP_OK+=1))
    else
        ((HTTP_FAIL+=1))
    fi

    if ping -I 192.168.200.11 \
       -c 1 -W 2 192.168.200.12 \
       >/dev/null 2>&1; then
        ((PING_OK+=1))
    else
        ((PING_FAIL+=1))
    fi

    sleep 2
done

echo "N02_END=$(date -u +%FT%TZ)"
echo "HTTP_OK=$HTTP_OK HTTP_FAIL=$HTTP_FAIL"
echo "PING_OK=$PING_OK PING_FAIL=$PING_FAIL"

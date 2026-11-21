#!/usr/bin/env python3
import subprocess
import time
from datetime import datetime, timezone

INTERVAL = 5
MIN_OF_CONNECTIONS = 4
MIN_MEMORY_MB = 2048

def log(message):
    print(
        datetime.now(timezone.utc).isoformat(),
        message,
        flush=True
    )

def openflow_connections():
    result = subprocess.run(
        ["ss", "-Htn", "state", "established",
         "( sport = :6653 )"],
        capture_output=True,
        text=True,
        check=True
    )
    return len(result.stdout.splitlines())

def available_memory_mb():
    with open("/proc/meminfo") as f:
        for line in f:
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1024
    raise RuntimeError("MemAvailable unavailable")

failures = 0

while True:
    try:
        of_count = openflow_connections()
        mem_mb = available_memory_mb()

        healthy = (
            of_count >= MIN_OF_CONNECTIONS
            and mem_mb >= MIN_MEMORY_MB
        )

        log(
            f"OF={of_count} "
            f"MEM_AVAILABLE_MB={mem_mb:.0f} "
            f"HEALTHY={healthy}"
        )

        if healthy:
            failures = 0
        else:
            failures += 1

        if failures >= 3:
            log("A02_MONITOR_ALERT")
            break

    except Exception as e:
        failures += 1
        log(f"MONITOR_ERROR={e}")

        if failures >= 3:
            log("A02_MONITOR_ALERT")
            break

    time.sleep(INTERVAL)

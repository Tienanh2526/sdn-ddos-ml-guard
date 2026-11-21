#!/usr/bin/env python3
import csv
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

OUT = Path.home() / "sdn-ai/monitor/http_qos_a03.csv"
URL = "http://192.168.200.12:8080/"
INTERVAL = 2
SAMPLES = 150

OUT.parent.mkdir(parents=True, exist_ok=True)

fields = [
    "timestamp_utc",
    "http_status",
    "response_time_ms",
    "curl_exit_code",
    "success"
]

with OUT.open("w", newline="", buffering=1) as f:
    writer = csv.DictWriter(f, fieldnames=fields)
    writer.writeheader()

    print("OUTPUT =", OUT, flush=True)

    for i in range(SAMPLES):
        started = time.monotonic()
        timestamp = datetime.now(timezone.utc).isoformat()

        cmd = [
            "curl", "-sS",
            "--noproxy", "*",
            "--interface", "192.168.200.11",
            "--connect-timeout", "3",
            "--max-time", "5",
            "-o", "/dev/null",
            "-w", "%{http_code} %{time_total}",
            URL
        ]

        try:
            result = subprocess.run(
                cmd, capture_output=True,
                text=True, timeout=7
            )
            parts = result.stdout.strip().split()
            status = parts[0] if len(parts) >= 1 else "000"
            latency = (
                round(float(parts[1]) * 1000, 3)
                if len(parts) >= 2 else ""
            )
            code = result.returncode
        except (subprocess.TimeoutExpired, ValueError):
            status = "000"
            latency = ""
            code = 124

        success = int(code == 0 and status == "200")

        writer.writerow({
            "timestamp_utc": timestamp,
            "http_status": status,
            "response_time_ms": latency,
            "curl_exit_code": code,
            "success": success
        })

        print(
            f"{i+1}/{SAMPLES} HTTP={status} "
            f"RTT={latency}ms SUCCESS={success}",
            flush=True
        )

        elapsed = time.monotonic() - started
        time.sleep(max(0, INTERVAL - elapsed))

print("COMPLETE =", OUT)

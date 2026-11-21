#!/usr/bin/env python3
import os
import csv
import json
import time
import base64
import getpass
import subprocess
import urllib.request
from pathlib import Path
from datetime import datetime, timezone

URL = (
    "http://127.0.0.1:8181/rests/data/"
    "opendaylight-inventory:nodes?content=nonconfig"
)

INTERVAL = float(os.environ.get("SDN_INTERVAL", "2"))
SAMPLES = int(os.environ.get("SDN_SAMPLES", "60"))
HZ = os.sysconf("SC_CLK_TCK")

FIELDS = [
    "timestamp_utc",
    "controller_cpu_pct",
    "controller_rss_mb",
    "api_latency_ms",
    "api_http_status",
    "openflow_connections",
    "openflow_nodes",
    "inventory_flow_count",
    "packet_in_rate",
    "flow_mod_rate"
]

def get_java_pid():
    result = subprocess.run(
        ["ps", "-C", "java", "-o", "pid=,args="],
        capture_output=True, text=True, check=True
    )
    for line in result.stdout.splitlines():
        if "karaf" in line.lower():
            return int(line.strip().split()[0])
    raise RuntimeError("OpenDaylight Java/Karaf process not found")

def cpu_ticks(pid):
    raw = Path(f"/proc/{pid}/stat").read_text()
    fields = raw[raw.rfind(")") + 2:].split()
    return int(fields[11]) + int(fields[12])

def rss_mb(pid):
    for line in Path(f"/proc/{pid}/status").read_text().splitlines():
        if line.startswith("VmRSS:"):
            return round(int(line.split()[1]) / 1024, 2)
    return None

def openflow_connections():
    result = subprocess.run(
        ["ss", "-H", "-tn", "state", "established",
         "(", "sport", "=", ":6653", ")"],
        capture_output=True, text=True, check=True
    )
    return len([
        line for line in result.stdout.splitlines()
        if line.strip()
    ])

def query_inventory(auth):
    request = urllib.request.Request(
        URL,
        headers={
            "Authorization": "Basic " + auth,
            "Accept": "application/yang-data+json"
        }
    )

    start = time.monotonic()

    try:
        with urllib.request.urlopen(
            request, timeout=10
        ) as response:
            data = json.load(response)
            status = response.status

        latency_ms = round(
            (time.monotonic() - start) * 1000, 2
        )

        nodes = data.get(
            "opendaylight-inventory:nodes", {}
        ).get("node", [])

        flow_count = 0

        for node in nodes:
            tables = node.get(
                "flow-node-inventory:table", []
            )
            for table in tables:
                flow_count += len(table.get("flow", []))

        return latency_ms, status, len(nodes), flow_count

    except Exception as error:
        print(
            f"API_ERROR={type(error).__name__}: {error}",
            flush=True
        )
        return None, getattr(error, "code", None), None, None

def main():
    if os.environ.get("SDN_AUTH_STDIN") == "1":
        import sys
        username = "admin"
        password = sys.stdin.readline().rstrip("\r\n")
        if not password:
            raise ValueError("Empty ODL password")
    else:
        username = input("ODL username [admin]: ").strip() or "admin"
        password = getpass.getpass("ODL password: ")

    auth = base64.b64encode(
        f"{username}:{password}".encode()
    ).decode()

    pid = get_java_pid()
    stamp = datetime.now(timezone.utc).strftime(
        "%Y%m%dT%H%M%SZ"
    )

    output = (
        Path.home() / "sdn-ai" / "telemetry"
        / f"baseline-{stamp}.csv"
    )

    prev_ticks = cpu_ticks(pid)
    prev_time = time.monotonic()

    print(f"ODL_PID={pid}", flush=True)
    print(f"OUTPUT={output}", flush=True)

    with output.open("w", newline="") as file:
        writer = csv.DictWriter(
            file, fieldnames=FIELDS
        )
        writer.writeheader()

        for i in range(SAMPLES):
            cycle_start = time.monotonic()

            ticks = cpu_ticks(pid)
            now = time.monotonic()
            elapsed = now - prev_time

            cpu_pct = (
                round(
                    100 * (ticks - prev_ticks)
                    / HZ / elapsed, 2
                )
                if elapsed > 0 else None
            )

            prev_ticks = ticks
            prev_time = now

            latency, status, nodes, flows = (
                query_inventory(auth)
            )

            row = {
                "timestamp_utc":
                    datetime.now(timezone.utc).isoformat(),
                "controller_cpu_pct": cpu_pct,
                "controller_rss_mb": rss_mb(pid),
                "api_latency_ms": latency,
                "api_http_status": status,
                "openflow_connections":
                    openflow_connections(),
                "openflow_nodes": nodes,
                "inventory_flow_count": flows,
                "packet_in_rate": "",
                "flow_mod_rate": ""
            }

            writer.writerow(row)
            file.flush()

            print(
                f"{i+1}/{SAMPLES} "
                f"CPU={cpu_pct}% "
                f"RAM={row['controller_rss_mb']}MB "
                f"API={latency}ms "
                f"HTTP={status} "
                f"OF={row['openflow_connections']}",
                flush=True
            )

            time.sleep(
                max(0, INTERVAL -
                    (time.monotonic() - cycle_start))
            )

    print(f"COMPLETE={output}", flush=True)

if __name__ == "__main__":
    main()

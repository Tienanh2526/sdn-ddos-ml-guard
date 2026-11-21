#!/usr/bin/env python3
import csv
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

PCAP = "/tmp/openflow-baseline.pcap"
OUTPUT = Path.home() / "sdn-ai/telemetry/openflow-baseline.csv"

TYPES = {
    "10": "packet_in",
    "13": "packet_out",
    "14": "flow_mod",
    "18": "multipart_request",
    "19": "multipart_reply",
    "20": "barrier_request",
    "21": "barrier_reply",
}

cmd = [
    "tshark", "-r", PCAP,
    "-d", "tcp.port==6653,openflow",
    "-o", "tcp.desegment_tcp_streams:TRUE",
    "-Y", "openflow_v4",
    "-T", "fields",
    "-e", "frame.time_epoch",
    "-e", "ip.src",
    "-e", "ip.dst",
    "-e", "openflow_v4.type",
    "-E", "occurrence=a",
    "-E", "aggregator=,"
]

result = subprocess.run(
    cmd, capture_output=True, text=True, check=True
)

stats = defaultdict(lambda: defaultdict(int))
first_ts = None
last_ts = None
decoded = 0

for line in result.stdout.splitlines():
    parts = line.split("\t")
    if len(parts) != 4:
        continue

    timestamp, src, dst, types = parts
    second = int(float(timestamp))

    if first_ts is None:
        first_ts = second
    last_ts = second

    for msg_type in types.split(","):
        msg_type = msg_type.strip()
        name = TYPES.get(msg_type)

        if not name:
            continue

        stats[second][name] += 1
        decoded += 1

        if name == "packet_in" and dst != "10.10.0.10":
            print("WARNING: Unexpected Packet-In direction", src, dst)
        if name == "flow_mod" and src != "10.10.0.10":
            print("WARNING: Unexpected Flow-Mod direction", src, dst)

if first_ts is None:
    raise RuntimeError("No OpenFlow messages decoded")

columns = [
    "timestamp_utc",
    "packet_in",
    "packet_out",
    "flow_mod",
    "multipart_request",
    "multipart_reply",
    "barrier_request",
    "barrier_reply"
]

with OUTPUT.open("w", newline="") as file:
    writer = csv.DictWriter(file, fieldnames=columns)
    writer.writeheader()

    for second in range(first_ts, last_ts + 1):
        row = {
            "timestamp_utc": datetime.fromtimestamp(
                second, timezone.utc
            ).isoformat()
        }
        for name in TYPES.values():
            row[name] = stats[second].get(name, 0)

        writer.writerow(row)

print("OUTPUT =", OUTPUT)
print("SECONDS =", last_ts - first_ts + 1)
print("COUNTED_MESSAGES =", decoded)

for name in TYPES.values():
    total = sum(s.get(name, 0) for s in stats.values())
    print(f"{name}: {total}")

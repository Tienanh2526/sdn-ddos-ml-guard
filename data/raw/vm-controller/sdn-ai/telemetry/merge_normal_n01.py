#!/usr/bin/env python3
import csv
import subprocess
from collections import defaultdict
from datetime import datetime
from pathlib import Path

BASE = Path.home() / "sdn-ai/telemetry"
HEALTH = BASE / "baseline-20261117T082242Z.csv"
PCAP = "/tmp/openflow-normal-n01.pcap"
OUTPUT = BASE / "normal-n01.csv"

cmd = [
    "tshark", "-r", PCAP,
    "-d", "tcp.port==6653,openflow",
    "-o", "tcp.desegment_tcp_streams:TRUE",
    "-Y", "openflow_v4",
    "-T", "fields",
    "-e", "frame.time_epoch",
    "-e", "openflow_v4.type",
    "-E", "occurrence=a",
    "-E", "aggregator=,"
]

result = subprocess.run(
    cmd, capture_output=True, text=True, check=True
)

events = []
for line in result.stdout.splitlines():
    parts = line.split("\t")
    if len(parts) != 2:
        continue
    ts = float(parts[0])
    for typ in parts[1].split(","):
        typ = typ.strip()
        if typ in ("10", "14"):
            events.append((ts, typ))

# Khoảng bao phủ từ bản capture thực tế.
# Bao gồm cả OpenFlow message type khác 10/14.
all_ts = []
for line in result.stdout.splitlines():
    parts = line.split("\t")
    if len(parts) == 2:
        all_ts.append(float(parts[0]))

if not all_ts:
    raise RuntimeError("PCAP has no decoded OpenFlow messages")

with HEALTH.open(newline="") as f:
    rows = list(csv.DictReader(f))
    original_fields = list(rows[0].keys())

extra = [
    "of_window_seconds",
    "packet_in_count",
    "flow_mod_count"
]
valid = 0

for i, row in enumerate(rows):
    # Không dùng mẫu đầu do thiếu khoảng CPU/OF trước đó
    row["packet_in_rate"] = ""
    row["flow_mod_rate"] = ""
    for key in extra:
        row[key] = ""

    if i == 0:
        continue

    start = datetime.fromisoformat(
        rows[i-1]["timestamp_utc"]
    ).timestamp()
    end = datetime.fromisoformat(
        row["timestamp_utc"]
    ).timestamp()

    # Chỉ tính cửa sổ nằm hoàn toàn trong PCAP
    if start < min(all_ts) or end > max(all_ts):
        continue

    duration = end - start
    if duration <= 0:
        continue

    packet_in = sum(
        1 for ts, typ in events
        if start < ts <= end and typ == "10"
    )
    flow_mod = sum(
        1 for ts, typ in events
        if start < ts <= end and typ == "14"
    )

    row["of_window_seconds"] = round(duration, 3)
    row["packet_in_count"] = packet_in
    row["flow_mod_count"] = flow_mod
    row["packet_in_rate"] = round(packet_in / duration, 4)
    row["flow_mod_rate"] = round(flow_mod / duration, 4)
    valid += 1

with OUTPUT.open("w", newline="") as f:
    writer = csv.DictWriter(
        f, fieldnames=original_fields + extra
    )
    writer.writeheader()
    writer.writerows(rows)

print("OUTPUT =", OUTPUT)
print("HEALTH_SAMPLES =", len(rows))
print("ALIGNED_WINDOWS =", valid)
print("MISSING_WINDOWS =", len(rows) - valid)
print("TOTAL_PACKET_IN =", sum(
    1 for _, typ in events if typ == "10"
))
print("TOTAL_FLOW_MOD =", sum(
    1 for _, typ in events if typ == "14"
))

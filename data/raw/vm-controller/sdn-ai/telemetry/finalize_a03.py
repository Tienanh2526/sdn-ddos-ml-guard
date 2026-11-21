import csv
from pathlib import Path
from datetime import datetime, timedelta
from bisect import bisect_left
from collections import Counter
from statistics import mean

DIR = Path.home() / "sdn-ai/dataset/attack/A03"

MERGED = DIR / "A03-merged.csv"
HTTP = DIR / "http_load.csv"
OUTPUT = DIR / "A03.csv"
SUMMARY = DIR / "A03-summary.csv"

attack_start = datetime.fromisoformat(
    "2026-11-19T09:06:45.831138+00:00"
)
attack_end = datetime.fromisoformat(
    "2026-11-19T09:14:45.832012+00:00"
)

phases = [
    ("P1", 0, 120, 40),
    ("P2", 120, 240, 60),
    ("P3", 240, 360, 80),
    ("P4", 360, 480, 100)
]

def read_csv(path):
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        return list(reader), list(reader.fieldnames)

def dt(value):
    return datetime.fromisoformat(value)

telemetry, fields = read_csv(MERGED)
http_rows, _ = read_csv(HTTP)

http_rows.sort(key=lambda r: r["timestamp_utc"])
http_times = [dt(r["timestamp_utc"]) for r in http_rows]

output_fields = fields + [
    "scenario_id",
    "traffic_type",
    "phase",
    "expected_udp_rate",
    "label",
    "http_requests",
    "http_successes",
    "http_success_rate",
    "http_latency_mean_ms",
    "http_latency_max_ms"
]

selected = []
excluded = Counter()
used_http = set()

for i in range(1, len(telemetry)):
    row = telemetry[i]

    if not row.get("of_window_seconds"):
        excluded["invalid_window"] += 1
        continue

    window_start = dt(telemetry[i - 1]["timestamp_utc"])
    window_end = dt(row["timestamp_utc"])

    phase = None
    rate = 0

    if window_end <= attack_start:
        phase = "baseline"

    elif window_start >= attack_end:
        phase = "recovery"

    else:
        for name, begin, finish, expected in phases:
            start = attack_start + timedelta(seconds=begin)
            end = (
                attack_end if name == "P4"
                else attack_start + timedelta(seconds=finish)
            )

            if window_start >= start and window_end <= end:
                phase = name
                rate = expected
                break

    if phase is None:
        excluded["phase_boundary"] += 1
        continue

    # HTTP timestamp là thời điểm bắt đầu yêu cầu.
    # Ghép vào cửa sổ [window_start, window_end).
    left = bisect_left(http_times, window_start)
    right = bisect_left(http_times, window_end)

    matched = http_rows[left:right]
    used_http.update(range(left, right))

    success = sum(r["success"] == "1" for r in matched)

    latencies = [
        float(r["response_time_ms"])
        for r in matched
        if r["success"] == "1" and r["response_time_ms"]
    ]

    item = row.copy()
    item.update({
        "scenario_id": "A03",
        "traffic_type": (
            "synthetic_packet_in_burst"
            if phase.startswith("P")
            else "no_a03_burst"
        ),
        "phase": phase,
        "expected_udp_rate": rate,
        "label": int(phase.startswith("P")),
        "http_requests": len(matched),
        "http_successes": success,
        "http_success_rate": (
            round(success / len(matched), 4)
            if matched else ""
        ),
        "http_latency_mean_ms": (
            round(mean(latencies), 3)
            if latencies else ""
        ),
        "http_latency_max_ms": (
            round(max(latencies), 3)
            if latencies else ""
        )
    })

    selected.append(item)

with OUTPUT.open("w", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=output_fields)
    writer.writeheader()
    writer.writerows(selected)

summary_rows = []

for phase in ["baseline", "P1", "P2", "P3", "P4", "recovery"]:
    group = [r for r in selected if r["phase"] == phase]

    if not group:
        continue

    def avg(field):
        values = [
            float(r[field]) for r in group
            if r[field] != ""
        ]
        return round(mean(values), 3) if values else ""

    requests = sum(int(r["http_requests"]) for r in group)
    successes = sum(int(r["http_successes"]) for r in group)

    summary_rows.append({
        "phase": phase,
        "samples": len(group),
        "expected_udp_rate": group[0]["expected_udp_rate"],
        "packet_in_rate_avg": avg("packet_in_rate"),
        "cpu_avg_pct": avg("controller_cpu_pct"),
        "ram_avg_mb": avg("controller_rss_mb"),
        "api_latency_avg_ms": avg("api_latency_ms"),
        "http_requests": requests,
        "http_successes": successes,
        "http_success_rate_pct": (
            round(100 * successes / requests, 2)
            if requests else ""
        ),
        "http_latency_avg_ms": avg("http_latency_mean_ms")
    })

with SUMMARY.open("w", newline="") as f:
    writer = csv.DictWriter(
        f, fieldnames=list(summary_rows[0].keys())
    )
    writer.writeheader()
    writer.writerows(summary_rows)

print("=== A03 FINAL DATASET ===")
print("SOURCE_ROWS =", len(telemetry))
print("SELECTED_ROWS =", len(selected))
print("EXCLUDED =", dict(excluded))
print("LABELS =", dict(Counter(r["label"] for r in selected)))
print("HTTP_MATCHED =", len(used_http), "/", len(http_rows))
print("ROWS_WITHOUT_HTTP =", sum(
    int(r["http_requests"]) == 0 for r in selected
))
print("OUTPUT =", OUTPUT)
print("SUMMARY =", SUMMARY)

print("\n=== A03 SUMMARY ===")
for row in summary_rows:
    print(row)

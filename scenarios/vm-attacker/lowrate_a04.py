#!/usr/bin/env python3
"""A04 — Tấn công tốc độ thấp (low-rate).

Mục đích: tạo "hard positive". 10–30 flow mới/s → Packet-In 10–30 pps, THẤP HƠN flash crowd
hợp lệ (N04, 20–70 pps) → ngưỡng tĩnh theo Packet-In bỏ sót hoặc báo nhầm. Hành vi vẫn là
tấn công: mỗi flow 1 gói, tới cổng UDP đóng (không có chiều về), cổng nguồn đổi liên tục.

Mặc định chạy trên vm-attacker; tráo vai: chạy trên vm-client với --src 192.168.200.11.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "common"))
from groundtruth import GroundTruth, add_src_arg, random_payload, send_udp_phases  # noqa: E402

TARGET = "192.168.200.12"   # vm-web overlay
DST_PORT = 45461

PHASES = [
    ("baseline", 0, 120, 0, 0),
    ("P1", 120, 300, 10, 1),
    ("P2", 300, 480, 20, 1),
    ("P3", 480, 660, 30, 1),
    ("recovery", 660, 780, 0, 0),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="run_id, vd A04-R1")
    ap.add_argument("--out-dir", default=None)
    add_src_arg(ap, "192.168.200.13")
    args = ap.parse_args()

    gt = GroundTruth(
        run_id=args.run, scenario="A04", target=TARGET,
        generator="lowrate_a04.py", phases=PHASES,
        default_label=1, out_dir=args.out_dir, source=args.src,
    ).begin()
    try:
        send_udp_phases(
            gt, DST_PORT, random_payload(b"A04"), src_ip=args.src,
            flow_mode="per_packet", max_duration=780,
        )
    finally:
        gt.finish()


if __name__ == "__main__":
    main()

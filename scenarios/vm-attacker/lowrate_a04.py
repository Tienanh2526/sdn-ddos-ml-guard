#!/usr/bin/env python3
"""A04 — Tấn công tốc độ thấp (low-rate).

Mục đích: tạo "hard positive". Tốc độ 10–30 pps, chồng lấn với mức cao của lưu
lượng bình thường (N03 lên tới 30 pps) → nhãn 1 nhưng Packet-In thấp. Ngưỡng tĩnh
sẽ bỏ sót; mô hình phải dựa thêm vào đặc trưng khác để bắt được.

Chạy trên vm-attacker (nguồn 192.168.200.13), chỉ nhắm vm-web.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "common"))
from groundtruth import GroundTruth, send_udp_phases  # noqa: E402

SRC = "192.168.200.13"      # vm-attacker overlay
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
    args = ap.parse_args()

    gt = GroundTruth(
        run_id=args.run, scenario="A04", target=TARGET,
        generator="lowrate_a04.py", phases=PHASES,
        default_label=1, out_dir=args.out_dir,
    ).begin()
    try:
        send_udp_phases(
            gt, DST_PORT, b"A04_LOW_RATE", src_ip=SRC,
            flow_mode="per_packet", max_duration=780,
        )
    finally:
        gt.finish()


if __name__ == "__main__":
    main()

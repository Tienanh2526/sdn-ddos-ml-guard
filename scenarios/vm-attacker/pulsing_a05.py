#!/usr/bin/env python3
"""A05 — Tấn công ngắt quãng (pulsing).

Mục đích: kiểm tra cơ chế xác nhận đa cửa sổ (temporal confirmation, mục 2.6.1).
Attacker bật ~20s rồi tắt ~20s, lặp lại ở tốc độ cao. Bộ phát hiện phải không báo
động nhấp nháy theo từng cửa sổ, nhưng vẫn giữ trạng thái phát hiện xuyên các đợt.

Nhãn: pha ON = 1, pha OFF = 0. Chạy trên vm-attacker, chỉ nhắm vm-web.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "common"))
from groundtruth import GroundTruth, send_udp_phases  # noqa: E402

SRC = "192.168.200.13"
TARGET = "192.168.200.12"
DST_PORT = 45462

RATE = 80           # pps khi bật
ON = 20             # giây mỗi đợt bật
OFF = 20            # giây mỗi đợt tắt
CYCLES = 10


def build_phases():
    phases = [("baseline", 0, 60, 0, 0)]
    t = 60
    for i in range(CYCLES):
        phases.append((f"on{i+1}", t, t + ON, RATE, 1))
        t += ON
        phases.append((f"off{i+1}", t, t + OFF, 0, 0))
        t += OFF
    phases.append(("recovery", t, t + 60, 0, 0))
    return phases


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="run_id, vd A05-R1")
    ap.add_argument("--out-dir", default=None)
    args = ap.parse_args()

    phases = build_phases()
    total = phases[-1][2]
    gt = GroundTruth(
        run_id=args.run, scenario="A05", target=TARGET,
        generator="pulsing_a05.py", phases=phases,
        default_label=1, out_dir=args.out_dir,
    ).begin()
    try:
        send_udp_phases(
            gt, DST_PORT, b"A05_PULSING", src_ip=SRC,
            flow_mode="per_packet", max_duration=total,
        )
    finally:
        gt.finish()


if __name__ == "__main__":
    main()

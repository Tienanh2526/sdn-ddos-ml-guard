#!/usr/bin/env python3
"""N04 — Flash crowd (lưu lượng hợp lệ đột biến).

Mục đích: tạo "hard negative". Packet-In tăng cao (40–80 pps, chồng lấn vùng tấn
công) NHƯNG đây là lưu lượng hợp lệ từ nhiều phiên của client → nhãn 0.
Ngưỡng tĩnh theo Packet-In sẽ báo nhầm; Random Forest kết hợp đặc trưng mặt phẳng
dữ liệu (ít IP nguồn, tỉ lệ SYN bình thường, HTTP vẫn thành công) thì phân biệt được.

Chạy trên vm-client (nguồn 192.168.200.11). Nên bật song song HTTP QoS monitor để
chứng minh dịch vụ vẫn thông suốt.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "common"))
from groundtruth import GroundTruth, send_udp_phases  # noqa: E402

SRC = "192.168.200.11"      # vm-client overlay
TARGET = "192.168.200.12"   # vm-web overlay
DST_PORT = 45460

# Đột biến hợp lệ: tăng dần rồi giữ ở mức cao, nằm trong vùng chồng lấn với tấn công.
PHASES = [
    ("warmup", 0, 120, 20, 0),
    ("surge1", 120, 300, 50, 0),
    ("surge2", 300, 480, 70, 0),
    ("cooldown", 480, 600, 30, 0),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="run_id, vd N04-R1")
    ap.add_argument("--out-dir", default=None)
    args = ap.parse_args()

    gt = GroundTruth(
        run_id=args.run, scenario="N04", target=TARGET,
        generator="flash_crowd_n04.py", phases=PHASES,
        default_label=0, out_dir=args.out_dir,
    ).begin()
    try:
        send_udp_phases(
            gt, DST_PORT, b"N04_FLASH_CROWD", src_ip=SRC,
            rotate_src_ports=True,   # nhiều phiên → giống nhiều người dùng thật
            max_duration=600,
        )
    finally:
        gt.finish()


if __name__ == "__main__":
    main()

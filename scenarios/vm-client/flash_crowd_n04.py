#!/usr/bin/env python3
"""N04 — Flash crowd (lưu lượng hợp lệ đột biến) bằng PHIÊN HTTP THẬT.

Mục đích: tạo "hard negative" CÓ CĂN CỨ. Mỗi kết nối HTTP mới sinh 2 Packet-In (chiều đi ở
leaf1 + chiều về ở leaf2), nên ở 10→25→35→15 kết nối/s Packet-In lên ~20→50→70→30 pps — chồng
lấn vùng tấn công → ngưỡng tĩnh theo Packet-In báo nhầm. Nhưng HÀNH VI khác tấn công:
bắt tay TCP đủ, có response (reply_ratio ≈ 1), nhiều gói/flow, cổng đích tập trung 8080,
thời điểm đến Poisson. Mô hình nhiều đặc trưng phân biệt được một cách hợp lệ.

Nhãn 0 toàn bộ. Mặc định chạy trên vm-client; tráo vai (--src 192.168.200.13) thì chạy trên
vm-attacker để mô hình không học thuộc "máy attacker = tấn công".
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "common"))
from groundtruth import GroundTruth  # noqa: E402
from traffic import PoissonHttp, Stats, TARGET, assert_source_allowed  # noqa: E402

# (name, begin_s, end_s, kết nối HTTP mới/s, label)
PHASES = [
    ("warmup", 0, 120, 10, 0),
    ("surge1", 120, 300, 25, 0),
    ("surge2", 300, 480, 35, 0),
    ("cooldown", 480, 600, 15, 0),
]
RATE_CAP = 50


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="run_id, vd N04-R1")
    ap.add_argument("--src", default="192.168.200.11", help="IP overlay nguồn (tráo vai: .13)")
    ap.add_argument("--out-dir", default=None)
    args = ap.parse_args()
    assert_source_allowed(args.src)
    assert all(r <= RATE_CAP for *_, r, _ in PHASES)

    gt = GroundTruth(run_id=args.run, scenario="N04", target=TARGET,
                     generator="flash_crowd_n04.py", phases=PHASES,
                     default_label=0, out_dir=args.out_dir, source=args.src).begin()
    stats = Stats()
    http = PoissonHttp(args.src, stats).start()
    start = time.monotonic()
    try:
        for name, b, e, rate, label in PHASES:
            while time.monotonic() - start < b:
                time.sleep(0.05)
            http.rate = rate
            ok0 = stats.http_ok + stats.http_fail
            while time.monotonic() - start < e:
                time.sleep(0.2)
            sent = stats.http_ok + stats.http_fail - ok0
            gt.total_sent += sent
            print(f"PHASE={name} {b}-{e}s CONN_RATE={rate}/s LABEL={label} "
                  f"CONN={sent} {stats.summary()}", flush=True)
    finally:
        http.rate = 0
        http.stop()
        gt.finish()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Lưu lượng NỀN hợp lệ — chạy suốt MỌI lần thu (cả kịch bản bình thường lẫn tấn công).

Mạng thật không bao giờ "im lặng" khi bị tấn công: người dùng vẫn duyệt web, vẫn có phiên
UDP ứng dụng. Chạy nền liên tục giúp:
  - cửa sổ tấn công luôn lẫn lưu lượng hợp lệ (khó hơn, giống thật hơn);
  - máy client xuất hiện ở CẢ HAI lớp → mô hình không học thuộc được "có client = bình thường".

Thành phần (tốc độ dao động ngẫu nhiên theo thời gian, không đều):
  - HTTP: Poisson, tốc độ đi bộ ngẫu nhiên trong [0.5, 3] kết nối/s;
  - UDP ứng dụng: trung bình 1 phiên/20 s, mỗi phiên 5–30 s, 10–40 pps, có echo.

Không ghi groundtruth (nhãn do kịch bản chính quyết định). Chạy trên vm-client:
  python3 ~/scenarios/vm-client/background_mix.py --duration 900
"""
import argparse
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "common"))
from groundtruth import now_iso  # noqa: E402
from traffic import PoissonHttp, Stats, UdpSessions  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--duration", type=float, required=True, help="giây (≥ thời lượng kịch bản + 60)")
    ap.add_argument("--src", default="192.168.200.11")
    ap.add_argument("--http-min", type=float, default=0.5)
    ap.add_argument("--http-max", type=float, default=3.0)
    ap.add_argument("--no-udp", action="store_true")
    args = ap.parse_args()

    stats = Stats()
    http = PoissonHttp(args.src, stats, workers=32).start()
    udp = None if args.no_udp else UdpSessions(args.src, stats).start()
    print(f"BACKGROUND_START= {now_iso()} SRC={args.src}", flush=True)

    rate = random.uniform(args.http_min, args.http_max)
    t_end = time.monotonic() + args.duration
    try:
        while time.monotonic() < t_end:
            # đi bộ ngẫu nhiên mỗi 10 s
            rate = min(args.http_max, max(args.http_min, rate + random.gauss(0, 0.4)))
            http.rate = rate
            time.sleep(min(10, max(0, t_end - time.monotonic())))
            print(f"{now_iso()} http_rate={rate:.2f} {stats.summary()}", flush=True)
    finally:
        http.stop()
        if udp:
            udp.stop()
        print(f"BACKGROUND_END= {now_iso()} {stats.summary()}", flush=True)


if __name__ == "__main__":
    main()

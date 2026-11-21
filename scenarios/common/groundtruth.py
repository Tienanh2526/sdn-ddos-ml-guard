#!/usr/bin/env python3
"""Thư viện chung cho các generator kịch bản.

Nhiệm vụ:
  - Ghi lại mốc thời gian bắt đầu/kết thúc và bảng pha của một lần chạy vào file
    `<run_id>.groundtruth.json`, để `src/build_dataset.py` gán nhãn tự động
    (thay cho việc chép tay attack_start/attack_end như finalize_a03.py).
  - Cung cấp vòng lặp gửi UDP theo pha, có giới hạn tốc độ, dùng chung cho các
    kịch bản N04/A04/A05/A07 (cùng cơ chế với attack_a02.py đã kiểm chứng:
    mỗi datagram sinh đúng 1 Packet-In).

Toàn bộ chạy trong VPC cô lập của nhóm, chỉ nhắm tới vm-web (overlay 192.168.200.12).
"""
import json
import socket
import time
from datetime import datetime, timezone
from pathlib import Path

# Chỉ cho phép sinh lưu lượng tới đúng VM của nhóm. Bất kỳ đích nào khác sẽ bị chặn
# để tránh vô tình bắn ra ngoài phạm vi thử nghiệm.
ALLOWED_TARGETS = {"192.168.200.12"}


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def assert_target_allowed(ip):
    if ip not in ALLOWED_TARGETS:
        raise SystemExit(
            f"TARGET_NOT_ALLOWED={ip} "
            f"(chỉ cho phép {sorted(ALLOWED_TARGETS)}). "
            "Kịch bản chỉ được nhắm tới vm-web trong lab."
        )


class GroundTruth:
    """Bản ghi sự thật nền (ground truth) cho một lần chạy.

    phases: list các tuple (name, begin_s, end_s, rate, label)
      - begin_s/end_s: giây tính từ lúc bắt đầu lần chạy
      - rate: pps dự kiến (0 nếu pha nghỉ)
      - label: 0 bình thường / 1 tấn công cho pha đó
    """

    def __init__(self, run_id, scenario, target, generator,
                 phases, default_label, out_dir=None):
        assert_target_allowed(target)
        self.run_id = run_id
        self.scenario = scenario
        self.target = target
        self.generator = generator
        self.phases = phases
        self.default_label = default_label
        self.start = None
        self.end = None
        self.total_sent = 0
        self.out_dir = Path(out_dir) if out_dir else (
            Path.home() / "sdn-ai" / "groundtruth"
        )

    def begin(self):
        self.start = now_iso()
        print(f"{self.scenario}_START= {self.start}", flush=True)
        print(f"RUN_ID= {self.run_id} TARGET= {self.target}", flush=True)
        return self

    def finish(self):
        self.end = now_iso()
        print(f"{self.scenario}_END= {self.end}", flush=True)
        print(f"TOTAL_SENT= {self.total_sent}", flush=True)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        path = self.out_dir / f"{self.run_id}.groundtruth.json"
        payload = {
            "run_id": self.run_id,
            "scenario": self.scenario,
            "target": self.target,
            "generator": self.generator,
            "default_label": self.default_label,
            "start_utc": self.start,
            "end_utc": self.end,
            "total_sent": self.total_sent,
            "phases": [
                {
                    "name": name,
                    "begin_s": begin_s,
                    "end_s": end_s,
                    "rate": rate,
                    "label": label,
                }
                for (name, begin_s, end_s, rate, label) in self.phases
            ],
        }
        path.write_text(json.dumps(payload, indent=2))
        print(f"GROUNDTRUTH= {path}", flush=True)
        return path


def send_udp_phases(gt, dst_port, payload, src_ip,
                    rotate_src_ports=False, max_duration=None):
    """Gửi UDP theo bảng pha trong gt, có giới hạn tốc độ.

    rotate_src_ports: nếu True, luân phiên nhiều cổng nguồn để mô phỏng nhiều
      phiên kết nối (dùng cho flash crowd N04). Nếu False, một cổng nguồn cố định
      (giống attack_a02.py).
    max_duration: chặn trên tuyệt đối thời lượng (giây) như một lớp an toàn.
    """
    assert_target_allowed(gt.target)
    dst = (gt.target, dst_port)
    total_planned = max((end for _, _, end, _, _ in gt.phases), default=0)
    if max_duration is not None:
        total_planned = min(total_planned, max_duration)

    # Tạo pool socket nguồn.
    socks = []
    n_ports = 8 if rotate_src_ports else 1
    for _ in range(n_ports):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind((src_ip, 0))  # cổng 0 = hệ điều hành tự cấp cổng nguồn
        socks.append(s)

    start = time.monotonic()
    try:
        for name, begin_s, end_s, rate, label in gt.phases:
            if begin_s >= total_planned:
                break
            phase_end = min(end_s, total_planned)

            # Chờ tới đầu pha.
            while time.monotonic() - start < begin_s:
                time.sleep(0.01)

            phase_sent = 0
            if rate <= 0:
                # Pha nghỉ: chỉ chờ cho hết pha.
                while time.monotonic() - start < phase_end:
                    time.sleep(0.05)
            else:
                next_send = time.monotonic()
                idx = 0
                while time.monotonic() - start < phase_end:
                    now = time.monotonic()
                    if now >= next_send:
                        sk = socks[idx % len(socks)]
                        sk.sendto(payload, dst)
                        idx += 1
                        phase_sent += 1
                        gt.total_sent += 1
                        next_send += 1.0 / rate
                    else:
                        time.sleep(min(0.005, next_send - now))

            print(
                f"PHASE={name} {begin_s}-{phase_end}s "
                f"RATE={rate}/s LABEL={label} SENT={phase_sent}",
                flush=True,
            )
    finally:
        for s in socks:
            s.close()

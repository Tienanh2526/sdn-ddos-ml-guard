#!/usr/bin/env python3
"""
detector.py — Phát hiện & giảm thiểu tấn công DoS/DDoS bảo vệ SDN Controller THỜI GIAN THỰC.

Chạy trên vm-controller (cạnh ODL). Mỗi 2 s:
  1. Thu 1 mẫu telemetry giống hệt lúc huấn luyện:
       - controller: CPU/RAM tiến trình ODL, độ trễ RESTCONF, số kết nối OpenFlow, số flow
         (dùng LẠI hàm của telemetry_collector_a03.py — cùng cách đo với dữ liệu huấn luyện);
       - Packet-In / Flow-Mod mỗi giây: tshark trực tiếp trên cổng 6653, đồng thời đọc GÓI GỐC
         nằm trong mỗi Packet-In để biết IP nguồn nào đang gây tải (xác định nguồn ngay tại controller);
       - đặc trưng mặt phẳng dữ liệu: leaf1 chạy dataplane_collector.py --push <controller>:47000.
  2. Dựng cửa sổ 5 mẫu (10 s) bằng CHÍNH hàm window_features() của src/build_dataset.py
     → không lệch đặc trưng giữa huấn luyện và triển khai.
  3. Random Forest (models/detector_rf.joblib) chấm xác suất tấn công; xác nhận k/n cửa sổ.
  4. Máy trạng thái NORMAL → SUSPECT → MITIGATING → RECOVERING → NORMAL và giảm thiểu 2 cấp qua
     RESTCONF của ODL:
       CẤP 1 (bảo vệ controller, không cắt dịch vụ): nguồn tấn công được chuyển tiếp thẳng
              (priority 700 > rule reactive 600) → hết Packet-In, gói vẫn tới đích.
       CẤP 2 (chặn, tùy chọn --allow-drop): nếu sau cấp 1 mô hình vẫn thấy tấn công (hành vi trên
              mặt phẳng dữ liệu) quá --escalate-after giây → drop src->web, có hard-timeout.
     Mọi rule có idle-timeout + hard-timeout và được gỡ khi kết thúc → không bao giờ để lại rule vĩnh viễn.
  5. Ghi decisions.csv (mỗi chu kỳ) + events.jsonl (mốc thời gian phát hiện/cài rule/hiệu lực)
     để đo độ trễ toàn chu trình và hiệu quả giảm thiểu (chương 4.4).

Ví dụ (trên vm-controller, trong venv có pandas/sklearn):
  python3 detector.py --model detector_rf.joblib --mode detect            # chỉ phát hiện
  python3 detector.py --model detector_rf.joblib --mode mitigate          # + cấp 1
  python3 detector.py --model detector_rf.joblib --mode mitigate --allow-drop
"""
from __future__ import annotations

import argparse
import base64
import collections
import csv
import importlib.util
import json
import signal
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))           # src/ (build_dataset)
from odl import ODL, bypass_flow, drop_flow    # noqa: E402

LEAF1, LEAF2 = "openflow:86529385323087", "openflow:103712597946445"
# IP overlay -> (switch, cổng vào, cổng uplink lên spine1). Lấy từ topo (README "Bản đồ mạng").
HOSTS = {
    "192.168.200.11": (LEAF1, 4, 2),   # vm-client   -> leaf1 port 4 (vx-client)
    "192.168.200.13": (LEAF1, 5, 2),   # vm-attacker -> leaf1 port 5 (vx-attacker)
    "192.168.200.12": (LEAF2, 4, 2),   # vm-web      -> leaf2 port 4 (vx-web)
}
PROTECTED = {"192.168.200.12"}         # máy chủ cần bảo vệ: không bao giờ bị bypass/chặn
TARGET = "192.168.200.12"
NOW = lambda: datetime.now(timezone.utc).isoformat()  # noqa: E731


# ============================================================================================
# Nguồn dữ liệu
# ============================================================================================
class PacketInSniffer(threading.Thread):
    """tshark trực tiếp trên cổng 6653: đếm Packet-In (10) / Flow-Mod (14) theo giây và đếm
    Packet-In theo IP nguồn của GÓI GỐC bên trong (lớp IP trong cùng)."""

    def __init__(self, iface="ens4"):
        super().__init__(daemon=True)
        self.iface = iface
        self.lock = threading.Lock()
        self.sec = collections.defaultdict(lambda: {"pin": 0, "fmod": 0, "src": collections.Counter()})
        self.proc = None

    def run(self):
        cmd = ["tshark", "-i", self.iface, "-l", "-n", "-f", "tcp port 6653",
               "-d", "tcp.port==6653,openflow", "-o", "tcp.desegment_tcp_streams:TRUE",
               "-Y", "openflow_v4.type==10 || openflow_v4.type==14",
               "-T", "fields", "-E", "separator=\t", "-E", "occurrence=a", "-E", "aggregator=,",
               "-e", "frame.time_epoch", "-e", "openflow_v4.type", "-e", "ip.src"]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        for line in self.proc.stdout:
            p = line.rstrip("\n").split("\t")
            if len(p) < 3:
                continue
            try:
                sec = int(float(p[0]))
            except ValueError:
                continue
            types = p[1].split(",")
            ips = p[2].split(",")[1:]          # [0] = IP switch (lớp ngoài); sau đó là IP gói gốc
            inner = [ip for ip in ips if ip.startswith("192.168.")]
            with self.lock:
                b = self.sec[sec]
                b["pin"] += types.count("10")
                b["fmod"] += types.count("14")
                b["src"].update(inner)

    def window(self, t_from, t_to):
        """Tổng trong (t_from, t_to] (giây epoch) + xóa dữ liệu cũ."""
        pin = fmod = 0
        src = collections.Counter()
        with self.lock:
            for s in list(self.sec):
                if s <= t_from - 120:
                    del self.sec[s]
                elif t_from < s <= t_to:
                    b = self.sec[s]
                    pin += b["pin"]; fmod += b["fmod"]; src.update(b["src"])
        return pin, fmod, src

    def stop(self):
        if self.proc:
            self.proc.terminate()


class DataPlaneReceiver(threading.Thread):
    """Nhận dòng đặc trưng mặt phẳng dữ liệu (JSON/UDP) do dataplane_collector.py --push gửi."""

    def __init__(self, port=47000):
        super().__init__(daemon=True)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("0.0.0.0", port))
        self.latest, self.latest_t = None, 0.0
        self.lock = threading.Lock()

    def run(self):
        while True:
            data, _ = self.sock.recvfrom(65535)
            try:
                row = json.loads(data.decode())
            except ValueError:
                continue
            with self.lock:
                self.latest, self.latest_t = row, time.time()

    def get(self, max_age=5.0):
        with self.lock:
            return (self.latest if time.time() - self.latest_t <= max_age else None), time.time() - self.latest_t


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ============================================================================================
# Bộ ra quyết định + giảm thiểu
# ============================================================================================
class Engine:
    def __init__(self, args, odl: ODL | None, log, clock=time.time):
        self.a, self.odl, self.log, self.clock = args, odl, log, clock
        self.state = "NORMAL"
        self.pos = collections.deque(maxlen=args.n)
        self.since = self.clock()
        self.target = None            # IP nguồn đang bị xử lý
        self.level = 0
        self.rules = []               # (node, flow_id)
        self.level1_t = None
        self.clear_cycles = 0

    def _set(self, st, **kw):
        self.log("state", frm=self.state, to=st, **kw)
        self.state, self.since = st, self.clock()

    def _install(self, flow, node, level):
        if self.odl is None:
            return
        t0 = self.clock()
        st, ms, body = self.odl.put_flow(node, flow)
        ok = st in (200, 201, 204)
        wait = self.odl.wait_on_switch(node, flow["id"]) if ok else None
        self.log("rule_install", level=level, node=node, flow_id=flow["id"], http=st, rest_ms=round(ms, 1),
                 on_switch_s=None if wait is None else round(wait, 2), total_s=round(self.clock() - t0, 2),
                 error=None if ok else body.get("error"))
        if ok:
            self.rules.append((node, flow["id"]))

    def _remove_all(self, why):
        for node, fid in self.rules:
            st, ms = self.odl.delete_flow(node, fid)
            self.log("rule_remove", node=node, flow_id=fid, http=st, why=why)
        self.rules.clear()
        self.level, self.target, self.level1_t = 0, None, None

    def attribute(self, src: collections.Counter):
        cand = {ip: c for ip, c in src.items() if ip not in PROTECTED and ip in HOSTS}
        tot = sum(cand.values())
        if not tot:
            return None, 0.0, 0
        ip, c = max(cand.items(), key=lambda x: x[1])
        return ip, c / tot, c

    def step(self, prob, src_counter):
        a = self.a
        pos = prob >= a.threshold
        self.pos.append(int(pos))
        confirmed = sum(self.pos) >= a.k
        action = ""
        if self.state == "NORMAL":
            if pos:
                self._set("SUSPECT", prob=round(prob, 3))
        elif self.state == "SUSPECT":
            if confirmed:
                ip, share, cnt = self.attribute(src_counter)
                self._set("MITIGATING", prob=round(prob, 3), top_src=ip, share=round(share, 2), pin_from_src=cnt)
                if a.mode == "mitigate" and ip and share >= a.min_share:
                    node, in_port, uplink = HOSTS[ip]
                    self.target = ip
                    self._install(bypass_flow("aig-l1-" + ip.replace(".", "-"), node, in_port, ip, uplink,
                                              idle_timeout=a.rule_idle, hard_timeout=a.rule_hard), node, 1)
                    self.level, self.level1_t = 1, self.clock()
                    action = f"L1 bypass {ip}"
            elif sum(self.pos) == 0:
                self._set("NORMAL")
        elif self.state == "MITIGATING":
            if pos:
                self.clear_cycles = 0
                if (a.mode == "mitigate" and a.allow_drop and self.level == 1 and self.target
                        and self.clock() - self.level1_t >= a.escalate_after):
                    node, in_port, _ = HOSTS[self.target]
                    self._install(drop_flow("aig-l2-" + self.target.replace(".", "-"), node, in_port,
                                            self.target, TARGET, idle_timeout=a.rule_idle, hard_timeout=a.rule_hard), node, 2)
                    self.level = 2
                    action = f"L2 drop {self.target}"
            else:
                self.clear_cycles += 1
                if self.clear_cycles >= a.clear_cycles:
                    self._set("RECOVERING")
        elif self.state == "RECOVERING":
            if confirmed:
                self._set("MITIGATING", prob=round(prob, 3), reason="tái phát")
                self.clear_cycles = 0
            elif self.clock() - self.since >= a.recover_s:
                if self.rules:
                    self._remove_all("hết tấn công")
                    action = "remove rules"
                self._set("NORMAL")
        return confirmed, action


# ============================================================================================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=str(HERE.parent.parent / "models" / "detector_rf.joblib"))
    ap.add_argument("--telemetry-module", default=str(Path.home() / "aiguard-tools" / "telemetry_collector_a03.py"))
    ap.add_argument("--mode", choices=["detect", "mitigate"], default="detect")
    ap.add_argument("--allow-drop", action="store_true", help="cho phép cấp 2 (drop)")
    ap.add_argument("--threshold", type=float, default=None, help="ngưỡng xác suất (mặc định theo model)")
    ap.add_argument("--k", type=int, default=None); ap.add_argument("--n", type=int, default=None)
    ap.add_argument("--min-share", type=float, default=0.5, help="tỉ lệ Packet-In tối thiểu của nguồn bị xử lý")
    ap.add_argument("--escalate-after", type=float, default=20.0)
    ap.add_argument("--clear-cycles", type=int, default=5, help="số chu kỳ âm liên tiếp để coi là hết tấn công")
    ap.add_argument("--recover-s", type=float, default=30.0)
    ap.add_argument("--rule-idle", type=int, default=60, help="rule tự gỡ sau N giây không còn gói khớp")
    ap.add_argument("--rule-hard", type=int, default=900, help="trần an toàn: rule luôn bị gỡ sau N giây")
    ap.add_argument("--dp-port", type=int, default=47000)
    ap.add_argument("--iface", default="ens4")
    ap.add_argument("--password", default="admin")
    ap.add_argument("--duration", type=float, default=0, help="giây chạy (0 = vô hạn)")
    ap.add_argument("--out-dir", default=str(Path.home() / "aiguard-detector" / datetime.now().strftime("%Y%m%dT%H%M%S")))
    args = ap.parse_args()

    bundle = joblib.load(args.model)
    model, feats = bundle["model"], bundle["features"]
    args.threshold = args.threshold if args.threshold is not None else bundle.get("threshold", 0.5)
    args.k = args.k or bundle.get("confirm_k", 3)
    args.n = args.n or bundle.get("confirm_n", 4)
    W, INTERVAL = int(bundle.get("window_size", 5)), float(bundle.get("interval_s", 2.0))

    import build_dataset as bd                                         # cùng hàm đặc trưng với huấn luyện
    tel = load_module(args.telemetry_module, "telemetry_collector")    # cùng hàm đo với huấn luyện
    auth = base64.b64encode(f"admin:{args.password}".encode()).decode()
    pid = tel.get_java_pid()

    out = Path(args.out_dir); out.mkdir(parents=True, exist_ok=True)
    ev = (out / "events.jsonl").open("a", buffering=1)

    def log(kind, **kw):
        rec = {"ts": NOW(), "event": kind, **kw}
        ev.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"{rec['ts'][11:19]} {kind} {json.dumps(kw, ensure_ascii=False)}", flush=True)

    sniff = PacketInSniffer(args.iface); sniff.start()
    dp = DataPlaneReceiver(args.dp_port); dp.start()
    odl = ODL(password=args.password) if args.mode == "mitigate" else None
    eng = Engine(args, odl, log)

    stop = {"flag": False}
    def on_sig(*_):
        stop["flag"] = True
    signal.signal(signal.SIGTERM, on_sig); signal.signal(signal.SIGINT, on_sig)

    cols = ["timestamp_utc", "state", "prob", "pos", "confirmed", "action", "packet_in_rate", "flow_mod_rate",
            "controller_cpu_pct", "api_latency_ms", "dp_age_s", "top_src", "top_share", "score_ms"]
    dec = csv.DictWriter((out / "decisions.csv").open("w", newline="", buffering=1), fieldnames=cols)
    dec.writeheader()
    log("start", mode=args.mode, allow_drop=args.allow_drop, threshold=args.threshold, k=args.k, n=args.n,
        features=len(feats), odl_pid=pid, out=str(out))

    buf = collections.deque(maxlen=W)
    prev_ticks, prev_t = tel.cpu_ticks(pid), time.monotonic()
    prev_wall = time.time()
    t_start, seq = time.time(), 0
    try:
        while not stop["flag"] and (args.duration <= 0 or time.time() - t_start < args.duration):
            time.sleep(max(0.0, INTERVAL - (time.time() - prev_wall)))
            now_wall = time.time()
            ticks, now = tel.cpu_ticks(pid), time.monotonic()
            cpu = 100 * (ticks - prev_ticks) / tel.HZ / (now - prev_t)
            prev_ticks, prev_t = ticks, now
            lat, status, nodes, flows = tel.query_inventory(auth)
            pin, fmod, src = sniff.window(prev_wall, now_wall)
            dt = now_wall - prev_wall
            prev_wall = now_wall
            row, age = dp.get()
            sample = dict(seq=seq, timestamp_utc=NOW(), t_rel_s=now_wall - t_start, phase="live", label=np.nan,
                          controller_cpu_pct=cpu, controller_rss_mb=tel.rss_mb(pid), api_latency_ms=lat,
                          api_http_status=status, openflow_connections=tel.openflow_connections(),
                          openflow_nodes=nodes, inventory_flow_count=flows,
                          packet_in_rate=pin / dt, flow_mod_rate=fmod / dt)
            for c in bd.DP_COLS:
                sample[c] = float(row[c]) if row and row.get(c) not in (None, "") else np.nan
            buf.append(sample); seq += 1
            prob, pos, confirmed, action, score_ms = np.nan, "", "", "", np.nan
            top, share, _ = eng.attribute(src)
            if len(buf) == W and row is not None:
                t0 = time.perf_counter()
                wf = bd.window_features(pd.DataFrame(list(buf)), W)[-1]
                x = pd.DataFrame([[wf[f] for f in feats]], columns=feats)
                if x.notna().all(axis=None):
                    prob = float(model.predict_proba(x)[0, 1])
                    score_ms = (time.perf_counter() - t0) * 1000
                    confirmed, action = eng.step(prob, src)
                    pos = int(prob >= args.threshold)
            dec.writerow(dict(timestamp_utc=sample["timestamp_utc"], state=eng.state, prob=prob, pos=pos,
                              confirmed=confirmed, action=action, packet_in_rate=round(sample["packet_in_rate"], 2),
                              flow_mod_rate=round(sample["flow_mod_rate"], 2), controller_cpu_pct=round(cpu, 2),
                              api_latency_ms=lat, dp_age_s=round(age, 1), top_src=top, top_share=round(share, 2),
                              score_ms=None if np.isnan(score_ms) else round(score_ms, 2)))
            print(f"{sample['timestamp_utc'][11:19]} {eng.state:10s} p={prob:.2f} pin={sample['packet_in_rate']:6.1f} "
                  f"cpu={cpu:5.1f} api={lat} top={top}({share:.2f}) {action}", flush=True)
    finally:
        if eng.rules and odl:
            eng._remove_all("detector dừng")
        sniff.stop()
        log("stop")


if __name__ == "__main__":
    main()

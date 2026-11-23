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
    """Máy trạng thái NORMAL -> SUSPECT -> MITIGATING -> RECOVERING + quy kết nguồn + giảm thiểu 2 cấp.

    QUY KẾT NGUỒN (bài học A04-M1, 22/11): lấy nguồn có tỉ lệ Packet-In cao nhất trong MỘT mẫu 2 s là sai với
    tấn công tốc độ thấp — lưu lượng nền hợp lệ cũng sinh Packet-In cùng cỡ, mẫu nhỏ thì nhiễu -> đã chặn nhầm
    người dùng .11. Cách làm hiện tại:
      * mức nền Packet-In của TỪNG nguồn (EWMA, chỉ học khi NORMAL)
      * bằng chứng = Packet-In VƯỢT mức nền, cộng dồn trong `attr_window` chu kỳ gần nhất (mặc định 10 s)
      * chỉ nhắm khi đủ bằng chứng (>= min_evidence gói vượt nền, chiếm >= min_share phần vượt nền); chưa đủ
        -> vẫn MITIGATING nhưng CHƯA cài rule, quy kết lại ở chu kỳ sau
      * đang xử lý mà Packet-In vượt nền vẫn đến từ nguồn KHÁC -> nhắm thêm nguồn đó (đa nguồn), và các nguồn
        đã nhắm trước bị gắn "nghi nhắm sai" -> KHÔNG bao giờ nâng lên cấp 2 (drop) => không chặn nhầm người dùng.
      * (A06/A07-M2) quy kết bổ sung chỉ sau khi CẢ cửa sổ bằng chứng nằm sau rule vừa cài (hết nhiễu chuyển
        tiếp), và chỉ gắn "nghi nhắm sai" khi nguồn mới vượt nền >= bằng chứng lúc nhắm nguồn cũ: Packet-In
        của người dùng tăng NHẸ do chính cuộc tấn công (retransmit, flow học chậm) không được chặn cấp 2 lên
        kẻ tấn công thật — với SYN flood chỉ cấp 2 cắt được SYN-ACK trả về (Packet-In ở leaf phía web).
    """

    def __init__(self, args, odl: ODL | None, log, clock=time.time):
        self.a, self.odl, self.log, self.clock = args, odl, log, clock
        self.state = "NORMAL"
        self.pos = collections.deque(maxlen=args.n)
        self.since = self.clock()
        self.targets = {}             # ip -> {"level": 1|2, "t1": thời điểm cấp 1, "doubt": bool}
        self.rules = []               # (node, flow_id)
        self.clear_cycles = 0
        self.hist = collections.deque(maxlen=getattr(args, "attr_window", 5))   # Counter Packet-In/nguồn mỗi chu kỳ
        self.base = collections.defaultdict(float)                               # EWMA Packet-In/chu kỳ mỗi nguồn
        self.n_learn = {}                                                        # số mẫu đã học mỗi nguồn
        self._hit_snap = None                                                    # (thời điểm, tổng gói khớp rule)
        self.alpha = getattr(args, "base_alpha", 0.05)
        self.min_evidence = getattr(args, "min_evidence", 15)
        self.cyc_since_target = 0                                                # chu kỳ kể từ lần nhắm gần nhất

    def _remove_secondary(self, why):
        sec = [ip for ip, t in self.targets.items() if t.get("l1_only")]
        for ip in sec:
            tag = ip.replace(".", "-")
            for node, fid in [r for r in self.rules if r[1].split("-", 2)[-1] == tag]:
                st, ms = self.odl.delete_flow(node, fid)
                self.log("rule_remove", node=node, flow_id=fid, http=st, why=why)
                self.rules.remove((node, fid))
            del self.targets[ip]

    def _rule_hit_pps(self):
        """Tốc độ gói khớp các rule (gói/s) kể từ mốc chụp trước; lần đầu chỉ chụp mốc, trả None."""
        if self.odl is None or not self.rules or not hasattr(self.odl, "flow_packets"):
            return None
        # Từng rule riêng: rule cấp 1 có thể đã bị switch xóa do idle-timeout (sau khi cấp 2 drop ở priority
        # cao hơn hứng hết gói, A04-M3) -> đọc trả None; rule đó bỏ qua chứ không làm hỏng cả phép đo.
        # Chỉ rule của nguồn QUY KẾT CHÍNH: nguồn nhắm bổ sung (cấp 1) có thể là người dùng hợp lệ — lưu lượng
        # của họ khớp rule liên tục nên bộ đếm không bao giờ về 0 -> giữ mãi (A04-M4). Nguồn phụ gỡ theo mô hình.
        prim = {ip.replace(".", "-") for ip, t in self.targets.items() if not t.get("l1_only")}
        counts = {fid: self.odl.flow_packets(node, fid) for node, fid in self.rules
                  if fid.split("-", 2)[-1] in prim}
        counts = {k: v for k, v in counts.items() if v is not None}
        now = self.clock()
        snap, self._hit_snap = self._hit_snap, (now, counts)
        if snap is None or now - snap[0] <= 0 or not counts:
            return None
        common = [k for k in counts if k in snap[1]]
        if not common:
            return None
        return sum(max(0, counts[k] - snap[1][k]) for k in common) / (now - snap[0])

    def _step_down(self):
        """Cấp 2 -> cấp 1 cho mọi nguồn đang bị drop. Rule cấp 1 có thể đã bị switch xóa (idle-timeout khi cấp 2
        hứng hết gói, A04-M3) -> xóa và CÀI LẠI cấp 1 thay vì giả định nó còn trên switch."""
        done = []
        for ip, t in self.targets.items():
            if t["level"] != 2:
                continue
            tag = ip.replace(".", "-")
            node, in_port, uplink = HOSTS[ip]
            for nd, fid in [r for r in self.rules if r[1] in ("aig-l2-" + tag, "aig-l1-" + tag)]:
                st, ms = self.odl.delete_flow(nd, fid)
                self.rules.remove((nd, fid))
                if fid.startswith("aig-l2-"):
                    self.log("rule_remove", node=nd, flow_id=fid, http=st, why="hạ bậc: mô hình đã bình thường")
            self._install(bypass_flow("aig-l1-" + tag, node, in_port, ip, uplink,
                                      idle_timeout=self.a.rule_idle, hard_timeout=self.a.rule_hard), node, 1)
            t["level"] = 1
            done.append(ip)
        self._hit_snap = None
        self.log("step_down", ips=done, why="mô hình bình thường nhưng nguồn còn gửi — bỏ drop, giữ cấp 1")
        return "step down " + ",".join(done)

    @property
    def target(self):
        return next(iter(self.targets), None)

    def _set(self, st, **kw):
        self.log("state", frm=self.state, to=st, **kw)
        self.state, self.since = st, self.clock()

    def _install(self, flow, node, level):
        if self.odl is None:
            return False
        t0 = self.clock()
        st, ms, body = self.odl.put_flow(node, flow)
        ok = st in (200, 201, 204)
        wait = self.odl.wait_on_switch(node, flow["id"]) if ok else None
        self.log("rule_install", level=level, node=node, flow_id=flow["id"], http=st, rest_ms=round(ms, 1),
                 on_switch_s=None if wait is None else round(wait, 2), total_s=round(self.clock() - t0, 2),
                 error=None if ok else body.get("error"))
        if ok:
            self.rules.append((node, flow["id"]))
        return ok

    def _remove_all(self, why):
        for node, fid in self.rules:
            st, ms = self.odl.delete_flow(node, fid)
            self.log("rule_remove", node=node, flow_id=fid, http=st, why=why)
        self.rules.clear()
        self.targets.clear()

    # ------------------------------------------------------------------ quy kết nguồn
    def observe(self, src, learn):
        self.hist.append(collections.Counter({ip: c for ip, c in src.items() if ip in HOSTS and ip not in PROTECTED}))
        if learn:   # chỉ học mức nền khi hệ thống bình thường
            # MỌI máy đã biết, kể cả máy im lặng (0 Packet-In) — nếu chỉ học khi máy xuất hiện thì máy tấn công
            # (im lặng lúc bình thường) có mức nền = tốc độ tấn công ở lần xuất hiện đầu (lỗi A04-M2: base .13 = 5,0)
            for ip in HOSTS:
                if ip not in PROTECTED:
                    # n mẫu đầu: trung bình tích lũy (ước lượng không chệch khi tấn công tới sớm); sau đó EWMA
                    n = self.n_learn[ip] = self.n_learn.get(ip, 0) + 1
                    w = max(self.alpha, 1.0 / n)
                    self.base[ip] += w * (src.get(ip, 0) - self.base[ip])

    def attribute(self, src=None, exclude=()):
        """-> (ip, share_of_excess, excess_count). Chưa đủ bằng chứng -> (None, share, excess).
        `src` (Counter 1 chu kỳ) chỉ dùng khi chưa có lịch sử — để in log."""
        hist = self.hist if self.hist else ([collections.Counter(src)] if src else [])
        win = collections.Counter()
        for c in hist:
            win.update(c)
        nwin = max(1, len(hist))
        excess, signif = {}, {}
        for ip in win:
            if ip in exclude or ip not in HOSTS or ip in PROTECTED:
                continue
            exp = self.base.get(ip, 0.0) * nwin          # kỳ vọng nếu nguồn vẫn như lúc bình thường
            e = win[ip] - exp
            if e <= 0:
                continue
            excess[ip] = e
            # Kiểm định: tăng >= 2 lần mức nền VÀ vượt kỳ vọng > 4 độ lệch chuẩn Poisson. Không có điều này,
            # khi chỉ còn 1 ứng viên (share = 100%) dao động ngẫu nhiên của nguồn hợp lệ sớm muộn cũng vượt
            # ngưỡng tuyệt đối -> nhắm nhầm (replay đã tái hiện).
            signif[ip] = (e >= self.min_evidence and win[ip] >= 2 * exp and e >= 4 * max(1.0, exp) ** 0.5)
        if not excess:
            return None, 0.0, 0
        ip, e = max(excess.items(), key=lambda x: x[1])
        share = e / sum(excess.values())
        if not signif[ip] or share < self.a.min_share:
            return None, share, int(e)
        return ip, share, int(e)

    def _target_l1(self, ip, share, ev, primary=True):
        """primary = quy kết lúc xác nhận tấn công (được phép lên cấp 2). Nguồn nhắm BỔ SUNG trong lúc đang
        xử lý chỉ cấp 1 (chuyển tiếp thẳng: controller được bảo vệ, dịch vụ không bị cắt) — khi không chắc
        chắn thì chọn hành động không gây hại."""
        node, in_port, uplink = HOSTS[ip]
        for t in self.targets.values():
            # Nguồn mới vượt nền ít nhất bằng bằng chứng đã dùng để nhắm nguồn cũ -> nghi nhắm sai nguồn cũ.
            # Nhỏ hơn hẳn (Packet-In người dùng tăng theo tấn công) -> nguồn cũ vẫn là kẻ tấn công chính.
            if ev >= t.get("ev", 0):
                t["doubt"] = True
        self.cyc_since_target = 0
        self.targets[ip] = {"level": 1, "t1": self.clock(), "doubt": False, "l1_only": not primary, "ev": ev}
        self.log("target", ip=ip, share=round(share, 2), excess_pin=ev, primary=primary,
                 base_pin_per_cycle={k: round(v, 2) for k, v in self.base.items()})
        self._install(bypass_flow("aig-l1-" + ip.replace(".", "-"), node, in_port, ip, uplink,
                                  idle_timeout=self.a.rule_idle, hard_timeout=self.a.rule_hard), node, 1)
        return f"L1 bypass {ip}"

    # ------------------------------------------------------------------ một chu kỳ
    def step(self, prob, src_counter):
        a = self.a
        pos = prob >= a.threshold
        self.pos.append(int(pos))
        confirmed = sum(self.pos) >= a.k
        self.observe(src_counter, learn=(self.state == "NORMAL" and not pos))
        self.cyc_since_target += 1
        action = ""
        mitigate = a.mode == "mitigate"
        if self.state == "NORMAL":
            if pos:
                self._set("SUSPECT", prob=round(prob, 3))
        elif self.state == "SUSPECT":
            if confirmed:
                ip, share, ev = self.attribute()
                self._set("MITIGATING", prob=round(prob, 3), top_src=ip, share=round(share, 2), pin_from_src=ev)
                if mitigate and ip:
                    action = self._target_l1(ip, share, ev)
            elif sum(self.pos) == 0:
                self._set("NORMAL")
        elif self.state == "MITIGATING":
            if pos:
                self.clear_cycles = 0
                # quy kết (lại): Packet-In vượt nền từ nguồn CHƯA xử lý -> nhắm thêm
                settled = not self.targets or self.cyc_since_target > (self.hist.maxlen or 1)
                if mitigate and settled:
                    ip, share, ev = self.attribute(exclude=tuple(self.targets))
                    if ip:
                        action = self._target_l1(ip, share, ev, primary=not self.targets)
                # cấp 2 chỉ cho nguồn quy kết chính, không bị nghi nhắm sai
                if mitigate and a.allow_drop:
                    for ip, t in self.targets.items():
                        if t["level"] != 1 or t["l1_only"]:
                            continue
                        if t["doubt"]:
                            if not t.get("doubt_logged"):
                                self.log("escalation_blocked", ip=ip,
                                         why="nghi nhắm sai: Packet-In vượt nền đến từ nguồn khác")
                                t["doubt_logged"] = True
                        elif self.clock() - t["t1"] >= a.escalate_after:
                            node, in_port, _ = HOSTS[ip]
                            self._install(drop_flow("aig-l2-" + ip.replace(".", "-"), node, in_port, ip, TARGET,
                                                    idle_timeout=a.rule_idle, hard_timeout=a.rule_hard), node, 2)
                            t["level"] = 2
                            action = (action + "; " if action else "") + f"L2 drop {ip}"
            else:
                self.clear_cycles += 1
                if self.clear_cycles >= a.clear_cycles:
                    self._set("RECOVERING")
        elif self.state == "RECOVERING":
            if confirmed:
                self._set("MITIGATING", prob=round(prob, 3), reason="tái phát")
                self.clear_cycles = 0
            elif self.clock() - self.since >= a.recover_s:
                # Phản hồi từ rule: mô hình thấy "bình thường" có thể chỉ vì rule đang làm tốt việc (hết Packet-In)
                # chứ nguồn vẫn đang gửi (A04-M2: gỡ -> tấn công quay lại -> cài lại, ~1 phút/lần). Đọc bộ đếm gói
                # của rule trên switch: nguồn bị nhắm còn gửi >= hit_pps -> GIỮ rule, kiểm tra lại sau recover_s.
                pps = self._rule_hit_pps()
                if pps is not None and pps >= getattr(a, "hit_pps", 2.0):
                    self.log("hold", why="rule vẫn bị khớp — nguồn còn gửi", hit_pps=round(pps, 1))
                    self.since = self.clock()
                    action = "hold rules"
                    self._remove_secondary("mô hình đã bình thường — gỡ nguồn nhắm bổ sung")
                    # Hạ bậc (A04/A06-S1): "còn gửi" có thể là lưu lượng HỢP LỆ của chính máy đó (máy bị lợi dụng
                    # vẫn có người dùng) -> không giữ DROP khi mô hình đã thấy bình thường. Về cấp 1: máy dùng
                    # mạng lại được, controller vẫn không nhận Packet-In từ máy đó; tấn công quay lại thì đặc
                    # trưng mặt phẳng dữ liệu vẫn thấy -> xác nhận -> nâng cấp 2 lại ngay (t1 giữ nguyên).
                    if any(t["level"] == 2 for t in self.targets.values()):
                        action = self._step_down()
                else:
                    if self.rules:
                        self._remove_all("hết tấn công" + (f" (rule khớp {pps:.1f} gói/s)" if pps is not None else ""))
                        action = "remove rules"
                    self._set("NORMAL")
        if self.state == "RECOVERING" and self.rules and self._hit_snap is None:
            self._rule_hit_pps()   # chụp mốc bộ đếm ngay khi vào RECOVERING
        elif self.state != "RECOVERING":
            self._hit_snap = None
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
    ap.add_argument("--attr-window", type=int, default=5, help="số chu kỳ cộng dồn bằng chứng quy kết (5 x 2 s)")
    ap.add_argument("--min-evidence", type=int, default=15, help="số Packet-In VƯỢT mức nền tối thiểu để nhắm 1 nguồn")
    ap.add_argument("--base-alpha", type=float, default=0.05, help="hệ số EWMA mức nền Packet-In mỗi nguồn")
    ap.add_argument("--hit-pps", type=float, default=2.0,
                    help="rule còn bị khớp >= mức này (gói/s) thì chưa gỡ dù mô hình thấy bình thường")
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
    # Dự phòng: mất kênh đặc trưng data-plane (leaf hỏng/nghẽn) -> mô hình chỉ dùng đặc trưng phía controller
    fb_model, fb_feats = bundle.get("fallback_model"), bundle.get("fallback_features", [])
    # Mỗi chu kỳ chỉ suy luận 1 cửa sổ: n_jobs=-1 (lúc huấn luyện) chỉ thêm chi phí khởi tạo luồng
    # (compare_models.py: RF ~60 ms -> vài ms với 1 luồng)
    for m_ in (model, fb_model):
        if m_ is not None and hasattr(m_, "n_jobs"):
            m_.set_params(n_jobs=1)
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
            "controller_cpu_pct", "api_latency_ms", "dp_age_s", "top_src", "top_share", "score_ms", "model"]
    dec = csv.DictWriter((out / "decisions.csv").open("w", newline="", buffering=1), fieldnames=cols)
    dec.writeheader()
    log("start", mode=args.mode, allow_drop=args.allow_drop, threshold=args.threshold, k=args.k, n=args.n,
        features=len(feats), fallback_features=len(fb_feats), odl_pid=pid, out=str(out))
    dp_ok = None          # None = chưa biết; True/False = kênh data-plane đang có/mất (log khi đổi)

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
            # chỉ để ghi log: nguồn chiếm nhiều Packet-In nhất trong chu kỳ này (quy kết thật nằm trong Engine)
            raw = {ip: c for ip, c in src.items() if ip in HOSTS and ip not in PROTECTED}
            top = max(raw, key=raw.get) if raw else None
            share = raw[top] / sum(raw.values()) if raw else 0.0
            now_ok = row is not None
            if now_ok != dp_ok and seq > W:
                log("dp_feed", ok=now_ok, age_s=round(age, 1),
                    model="primary" if now_ok else ("fallback" if fb_model is not None else "none"))
                dp_ok = now_ok
            # cửa sổ đủ dữ liệu data-plane -> mô hình chính; không -> mô hình dự phòng (nếu có)
            use, m_feats, m_name = (model, feats, "primary") if now_ok else (fb_model, fb_feats, "fallback")
            if len(buf) == W and use is not None:
                t0 = time.perf_counter()
                wf = bd.window_features(pd.DataFrame(list(buf)), W)[-1]
                x = pd.DataFrame([[wf[f] for f in m_feats]], columns=m_feats)
                if not x.notna().all(axis=None) and m_name == "primary" and fb_model is not None:
                    use, m_feats, m_name = fb_model, fb_feats, "fallback"     # cửa sổ còn mẫu thiếu dp_*
                    x = pd.DataFrame([[wf[f] for f in m_feats]], columns=m_feats)
                if x.notna().all(axis=None):
                    prob = float(use.predict_proba(x)[0, 1])
                    score_ms = (time.perf_counter() - t0) * 1000
                    confirmed, action = eng.step(prob, src)
                    pos = int(prob >= args.threshold)
            dec.writerow(dict(timestamp_utc=sample["timestamp_utc"], state=eng.state, prob=prob, pos=pos,
                              confirmed=confirmed, action=action, packet_in_rate=round(sample["packet_in_rate"], 2),
                              flow_mod_rate=round(sample["flow_mod_rate"], 2), controller_cpu_pct=round(cpu, 2),
                              api_latency_ms=lat, dp_age_s=round(age, 1), top_src=top, top_share=round(share, 2),
                              score_ms=None if np.isnan(score_ms) else round(score_ms, 2),
                              model=m_name if not np.isnan(prob) else ""))
            print(f"{sample['timestamp_utc'][11:19]} {eng.state:10s} p={prob:.2f} pin={sample['packet_in_rate']:6.1f} "
                  f"cpu={cpu:5.1f} api={lat} top={top}({share:.2f}) {m_name if not np.isnan(prob) else '-'} {action}", flush=True)
    finally:
        if eng.rules and odl:
            eng._remove_all("detector dừng")
        sniff.stop()
        log("stop")


if __name__ == "__main__":
    main()

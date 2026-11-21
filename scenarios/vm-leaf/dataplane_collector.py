#!/usr/bin/env python3
"""Thu thập đặc trưng mặt phẳng dữ liệu trên OVS Leaf.

Bổ sung nhóm đặc trưng còn thiếu (features.json: data_plane_traffic) để:
  - So sánh mô hình Traffic-only vs Controller-aware (mục 4.5.2).
  - Xác định nguồn/luồng đáng ngờ (mục 2.6.2).

Hai nguồn số liệu, ghi mỗi INTERVAL giây:
  1. `ovs-ofctl dump-ports`  -> pps/bps theo từng cổng OpenFlow (delta bộ đếm).
  2. `tshark` trên ens4, 2 luồng: gói VXLAN host->leaf (ingress) và leaf->host (egress, chiều
     về), đọc trường của GÓI BÊN TRONG (overlay). Đặc trưng hành vi (xem features()):
       dp_flows, dp_pkts_per_flow  — tấn công ≈ 1 gói/flow, phiên hợp lệ nhiều gói/flow
       dp_reply_ratio              — egress/ingress: hợp lệ có chiều về, flood thì không
       dp_syn_ratio                — SYN dở dang (SYN flood)
       dp_dst_port_entropy         — cổng đích rải rác (flow burst / quét cổng)
       dp_mean_pkt_size, dp_udp_frac, dp_distinct_src

Vì sao lọc như vậy: mỗi host nối vào leaf bằng một tunnel VXLAN riêng (vx-client, vx-attacker,
vx-web ...). Trên ens4 một gói xuất hiện 2 lần (host->leaf rồi leaf->spine) và mang 2 lớp IP
(underlay 10.10.0.x + overlay 192.168.200.x). Bắt `src host <VTEP host> and dst host <leaf>` +
`occurrence=l` (lớp trong cùng) -> đếm đúng 1 lần, đúng IP/giao thức/cờ TCP của overlay.

VTEP của host được tự dò từ OVS (cổng vxlan không phải vx-spine*/vx-leaf*), có thể ghi đè
bằng --host-vteps. Chạy trên vm-leaf1 / vm-leaf2. Cần: sudo, Open vSwitch, tshark.

Lưu ý cho học máy: cột theo cổng (rxpps_<port>) gắn với DANH TÍNH host (cổng 5 = attacker)
→ dùng để xác định nguồn khi giảm thiểu, KHÔNG nên đưa thẳng vào mô hình (rò rỉ nhãn).
Các cột dp_* tổng hợp (không phụ thuộc cổng) mới là đặc trưng cho mô hình.
"""
import argparse
import csv
import json
import math
import os
import re
import subprocess
import tempfile
import threading
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

PORT_RE = re.compile(
    r'port\s+"?(?P<port>[\w\-./]+)"?:.*?'
    r'rx pkts=(?P<rxp>\d+).*?bytes=(?P<rxb>\d+).*?'
    r'tx pkts=(?P<txp>\d+).*?bytes=(?P<txb>\d+)',
    re.S,
)
FABRIC_PREFIXES = ("vx-spine", "vx-leaf")

# Thứ tự trường tshark (mọi trường lấy lớp TRONG CÙNG nhờ -E occurrence=l).
TSHARK_FIELDS = ["frame.time_epoch", "ip.src", "ip.dst", "ip.proto", "ip.len",
                 "tcp.srcport", "tcp.dstport", "udp.srcport", "udp.dstport",
                 "tcp.flags.syn", "tcp.flags.ack"]


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def dump_ports(bridge):
    out = subprocess.run(
        ["ovs-ofctl", "-O", "OpenFlow13", "dump-ports", bridge],
        capture_output=True, text=True, check=True,
    ).stdout
    stats = {}
    for m in PORT_RE.finditer(out):
        stats[m.group("port")] = (
            int(m.group("rxp")), int(m.group("rxb")),
            int(m.group("txp")), int(m.group("txb")),
        )
    return stats


def discover_vteps():
    """Trả về (local_ip, {tên cổng host: remote_ip}) từ cấu hình VXLAN của OVS."""
    out = subprocess.run(
        ["ovs-vsctl", "--format=json", "--columns=name,type,options", "list", "Interface"],
        capture_output=True, text=True, check=True,
    ).stdout
    data = json.loads(out)
    local, hosts = None, {}
    for name, typ, opts in data["data"]:
        if typ != "vxlan":
            continue
        o = dict(opts[1])
        local = o.get("local_ip", local)
        if not name.startswith(FABRIC_PREFIXES) and "remote_ip" in o:
            hosts[name] = o["remote_ip"]
    return local, hosts


def entropy(counter):
    """Entropy Shannon (bit) của phân bố trong Counter."""
    n = sum(counter.values())
    if n == 0:
        return 0.0
    return max(0.0, -sum((c / n) * math.log2(c / n) for c in counter.values()))


def _new_bucket():
    return {"pkts": 0, "bytes": 0, "syn": 0, "udp": 0, "tcp": 0,
            "srcs": set(), "flows": set(), "dports": Counter()}


class PacketAggregator(threading.Thread):
    """Đọc tshark liên tục, gom theo giây (một chiều: ingress hoặc egress)."""

    def __init__(self, iface, bpf):
        super().__init__(daemon=True)
        self.iface, self.bpf = iface, bpf
        self.lock = threading.Lock()
        self.sec = defaultdict(_new_bucket)
        self.proc = None
        self.errf = tempfile.TemporaryFile(mode="w+")   # stderr tshark (không dùng PIPE: đầy là treo)

    def error_text(self):
        self.errf.seek(0)
        return self.errf.read()

    def run(self):
        cmd = ["tshark", "-i", self.iface, "-l", "-n", "-f", self.bpf,
               "-T", "fields", "-E", "separator=\t", "-E", "occurrence=l"]
        for f in TSHARK_FIELDS:
            cmd += ["-e", f]
        self.proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=self.errf, text=True,
        )
        for line in self.proc.stdout:
            p = line.rstrip("\n").split("\t")
            p += [""] * (len(TSHARK_FIELDS) - len(p))
            ts, src, dst, proto, iplen, tsp, tdp, usp, udp_, syn, ack = p[:11]
            try:
                sec = int(float(ts))
            except ValueError:
                continue
            sport = tsp or usp
            dport = tdp or udp_
            with self.lock:
                b = self.sec[sec]
                b["pkts"] += 1
                if iplen.isdigit():
                    b["bytes"] += int(iplen)
                if src:
                    b["srcs"].add(src)
                    b["flows"].add((src, dst, proto, sport, dport))
                if dport:
                    b["dports"][dport] += 1
                if proto == "17":
                    b["udp"] += 1
                elif proto == "6":
                    b["tcp"] += 1
                    if syn == "1" and ack != "1":
                        b["syn"] += 1

    def drain(self, up_to_sec):
        """Lấy và xóa các giây <= up_to_sec, trả về bucket gộp."""
        tot = _new_bucket()
        with self.lock:
            for s in [s for s in self.sec if s <= up_to_sec]:
                b = self.sec.pop(s)
                for k in ("pkts", "bytes", "syn", "udp", "tcp"):
                    tot[k] += b[k]
                tot["srcs"] |= b["srcs"]
                tot["flows"] |= b["flows"]
                tot["dports"].update(b["dports"])
        return tot

    def stop(self):
        if self.proc:
            self.proc.terminate()


def features(ing, egr):
    """Đặc trưng data-plane của 1 khoảng (số đếm tuyệt đối + tỉ lệ hành vi).

    ing: gói host->leaf (đi vào fabric); egr: gói leaf->host (chiều về cho các host này).
    Mọi đặc trưng đều TỔNG HỢP, không phụ thuộc host nào → dùng được cho mô hình.
    """
    pk, fl = ing["pkts"], len(ing["flows"])
    return {
        "dp_pkts": pk,
        "dp_flows": fl,                                  # số 5-tuple hoạt động trong khoảng
        "dp_distinct_src": len(ing["srcs"]),
        "dp_syn": ing["syn"],
        "dp_syn_ratio": round(ing["syn"] / ing["tcp"], 4) if ing["tcp"] else 0.0,
        "dp_udp": ing["udp"],
        "dp_tcp": ing["tcp"],
        "dp_udp_frac": round(ing["udp"] / pk, 4) if pk else 0.0,
        "dp_pkts_per_flow": round(pk / fl, 4) if fl else 0.0,
        "dp_mean_pkt_size": round(ing["bytes"] / pk, 2) if pk else 0.0,
        "dp_dst_port_entropy": round(entropy(ing["dports"]), 4),
        "dp_reply_pkts": egr["pkts"],
        "dp_reply_ratio": round(egr["pkts"] / pk, 4) if pk else 0.0,
    }


DP_FIELDS = list(features(_new_bucket(), _new_bucket()).keys())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bridge", default="br-sdn", help="tên OVS bridge (mặc định br-sdn)")
    ap.add_argument("--iface", default="ens4", help="iface underlay để tshark bắt VXLAN")
    ap.add_argument("--host-vteps", default=None,
                    help="IP underlay của các host, phân cách dấu phẩy (mặc định: tự dò từ OVS)")
    ap.add_argument("--interval", type=float,
                    default=float(os.environ.get("SDN_INTERVAL", "2")))
    ap.add_argument("--samples", type=int,
                    default=int(os.environ.get("SDN_SAMPLES", "450")))
    ap.add_argument("--out", required=True, help="file CSV đầu ra")
    args = ap.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        raise SystemExit(f"Output đã tồn tại: {out}")

    local, hosts = discover_vteps()
    vteps = args.host_vteps.split(",") if args.host_vteps else sorted(set(hosts.values()))
    if not local or not vteps:
        raise SystemExit(f"Không dò được VTEP (local={local}, hosts={hosts}) — dùng --host-vteps")
    src_or = " or ".join(f"src host {v}" for v in vteps)
    dst_or = " or ".join(f"dst host {v}" for v in vteps)
    bpf_in = f"udp dst port 4789 and dst host {local} and ({src_or})"
    bpf_out = f"udp dst port 4789 and src host {local} and ({dst_or})"

    agg_in = PacketAggregator(args.iface, bpf_in)
    agg_out = PacketAggregator(args.iface, bpf_out)
    agg_in.start()
    agg_out.start()
    time.sleep(3)
    for a in (agg_in, agg_out):   # tshark chết (BPF sai, thiếu quyền...) -> dừng, không ghi số 0 giả
        if a.proc is None or a.proc.poll() is not None:
            raise SystemExit(f"TSHARK_FAILED bpf={a.bpf!r}\n{a.error_text()}")

    prev = dump_ports(args.bridge)
    prev_t = time.monotonic()
    ports = sorted(prev.keys())

    fields = ["timestamp_utc"]
    for p in ports:
        fields += [f"rxpps_{p}", f"rxbps_{p}", f"txpps_{p}", f"txbps_{p}"]
    fields += DP_FIELDS

    print(f"BRIDGE={args.bridge} IFACE={args.iface} OUT={out}", flush=True)
    print(f"HOST_PORTS={hosts} LOCAL_VTEP={local}", flush=True)
    print(f"BPF_IN={bpf_in}", flush=True)
    print(f"BPF_OUT={bpf_out}", flush=True)
    print(f"PORTS={ports}", flush=True)

    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for i in range(args.samples):
            time.sleep(args.interval)

            cur = dump_ports(args.bridge)
            now = time.monotonic()
            dt = now - prev_t
            row = {"timestamp_utc": now_iso()}
            for p in ports:
                if p in cur and p in prev:
                    rxp = (cur[p][0] - prev[p][0]) / dt
                    rxb = (cur[p][1] - prev[p][1]) / dt
                    txp = (cur[p][2] - prev[p][2]) / dt
                    txb = (cur[p][3] - prev[p][3]) / dt
                else:
                    rxp = rxb = txp = txb = 0
                row[f"rxpps_{p}"] = round(rxp, 2)
                row[f"rxbps_{p}"] = round(rxb, 2)
                row[f"txpps_{p}"] = round(txp, 2)
                row[f"txbps_{p}"] = round(txb, 2)
            prev, prev_t = cur, now

            cut = int(time.time()) - 1
            row.update(features(agg_in.drain(cut), agg_out.drain(cut)))
            w.writerow(row)
            f.flush()
            print(
                f"{i+1}/{args.samples} pkts={row['dp_pkts']} "
                f"flows={row['dp_flows']} ppf={row['dp_pkts_per_flow']} "
                f"reply={row['dp_reply_ratio']} syn={row['dp_syn_ratio']} "
                f"udp_frac={row['dp_udp_frac']} size={row['dp_mean_pkt_size']} "
                f"dport_H={row['dp_dst_port_entropy']}",
                flush=True,
            )

    agg_in.stop()
    agg_out.stop()
    print(f"COMPLETE={out}", flush=True)


if __name__ == "__main__":
    main()

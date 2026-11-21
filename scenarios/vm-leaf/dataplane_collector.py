#!/usr/bin/env python3
"""Thu thập đặc trưng mặt phẳng dữ liệu trên OVS Leaf.

Bổ sung nhóm đặc trưng còn thiếu (features.json: data_plane_traffic) để:
  - So sánh mô hình Traffic-only vs Controller-aware (mục 4.5.2).
  - Xác định nguồn/luồng đáng ngờ (mục 2.6.2).

Hai nguồn số liệu, ghi mỗi INTERVAL giây:
  1. `ovs-ofctl dump-ports`  -> pps/bps theo từng cổng OpenFlow (delta bộ đếm).
  2. `tshark` trên ens4, CHỈ bắt gói VXLAN đi VÀO leaf từ các máy host (client/attacker/web),
     đọc trường của GÓI BÊN TRONG (overlay) -> số IP nguồn, số flow mới (5-tuple), SYN, UDP/TCP.

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
import os
import re
import subprocess
import threading
import time
from collections import defaultdict
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
TSHARK_FIELDS = ["frame.time_epoch", "ip.src", "ip.dst", "ip.proto",
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


class PacketAggregator(threading.Thread):
    """Đọc tshark liên tục, gom theo giây: gói, IP nguồn, flow (5-tuple), SYN, UDP, TCP."""

    def __init__(self, iface, bpf):
        super().__init__(daemon=True)
        self.iface, self.bpf = iface, bpf
        self.lock = threading.Lock()
        self.sec = defaultdict(lambda: {
            "pkts": 0, "syn": 0, "udp": 0, "tcp": 0, "srcs": set(), "flows": set(),
        })
        self.proc = None

    def run(self):
        cmd = ["tshark", "-i", self.iface, "-l", "-n", "-f", self.bpf,
               "-T", "fields", "-E", "separator=\t", "-E", "occurrence=l"]
        for f in TSHARK_FIELDS:
            cmd += ["-e", f]
        self.proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        )
        for line in self.proc.stdout:
            p = line.rstrip("\n").split("\t")
            p += [""] * (len(TSHARK_FIELDS) - len(p))
            ts, src, dst, proto, tsp, tdp, usp, udp_, syn, ack = p[:10]
            try:
                sec = int(float(ts))
            except ValueError:
                continue
            sport = tsp or usp
            dport = tdp or udp_
            with self.lock:
                b = self.sec[sec]
                b["pkts"] += 1
                if src:
                    b["srcs"].add(src)
                    b["flows"].add((src, dst, proto, sport, dport))
                if proto == "17":
                    b["udp"] += 1
                elif proto == "6":
                    b["tcp"] += 1
                    if syn == "1" and ack != "1":
                        b["syn"] += 1

    def drain(self, up_to_sec):
        """Lấy và xóa các giây <= up_to_sec, trả về tổng hợp (số đếm tuyệt đối trong khoảng)."""
        pkts = syn = udp = tcp = 0
        srcs, flows = set(), set()
        with self.lock:
            for s in [s for s in self.sec if s <= up_to_sec]:
                b = self.sec.pop(s)
                pkts += b["pkts"]
                syn += b["syn"]
                udp += b["udp"]
                tcp += b["tcp"]
                srcs |= b["srcs"]
                flows |= b["flows"]
        return {
            "dp_pkts": pkts, "dp_syn": syn, "dp_udp": udp, "dp_tcp": tcp,
            "dp_distinct_src": len(srcs),
            "dp_new_flows": len(flows),
            "dp_syn_ratio": round(syn / tcp, 4) if tcp else 0.0,
        }

    def stop(self):
        if self.proc:
            self.proc.terminate()


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
    bpf = (f"udp dst port 4789 and dst host {local} and "
           f"({' or '.join(f'src host {v}' for v in vteps)})")

    agg = PacketAggregator(args.iface, bpf)
    agg.start()
    time.sleep(2)

    prev = dump_ports(args.bridge)
    prev_t = time.monotonic()
    ports = sorted(prev.keys())

    fields = ["timestamp_utc"]
    for p in ports:
        fields += [f"rxpps_{p}", f"rxbps_{p}", f"txpps_{p}", f"txbps_{p}"]
    fields += ["dp_pkts", "dp_distinct_src", "dp_new_flows", "dp_syn", "dp_syn_ratio",
               "dp_udp", "dp_tcp"]

    print(f"BRIDGE={args.bridge} IFACE={args.iface} OUT={out}", flush=True)
    print(f"HOST_PORTS={hosts} LOCAL_VTEP={local}", flush=True)
    print(f"BPF={bpf}", flush=True)
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

            row.update(agg.drain(int(time.time()) - 1))
            w.writerow(row)
            f.flush()
            print(
                f"{i+1}/{args.samples} pkts={row['dp_pkts']} "
                f"src={row['dp_distinct_src']} flows={row['dp_new_flows']} "
                f"syn_ratio={row['dp_syn_ratio']} udp={row['dp_udp']} tcp={row['dp_tcp']}",
                flush=True,
            )

    agg.stop()
    print(f"COMPLETE={out}", flush=True)


if __name__ == "__main__":
    main()

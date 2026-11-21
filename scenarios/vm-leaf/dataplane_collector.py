#!/usr/bin/env python3
"""Thu thập đặc trưng mặt phẳng dữ liệu trên OVS Leaf.

Bổ sung nhóm đặc trưng còn thiếu (features.json: data_plane_traffic) để:
  - So sánh mô hình Traffic-only vs Controller-aware (mục 4.5.2).
  - Xác định nguồn/luồng đáng ngờ (mục 2.6.2).

Hai nguồn số liệu, ghi mỗi INTERVAL giây:
  1. `ovs-ofctl dump-ports`  -> pps/bps theo từng cổng (delta bộ đếm).
  2. `tshark` liên tục trên cổng ingress -> số IP nguồn khác nhau, tỉ lệ SYN,
     số gói UDP/TCP trong cửa sổ.

Chạy trên vm-leaf1 / vm-leaf2. Cần: sudo, open vswitch, tshark.
Tìm tên bridge và iface bằng `scenarios/vm-leaf/preflight.sh`.
"""
import argparse
import csv
import os
import re
import subprocess
import sys
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


class PacketAggregator(threading.Thread):
    """Đọc tshark liên tục, gom theo giây: distinct src IP, SYN, UDP, TCP."""

    def __init__(self, iface):
        super().__init__(daemon=True)
        self.iface = iface
        self.lock = threading.Lock()
        self.sec = defaultdict(lambda: {
            "pkts": 0, "syn": 0, "udp": 0, "tcp": 0, "srcs": set()
        })
        self.proc = None

    def run(self):
        cmd = [
            "tshark", "-i", self.iface, "-l", "-n",
            "-T", "fields",
            "-e", "frame.time_epoch",
            "-e", "ip.src",
            "-e", "ip.proto",
            "-e", "tcp.flags.syn",
            "-e", "tcp.flags.ack",
            "-E", "separator=\t",
        ]
        self.proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        )
        for line in self.proc.stdout:
            parts = line.rstrip("\n").split("\t")
            if not parts or not parts[0]:
                continue
            try:
                sec = int(float(parts[0]))
            except ValueError:
                continue
            src = parts[1] if len(parts) > 1 else ""
            proto = parts[2] if len(parts) > 2 else ""
            syn = parts[3] if len(parts) > 3 else ""
            ack = parts[4] if len(parts) > 4 else ""
            with self.lock:
                b = self.sec[sec]
                b["pkts"] += 1
                if src:
                    b["srcs"].add(src)
                if proto == "17":
                    b["udp"] += 1
                elif proto == "6":
                    b["tcp"] += 1
                if syn == "1" and ack != "1":
                    b["syn"] += 1

    def drain(self, up_to_sec):
        """Lấy và xóa các giây <= up_to_sec, trả về tổng hợp."""
        pkts = syn = udp = tcp = 0
        srcs = set()
        with self.lock:
            for s in [s for s in self.sec if s <= up_to_sec]:
                b = self.sec.pop(s)
                pkts += b["pkts"]
                syn += b["syn"]
                udp += b["udp"]
                tcp += b["tcp"]
                srcs |= b["srcs"]
        return {
            "dp_pkts": pkts, "dp_syn": syn, "dp_udp": udp, "dp_tcp": tcp,
            "dp_distinct_src": len(srcs),
            "dp_syn_ratio": round(syn / tcp, 4) if tcp else 0.0,
        }

    def stop(self):
        if self.proc:
            self.proc.terminate()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bridge", required=True, help="tên OVS bridge, vd br-int")
    ap.add_argument("--iface", required=True,
                    help="iface ingress để tshark lắng nghe, vd ens4 hoặc cổng OVS")
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

    agg = PacketAggregator(args.iface)
    agg.start()
    time.sleep(1)

    prev = dump_ports(args.bridge)
    prev_t = time.monotonic()
    ports = sorted(prev.keys())

    fields = ["timestamp_utc"]
    for p in ports:
        fields += [f"rxpps_{p}", f"rxbps_{p}", f"txpps_{p}", f"txbps_{p}"]
    fields += ["dp_pkts", "dp_distinct_src", "dp_syn", "dp_syn_ratio",
               "dp_udp", "dp_tcp"]

    print(f"BRIDGE={args.bridge} IFACE={args.iface} OUT={out}", flush=True)
    print(f"PORTS={ports}", flush=True)

    with out.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for i in range(args.samples):
            cycle = time.monotonic()
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

            packet_feats = agg.drain(int(time.time()) - 1)
            row.update(packet_feats)
            w.writerow(row)
            f.flush()
            print(
                f"{i+1}/{args.samples} "
                f"src={row['dp_distinct_src']} "
                f"syn_ratio={row['dp_syn_ratio']} "
                f"udp={row['dp_udp']} tcp={row['dp_tcp']}",
                flush=True,
            )

    agg.stop()
    print(f"COMPLETE={out}", flush=True)


if __name__ == "__main__":
    main()

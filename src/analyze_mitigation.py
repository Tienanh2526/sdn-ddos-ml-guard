#!/usr/bin/env python3
"""
analyze_mitigation.py — Đánh giá HIỆU QUẢ GIẢM THIỂU (chương 4.4): cùng một kịch bản tấn công,
chạy KHÔNG có module (A06-R1...) và CÓ module thời gian thực (A06-M1...), so sánh theo từng pha:
  - Packet-In tới controller, CPU controller, độ trễ RESTCONF (sức khỏe controller — đề tài bảo vệ)
  - Độ trễ / tỉ lệ thành công HTTP của người dùng hợp lệ (khả năng duy trì dịch vụ)
và dòng thời gian của module: tấn công bắt đầu -> phát hiện -> cài rule -> rule nằm trên switch.

  .\\.venv\\Scripts\\python.exe src\\analyze_mitigation.py --base A06-R1 --mit A06-M1
Đầu ra: reports/mitigation_<mit>.md + reports/figures/mit_<mit>.png
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DS = ROOT / "data" / "raw" / "vm-controller" / "sdn-ai" / "dataset"
C_BASE, C_MIT = "#898781", "#2a78d6"     # xám = không giảm thiểu, xanh = có module
INK, INK2, GRID, BASE, SURFACE = "#0b0b0b", "#52514e", "#e1e0d9", "#c3c2b7", "#fcfcfb"
METRICS = [("packet_in_rate", "Packet-In (pps)"), ("controller_cpu_pct", "CPU controller (%)"),
           ("api_latency_ms", "Độ trễ RESTCONF (ms)"), ("http_latency_mean_ms", "Độ trễ HTTP người dùng (ms)")]


def run_dir(run):
    hits = list(DS.glob(f"*/*/{run}"))
    if not hits:
        raise SystemExit(f"Không thấy thư mục lần chạy {run} trong {DS}")
    return hits[0]


def load(run):
    d = run_dir(run)
    df = pd.read_csv(d / f"{run}.csv")
    gt = json.loads((d / f"{run}.groundtruth.json").read_text())
    t0 = pd.Timestamp(gt["start_utc"])
    df["t"] = (pd.to_datetime(df["timestamp_utc"], format="ISO8601", utc=True) - t0).dt.total_seconds()
    ev = []
    f = d / "detector" / "events.jsonl"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            e = json.loads(line)
            e["t"] = (pd.Timestamp(e["ts"]) - t0).total_seconds()
            ev.append(e)
    return df, gt, ev


def phase_table(df):
    g = df.groupby("phase")[[m for m, _ in METRICS] + ["http_success_rate"]].mean()
    first = df.groupby("phase")["t"].min().sort_values()     # sắp các pha theo thứ tự thời gian
    return g.loc[first.index]


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="lần chạy KHÔNG có module, vd A06-R1")
    ap.add_argument("--mit", required=True, help="lần chạy CÓ module, vd A06-M1")
    a = ap.parse_args()
    b, gtb, _ = load(a.base)
    m, gtm, ev = load(a.mit)

    tb, tm = phase_table(b), phase_table(m)
    common = [p for p in tm.index if p in tb.index]
    rows = []
    for p in common:
        for col, name in METRICS + [("http_success_rate", "HTTP thành công")]:
            x, y = tb.at[p, col], tm.at[p, col]
            rows.append(dict(pha=p, chi_so=name, khong_giam_thieu=x, co_module=y,
                             thay_doi=(y - x) / x * 100 if x and not np.isnan(x) else np.nan))
    cmp = pd.DataFrame(rows)

    # dòng thời gian module
    att_phases = [p for p in gtm["phases"] if p["label"] == 1]
    att_start = min(p["begin_s"] for p in att_phases) if att_phases else np.nan
    def first(kind, **kw):
        for e in ev:
            if e["event"] == kind and all(e.get(k) == v for k, v in kw.items()):
                return e
        return None
    det = first("state", to="MITIGATING")
    sus = first("state", to="SUSPECT")
    l1 = first("rule_install", level=1)
    l2 = first("rule_install", level=2)
    tl = []
    if sus: tl.append(("Nghi ngờ (cửa sổ đầu dương tính)", sus["t"] - att_start))
    if det: tl.append(("Xác nhận tấn công (k/n)", det["t"] - att_start))
    if l1:
        tl.append(("Cài rule cấp 1 xong (RESTCONF)", l1["t"] - att_start))
        if l1.get("on_switch_s") is not None:
            tl.append(("Rule cấp 1 nằm trên switch", l1["t"] - att_start))
    if l2: tl.append(("Cài rule cấp 2 (drop)", l2["t"] - att_start))

    # hình: 4 chỉ số theo thời gian, 2 đường (không / có module) — small multiples, 1 trục mỗi hình
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(len(METRICS), 1, figsize=(11, 9), sharex=True, facecolor=SURFACE)
    for ax, (col, name) in zip(axes, METRICS):
        ax.set_facecolor(SURFACE)
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
        for sp in ("left", "bottom"):
            ax.spines[sp].set_color(BASE)
        ax.grid(axis="y", color=GRID, lw=0.8); ax.tick_params(colors=INK2, labelsize=8)
        for p in att_phases:
            ax.axvspan(p["begin_s"], p["end_s"], color="#f0efec", lw=0)
        ax.plot(b["t"], b[col], color=C_BASE, lw=2, label=f"Không giảm thiểu ({a.base})")
        ax.plot(m["t"], m[col], color=C_MIT, lw=2, label=f"Có module ({a.mit})")
        ax.set_ylabel(name, fontsize=8, color=INK2)
        for j, (e, lab) in enumerate(((det, "phát hiện"), (l1, "cấp 1"), (l2, "cấp 2 (drop)"))):
            if e:
                ax.axvline(e["t"], color=INK2, lw=1, ls="--")
                if ax is axes[0]:   # các mốc có thể cách nhau ~1 s -> so le độ cao để nhãn không đè nhau
                    ax.text(e["t"], ax.get_ylim()[1] * (0.97 - 0.13 * j), f" {lab}", fontsize=8, color=INK2,
                            va="top", backgroundcolor=SURFACE)
    # chú giải ở khung thứ 2, góc trái (pha baseline, không có dữ liệu cao); khung 1 dành cho nhãn mốc
    axes[1 if len(axes) > 1 else 0].legend(loc="upper left", fontsize=8, frameon=False)
    axes[0].set_title(f"Hiệu quả giảm thiểu — {a.mit} so với {a.base} (nền nhạt = pha tấn công)",
                      fontsize=10, color=INK, loc="left")
    axes[-1].set_xlabel("Thời gian từ lúc generator bắt đầu (s)", fontsize=9, color=INK2)
    fig.tight_layout()
    fig_path = ROOT / "reports" / "figures" / f"mit_{a.mit}.png"
    fig_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(fig_path, dpi=150, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)

    def fmt(v, pct=False):
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return "—"
        return f"{v:+.0f}%" if pct else f"{v:.2f}"
    R = [f"# Hiệu quả giảm thiểu — {a.mit} so với {a.base}\n",
         f"Sinh bởi `src/analyze_mitigation.py`. Cùng kịch bản {gtm['scenario']}, lưu lượng nền như nhau; "
         f"{a.base} chạy không có module, {a.mit} chạy có module thời gian thực.\n",
         "## Dòng thời gian của module (tính từ lúc tấn công bắt đầu)\n",
         "| Mốc | Giây sau khi tấn công bắt đầu |", "|---|---|",
         *[f"| {k} | {v:.1f} |" for k, v in tl], "",
         (f"Rule cấp 1: RESTCONF {l1.get('rest_ms')} ms, lên switch sau {l1.get('on_switch_s')} s." if l1 else ""), "",
         "## So sánh theo pha (trung bình)\n",
         "| Pha | Chỉ số | Không giảm thiểu | Có module | Thay đổi |", "|---|---|---|---|---|",
         *[f"| {r.pha} | {r.chi_so} | {fmt(r.khong_giam_thieu)} | {fmt(r.co_module)} | {fmt(r.thay_doi, True)} |"
           for r in cmp.itertuples()], "",
         f"![Hiệu quả giảm thiểu](figures/{fig_path.name})\n"]
    out = ROOT / "reports" / f"mitigation_{a.mit}.md"
    out.write_text("\n".join(R), encoding="utf-8")
    print("\n".join(R[:12]))
    print(f"-> {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

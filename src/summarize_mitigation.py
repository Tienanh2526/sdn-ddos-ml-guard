#!/usr/bin/env python3
"""
summarize_mitigation.py — Bảng & hình TỔNG HỢP hiệu quả giảm thiểu (chương 4.4) trên mọi kịch bản.

Với mỗi cặp (không module, có module): trung bình các chỉ số trên các PHA TẤN CÔNG (nhãn 1; N04 = toàn bộ
lần chạy) + dòng thời gian module (phát hiện, cấp 1 trên switch, cấp 2) + số rule nhắm người dùng hợp lệ.

  .\\.venv\\Scripts\\python.exe src\\summarize_mitigation.py
Đầu ra: reports/mitigation_summary.md + reports/figures/mit_summary.png
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from analyze_mitigation import C_BASE, C_MIT, GRID, INK, INK2, ROOT, SURFACE, BASE, load

PAIRS = [  # (kịch bản, lần chạy không module, lần chạy có module, ghi chú) — bản module cuối cùng
    ("A04 low-rate", "A04-R1", "A04-M4", "sau 3 vòng sửa quy kết (A04-M1..M3)"),
    ("A05 pulsing", "A05-R1", "A05-M2", ""),
    ("A06 SYN flood", "A06-R1", "A06-M3", "sau sửa quy kết bổ sung (A06-M2)"),
    ("A07 flow burst", "A07-R1", "A07-M3", "sau sửa quy kết bổ sung (A07-M2)"),
    ("N04 flash crowd", "N04-R1", "N04-M2", "lưu lượng hợp lệ: mong đợi KHÔNG can thiệp"),
]
# Tráo vai: tấn công từ .11 (vm-client, cũng chạy lưu lượng nền hợp lệ), người dùng đo HTTP = .13 (vm-attacker)
SWAP_PAIRS = [
    ("A04 low-rate — tráo vai", "A04-R4", "A04-S2", "nguồn tấn công .11 mô hình chưa từng thấy khi huấn luyện R1–R3"),
    ("A06 SYN flood — tráo vai", "A06-R4", "A06-S2", ""),
]
METRICS = [("packet_in_rate", "Packet-In (pps)"), ("controller_cpu_pct", "CPU ODL (%)"),
           ("api_latency_ms", "RESTCONF (ms)"), ("http_latency_mean_ms", "HTTP người dùng (ms)"),
           ("http_success_rate", "HTTP thành công")]
LEGIT = {"192.168.200.11"}


def attack_mean(df):
    sel = df["label"] == 1 if (df["label"] == 1).any() else slice(None)
    return df.loc[sel, [m for m, _ in METRICS]].mean()


def timeline(gt, ev, legit=frozenset({"192.168.200.11"})):
    att = [p for p in gt["phases"] if p["label"] == 1]
    t0 = min(p["begin_s"] for p in att) if att else 0.0
    first = lambda f: next((e["t"] - t0 for e in ev if f(e)), np.nan)  # noqa: E731
    return dict(
        phat_hien_s=first(lambda e: e["event"] == "state" and e.get("to") == "MITIGATING"),
        cap1_s=first(lambda e: e["event"] == "rule_install" and e.get("level") == 1),
        cap2_s=first(lambda e: e["event"] == "rule_install" and e.get("level") == 2),
        so_lan_cai_cap1=sum(1 for e in ev if e["event"] == "rule_install" and e.get("level") == 1),
        rule_nguoi_dung=sum(1 for e in ev if e["event"] == "rule_install"
                            and any(ip.replace(".", "-") in e.get("flow_id", "") for ip in legit)),
        drop_nguoi_dung=sum(1 for e in ev if e["event"] == "rule_install" and e.get("level") == 2
                            and any(ip.replace(".", "-") in e.get("flow_id", "") for ip in legit)),
        hold=sum(1 for e in ev if e["event"] == "hold"),
        ha_bac_s=first(lambda e: e["event"] == "step_down") - (max(p["end_s"] for p in att) - t0 if att else 0),
    )


def bg_after(run, t_from):
    """Lưu lượng nền hợp lệ của vm-client (.11): số request HTTP thành công / lỗi SAU mốc t_from (giây UTC)."""
    import json as _j, re
    from analyze_mitigation import run_dir
    d = run_dir(run)
    rows = []
    for line in (d / "background.log").read_text(encoding="utf-8").splitlines():
        m = re.match(r"(\d\S+) .*HTTP_OK=(\d+) HTTP_FAIL=(\d+)", line)
        if m:
            rows.append((pd.Timestamp(m.group(1)).timestamp(), int(m.group(2)), int(m.group(3))))
    if not rows:
        return None
    before = [r for r in rows if r[0] <= t_from]
    b = before[-1] if before else rows[0]
    e = rows[-1]
    return e[1] - b[1], e[2] - b[2]


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    rows = []
    for name, base, mit, note in PAIRS:
        b, _, _ = load(base)
        m, gt, ev = load(mit)
        mb, mm = attack_mean(b), attack_mean(m)
        r = dict(kich_ban=name, base=base, mit=mit, note=note, **timeline(gt, ev))
        for col, _ in METRICS:
            r[col + "_base"], r[col + "_mit"] = mb[col], mm[col]
        rows.append(r)
    t = pd.DataFrame(rows)
    srows = []
    for name, base, mit, note in SWAP_PAIRS:
        b, _, _ = load(base)
        m, gt, ev = load(mit)
        mb, mm = attack_mean(b), attack_mean(m)
        r = dict(kich_ban=name, base=base, mit=mit, note=note, **timeline(gt, ev, legit={"192.168.200.13"}))
        sd = next((pd.Timestamp(e["ts"]).timestamp() for e in ev if e["event"] == "step_down"), None)
        r["bg_after"] = bg_after(mit, sd) if sd else None
        for col, _ in METRICS:
            r[col + "_base"], r[col + "_mit"] = mb[col], mm[col]
        srows.append(r)
    ts = pd.DataFrame(srows)

    # ---- hình: small multiples, mỗi chỉ số 1 khung, 2 cột (không / có module) mỗi kịch bản
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    show = METRICS[:4]
    fig, axes = plt.subplots(1, len(show), figsize=(14, 4.2), facecolor=SURFACE)
    y = np.arange(len(t))
    for ax, (col, label) in zip(axes, show):
        ax.set_facecolor(SURFACE)
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
        for sp in ("left", "bottom"):
            ax.spines[sp].set_color(BASE)
        ax.grid(axis="x", color=GRID, lw=0.8); ax.set_axisbelow(True)
        ax.barh(y + 0.2, t[col + "_base"], height=0.38, color=C_BASE, label="Không giảm thiểu")
        ax.barh(y - 0.2, t[col + "_mit"], height=0.38, color=C_MIT, label="Có module")
        for yi, v in zip(y, t[col + "_mit"]):
            ax.text(v, yi - 0.2, f" {v:.0f}", va="center", fontsize=7, color=INK2)
        for yi, v in zip(y, t[col + "_base"]):
            ax.text(v, yi + 0.2, f" {v:.0f}", va="center", fontsize=7, color=INK2)
        ax.set_yticks(y); ax.set_yticklabels(t["kich_ban"] if ax is axes[0] else [], fontsize=8, color=INK)
        ax.invert_yaxis(); ax.tick_params(colors=INK2, labelsize=8)
        ax.set_title(label, fontsize=9, color=INK, loc="left")
        ax.margins(x=0.18)
    axes[0].legend(loc="lower right", fontsize=8, frameon=False)
    fig.suptitle("Hiệu quả giảm thiểu trên các pha tấn công (trung bình) — N04 là lưu lượng hợp lệ",
                 fontsize=10, color=INK, x=0.01, ha="left")
    fig.tight_layout()
    fig_path = ROOT / "reports" / "figures" / "mit_summary.png"
    fig.savefig(fig_path, dpi=150, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)

    def pct(a, b):
        return "—" if not a or np.isnan(a) or np.isnan(b) else f"{(b - a) / a * 100:+.0f}%"
    f1 = lambda v: "—" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v:.1f}"  # noqa: E731
    R = ["# Tổng hợp hiệu quả giảm thiểu thời gian thực (chương 4.4)\n",
         "Sinh bởi `src/summarize_mitigation.py`. Mỗi kịch bản: một lần chạy KHÔNG có module (R1) và một lần CÓ "
         "module (M), cùng generator, cùng lưu lượng nền. Chỉ số = trung bình trên các **pha tấn công** "
         "(N04: toàn bộ lần chạy). Module chạy `--mode mitigate --allow-drop`.\n",
         "## 1. Sức khỏe controller và dịch vụ\n",
         "| Kịch bản | Packet-In (pps) | CPU ODL (%) | RESTCONF (ms) | HTTP người dùng (ms) | HTTP thành công |",
         "|---|---|---|---|---|---|"]
    for r in t.itertuples():
        cells = []
        for col, _ in METRICS:
            a, b = getattr(r, col + "_base"), getattr(r, col + "_mit")
            cells.append(f"{a:.2f} → {b:.2f}" if col == "http_success_rate" else f"{a:.1f} → {b:.1f} ({pct(a, b)})")
        R.append(f"| {r.kich_ban} | " + " | ".join(cells) + " |")
    R += ["", "## 2. Dòng thời gian module và an toàn\n",
          "| Kịch bản | Lần chạy | Phát hiện (s) | Cấp 1 trên switch (s) | Cấp 2 (s) | Số lần cài cấp 1 | Giữ rule (hold) | Rule nhắm người dùng | Drop người dùng | Ghi chú |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for r in t.itertuples():
        R.append(f"| {r.kich_ban} | {r.mit} | {f1(r.phat_hien_s)} | {f1(r.cap1_s)} | {f1(r.cap2_s)} | "
                 f"{r.so_lan_cai_cap1} | {r.hold} | {r.rule_nguoi_dung} | {r.drop_nguoi_dung} | {r.note} |")
    R += ["", "Thời gian tính từ lúc pha tấn công đầu tiên bắt đầu. *Rule nhắm người dùng* = rule cấp 1 (chuyển tiếp "
          "thẳng, không cắt dịch vụ) đặt lên nguồn hợp lệ do quy kết bổ sung; *Drop người dùng* phải luôn bằng 0.\n",
          f"![Tổng hợp](figures/{fig_path.name})\n",
          "## 3. Quá trình hoàn thiện module (lỗi thấy khi chạy thật → sửa → kiểm thử hồi quy)\n",
          "| Lần chạy | Vấn đề quan sát | Sửa |", "|---|---|---|",
          "| A04-M1 | Quy kết theo 1 mẫu 2 s → nhắm & drop người dùng .11, HTTP thành công 0% | Mức nền Packet-In mỗi nguồn, bằng chứng 10 s, kiểm định ≥ 2× và > 4σ Poisson; nguồn bổ sung chỉ cấp 1 |",
          "| A04-M2 | HTTP 100% nhưng mức nền máy tấn công học sai (5,0) → nhắm .11 trước; gỡ/cài lại ~1 phút/lần | Mức nền cho mọi máy (im lặng = 0); đọc bộ đếm gói của rule trước khi gỡ (hold) |",
          "| A04-M3 | Nhắm đúng ngay; hold hoạt động nhưng đo hỏng khi rule cấp 1 bị idle-timeout | Đo bộ đếm theo từng rule |",
          "| A04-M4 | Cấp 1 cài một lần; Packet-In −78…−95%; nguồn phụ hợp lệ bị giữ rule tới hết | Phản hồi bộ đếm chỉ cho nguồn chính; nguồn phụ gỡ theo mô hình (`test_engine.py`) |",
          "| A06/A07-M2 | Packet-In người dùng tăng nhẹ do chính tấn công → nhắm bổ sung .11 → .13 bị nghi nhắm sai, không lên cấp 2; SYN flood chỉ giảm −66% (SYN-ACK trả về vẫn sinh Packet-In ở leaf phía web) | Quy kết bổ sung sau khi cửa sổ bằng chứng nằm trọn sau rule; chỉ nghi nhắm sai khi nguồn mới vượt nền ≥ bằng chứng nguồn cũ |",
          "| A04/A06-S1 (tráo vai) | Nhắm đúng .11; nhưng .11 còn lưu lượng HỢP LỆ → rule drop vẫn bị khớp → hold giữ DROP tới khi tắt module (máy bị lợi dụng mất mạng sau khi tấn công đã dừng) | Hạ bậc: mô hình bình thường mà rule còn bị khớp → bỏ drop, cài lại cấp 1 (controller vẫn được bảo vệ); tái phát → cấp 2 lại ngay |",
          ""]
    R += ["## 4. Tráo vai máy — module trên nguồn tấn công chưa từng thấy (chạy thật)\n",
          "Tấn công phát từ **.11** (vm-client — máy này đồng thời chạy lưu lượng nền hợp lệ), người dùng đo HTTP là "
          "**.13**. Mô hình huấn luyện cả 25 lần chạy, nhưng mọi lần chạy có module trước đó đều tấn công từ .13. "
          "So với lần chạy cùng vai không module (R4).\n",
          "| Kịch bản | Packet-In (pps) | CPU ODL (%) | RESTCONF (ms) | HTTP người dùng .13 (ms) | HTTP .13 thành công |",
          "|---|---|---|---|---|---|"]
    for r in ts.itertuples():
        cells = []
        for col, _ in METRICS:
            a, b = getattr(r, col + "_base"), getattr(r, col + "_mit")
            cells.append(f"{a:.2f} → {b:.2f}" if col == "http_success_rate" else f"{a:.1f} → {b:.1f} ({pct(a, b)})")
        R.append(f"| {r.kich_ban} | " + " | ".join(cells) + " |")
    R += ["", "| Kịch bản | Lần chạy | Phát hiện (s) | Cấp 1 (s) | Cấp 2 (s) | Rule nhắm .13 | Drop .13 | Hạ bậc sau khi tấn công dừng (s) | Nền .11 sau hạ bậc (OK / lỗi) |",
          "|---|---|---|---|---|---|---|---|---|"]
    for r in ts.itertuples():
        bg = f"{r.bg_after[0]} / {r.bg_after[1]}" if r.bg_after else "—"
        R.append(f"| {r.kich_ban} | {r.mit} | {f1(r.phat_hien_s)} | {f1(r.cap1_s)} | {f1(r.cap2_s)} | {r.rule_nguoi_dung} | "
                 f"{r.drop_nguoi_dung} | {f1(r.ha_bac_s)} | {bg} |")
    R += ["", "*Giới hạn (trung thực):* giảm thiểu theo địa chỉ IP nên trong lúc drop, lưu lượng hợp lệ của chính máy "
          "tấn công (.11) cũng bị chặn; hạ bậc rút ngắn thời gian này về ~1 phút sau khi tấn công dừng (trước khi sửa, "
          "A04/A06-S1: bị chặn tới khi tắt module).\n"]
    out = ROOT / "reports" / "mitigation_summary.md"
    out.write_text("\n".join(R), encoding="utf-8")
    print("\n".join(R[3:12]))
    print(f"-> {out.relative_to(ROOT)}, {fig_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

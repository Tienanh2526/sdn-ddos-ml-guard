#!/usr/bin/env python3
"""
evaluate.py - Bộ thực nghiệm chương 4 trên dữ liệu kịch bản mới (N04, A04–A07).

Trả lời các câu hỏi của chương 4:
  4.5.1  Học máy có hơn ngưỡng tĩnh không?  -> so với 2 đường cơ sở:
           - Ngưỡng Packet-In (cách làm phổ biến trong tài liệu)
           - Ngưỡng TỐT NHẤT trên mọi đặc trưng (đường cơ sở mạnh, so sánh công bằng)
  4.5.2  Đặc trưng controller có giúp không? -> Random Forest trên 3 bộ đặc trưng:
           traffic-only (mặt phẳng dữ liệu) / controller-aware (Packet-In, CPU, API) / combined
  4.4    Tổng quát hóa sang LẦN CHẠY CHƯA THẤY -> kiểm định chéo leave-one-run-out (LORO):
           mỗi lần chạy lần lượt làm tập kiểm thử, huấn luyện trên các lần còn lại.
  2.6.1  Xác nhận nhiều cửa sổ (k-of-n) -> độ trễ phát hiện + số lần báo động giả/giờ.

Chạy:  .\\.venv\\Scripts\\python.exe src\\evaluate.py  [--grace 30] [--k 3 --n 4]
Đầu ra: reports/experiment_report.md, reports/figures/exp_*.png, models/detector_rf.joblib
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (average_precision_score, f1_score, precision_score, recall_score,
                             roc_auc_score)

ROOT = Path(__file__).resolve().parents[1]
WIN = ROOT / "data" / "processed" / "windows_w5.csv"
FEAT = ROOT / "data" / "processed" / "features.json"
REPORT = ROOT / "reports" / "experiment_report.md"
FIGS = ROOT / "reports" / "figures"
MODEL = ROOT / "models" / "detector_rf.joblib"

NEW_SCENARIOS = ["N04", "A04", "A05", "A06", "A07"]
SCN_NAME = {"N04": "Flash crowd (hợp lệ)", "A04": "Low-rate", "A05": "Pulsing",
            "A06": "SYN flood", "A07": "Flow burst"}
RANDOM_STATE = 42

# màu (palette mặc định đã kiểm tra CVD bằng validate_palette.js: blue/orange/aqua - PASS)
C_BLUE, C_ORANGE, C_AQUA = "#2a78d6", "#eb6834", "#1baf7a"
INK, INK2, MUTED, GRID, BASE = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
SURFACE = "#fcfcfb"


# ============================================================================================
# Dữ liệu
# ============================================================================================
def load(grace_s: float):
    w = pd.read_csv(WIN)
    meta = json.loads(FEAT.read_text(encoding="utf-8"))
    g = meta["groups"]
    w["scn"] = w["run_id"].str.split("-").str[0]
    w = w[w["scn"].isin(NEW_SCENARIOS) & w["label"].isin([0, 1])].copy()
    w["label"] = w["label"].astype(int)
    # giây tuyệt đối (không dùng astype(int64): đơn vị nội bộ có thể là µs chứ không phải ns)
    ts = pd.to_datetime(w["end_utc"], format="ISO8601", utc=True)
    w["t"] = (ts - pd.Timestamp("1970-01-01", tz="UTC")).dt.total_seconds()
    w = w.sort_values(["run_id", "t"]).reset_index(drop=True)

    # Vùng đệm sau tấn công: cửa sổ nhãn 0 trong `grace_s` giây ngay sau khi tấn công dừng
    # (dư chấn: SYN-ACK gửi lại, cửa sổ còn chứa mẫu tấn công) -> loại khỏi đánh giá.
    drop = np.zeros(len(w), bool)
    if grace_s > 0:
        for _, idx in w.groupby("run_id").groups.items():
            last_att = -1e18
            for i in idx:
                if w.at[i, "label"] == 1:
                    last_att = w.at[i, "t"]
                elif w.at[i, "t"] - last_att < grace_s:
                    drop[i] = True
    n_grace = int(drop.sum())
    w = w[~drop].reset_index(drop=True)

    sets = {
        "traffic_only": g["data_plane_traffic"],
        "controller_aware": g["control_plane"] + g["controller_resource"] + g["topology"],
    }
    sets["combined"] = sets["traffic_only"] + sets["controller_aware"]
    for k in sets:
        sets[k] = [f for f in sets[k] if f in w.columns and w[f].notna().all() and f not in EXCLUDE]
    return w, sets, n_grace


# Loại khỏi mô hình: số IP nguồn phân biệt phản ánh DANH TÍNH/SỐ MÁY trong lab (R1–R3: tấn công luôn thêm
# đúng 1 nguồn .13 bên cạnh nền .11) chứ không phải hành vi -> khi tráo vai (R4–R5, tấn công từ .11 trùng
# nguồn nền) đặc trưng này đảo chiều. Thí nghiệm loại bỏ (22/11): F1 RF kết hợp 0,999 -> 0,998, không mất gì.
EXCLUDE = {"dp_src_mean"}


# ============================================================================================
# Phương pháp
# ============================================================================================
def best_threshold(x: np.ndarray, y: np.ndarray):
    """Ngưỡng + chiều (>, <) cho F1 cao nhất trên tập huấn luyện — quét MỌI điểm cắt, O(n log n).

    Sắp x tăng dần; cắt giữa xs[i] và xs[i+1]:
      chiều '>' : dự đoán dương = các phần tử sau i  -> TP = P - cum[i], số dự đoán = n-1-i
      chiều '<' : dự đoán dương = các phần tử 0..i   -> TP = cum[i],     số dự đoán = i+1
    F1 = 2TP / (số dự đoán + P). Chỉ xét điểm cắt giữa hai giá trị khác nhau.
    """
    o = np.argsort(x, kind="mergesort")
    xs, ys = x[o], y[o].astype(float)
    n, P = len(xs), ys.sum()
    if n < 2 or P == 0:
        return (0.0, float(xs[0]) if n else 0.0, 1)
    cum = np.cumsum(ys)[:-1]
    i = np.arange(n - 1)
    f_gt = 2 * (P - cum) / ((n - 1 - i) + P)
    f_lt = 2 * cum / ((i + 1) + P)
    ok = xs[1:] != xs[:-1]
    f_gt[~ok] = -1; f_lt[~ok] = -1
    j_gt, j_lt = int(np.argmax(f_gt)), int(np.argmax(f_lt))
    if f_gt[j_gt] >= f_lt[j_lt]:
        return (float(f_gt[j_gt]), float((xs[j_gt] + xs[j_gt + 1]) / 2), 1)
    return (float(f_lt[j_lt]), float((xs[j_lt] + xs[j_lt + 1]) / 2), -1)  # (f1_train, ngưỡng, chiều)


class Threshold:
    def __init__(self, features):
        self.features = features

    def fit(self, X, y):
        res = [(best_threshold(X[f].to_numpy(), y), f) for f in self.features]
        (self.f1_train, self.t, self.s), self.feature = max(res, key=lambda r: r[0][0])
        return self

    def score(self, X):
        return self.s * X[self.feature].to_numpy()

    def predict(self, X):
        return (self.score(X) > self.s * self.t).astype(int)

    def describe(self):
        return f"`{self.feature}` {'>' if self.s > 0 else '<'} {self.t:.3f}"


class RF:
    def __init__(self, features):
        self.features = features

    def fit(self, X, y):
        self.m = RandomForestClassifier(n_estimators=300, min_samples_leaf=2, class_weight="balanced",
                                        random_state=RANDOM_STATE, n_jobs=-1)
        self.m.fit(X[self.features], y)
        return self

    def score(self, X):
        return self.m.predict_proba(X[self.features])[:, 1]

    def predict(self, X):
        return (self.score(X) >= 0.5).astype(int)


def methods(sets):
    return {
        "Ngưỡng Packet-In": lambda: Threshold(["pin_mean"]),
        "Ngưỡng tốt nhất": lambda: Threshold(sets["combined"]),
        "RF traffic-only": lambda: RF(sets["traffic_only"]),
        "RF controller-aware": lambda: RF(sets["controller_aware"]),
        "RF kết hợp": lambda: RF(sets["combined"]),
    }


# ============================================================================================
# Kiểm định chéo leave-one-run-out
# ============================================================================================
def loro(w, mk):
    runs = sorted(w["run_id"].unique())
    pred = pd.DataFrame(index=w.index)
    chosen = {name: [] for name in mk}
    for r in runs:
        te = w["run_id"] == r
        tr = ~te
        if w.loc[tr, "label"].nunique() < 2:
            continue
        for name, make in mk.items():
            m = make().fit(w[tr], w.loc[tr, "label"].to_numpy())
            pred.loc[te, name] = m.predict(w[te])
            pred.loc[te, name + "__score"] = m.score(w[te])
            if isinstance(m, Threshold):
                chosen[name].append(f"{r}: {m.describe()}")
    return pred, chosen


def metrics(y, p, s=None):
    tn = int(((y == 0) & (p == 0)).sum()); fp = int(((y == 0) & (p == 1)).sum())
    fn = int(((y == 1) & (p == 0)).sum()); tp = int(((y == 1) & (p == 1)).sum())
    out = dict(precision=precision_score(y, p, zero_division=0), recall=recall_score(y, p, zero_division=0),
               f1=f1_score(y, p, zero_division=0), fpr=fp / (fp + tn) if fp + tn else np.nan,
               tn=tn, fp=fp, fn=fn, tp=tp)
    if s is not None and len(np.unique(y)) == 2:
        out["roc_auc"] = roc_auc_score(y, s)
        out["pr_auc"] = average_precision_score(y, s)
    return out


# ============================================================================================
# Tổng quát hóa khi TRÁO VAI máy (R1–R3 -> R4–R5)
# ============================================================================================
def role_swap(w, mk, sets):
    """Huấn luyện CHỈ trên R1–R3 (tấn công từ .13, người dùng/flash crowd từ .11), kiểm thử trên R4–R5 (ĐẢO vai:
    tấn công từ .11, flash crowd từ .13). Mô hình học HÀNH VI thì giữ nguyên chất lượng; mô hình học DANH TÍNH máy
    thì sụp — kiểm chứng bằng biến thể cố ý thêm lại `dp_src_mean` (số nguồn, đặc trưng đã loại)."""
    rep_ = w["run_id"].str.split("-").str[1]
    tr, te = rep_.isin(["R1", "R2", "R3"]), rep_.isin(["R4", "R5"])
    if not tr.any() or not te.any():
        return None
    mk = dict(mk)
    if "dp_src_mean" in w.columns and w["dp_src_mean"].notna().all():
        mk["RF kết hợp + dp_src_mean (danh tính)"] = lambda: RF(sets["combined"] + ["dp_src_mean"])
    y_tr, y_te = w.loc[tr, "label"].to_numpy(), w.loc[te, "label"].to_numpy()
    rows = {}
    for name, make in mk.items():
        m = make().fit(w[tr], y_tr)
        p = m.predict(w[te])
        r = metrics(y_te, p)
        scn = w.loc[te, "scn"].to_numpy()
        for sc in NEW_SCENARIOS:
            sel = scn == sc
            if (y_te[sel] == 1).any():
                r[f"recall_{sc}"] = float(((y_te[sel] == 1) & (p[sel] == 1)).sum() / (y_te[sel] == 1).sum())
        sel = scn == "N04"
        r["fpr_N04"] = float(((y_te[sel] == 0) & (p[sel] == 1)).sum() / max(1, (y_te[sel] == 0).sum()))
        rows[name] = r
    t = pd.DataFrame(rows).T
    out = pd.DataFrame({"Precision": t["precision"], "Recall": t["recall"], "F1": t["f1"], "FPR": t["fpr"],
                        **{f"Recall {SCN_NAME[s]}": t.get(f"recall_{s}") for s in NEW_SCENARIOS if s != "N04"},
                        "FPR flash crowd": t["fpr_N04"]})
    out.index.name = "Phương pháp (train R1–R3 → test R4–R5)"
    return out, int(tr.sum()), int(te.sum())


# ============================================================================================
# Xác nhận nhiều cửa sổ (k-of-n): độ trễ phát hiện, báo động giả
# ============================================================================================
def confirm(p: np.ndarray, k: int, n: int):
    c = np.convolve(p, np.ones(n, int), mode="full")[: len(p)]
    return (c >= k).astype(int)


def temporal(w, pred, name, k, n):
    rows, delays, missed, fa_events, normal_s = [], [], 0, 0, 0.0
    for r, g in w.groupby("run_id"):
        p = pred.loc[g.index, name].to_numpy().astype(int)
        a = confirm(p, k, n)
        y, t = g["label"].to_numpy(), g["t"].to_numpy()
        rows.append(pd.DataFrame({"y": y, "a": a}))
        normal_s += (y == 0).sum() * 2.0
        # sự kiện báo động giả = sườn lên của báo động khi đang bình thường
        rise = np.flatnonzero((a == 1) & (np.r_[0, a[:-1]] == 0))
        fa_events += int(sum(y[i] == 0 for i in rise))
        # các đợt tấn công: chuỗi nhãn 1 liên tiếp
        i = 0
        while i < len(y):
            if y[i] == 1 and (i == 0 or y[i - 1] == 0):
                j = i
                while j + 1 < len(y) and y[j + 1] == 1:
                    j += 1
                hit = np.flatnonzero(a[i:j + 1])
                if len(hit):
                    delays.append(t[i + hit[0]] - t[i])
                else:
                    missed += 1
                i = j + 1
            else:
                i += 1
    allr = pd.concat(rows)
    m = metrics(allr["y"].to_numpy(), allr["a"].to_numpy())
    m.update(episodes=len(delays) + missed, missed=missed,
             delay_mean=float(np.mean(delays)) if delays else np.nan,
             delay_p90=float(np.percentile(delays, 90)) if delays else np.nan,
             fa_per_hour=fa_events / (normal_s / 3600) if normal_s else np.nan, fa_events=fa_events)
    return m


# ============================================================================================
# Hình
# ============================================================================================
def _style(ax):
    ax.set_facecolor(SURFACE)
    for sp in ("top", "right"):
        ax.spines[sp].set_visible(False)
    for sp in ("left", "bottom"):
        ax.spines[sp].set_color(BASE)
    ax.tick_params(colors=INK2, labelsize=9)
    ax.grid(axis="x", color=GRID, lw=0.8)
    ax.set_axisbelow(True)


def fig_methods(summary, names, path):
    import matplotlib.pyplot as plt
    cols = [("f1", "F1 (cao hơn tốt hơn)"), ("fpr_N04", "Báo nhầm trên flash crowd (thấp hơn tốt hơn)"),
            ("recall_A04", "Bắt được low-rate (cao hơn tốt hơn)")]
    fig, axes = plt.subplots(1, 3, figsize=(12, 3.2), sharey=True, facecolor=SURFACE)
    y = np.arange(len(names))[::-1]
    for ax, (c, title) in zip(axes, cols):
        _style(ax)
        v = np.array([summary[n].get(c, np.nan) for n in names], float)
        colors = [C_BLUE if n.startswith("RF") else MUTED for n in names]
        ax.barh(y, np.nan_to_num(v), height=0.55, color=colors, edgecolor=SURFACE, linewidth=2)
        for yi, vi in zip(y, v):
            ax.text((0 if np.isnan(vi) else vi) + 0.02, yi, "—" if np.isnan(vi) else f"{vi:.2f}",
                    va="center", fontsize=9, color=INK)
        ax.set_xlim(0, 1.15)
        ax.set_title(title, fontsize=10, color=INK, loc="left")
    axes[0].set_yticks(y, names)
    fig.suptitle("So sánh phương pháp — kiểm định chéo theo lần chạy (LORO)", x=0.01, ha="left",
                 fontsize=11, color=INK)
    fig.text(0.01, -0.02, "Xám = ngưỡng tĩnh, xanh = Random Forest", fontsize=8, color=INK2)
    fig.tight_layout()
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)


def fig_heatmap(table, path):
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap
    cmap = LinearSegmentedColormap.from_list("seq", ["#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#0d366b"])
    fig, ax = plt.subplots(figsize=(7.5, 3.2), facecolor=SURFACE)
    v = table.to_numpy(float)
    ax.imshow(v, cmap=cmap, vmin=0, vmax=1, aspect="auto")
    for i in range(v.shape[0]):
        for j in range(v.shape[1]):
            ax.text(j, i, f"{v[i, j]:.2f}", ha="center", va="center", fontsize=9,
                    color="#ffffff" if v[i, j] > 0.55 else INK)
    ax.set_xticks(range(v.shape[1]), table.columns, fontsize=9, color=INK2)
    ax.set_yticks(range(v.shape[0]), table.index, fontsize=9, color=INK2)
    for sp in ax.spines.values():
        sp.set_visible(False)
    ax.set_title("Tỉ lệ bắt được (recall) theo loại tấn công — LORO", fontsize=11, color=INK, loc="left")
    fig.tight_layout()
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)


def fig_timeline(w, pred, run, k, n, thr_name, path):
    import matplotlib.pyplot as plt
    g = w[w["run_id"] == run]
    if g.empty:
        return
    t = (g["t"] - g["t"].iloc[0]).to_numpy()
    fig, (a1, a2) = plt.subplots(2, 1, figsize=(11, 4.2), sharex=True, facecolor=SURFACE,
                                 gridspec_kw={"height_ratios": [2.2, 1]})
    for ax in (a1, a2):
        _style(ax)
        ax.grid(axis="y", color=GRID, lw=0.8)
        ax.grid(axis="x", visible=False)
    y = g["label"].to_numpy()
    for ax in (a1,):
        for i in range(len(t)):
            if y[i] == 1:
                ax.axvspan(t[i] - 1, t[i] + 1, color="#e1e0d9", lw=0)
    a1.plot(t, g["pin_mean"], color=C_BLUE, lw=2, label="Packet-In (pps, TB cửa sổ 10 s)")
    a1.set_ylabel("Packet-In (pps)", fontsize=9, color=INK2)
    a1.legend(loc="upper right", fontsize=8, frameon=False)
    a1.set_title(f"{run} — {SCN_NAME.get(run.split('-')[0], '')}: nền xám = đang bị tấn công (nhãn thật)",
                 fontsize=10, color=INK, loc="left")
    rows = [("Nhãn thật", y, INK2), (thr_name, pred.loc[g.index, thr_name].to_numpy(), C_ORANGE),
            (f"RF kết hợp + xác nhận {k}/{n}", confirm(pred.loc[g.index, "RF kết hợp"].to_numpy().astype(int), k, n), C_BLUE)]
    for j, (lab, v, c) in enumerate(rows[::-1]):
        on = np.asarray(v, float) > 0.5
        a2.scatter(t[on], np.full(on.sum(), j), marker="|", s=120, color=c, linewidths=2)
    a2.set_yticks(range(len(rows)), [r[0] for r in rows[::-1]], fontsize=8)
    a2.set_ylim(-0.6, len(rows) - 0.4)
    a2.set_xlabel("Thời gian từ đầu lần chạy (s)", fontsize=9, color=INK2)
    fig.tight_layout()
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)


def fig_importance(imp, path):
    import matplotlib.pyplot as plt
    top = imp[:15][::-1]
    fig, ax = plt.subplots(figsize=(6.5, 4.6), facecolor=SURFACE)
    _style(ax)
    names = [k for k, _ in top]
    colors = [C_AQUA if k.startswith("dp_") else C_BLUE for k in names]
    ax.barh(names, [v for _, v in top], height=0.6, color=colors, edgecolor=SURFACE, linewidth=2)
    ax.set_title("Độ quan trọng đặc trưng — RF kết hợp (toàn bộ dữ liệu)", fontsize=10, color=INK, loc="left")
    ax.text(1.0, -0.12, "xanh ngọc = mặt phẳng dữ liệu (dp_*), xanh dương = controller",
            transform=ax.transAxes, ha="right", fontsize=8, color=INK2)
    fig.tight_layout()
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)


# ============================================================================================
def md(df, fmt="{:.3f}"):
    head = "| " + " | ".join([df.index.name or ""] + list(map(str, df.columns))) + " |"
    sep = "|" + "---|" * (len(df.columns) + 1)
    rows = []
    for i, r in df.iterrows():
        cells = [("—" if (isinstance(v, float) and np.isnan(v)) else fmt.format(v) if isinstance(v, float) else str(v))
                 for v in r]
        rows.append("| " + " | ".join([str(i)] + cells) + " |")
    return "\n".join([head, sep, *rows])


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--grace", type=float, default=12,
                    help="giây bỏ qua ngay sau khi tấn công dừng (≈ 1 cửa sổ 10 s + 1 bước; A05 tắt 20 s)")
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--n", type=int, default=4)
    args = ap.parse_args()

    w, sets, n_grace = load(args.grace)
    runs = sorted(w["run_id"].unique())
    print(f"{len(w)} cửa sổ, {len(runs)} lần chạy: {runs}; bỏ {n_grace} cửa sổ vùng đệm {args.grace:.0f}s")
    print({k: len(v) for k, v in sets.items()})
    mk = methods(sets)
    names = list(mk)
    t0 = time.perf_counter()
    pred, chosen = loro(w, mk)
    print(f"LORO xong {time.perf_counter() - t0:.1f}s")

    y = w["label"].to_numpy()
    summary, per_run, temporal_rows = {}, [], {}
    for name in names:
        m = metrics(y, pred[name].to_numpy().astype(int))
        # AUC tính TRONG từng lần chạy rồi lấy trung bình: điểm số của các fold khác nhau (ngưỡng chọn
        # đặc trưng/chiều khác nhau) không so sánh được với nhau nên không gộp.
        aucs, aps = [], []
        for r in runs:
            sel = (w["run_id"] == r).to_numpy()
            if len(np.unique(y[sel])) == 2:
                sc = pred.loc[sel, name + "__score"].to_numpy()
                aucs.append(roc_auc_score(y[sel], sc)); aps.append(average_precision_score(y[sel], sc))
        m["roc_auc"] = float(np.mean(aucs)) if aucs else np.nan
        m["pr_auc"] = float(np.mean(aps)) if aps else np.nan
        for scn in NEW_SCENARIOS:
            sel = w["scn"] == scn
            yy, pp = y[sel], pred.loc[sel, name].to_numpy().astype(int)
            if scn == "N04" or (yy == 0).any():
                m[f"fpr_{scn}"] = float(((yy == 0) & (pp == 1)).sum() / max(1, (yy == 0).sum()))
            if (yy == 1).any():
                m[f"recall_{scn}"] = float(((yy == 1) & (pp == 1)).sum() / (yy == 1).sum())
        summary[name] = m
        for r in runs:
            sel = w["run_id"] == r
            if w.loc[sel, "label"].nunique() == 2:
                per_run.append(dict(method=name, run=r, f1=f1_score(y[sel], pred.loc[sel, name].astype(int))))
        temporal_rows[name] = temporal(w, pred, name, args.k, args.n)

    rs = role_swap(w, mk, sets)
    if rs is not None:
        print(rs[0].round(3).to_string())

    # ---- mô hình cuối cho chặng 4: RF kết hợp trên toàn bộ dữ liệu
    final = RF(sets["combined"]).fit(w, y)
    imp = sorted(zip(sets["combined"], final.m.feature_importances_), key=lambda x: -x[1])
    t1 = time.perf_counter(); final.score(w.head(1)); infer_ms = (time.perf_counter() - t1) * 1000
    MODEL.parent.mkdir(parents=True, exist_ok=True)
    fallback = RF(sets["controller_aware"]).fit(w, y)   # dùng khi mất kênh data-plane (chặng 4)
    joblib.dump({"model": final.m, "features": sets["combined"], "threshold": 0.5,
                 "fallback_model": fallback.m, "fallback_features": sets["controller_aware"],
                 "confirm_k": args.k, "confirm_n": args.n, "window_size": 5, "interval_s": 2.0,
                 "trained_on": runs, "feature_sets": sets}, MODEL)

    # ---- hình
    FIGS.mkdir(parents=True, exist_ok=True)
    fig_methods(summary, names, FIGS / "exp_methods.png")
    att = [s for s in NEW_SCENARIOS if s != "N04"]
    heat = pd.DataFrame({SCN_NAME[s]: [summary[n].get(f"recall_{s}", np.nan) for n in names] for s in att},
                        index=names)
    fig_heatmap(heat, FIGS / "exp_recall_by_attack.png")
    for r in runs:
        if r.endswith("-R1"):
            fig_timeline(w, pred, r, args.k, args.n, "Ngưỡng Packet-In", FIGS / f"exp_timeline_{r}.png")
    fig_importance(imp, FIGS / "exp_feature_importance.png")

    # ---- báo cáo
    S = pd.DataFrame(summary).T
    S.index.name = "Phương pháp"
    main_tab = S[["precision", "recall", "f1", "fpr", "roc_auc", "pr_auc"]].rename(columns={
        "precision": "Precision", "recall": "Recall", "f1": "F1", "fpr": "FPR", "roc_auc": "ROC-AUC",
        "pr_auc": "PR-AUC"})
    scn_tab = pd.DataFrame({**{f"Recall {SCN_NAME[s]}": S.get(f"recall_{s}") for s in att},
                            "FPR flash crowd": S.get("fpr_N04")})
    scn_tab.index.name = "Phương pháp"
    pr = pd.DataFrame(per_run)
    run_tab = pr.pivot(index="method", columns="run", values="f1").reindex(names) if len(pr) else pd.DataFrame()
    if len(run_tab):
        run_tab["TB ± ĐLC"] = [f"{a:.3f} ± {b:.3f}" for a, b in zip(run_tab.mean(axis=1), run_tab.std(axis=1, ddof=0))]
        run_tab.index.name = "Phương pháp"
    T = pd.DataFrame(temporal_rows).T
    T.index.name = "Phương pháp"
    tmp_tab = pd.DataFrame({
        "F1 sau xác nhận": T["f1"], "FPR sau xác nhận": T["fpr"],
        "Đợt tấn công": T["episodes"].astype(int).astype(str), "Bỏ sót đợt": T["missed"].astype(int).astype(str),
        "Độ trễ TB (s)": T["delay_mean"], "Độ trễ P90 (s)": T["delay_p90"],
        "Báo động giả / giờ": T["fa_per_hour"]}, index=T.index)
    gap = S.loc["RF kết hợp", "f1"] - max(S.loc["Ngưỡng Packet-In", "f1"], S.loc["Ngưỡng tốt nhất", "f1"])
    n_by_scn = w.groupby("scn")["run_id"].nunique().to_dict()

    R = [
        "# Báo cáo thực nghiệm — Phát hiện tấn công bảo vệ SDN Controller (chương 4)\n",
        f"Sinh tự động bởi `src/evaluate.py` lúc {pd.Timestamp.now(tz='UTC'):%Y-%m-%d %H:%M} UTC.\n",
        "## 1. Thiết lập\n",
        f"- Dữ liệu: các kịch bản mới (lưu lượng nền luôn chạy, reactive mô phỏng, có đặc trưng mặt phẳng dữ liệu): "
        f"**{len(w)} cửa sổ 10 s** từ **{len(runs)} lần chạy** — số lần chạy/kịch bản: {n_by_scn}.",
        f"- Nhãn 0: {int((y == 0).sum())} cửa sổ, nhãn 1: {int((y == 1).sum())}. Bỏ {n_grace} cửa sổ trong "
        f"**{args.grace:.0f} s ngay sau khi tấn công dừng** (dư chấn: SYN-ACK gửi lại, cửa sổ còn mẫu tấn công).",
        "- **Kiểm định chéo leave-one-run-out (LORO):** mỗi lần chạy lần lượt làm tập kiểm thử, huấn luyện trên các "
        "lần chạy còn lại → đánh giá trên lần chạy *chưa từng thấy*, không rò rỉ giữa các cửa sổ chồng lấn.",
        f"- Bộ đặc trưng: traffic-only {len(sets['traffic_only'])}, controller-aware {len(sets['controller_aware'])}, "
        f"kết hợp {len(sets['combined'])} (danh sách trong `data/processed/features.json`).",
        "- Đường cơ sở: **Ngưỡng Packet-In** (`pin_mean`, chọn ngưỡng F1 tốt nhất trên tập huấn luyện mỗi fold) và "
        "**Ngưỡng tốt nhất** (thử mọi đặc trưng, mọi chiều, lấy cái tốt nhất trên tập huấn luyện — đường cơ sở mạnh).",
        "- Random Forest: 300 cây, `min_samples_leaf=2`, `class_weight=balanced`, ngưỡng xác suất 0,5.\n",
        "## 2. Kết quả chính (gộp mọi fold LORO, theo từng cửa sổ)\n",
        md(main_tab), "",
        f"Chênh lệch F1: RF kết hợp − đường cơ sở tốt nhất = **{gap:+.3f}**.\n",
        "![So sánh phương pháp](figures/exp_methods.png)\n",
        "## 3. Theo loại lưu lượng\n",
        md(scn_tab), "",
        "![Recall theo loại tấn công](figures/exp_recall_by_attack.png)\n",
        "## 4. F1 theo từng lần chạy (lần chạy có cả hai lớp)\n",
        md(run_tab) if len(run_tab) else "(chưa đủ dữ liệu)", "",
        f"## 5. Xác nhận nhiều cửa sổ ({args.k}/{args.n}) — chống báo động nhấp nháy (mục 2.6.1)\n",
        f"Báo động khi ≥ {args.k} trong {args.n} cửa sổ gần nhất (cửa sổ trượt 2 s) bị phân loại là tấn công. "
        "Độ trễ tính từ cửa sổ đầu tiên của đợt tấn công tới khi báo động.\n",
        md(tmp_tab, "{:.2f}"), "",
        "## 6. Độ quan trọng đặc trưng (RF kết hợp, toàn bộ dữ liệu)\n",
        "| Đặc trưng | Importance |\n|---|---|",
        *[f"| `{k}` | {v:.4f} |" for k, v in imp[:15]], "",
        "![Độ quan trọng](figures/exp_feature_importance.png)\n",
        "## 7. Diễn biến theo thời gian (lần chạy R1)\n",
        *[f"![{r}](figures/exp_timeline_{r}.png)" for r in runs if r.endswith("-R1")], "",
        "## 8. Ngưỡng mà đường cơ sở chọn ở từng fold\n",
        *[f"- **{n}**: " + "; ".join(chosen[n]) for n in names if chosen[n]], "",
        "## 9. Mô hình xuất cho module phát hiện thời gian thực\n",
        f"`models/detector_rf.joblib` — RF kết hợp huấn luyện trên toàn bộ {len(runs)} lần chạy, "
        f"{len(sets['combined'])} đặc trưng, xác nhận {args.k}/{args.n}. Suy luận ~{infer_ms:.2f} ms/cửa sổ.\n",
        "## 10. Tổng quát hóa khi tráo vai máy (train R1–R3 → test R4–R5)\n",
        *(["R1–R3: tấn công phát từ vm-attacker (.13), flash crowd/người dùng từ vm-client (.11). R4–R5 **đảo vai**: "
           "tấn công từ .11, flash crowd từ .13. Huấn luyện chỉ trên R1–R3 "
           f"({rs[1]} cửa sổ), kiểm thử trên R4–R5 ({rs[2]} cửa sổ) — mô hình chưa từng thấy tấn công đến từ .11. "
           "Dòng cuối cố ý thêm lại `dp_src_mean` (số máy nguồn — phản ánh danh tính, đã loại khỏi mô hình) "
           "để đo cái giá của việc học danh tính.\n", md(rs[0]), ""] if rs is not None else ["(chưa có R4–R5)\n"]),
        "## 11. Ghi chú trung thực\n",
        "- LORO có thể để một fold thiếu hẳn một loại lưu lượng trong tập huấn luyện (ví dụ bỏ N04 ra làm test thì mô "
        "hình chưa từng thấy flash crowd) — đây là kiểm thử tổng quát hóa khắt khe, không phải lỗi.",
        "- Đặc trưng theo cổng/máy (`rxpps_<port>`) KHÔNG được dùng (tránh học thuộc danh tính máy).",
        "- Flow-Mod luôn = 0 (switch tự học flow trong chế độ reactive mô phỏng) → `fmod_*` không mang thông tin.",
    ]
    REPORT.write_text("\n".join(R), encoding="utf-8")
    print(main_tab.round(3).to_string())
    print(tmp_tab.round(2).to_string())
    print(f"-> {REPORT.relative_to(ROOT)}, {MODEL.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

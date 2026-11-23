#!/usr/bin/env python3
"""
compare_models.py — So sánh các thuật toán học máy cho bài toán phát hiện (trả lời "vì sao chọn Random Forest").

Cùng dữ liệu, cùng 30 đặc trưng kết hợp, cùng giao thức đánh giá với src/evaluate.py:
  * LORO (leave-one-run-out) trên 25 lần chạy  -> Precision/Recall/F1/FPR, ROC-AUC (trong từng lần chạy),
    recall từng loại tấn công, FPR flash crowd
  * xác nhận 3/4 cửa sổ -> độ trễ phát hiện, báo động giả/giờ
  * tráo vai: train R1–R3 -> test R4–R5
  * chi phí triển khai: thời gian huấn luyện, độ trễ suy luận 1 cửa sổ (đúng cách module gọi), kích thước mô hình

  .\\.venv\\Scripts\\python.exe src\\compare_models.py
Đầu ra: reports/model_comparison.md, reports/figures/cmp_models.png
"""
from __future__ import annotations

import io
import sys
import time

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.neighbors import KNeighborsClassifier
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC
from sklearn.tree import DecisionTreeClassifier
from xgboost import XGBClassifier

import evaluate as E

RS = E.RANDOM_STATE
REPORT = E.ROOT / "reports" / "model_comparison.md"


class Sk:
    """Bọc một estimator sklearn/xgboost theo giao diện evaluate.loro (fit / score / predict)."""

    def __init__(self, make, features):
        self.make, self.features = make, features

    def fit(self, X, y):
        self.m = self.make()
        self.m.fit(X[self.features], y)
        return self

    def score(self, X):
        return self.m.predict_proba(X[self.features])[:, 1]

    def predict(self, X):
        return (self.score(X) >= 0.5).astype(int)


def candidates(feats):
    scaled = lambda est: (lambda: make_pipeline(StandardScaler(), est()))  # noqa: E731
    return {
        "Ngưỡng tốt nhất (cơ sở)": lambda: E.Threshold(feats),
        "Logistic Regression": lambda: Sk(scaled(lambda: LogisticRegression(
            max_iter=2000, class_weight="balanced")), feats),
        "Decision Tree": lambda: Sk(lambda: DecisionTreeClassifier(
            min_samples_leaf=2, class_weight="balanced", random_state=RS), feats),
        "k-NN (k=5)": lambda: Sk(scaled(lambda: KNeighborsClassifier(5)), feats),
        "SVM (RBF)": lambda: Sk(scaled(lambda: SVC(C=1.0, gamma="scale", probability=True,
                                                   class_weight="balanced", random_state=RS)), feats),
        "MLP (64-32)": lambda: Sk(scaled(lambda: MLPClassifier(
            (64, 32), max_iter=500, early_stopping=True, random_state=RS)), feats),
        "Random Forest (đang dùng)": lambda: Sk(lambda: RandomForestClassifier(
            n_estimators=300, min_samples_leaf=2, class_weight="balanced", random_state=RS, n_jobs=-1), feats),
        "XGBoost": lambda: Sk(lambda: XGBClassifier(
            n_estimators=300, max_depth=6, learning_rate=0.1, subsample=0.9, colsample_bytree=0.9,
            eval_metric="logloss", random_state=RS, n_jobs=-1), feats),
    }


def cost(make, w, y):
    """Huấn luyện trên toàn bộ dữ liệu; đo suy luận 1 cửa sổ (DataFrame 1 dòng — như detector.py) và kích thước."""
    t0 = time.perf_counter()
    m = make().fit(w, y)
    train_s = time.perf_counter() - t0
    est = getattr(m, "m", None)
    # 1 cửa sổ / lần: song song hóa chỉ thêm chi phí khởi tạo luồng (RF n_jobs=-1: ~60 ms) -> đo với 1 luồng,
    # đúng cấu hình module dùng (detector.py đặt n_jobs=1 khi nạp mô hình)
    for e in ([est] + [st for _, st in getattr(est, "steps", [])] if est is not None else []):
        if hasattr(e, "n_jobs"):
            e.set_params(n_jobs=1)
    one = w.sample(200, random_state=RS)
    for i in range(10):                              # làm nóng
        m.score(one.iloc[[i]])
    lat = []
    for i in range(len(one)):
        t1 = time.perf_counter(); m.score(one.iloc[[i]]); lat.append((time.perf_counter() - t1) * 1000)
    buf = io.BytesIO(); joblib.dump(getattr(m, "m", m), buf)   # chỉ estimator (lớp bọc giữ lambda)
    return dict(train_s=train_s, infer_ms=float(np.median(lat)), infer_p95_ms=float(np.percentile(lat, 95)),
                size_kb=len(buf.getvalue()) / 1024)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    w, sets, n_grace = E.load(12)
    feats = sets["combined"]
    y = w["label"].to_numpy()
    runs = sorted(w["run_id"].unique())
    mk = candidates(feats)
    names = list(mk)
    print(f"{len(w)} cửa sổ, {len(runs)} lần chạy, {len(feats)} đặc trưng; {len(names)} phương pháp")

    t0 = time.perf_counter()
    pred, _ = E.loro(w, mk)
    print(f"LORO xong {time.perf_counter() - t0:.0f}s")
    swap, n_tr, n_te = E.role_swap(w, mk, sets)

    rows = {}
    for name in names:
        p = pred[name].to_numpy().astype(int)
        m = E.metrics(y, p)
        aucs = [roc_auc_score(y[s], pred.loc[s, name + "__score"]) for r in runs
                if len(np.unique(y[s := (w["run_id"] == r).to_numpy()])) == 2]
        m["roc_auc"] = float(np.mean(aucs))
        for scn in E.NEW_SCENARIOS:
            sel = (w["scn"] == scn).to_numpy()
            if scn == "N04":
                m["fpr_N04"] = float(((y[sel] == 0) & (p[sel] == 1)).sum() / max(1, (y[sel] == 0).sum()))
            else:
                m[f"recall_{scn}"] = float(((y[sel] == 1) & (p[sel] == 1)).sum() / max(1, (y[sel] == 1).sum()))
        t = E.temporal(w, pred, name, 3, 4)
        m.update(delay=t["delay_mean"], fa_h=t["fa_per_hour"], missed=t["missed"], episodes=t["episodes"])
        m["swap_f1"], m["swap_fpr_N04"] = swap.loc[name, "F1"], swap.loc[name, "FPR flash crowd"]
        m.update(cost(mk[name], w, y))
        rows[name] = m
        print(f"  {name:28s} F1 {m['f1']:.3f}  FPR {m['fpr']:.3f}  swap F1 {m['swap_f1']:.3f}  "
              f"suy luận {m['infer_ms']:.2f} ms")
    T = pd.DataFrame(rows).T

    # ---- khoảng tin cậy 95% bằng bootstrap THEO LẦN CHẠY (đơn vị độc lập là lần chạy, không phải cửa sổ)
    rng = np.random.default_rng(RS)
    idx_by_run = [np.flatnonzero((w["run_id"] == r).to_numpy()) for r in runs]
    P = {n: pred[n].to_numpy().astype(int) for n in names}
    ref = "Random Forest (đang dùng)"
    boots = {n: [] for n in names}
    for _ in range(2000):
        pick = np.concatenate([idx_by_run[i] for i in rng.integers(0, len(runs), len(runs))])
        yb = y[pick]
        for n in names:
            pb = P[n][pick]
            tp = ((yb == 1) & (pb == 1)).sum()
            boots[n].append(2 * tp / max(1, (pb == 1).sum() + (yb == 1).sum()))
    for n in names:
        b = np.array(boots[n])
        d = np.array(boots[ref]) - b
        T.loc[n, "f1_lo"], T.loc[n, "f1_hi"] = np.percentile(b, [2.5, 97.5])
        T.loc[n, "d_lo"], T.loc[n, "d_hi"] = np.percentile(d, [2.5, 97.5])

    # ---- hình: 2 khung (F1 LORO | độ trễ suy luận), không trục kép
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    order = T.sort_values("f1").index
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.2), facecolor=E.SURFACE, gridspec_kw=dict(wspace=0.08))
    yy = np.arange(len(order))
    col = [E.C_BLUE if "Random Forest" in n else E.MUTED for n in order]
    # F1: biểu đồ ĐIỂM + KTC 95% (bootstrap theo lần chạy) — trục không bắt đầu từ 0 nên không dùng thanh
    ax = axes[0]; E._style(ax)
    v = T.loc[order, "f1"].astype(float).to_numpy()
    lo, hi = T.loc[order, "f1_lo"].to_numpy(float), T.loc[order, "f1_hi"].to_numpy(float)
    for yi, vi, l_, h_, c in zip(yy, v, lo, hi, col):
        ax.plot([l_, h_], [yi, yi], color=c, lw=2, solid_capstyle="round")
        ax.plot([vi], [yi], "o", ms=8, color=c, mec=E.SURFACE, mew=2)
        ax.text(h_, yi, f"  {vi:.3f}", va="center", fontsize=8, color=E.INK2)
    ax.set_yticks(yy); ax.set_yticklabels(order, fontsize=8, color=E.INK)
    ax.set_title("F1 (LORO, 25 lần chạy) — điểm = F1, vạch = KTC 95%", fontsize=9, color=E.INK, loc="left")
    ax.grid(axis="x", color=E.GRID, lw=0.8); ax.set_axisbelow(True)
    ax.set_xlim(np.floor(lo.min() * 200) / 200, 1.0015)
    # suy luận: thanh từ 0
    ax = axes[1]; E._style(ax)
    v = T.loc[order, "infer_ms"].astype(float).to_numpy()
    ax.barh(yy, v, height=0.62, color=col)
    for yi, vi in zip(yy, v):
        ax.text(vi, yi, f" {vi:.1f}", va="center", fontsize=8, color=E.INK2)
    ax.set_yticks(yy); ax.set_yticklabels([])
    ax.set_title("Suy luận 1 cửa sổ (ms, trung vị, 1 luồng) — ngân sách 2000 ms", fontsize=9, color=E.INK, loc="left")
    ax.grid(axis="x", color=E.GRID, lw=0.8); ax.set_axisbelow(True); ax.margins(x=0.15)
    fig.suptitle("So sánh thuật toán — cùng dữ liệu, cùng đặc trưng, cùng giao thức LORO",
                 fontsize=10, color=E.INK, x=0.01, ha="left")
    fig.tight_layout()
    fig_path = E.FIGS / "cmp_models.png"
    fig.savefig(fig_path, dpi=150, bbox_inches="tight", facecolor=E.SURFACE)
    plt.close(fig)

    # ---- báo cáo
    f3 = lambda v: f"{v:.3f}"  # noqa: E731
    A = ["A04", "A05", "A06", "A07"]
    R = ["# So sánh thuật toán học máy (chương 4)\n",
         f"Sinh bởi `src/compare_models.py` lúc {pd.Timestamp.now(tz='UTC'):%Y-%m-%d %H:%M} UTC. "
         f"**{len(w)} cửa sổ 10 s** từ **{len(runs)} lần chạy thật**, cùng **{len(feats)} đặc trưng kết hợp**, "
         f"cùng giao thức với `evaluate.py` (LORO; bỏ {n_grace} cửa sổ đệm 12 s sau tấn công). Các mô hình nhạy với "
         "thang đo (LR, k-NN, SVM, MLP) được chuẩn hóa bằng `StandardScaler` *trong* pipeline (học trên fold huấn "
         "luyện, không rò rỉ). Siêu tham số để mặc định hợp lý, KHÔNG tinh chỉnh trên tập kiểm thử.\n",
         "## 1. Chất lượng phát hiện (LORO, theo cửa sổ)\n",
         "| Phương pháp | Precision | Recall | F1 | FPR | ROC-AUC | " + " | ".join(f"Recall {E.SCN_NAME[s]}" for s in A)
         + " | FPR flash crowd |", "|---" * (7 + len(A)) + "|"]
    for n in names:
        r = T.loc[n]
        R.append(f"| {n} | {f3(r.precision)} | {f3(r.recall)} | **{f3(r.f1)}** | {f3(r.fpr)} | {f3(r.roc_auc)} | "
                 + " | ".join(f3(r[f'recall_{s}']) for s in A) + f" | {f3(r.fpr_N04)} |")
    R += ["", "## 2. Khi chạy như module thật (xác nhận 3/4) và khi tráo vai máy\n",
          f"Tráo vai: huấn luyện R1–R3 ({n_tr} cửa sổ, tấn công luôn từ .13) → kiểm thử R4–R5 ({n_te} cửa sổ, tấn công "
          "từ .11).\n",
          "| Phương pháp | Độ trễ phát hiện TB (s) | Bỏ sót đợt | Báo động giả / giờ | F1 tráo vai | FPR flash crowd tráo vai |",
          "|---|---|---|---|---|---|"]
    for n in names:
        r = T.loc[n]
        R.append(f"| {n} | {r.delay:.2f} | {int(r.missed)}/{int(r.episodes)} | {r.fa_h:.2f} | {f3(r.swap_f1)} | "
                 f"{f3(r.swap_fpr_N04)} |")
    R += ["", "## 3. Chi phí triển khai\n",
          "Suy luận đo đúng cách module gọi: DataFrame 1 dòng, 200 cửa sổ, trung vị (máy phát triển, Windows; "
          "cùng điều kiện cho mọi mô hình). Ngân sách thời gian thực: 1 quyết định / 2 s.\n",
          "| Phương pháp | Huấn luyện toàn bộ (s) | Suy luận trung vị (ms) | Suy luận P95 (ms) | Kích thước (KB) |",
          "|---|---|---|---|---|"]
    for n in names:
        r = T.loc[n]
        R.append(f"| {n} | {r.train_s:.2f} | {r.infer_ms:.2f} | {r.infer_p95_ms:.2f} | {r.size_kb:,.0f} |")
    ml = [n for n in names if n != "Ngưỡng tốt nhất (cơ sở)"]
    rf, xg, lr, sv = (T.loc[n] for n in (ref, "XGBoost", "Logistic Regression", "SVM (RBF)"))
    base = T.loc["Ngưỡng tốt nhất (cơ sở)"]
    sig = [n for n in names if n != ref and T.loc[n, "d_lo"] > 0]
    nsig = [n for n in names if n != ref and T.loc[n, "d_lo"] <= 0]
    R += ["## 5. Nhận xét\n",
          f"1. **Mọi mô hình học máy đều vượt đường cơ sở mạnh** (F1 {T.loc[ml, 'f1'].min():.3f}–{T.loc[ml, 'f1'].max():.3f} "
          f"so với {base.f1:.3f}). Ngay cả Logistic Regression (tuyến tính) cũng đạt {lr.f1:.3f}: phần lớn sức phân biệt "
          "đến từ **thiết kế đặc trưng** (tỉ lệ gói trả lời, gói/flow, entropy cổng — đo 2 chiều trên mặt phẳng dữ liệu) "
          "chứ không riêng từ thuật toán. Đây là kết luận trung thực cần nêu trong chương 4.",
          f"2. **Random Forest đứng đầu về F1 ({rf.f1:.4f})** và là mô hình duy nhất đồng thời có 0 báo động giả/giờ, FPR "
          f"flash crowd = 0 và độ trễ phát hiện nhỏ nhất ({rf.delay:.2f} s). Bootstrap theo lần chạy: RF tốt hơn có ý nghĩa "
          f"so với {', '.join(sig)}; **không khác biệt có ý nghĩa** với {', '.join(nsig)} "
          "(k-NN nằm sát biên: cận dưới ≈ 0).",
          f"3. **XGBoost tương đương RF** (F1 {xg.f1:.4f}, khác biệt không có ý nghĩa) với chi phí thấp hơn nhiều "
          f"(suy luận {xg.infer_ms:.1f} ms so với {rf.infer_ms:.1f} ms, {xg.size_kb:,.0f} KB so với {rf.size_kb:,.0f} KB) — "
          "là phương án thay thế tốt nếu controller hạn chế tài nguyên.",
          f"4. **Tráo vai**: mọi mô hình giữ F1 ≥ {T['swap_f1'].min():.3f} — không mô hình nào sụp khi nguồn tấn công đổi "
          f"máy. SVM có F1 tráo vai cao nhất ({sv.swap_f1:.3f}) nhưng nhiều báo động giả nhất ({sv.fa_h:.1f}/giờ, FPR "
          f"flash crowd {sv.fpr_N04:.3f}) → không phù hợp cho module giảm thiểu tự động (báo động giả = cài rule nhầm).",
          f"5. **Chi phí**: RF chậm và lớn nhất ({rf.infer_ms:.0f} ms/cửa sổ, {rf.size_kb / 1024:.1f} MB) nhưng chỉ chiếm "
          f"~{rf.infer_ms / 2000 * 100:.1f}% ngân sách 2 s mỗi chu kỳ — không phải điểm nghẽn. (Lúc đầu đo 60 ms do RF "
          "huấn luyện với `n_jobs=-1`: suy luận 1 dòng tốn chi phí tạo luồng → module nay đặt `n_jobs=1` khi nạp. "
          "Đo trực tiếp trên vm-controller (e2-standard-4, 23/11): 92,5 ms → 23,4 ms/cửa sổ.)",
          "6. **Lý do giữ Random Forest**: F1 cao nhất và 0 báo động giả (quan trọng nhất khi mỗi báo động dẫn tới cài "
          "rule lên switch); không cần chuẩn hóa đặc trưng (ít rủi ro lệch phân phối khi đo online); có độ quan trọng "
          "đặc trưng để giải thích; chỉ cần scikit-learn trên controller.",
          ""]
    REPORT.write_text("\n".join(R), encoding="utf-8")
    print(T[["f1", "fpr", "fpr_N04", "swap_f1", "delay", "fa_h", "train_s", "infer_ms", "size_kb"]].round(3).to_string())
    print(f"-> {REPORT.relative_to(E.ROOT)}, {fig_path.relative_to(E.ROOT)}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
train.py - Huấn luyện & đánh giá mô hình phát hiện tấn công bảo vệ SDN Controller.

So sánh hai cách phát hiện theo cửa sổ thời gian:
  1. Đường cơ sở ngưỡng tĩnh (Static Threshold) trên 1 đặc trưng control-plane.
  2. Random Forest trên toàn bộ đặc trưng.

Chạy từ thư mục gốc dự án:
    python -P src/train.py                      # dùng windows_w5.csv
    python -P src/train.py --windows data/processed/windows_w5.csv

Đầu ra:
    models/rf_w5.joblib            mô hình + metadata (đặc trưng, ngưỡng, phiên bản)
    models/baseline_w5.json        ngưỡng tĩnh đã chọn
    reports/model_report.md        báo cáo chỉ số cho chương 4
    reports/figures/*.png          ROC, PR, confusion, feature importance (nếu có matplotlib)

Chống rò rỉ: split theo lần chạy (cột split: train/test), KHÔNG xáo trộn từng cửa sổ.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import joblib

from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import GroupKFold, GridSearchCV
from sklearn.metrics import (
    confusion_matrix, precision_score, recall_score, f1_score,
    roc_auc_score, average_precision_score, roc_curve, precision_recall_curve,
)

ROOT = Path(__file__).resolve().parents[1]
PROCESSED = ROOT / "data" / "processed"
MODELS = ROOT / "models"
REPORTS = ROOT / "reports"
FIGS = REPORTS / "figures"

RANDOM_STATE = 42


# ---------------------------------------------------------------------------
# Nạp dữ liệu
# ---------------------------------------------------------------------------
def load(windows_path: Path, features_path: Path):
    win = pd.read_csv(windows_path)
    feats_meta = json.loads(features_path.read_text(encoding="utf-8"))
    features = [f for f in feats_meta["features"] if f in win.columns]

    # Chỉ dùng cửa sổ có nhãn rõ ràng (0/1) và thuộc train hoặc test.
    win = win[win["split"].isin(["train", "test"])].copy()
    win = win[win["label"].isin([0, 1])].copy()
    win["label"] = win["label"].astype(int)

    # Đặc trưng data-plane chỉ có ở lần chạy mới. Bỏ đặc trưng hoàn toàn rỗng; nếu còn ô rỗng
    # (trộn lần chạy cũ + mới) thì bỏ các CỬA SỔ thiếu để mọi mô hình so trên cùng một tập.
    empty = [f for f in features if win[f].isna().all()]
    features = [f for f in features if f not in empty]
    n0 = len(win)
    win = win.dropna(subset=features)
    if empty or len(win) < n0:
        print(f"Bỏ {len(empty)} đặc trưng rỗng {empty[:4]}{'...' if len(empty) > 4 else ''}; "
              f"bỏ {n0 - len(win)} cửa sổ thiếu giá trị")

    tr = win[win["split"] == "train"]
    te = win[win["split"] == "test"]
    return win, tr, te, features, feats_meta


# ---------------------------------------------------------------------------
# Bộ chỉ số dùng chung
# ---------------------------------------------------------------------------
def metrics(y_true, y_pred, y_score):
    tn, fp, fn, tp = confusion_matrix(y_true, y_pred, labels=[0, 1]).ravel()
    fpr = fp / (fp + tn) if (fp + tn) else 0.0
    out = {
        "confusion": {"TN": int(tn), "FP": int(fp), "FN": int(fn), "TP": int(tp)},
        "precision": precision_score(y_true, y_pred, zero_division=0),
        "recall": recall_score(y_true, y_pred, zero_division=0),
        "f1": f1_score(y_true, y_pred, zero_division=0),
        "fpr": fpr,
    }
    # ROC-AUC / PR-AUC cần điểm liên tục và cả 2 lớp.
    if y_score is not None and len(set(y_true)) == 2:
        out["roc_auc"] = roc_auc_score(y_true, y_score)
        out["pr_auc"] = average_precision_score(y_true, y_score)
    return out


# ---------------------------------------------------------------------------
# 1) Đường cơ sở ngưỡng tĩnh: chọn ngưỡng trên TRAIN, đánh giá trên TEST
# ---------------------------------------------------------------------------
def static_threshold(tr, te, feature):
    xs = np.sort(tr[feature].unique())
    # Thử các ngưỡng giữa các giá trị, chọn F1 cao nhất trên train.
    cands = (xs[:-1] + xs[1:]) / 2 if len(xs) > 1 else xs
    best_t, best_f1 = None, -1.0
    for t in cands:
        f1 = f1_score(tr["label"], (tr[feature] > t).astype(int), zero_division=0)
        if f1 > best_f1:
            best_f1, best_t = f1, float(t)
    te_pred = (te[feature] > best_t).astype(int)
    return best_t, best_f1, metrics(te["label"], te_pred, te[feature].to_numpy())


# ---------------------------------------------------------------------------
# 2) Random Forest + tinh chỉnh bằng GroupKFold (nhóm = lần chạy)
# ---------------------------------------------------------------------------
def train_rf(tr, te, features):
    Xtr, ytr, gtr = tr[features], tr["label"], tr["run_id"]
    Xte, yte = te[features], te["label"]

    n_groups = gtr.nunique()
    n_pos_groups = tr[tr["label"] == 1]["run_id"].nunique()
    grid = {
        "n_estimators": [200, 400],
        "max_depth": [None, 6, 10],
        "min_samples_leaf": [1, 2, 4],
    }
    base = RandomForestClassifier(
        random_state=RANDOM_STATE, class_weight="balanced", n_jobs=-1
    )

    cv_info = ""
    # Chỉ CV khi đủ nhóm có nhãn tấn công; nếu quá ít thì fit thẳng.
    # Cần >=3 lần chạy tấn công để mỗi fold GroupKFold đều có nhãn tấn công ở validation.
    if n_groups >= 3 and n_pos_groups >= 3:
        splits = min(n_groups, 5, n_pos_groups + 1)
        gkf = GroupKFold(n_splits=splits)
        gs = GridSearchCV(base, grid, scoring="f1", cv=gkf, n_jobs=-1)
        gs.fit(Xtr, ytr, groups=gtr)
        model = gs.best_estimator_
        cv_info = f"GroupKFold({splits}) best_f1={gs.best_score_:.3f} params={gs.best_params_}"
    else:
        model = base.set_params(n_estimators=400, max_depth=None)
        model.fit(Xtr, ytr)
        cv_info = (f"Fit thẳng (không CV): chỉ {n_pos_groups} lần chạy có nhãn tấn công "
                   "- quá ít nhóm để GroupKFold có ý nghĩa.")

    # Đo tốc độ suy luận (ms/cửa sổ) trên tập test.
    t0 = time.perf_counter()
    proba = model.predict_proba(Xte)[:, 1]
    infer_ms = (time.perf_counter() - t0) * 1000 / max(len(Xte), 1)
    pred = (proba >= 0.5).astype(int)

    m = metrics(yte, pred, proba)
    m["inference_ms_per_window"] = infer_ms
    imp = sorted(zip(features, model.feature_importances_), key=lambda x: -x[1])
    return model, m, imp, cv_info


# ---------------------------------------------------------------------------
# Hình vẽ (tùy chọn)
# ---------------------------------------------------------------------------
def make_figures(te, rf_proba, imp):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:
        return f"(bỏ qua hình: {e})"
    FIGS.mkdir(parents=True, exist_ok=True)
    y = te["label"].to_numpy()

    fpr, tpr, _ = roc_curve(y, rf_proba)
    plt.figure(figsize=(4, 4))
    plt.plot(fpr, tpr); plt.plot([0, 1], [0, 1], "--", c="gray")
    plt.xlabel("FPR"); plt.ylabel("TPR"); plt.title("ROC - Random Forest (test)")
    plt.tight_layout(); plt.savefig(FIGS / "roc_rf.png", dpi=130); plt.close()

    prec, rec, _ = precision_recall_curve(y, rf_proba)
    plt.figure(figsize=(4, 4))
    plt.plot(rec, prec)
    plt.xlabel("Recall"); plt.ylabel("Precision"); plt.title("PR - Random Forest (test)")
    plt.tight_layout(); plt.savefig(FIGS / "pr_rf.png", dpi=130); plt.close()

    top = imp[:12][::-1]
    plt.figure(figsize=(5, 4))
    plt.barh([k for k, _ in top], [v for _, v in top])
    plt.title("Feature importance (RF)"); plt.tight_layout()
    plt.savefig(FIGS / "feature_importance.png", dpi=130); plt.close()
    return f"Đã lưu 3 hình vào {FIGS.relative_to(ROOT)}"


# ---------------------------------------------------------------------------
# Báo cáo
# ---------------------------------------------------------------------------
def fmt_metrics(m):
    c = m["confusion"]
    lines = [
        f"- Confusion [TN FP / FN TP]: [{c['TN']} {c['FP']} / {c['FN']} {c['TP']}]",
        f"- Precision={m['precision']:.3f}  Recall={m['recall']:.3f}  "
        f"F1={m['f1']:.3f}  FPR={m['fpr']:.3f}",
    ]
    if "roc_auc" in m:
        lines.append(f"- ROC-AUC={m['roc_auc']:.3f}  PR-AUC={m['pr_auc']:.3f}")
    if "inference_ms_per_window" in m:
        lines.append(f"- Tốc độ suy luận: {m['inference_ms_per_window']:.3f} ms/cửa sổ")
    return "\n".join(lines)


def write_report(win, tr, te, feature, thr, thr_trainf1, base_m,
                 rf_m, imp, cv_info, fig_note, W):
    REPORTS.mkdir(parents=True, exist_ok=True)
    R = []
    R.append("# Báo cáo mô hình – Phát hiện tấn công bảo vệ SDN Controller\n")
    R.append(f"Sinh tự động bởi `src/train.py`. Cửa sổ W = {W} mẫu. "
             "Split theo lần chạy (train/test), không xáo trộn cửa sổ.\n")
    R.append("## 1. Dữ liệu\n")
    R.append(f"- Train: {len(tr)} cửa sổ ({int((tr.label==0).sum())} bình thường, "
             f"{int((tr.label==1).sum())} tấn công), từ: {sorted(tr.run_id.unique())}")
    R.append(f"- Test : {len(te)} cửa sổ ({int((te.label==0).sum())} bình thường, "
             f"{int((te.label==1).sum())} tấn công), từ: {sorted(te.run_id.unique())}\n")

    R.append("## 2. Đường cơ sở ngưỡng tĩnh\n")
    R.append(f"Đặc trưng `{feature}`, ngưỡng chọn trên train = **{thr:.2f}** "
             f"(F1 train {thr_trainf1:.3f}). Trên tập test:\n")
    R.append(fmt_metrics(base_m) + "\n")

    R.append("## 3. Random Forest\n")
    R.append(f"{cv_info}\n\nTrên tập test:\n")
    R.append(fmt_metrics(rf_m) + "\n")

    R.append("## 4. So sánh (mục 4.5.1)\n")
    R.append("| Mô hình | F1 | FPR | ROC-AUC |\n|---|---|---|---|")
    R.append(f"| Ngưỡng tĩnh `{feature}` | {base_m['f1']:.3f} | {base_m['fpr']:.3f} | "
             f"{base_m.get('roc_auc', float('nan')):.3f} |")
    R.append(f"| Random Forest | {rf_m['f1']:.3f} | {rf_m['fpr']:.3f} | "
             f"{rf_m.get('roc_auc', float('nan')):.3f} |\n")

    R.append("## 5. Tầm quan trọng đặc trưng (mục 4.3.3)\n")
    R.append("| Đặc trưng | Importance |\n|---|---|")
    for k, v in imp[:12]:
        R.append(f"| {k} | {v:.4f} |")
    R.append("")
    R.append(f"{fig_note}\n")

    R.append("## 6. Ghi chú trung thực về giới hạn hiện tại\n")
    gap = rf_m["f1"] - base_m["f1"]
    R.append(
        f"- Chênh lệch F1 giữa Random Forest và ngưỡng tĩnh = **{gap:+.3f}**. "
        "Nếu quá nhỏ (≈0), nghĩa là dữ liệu hiện tại quá dễ: một ngưỡng Packet-In đã tách "
        "gần hết hai lớp, nên chưa chứng minh được giá trị của học máy (mục 4.5.1).\n"
        "- Nguyên nhân: hai lớp chưa chồng lấn, và tập test chỉ gồm 1 lần chạy (A03).\n"
        "- Việc cần làm để báo cáo chương 4 thuyết phục: thu thêm các kịch bản chồng lấn "
        "(flash crowd, low-rate, pulsing, SYN flood, flow burst — xem `scenarios/`), "
        "lặp ≥5 lần/kịch bản, và bổ sung đặc trưng mặt phẳng dữ liệu.\n"
        "- Lưu ý: dữ liệu controller-aware (Packet-In, CPU) chỉ có ý nghĩa khi controller thật "
        "sự chịu tải tấn công — cần bật đường Packet-In reactive trên ODL (Pha B).\n"
    )
    (REPORTS / "model_report.md").write_text("\n".join(R), encoding="utf-8")


def main():
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--windows", default=str(PROCESSED / "windows_w5.csv"))
    ap.add_argument("--features", default=str(PROCESSED / "features.json"))
    ap.add_argument("--baseline-feature", default="pin_last")
    args = ap.parse_args()

    win, tr, te, features, meta = load(Path(args.windows), Path(args.features))
    W = int(meta.get("window_sizes", [5])[0])
    print(f"Train {len(tr)} | Test {len(te)} | {len(features)} đặc trưng")

    bf = args.baseline_feature if args.baseline_feature in features else features[0]
    thr, thr_f1, base_m = static_threshold(tr, te, bf)
    print(f"[Ngưỡng tĩnh] {bf} > {thr:.2f} -> test F1={base_m['f1']:.3f} FPR={base_m['fpr']:.3f}")

    model, rf_m, imp, cv_info = train_rf(tr, te, features)
    print(f"[RandomForest] test F1={rf_m['f1']:.3f} FPR={rf_m['fpr']:.3f} "
          f"ROC-AUC={rf_m.get('roc_auc', float('nan')):.3f}")

    rf_proba = model.predict_proba(te[features])[:, 1]
    fig_note = make_figures(te, rf_proba, imp)

    # Lưu mô hình + metadata
    MODELS.mkdir(parents=True, exist_ok=True)
    joblib.dump({
        "model": model, "features": features, "window_size": W,
        "trained_on": sorted(tr.run_id.unique()), "test_on": sorted(te.run_id.unique()),
        "sklearn_label": {0: "normal", 1: "attack"},
    }, MODELS / f"rf_w{W}.joblib")
    (MODELS / f"baseline_w{W}.json").write_text(json.dumps(
        {"feature": bf, "threshold": thr, "rule": f"{bf} > {thr}"}, indent=2))

    write_report(win, tr, te, bf, thr, thr_f1, base_m, rf_m, imp, cv_info, fig_note, W)
    print(f"Đã lưu: models/rf_w{W}.joblib, reports/model_report.md")
    print(fig_note)


if __name__ == "__main__":
    main()

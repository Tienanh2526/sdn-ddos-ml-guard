#!/usr/bin/env python3
"""replay.py — Kiểm thử OFFLINE module thời gian thực bằng cách phát lại dữ liệu đã thu.

Cho từng mẫu 2 s của các lần chạy thật (data/processed/samples.csv) đi qua ĐÚNG đường ống của
detector.py: hàng đợi 5 mẫu -> build_dataset.window_features() -> Random Forest -> Engine (máy
trạng thái + giảm thiểu). ODL được thay bằng FakeODL (ghi lại lệnh, không đụng hạ tầng).

Mục đích: (1) bắt lỗi logic trước khi chạy trên VM; (2) kiểm tra đặc trưng online khớp offline;
(3) ước lượng độ trễ phát hiện & số lần kích hoạt nhầm của máy trạng thái.
Lưu ý: model huấn luyện trên chính các lần chạy này (in-sample) -> đây là kiểm thử LOGIC, không phải
số liệu đánh giá (số liệu đánh giá = src/evaluate.py, LORO).

  .\\.venv\\Scripts\\python.exe src\\realtime\\replay.py [--runs A06-R1,N04-R1] [--mode mitigate]
"""
from __future__ import annotations

import argparse
import collections
import sys
from pathlib import Path
from types import SimpleNamespace

import joblib
import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent))
import build_dataset as bd   # noqa: E402
from detector import Engine, HOSTS  # noqa: E402

ROOT = HERE.parent.parent


class FakeODL:
    def __init__(self):
        self.calls = []

    def put_flow(self, node, flow, table=0):
        self.calls.append(("PUT", node, flow["id"], flow.get("priority")))
        return 200, 12.0, {}

    def wait_on_switch(self, node, fid, table=0, timeout_s=15, poll_s=0.5):
        return 0.6

    def delete_flow(self, node, fid, table=0):
        self.calls.append(("DELETE", node, fid))
        return 204, 8.0


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default="")
    ap.add_argument("--mode", default="mitigate", choices=["detect", "mitigate"])
    ap.add_argument("--allow-drop", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--fallback", action="store_true",
                    help="giả lập MẤT kênh data-plane: xóa cột dp_*, dùng mô hình dự phòng (controller-only)")
    a = ap.parse_args()

    b = joblib.load(ROOT / "models" / "detector_rf.joblib")
    model, feats, W = b["model"], b["features"], b["window_size"]
    s = pd.read_csv(ROOT / "data" / "processed" / "samples.csv")
    s = s[s["is_valid"] == 1]
    if a.fallback:
        model, feats = b["fallback_model"], b["fallback_features"]
        s = s.assign(**{c: np.nan for c in bd.DP_COLS})
    runs = a.runs.split(",") if a.runs else [r for r in sorted(s["run_id"].unique())
                                             if r.split("-")[0] in ("N04", "A04", "A05", "A06", "A07")]
    args = SimpleNamespace(mode=a.mode, allow_drop=a.allow_drop, threshold=b["threshold"], k=b["confirm_k"],
                           n=b["confirm_n"], min_share=0.5, escalate_after=20.0, clear_cycles=5, recover_s=30.0,
                           rule_idle=60, rule_hard=900)
    print(f"model: {len(feats)} đặc trưng, ngưỡng {args.threshold}, xác nhận {args.k}/{args.n}, mode={a.mode}")
    summary = []
    for r in runs:
        g = s[s["run_id"] == r].sort_values("seq")
        clock = {"t": 0.0}
        events = []
        odl = FakeODL()
        eng = Engine(args, odl if a.mode == "mitigate" else None,
                     lambda kind, **kw: events.append((clock["t"], kind, kw)), clock=lambda: clock["t"])
        buf = collections.deque(maxlen=W)
        attacker = "192.168.200.11" if r.endswith(("-R4", "-R5")) else "192.168.200.13"
        first_att = first_mit = None
        mit_in_normal = 0
        prev_state = "NORMAL"
        for _, row in g.iterrows():
            clock["t"] = float(row["t_rel_s"])
            buf.append(row.to_dict())
            if len(buf) < W:
                continue
            wf = bd.window_features(pd.DataFrame(list(buf)), W)[-1]
            x = pd.DataFrame([[wf[f] for f in feats]], columns=feats)
            if not x.notna().all(axis=None):
                continue
            p = float(model.predict_proba(x)[0, 1])
            lab = row["label"]
            src = collections.Counter({attacker: 50} if lab == 1 else {"192.168.200.11": 3})
            eng.step(p, src)
            if lab == 1 and first_att is None:
                first_att = clock["t"]
            if eng.state == "MITIGATING" and prev_state != "MITIGATING":
                if lab == 0:
                    mit_in_normal += 1
                elif first_mit is None and first_att is not None:
                    first_mit = clock["t"]
            prev_state = eng.state
            if a.verbose:
                print(f"  {r} t={clock['t']:6.1f} lab={lab} p={p:.2f} {eng.state}")
        delay = (first_mit - first_att) if (first_mit is not None and first_att is not None) else np.nan
        puts = [c for c in odl.calls if c[0] == "PUT"]; dels = [c for c in odl.calls if c[0] == "DELETE"]
        summary.append(dict(run=r, transitions=sum(1 for e in events if e[1] == "state"),
                            delay_s=round(delay, 1) if not np.isnan(delay) else None,
                            mitigate_on_normal=mit_in_normal, rules_put=len(puts), rules_deleted=len(dels),
                            final_state=eng.state))
        for t, kind, kw in events:
            if kind in ("state", "rule_install", "rule_remove"):
                print(f"  {r} t={t:6.1f}s {kind} {kw}")
    print(pd.DataFrame(summary).to_string(index=False))


if __name__ == "__main__":
    main()

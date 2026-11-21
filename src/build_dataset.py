#!/usr/bin/env python3
"""
build_dataset.py - Gộp telemetry thô của nhóm thành dataset thống nhất + cơ sở dữ liệu SQLite.

Chạy từ thư mục gốc dự án (D:\\sdn-ai-ml):
    python -P src/build_dataset.py                 # cửa sổ mặc định W = 5 mẫu (10 s)
    python -P src/build_dataset.py --window 5 --window 3

Đầu vào : data/raw/<vm>/sdn-ai/...  (chép nguyên trạng từ các VM, không sửa)
Đầu ra  :
    data/processed/samples.csv        1 dòng = 1 mẫu telemetry (chu kỳ 2 s)
    data/processed/windows_w{W}.csv   1 dòng = 1 cửa sổ trượt W mẫu -> đặc trưng cho ML
    data/processed/features.json      danh sách đặc trưng, nhóm đặc trưng, cấu hình cửa sổ
    data/db/sdn_ai.db                 SQLite: scenarios, runs, samples, windows, http_qos, openflow_messages, meta
    reports/data_quality_report.md    báo cáo chất lượng dữ liệu (sinh tự động)

Thêm một lần chạy mới: chép CSV vào data/raw, thêm 1 dòng vào RUNS (và SCENARIOS nếu là kịch bản mới), chạy lại.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
CTRL = RAW / "vm-controller" / "sdn-ai"
CLIENT = RAW / "vm-client" / "sdn-ai"
PROCESSED = ROOT / "data" / "processed"
DB_PATH = ROOT / "data" / "db" / "sdn_ai.db"
REPORT_PATH = ROOT / "reports" / "data_quality_report.md"

INTERVAL_S = 2.0      # chu kỳ lấy mẫu của collector
MAX_GAP_S = 4.5       # khoảng cách > 4.5 s giữa 2 mẫu hợp lệ => cắt đoạn (cho phép hụt đúng 1 mẫu)

HOSTS = {"client": "192.168.200.11 (vm-client)", "web": "192.168.200.12 (vm-web)",
         "attacker": "192.168.200.13 (vm-attacker)"}

# --------------------------------------------------------------------------------------------
# Danh mục kịch bản. category: normal | attack | calibration (hiệu chuẩn, không gán nhãn)
# --------------------------------------------------------------------------------------------
SCENARIOS = [
    dict(scenario_id="N00", category="normal", attack_type=None, traffic_type="idle",
         description="Baseline nền 2 phút, không chủ động sinh lưu lượng (chạy thử, suy ra từ dữ liệu)",
         src_host=None, dst_host=None, generator=None, rate_profile="0 pps"),
    dict(scenario_id="N01", category="normal", attack_type=None, traffic_type="normal_pilot",
         description="Chạy thử lưu lượng bình thường 2 phút, không có Packet-In",
         src_host=None, dst_host=None, generator=None, rate_profile="0 pps Packet-In"),
    dict(scenario_id="N02", category="normal", attack_type=None, traffic_type="http_ping_client",
         description="HTTP GET + ping mỗi 2 s từ client tới web:8080, ~1000 s, không sinh Packet-In",
         src_host=HOSTS["client"], dst_host=HOSTS["web"], generator="vm-client/normal_n02.sh",
         rate_profile="0.5 req/s"),
    dict(scenario_id="N03", category="normal", attack_type=None, traffic_type="synthetic_benign_packet_in",
         description="UDP hợp lệ gây Packet-In tăng dần 2->5->10->20->30 pps, mỗi mức 200 s (cổng 45456)",
         src_host=HOSTS["attacker"], dst_host=HOSTS["web"], generator="vm-attacker/normal_n03.py",
         rate_profile="2,5,10,20,30 pps x 200 s"),
    dict(scenario_id="E3", category="calibration", attack_type=None, traffic_type="calibration_burst",
         description="Hiệu chuẩn đo Packet-In: 1 cụm ngắn ~10/30/50 pps trong 2 phút, không có nhãn",
         src_host=None, dst_host=None, generator=None, rate_profile="burst ~10/30/50 pps"),
    dict(scenario_id="A01", category="attack", attack_type="udp_packet_in_flood", traffic_type="udp_packet_in_burst",
         description="Chạy thử tấn công: cụm UDP ~50 pps gây Packet-In trong ~18 s",
         src_host=HOSTS["attacker"], dst_host=HOSTS["web"], generator=None, rate_profile="~50 pps x 18 s"),
    dict(scenario_id="A02", category="attack", attack_type="udp_packet_in_flood", traffic_type="synthetic_packet_in_burst",
         description="UDP flood gây Packet-In tăng bậc 40->60->80->100 pps, mỗi bậc 120 s (cổng 45457)",
         src_host=HOSTS["attacker"], dst_host=HOSTS["web"], generator="vm-attacker/attack_a02.py",
         rate_profile="40,60,80,100 pps x 120 s"),
    dict(scenario_id="A03", category="attack", attack_type="udp_packet_in_flood", traffic_type="synthetic_packet_in_burst",
         description="Lặp lại A02 + đo chất lượng HTTP từ client trong suốt kịch bản",
         src_host=HOSTS["attacker"], dst_host=HOSTS["web"], generator="vm-attacker/attack_a02.py + vm-client/monitor/http_qos_monitor_a03.py",
         rate_profile="40,60,80,100 pps x 120 s"),
]

# --------------------------------------------------------------------------------------------
# Danh mục lần chạy. role: dataset | pilot | calibration ; split mặc định: train | test | excluded
# Quy ước run_id = <scenario>-R<lần lặp>, ví dụ A02-R1, A02-R2 ...
# --------------------------------------------------------------------------------------------
RUNS = [
    dict(run_id="N00-R1", scenario_id="N00", file=CTRL / "telemetry/joint-baseline.csv", role="pilot", split="train",
         notes="Chạy thử 60 mẫu ngày 17/11"),
    dict(run_id="N01-R1", scenario_id="N01", file=CTRL / "telemetry/normal-n01.csv", role="pilot", split="train",
         notes="Chạy thử 60 mẫu ngày 17/11"),
    dict(run_id="E3-LOW", scenario_id="E3", file=CTRL / "telemetry/e3-lowload.csv", role="calibration", split="excluded",
         notes="Cụm ~10 pps, không nhãn"),
    dict(run_id="E3-MED", scenario_id="E3", file=CTRL / "telemetry/e3-medium.csv", role="calibration", split="excluded",
         notes="Cụm ~30 pps, không nhãn"),
    dict(run_id="E3-HIGH", scenario_id="E3", file=CTRL / "telemetry/e3-high.csv", role="calibration", split="excluded",
         notes="Cụm ~50 pps, không nhãn"),
    dict(run_id="A01-R1", scenario_id="A01", file=CTRL / "telemetry/a01-labeled.csv", role="pilot", split="train",
         notes="Nhãn -1 (cửa sổ vắt qua ranh giới) -> nhãn rỗng"),
    dict(run_id="N02-R1", scenario_id="N02", file=CTRL / "dataset/normal/N02/N02.csv", role="dataset", split="train",
         notes=""),
    dict(run_id="N03-R1", scenario_id="N03", file=CTRL / "dataset/normal/N03/N03.csv", role="dataset", split="train",
         notes="Lưu lượng 'bình thường' gửi từ IP attacker"),
    dict(run_id="A02-R1", scenario_id="A02", file=CTRL / "dataset/attack/A02/A02.csv", role="dataset", split="train",
         notes=""),
    dict(run_id="A03-R1", scenario_id="A03", file=CTRL / "dataset/attack/A03/A03.csv", role="dataset", split="test",
         notes="Tập kiểm thử độc lập (lần chạy riêng)"),
]
COLLECTOR = "health collector (CPU/RSS/RESTCONF) + tcpdump OpenFlow :6653 -> tshark (type 10/14)"

SAMPLE_METRICS = ["controller_cpu_pct", "controller_rss_mb", "api_latency_ms", "api_http_status",
                  "openflow_connections", "openflow_nodes", "inventory_flow_count", "packet_in_rate",
                  "flow_mod_rate", "of_window_seconds", "packet_in_count", "flow_mod_count"]
HTTP_AGG = ["http_requests", "http_successes", "http_success_rate", "http_latency_mean_ms", "http_latency_max_ms"]
# Cột mặt phẳng dữ liệu từ scenarios/vm-leaf/dataplane_collector.py (qua finalize_run.py).
# Lần chạy cũ (A01–A03, N00–N03) không có -> NaN. KHÔNG lấy cột theo cổng rxpps_<port>
# (gắn danh tính host -> rò rỉ nhãn), chỉ lấy đặc trưng tổng hợp.
DP_COLS = ["dp_pkts", "dp_flows", "dp_distinct_src", "dp_syn_ratio", "dp_udp_frac",
           "dp_pkts_per_flow", "dp_mean_pkt_size", "dp_dst_port_entropy", "dp_reply_ratio"]
SAMPLE_COLS = ["timestamp_utc", *SAMPLE_METRICS, "traffic_type", "phase", "expected_rate", "label", *HTTP_AGG,
               *DP_COLS]
# đặc trưng cửa sổ: (tên, cột mẫu, phép gộp)
DP_WINDOW = [("dp_pkts_mean", "dp_pkts", "mean"), ("dp_pkts_max", "dp_pkts", "max"),
             ("dp_flows_mean", "dp_flows", "mean"), ("dp_flows_max", "dp_flows", "max"),
             ("dp_src_mean", "dp_distinct_src", "mean"), ("dp_syn_mean", "dp_syn_ratio", "mean"),
             ("dp_udp_frac_mean", "dp_udp_frac", "mean"), ("dp_ppf_mean", "dp_pkts_per_flow", "mean"),
             ("dp_size_mean", "dp_mean_pkt_size", "mean"), ("dp_dport_h_mean", "dp_dst_port_entropy", "mean"),
             ("dp_reply_mean", "dp_reply_ratio", "mean")]

FEATURE_GROUPS = {
    # Tín hiệu mặt phẳng điều khiển (OpenFlow giữa switch và controller)
    "control_plane": ["pin_last", "pin_mean", "pin_std", "pin_min", "pin_max", "pin_slope", "fmod_mean", "fmod_max"],
    # Tài nguyên / sức khỏe của chính controller
    "controller_resource": ["cpu_last", "cpu_mean", "cpu_std", "cpu_max", "api_last", "api_mean", "api_std",
                            "api_max", "rss_delta", "api_err_cnt", "ofconn_min"],
    # Thay đổi trạng thái bảng flow / topo
    "topology": ["flows_delta"],
    # Hành vi lưu lượng mặt phẳng dữ liệu (leaf) - chỉ có ở các lần chạy mới (scenarios/)
    "data_plane_traffic": [name for name, _, _ in DP_WINDOW],
}
FEATURES = [f for g in FEATURE_GROUPS.values() for f in g]
INT_COLS = {"seq", "api_http_status", "openflow_connections", "openflow_nodes", "inventory_flow_count",
            "packet_in_count", "flow_mod_count", "label", "http_requests", "http_successes", "is_valid",
            "segment", "window_size", "start_seq", "end_seq", "mixed", "api_err_cnt", "n_samples", "n_valid",
            "n_label0", "n_label1", "http_status", "curl_exit_code", "success", "packet_in", "packet_out",
            "flow_mod", "multipart_request", "multipart_reply", "barrier_request", "barrier_reply"}


# ============================================================================================
# 1. Đọc và chuẩn hóa từng lần chạy
# ============================================================================================
def load_run(run: dict, scen: dict) -> tuple[pd.DataFrame, dict]:
    raw = pd.read_csv(run["file"]).rename(columns={"expected_udp_rate": "expected_rate"})
    df = raw.reindex(columns=SAMPLE_COLS).copy()
    ts = pd.to_datetime(df["timestamp_utc"], format="ISO8601", utc=True)

    df.insert(0, "run_id", run["run_id"])
    df.insert(1, "seq", np.arange(len(df)))
    df["t_rel_s"] = (ts - ts.iloc[0]).dt.total_seconds().round(3)
    df["dt_s"] = ts.diff().dt.total_seconds().round(3)

    # --- nhãn: 0 bình thường, 1 tấn công, rỗng = không xác định (biên pha / hiệu chuẩn)
    if "label" in raw.columns:
        label = pd.to_numeric(raw["label"], errors="coerce")
    else:
        label = pd.Series(0.0 if scen["category"] == "normal" else np.nan, index=df.index)
    boundary = label.eq(-1)
    df["label"] = label.mask(boundary)

    # --- pha
    if "phase" not in raw.columns:
        if scen["category"] in ("normal", "calibration"):
            phase = np.full(len(df), scen["category"], dtype=object)
        else:  # tấn công không có cột pha (A01): suy ra từ nhãn
            att = df["label"].eq(1).to_numpy()
            idx = np.flatnonzero(att)
            first, last = (idx[0], idx[-1]) if len(idx) else (len(df), -1)
            pos = np.arange(len(df))
            phase = np.where(pos < first, "baseline", np.where(pos > last, "recovery", "attack")).astype(object)
        df["phase"] = phase
    df["phase"] = df["phase"].astype(object)
    df.loc[boundary.to_numpy(), "phase"] = "boundary"

    df["traffic_type"] = df["traffic_type"].astype(object).where(df["traffic_type"].notna(), scen["traffic_type"])

    # --- hợp lệ về mặt đo đạc: đã có khoảng tính tốc độ Packet-In / CPU
    df["is_valid"] = (df["packet_in_rate"].notna() & df["of_window_seconds"].notna()).astype(int)

    # --- đoạn liên tục (để cửa sổ trượt không nhảy qua chỗ mất dữ liệu)
    seg = np.full(len(df), -1)
    seg_id, prev = -1, None
    for i, (ok, t) in enumerate(zip(df["is_valid"].to_numpy(), ts)):
        if not ok:
            prev = None
            continue
        if prev is None or (t - prev).total_seconds() > MAX_GAP_S:
            seg_id += 1
        seg[i] = seg_id
        prev = t
    df["segment"] = seg

    valid = df["is_valid"].eq(1)
    info = dict(
        run_id=run["run_id"], scenario_id=run["scenario_id"], role=run["role"], default_split=run["split"],
        start_utc=ts.min().isoformat(), end_utc=ts.max().isoformat(),
        duration_s=round((ts.max() - ts.min()).total_seconds(), 1),
        n_samples=len(df), n_valid=int(valid.sum()),
        n_label0=int((valid & df["label"].eq(0)).sum()), n_label1=int((valid & df["label"].eq(1)).sum()),
        interval_s=round(float(df["dt_s"].median()), 3), collector=COLLECTOR,
        source_file=str(Path(run["file"]).relative_to(ROOT)).replace("\\", "/"), notes=run["notes"],
    )
    return df, info


# ============================================================================================
# 2. Cửa sổ trượt -> đặc trưng
# ============================================================================================
def window_features(g: pd.DataFrame, W: int) -> list[dict]:
    a = {c: g[c].to_numpy(dtype=float) for c in
         ["packet_in_rate", "flow_mod_rate", "controller_cpu_pct", "api_latency_ms", "controller_rss_mb",
          "inventory_flow_count", "api_http_status", "openflow_connections", "label", "t_rel_s", *DP_COLS]}
    seqs, ts, phases = g["seq"].to_numpy(), g["timestamp_utc"].to_numpy(), g["phase"].to_numpy()
    out = []
    for end in range(W - 1, len(g)):
        s = slice(end - W + 1, end + 1)
        pin, cpu, api = a["packet_in_rate"][s], a["controller_cpu_pct"][s], a["api_latency_ms"][s]
        span = a["t_rel_s"][s][-1] - a["t_rel_s"][s][0]
        lab = a["label"][s]
        frac = float(np.nanmean(lab)) if np.isfinite(lab).any() else np.nan
        dp = {}
        for name, col, how in DP_WINDOW:
            v = a[col][s]
            v = v[np.isfinite(v)]
            dp[name] = (v.mean() if how == "mean" else v.max()) if len(v) else np.nan
        out.append(dict(**dp,
            start_seq=int(seqs[s][0]), end_seq=int(seqs[s][-1]), start_utc=ts[s][0], end_utc=ts[s][-1],
            span_s=round(span, 3),
            pin_last=pin[-1], pin_mean=pin.mean(), pin_std=pin.std(), pin_min=pin.min(), pin_max=pin.max(),
            pin_slope=(pin[-1] - pin[0]) / span if span > 0 else 0.0,
            fmod_mean=a["flow_mod_rate"][s].mean(), fmod_max=a["flow_mod_rate"][s].max(),
            cpu_last=cpu[-1], cpu_mean=cpu.mean(), cpu_std=cpu.std(), cpu_max=cpu.max(),
            api_last=api[-1], api_mean=api.mean(), api_std=api.std(), api_max=api.max(),
            rss_delta=a["controller_rss_mb"][s][-1] - a["controller_rss_mb"][s][0],
            api_err_cnt=int((a["api_http_status"][s] != 200).sum()),
            ofconn_min=a["openflow_connections"][s].min(),
            flows_delta=a["inventory_flow_count"][s][-1] - a["inventory_flow_count"][s][0],
            label=lab[-1], label_frac=frac,
            mixed=int(np.isfinite(frac) and 0 < frac < 1), phase=phases[s][-1],
        ))
    return out


def build_windows(samples: pd.DataFrame, runs: pd.DataFrame, W: int) -> pd.DataFrame:
    split_of = runs.set_index("run_id")["default_split"]
    rows = []
    for (run_id, _seg), g in samples[samples["segment"] >= 0].groupby(["run_id", "segment"], sort=False):
        g = g.sort_values("seq")
        if len(g) < W:
            continue
        for r in window_features(g, W):
            rows.append({"run_id": run_id, "window_size": W, **r})
    win = pd.DataFrame(rows)
    win["split"] = win["run_id"].map(split_of)
    win.loc[win["label"].isna(), "split"] = "excluded"
    win.insert(1, "window_id", win.groupby("run_id").cumcount())
    num = [c for c in FEATURES + ["label_frac", "span_s"] if c in win]
    win[num] = win[num].astype(float).round(4)
    return win


# ============================================================================================
# 3. Bảng phụ: HTTP QoS từ client, đếm bản tin OpenFlow (chạy thử)
# ============================================================================================
def build_http(runs: pd.DataFrame) -> pd.DataFrame:
    frames = []
    for f in [CLIENT / "dataset/attack/A03/http_load.csv", CLIENT / "dataset/attack/A03/http_baseline.csv"]:
        d = pd.read_csv(f)
        t = pd.to_datetime(d["timestamp_utc"], format="ISO8601", utc=True)
        mid = t.median()
        run_id = None
        for r in runs.itertuples():
            if pd.Timestamp(r.start_utc) <= mid <= pd.Timestamp(r.end_utc):
                run_id = r.run_id
        d.insert(0, "run_id", run_id)
        d.insert(1, "source", str(f.relative_to(ROOT)).replace("\\", "/"))
        frames.append(d)
    return pd.concat(frames, ignore_index=True)


def build_openflow_msgs() -> pd.DataFrame:
    d = pd.read_csv(CTRL / "telemetry/openflow-baseline.csv")
    d.insert(0, "source", "telemetry/openflow-baseline.csv (chạy thử 17/11, đếm theo giây)")
    return d


# ============================================================================================
# 4. Ghi SQLite
# ============================================================================================
def sql_type(col: str, dtype) -> str:
    if col in INT_COLS:
        return "INTEGER"
    if pd.api.types.is_bool_dtype(dtype) or pd.api.types.is_integer_dtype(dtype):
        return "INTEGER"
    if pd.api.types.is_float_dtype(dtype):
        return "REAL"
    return "TEXT"


def create_table(con, name: str, df: pd.DataFrame, pk: list[str] | None = None, fk: str | None = None):
    cols = [f'"{c}" {sql_type(c, df[c].dtype)}' for c in df.columns]
    if pk:
        cols.append(f"PRIMARY KEY ({', '.join(pk)})")
    if fk:
        cols.append(fk)
    con.execute(f'CREATE TABLE "{name}" (\n  ' + ",\n  ".join(cols) + "\n)")
    df.to_sql(name, con, if_exists="append", index=False)


def write_db(scen, runs, samples, windows, http, ofm, meta):
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA foreign_keys = ON")
    fk_run = "FOREIGN KEY (run_id) REFERENCES runs(run_id)"
    create_table(con, "scenarios", scen, pk=["scenario_id"])
    create_table(con, "runs", runs, pk=["run_id"], fk="FOREIGN KEY (scenario_id) REFERENCES scenarios(scenario_id)")
    create_table(con, "samples", samples, pk=["run_id", "seq"], fk=fk_run)
    create_table(con, "windows", windows, pk=["run_id", "window_size", "end_seq"], fk=fk_run)
    create_table(con, "http_qos", http)
    create_table(con, "openflow_messages", ofm)
    create_table(con, "meta", meta, pk=["key"])
    con.executescript("""
        CREATE INDEX idx_samples_ts   ON samples(run_id, timestamp_utc);
        CREATE INDEX idx_windows_split ON windows(window_size, split, label);
        CREATE VIEW v_run_summary AS
            SELECT r.run_id, s.category, s.attack_type, r.role, r.default_split, r.start_utc,
                   r.duration_s, r.n_samples, r.n_valid, r.n_label0, r.n_label1
            FROM runs r JOIN scenarios s USING (scenario_id);
        CREATE VIEW v_windows_train AS SELECT * FROM windows WHERE split = 'train';
        CREATE VIEW v_windows_test  AS SELECT * FROM windows WHERE split = 'test';
    """)
    con.commit()
    con.close()


# ============================================================================================
# 5. Báo cáo chất lượng dữ liệu
# ============================================================================================
def md_table(df: pd.DataFrame, fmt: str = "{:.2f}") -> str:
    lines = ["| " + " | ".join(map(str, df.columns)) + " |", "|" + "|".join("---" for _ in df.columns) + "|"]
    for row in df.itertuples(index=False):
        cells = []
        for v in row:
            if v is None or (not isinstance(v, str) and pd.isna(v)):
                cells.append("")
            elif isinstance(v, float):
                cells.append(fmt.format(v))
            else:
                cells.append(str(v))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def build_report(scen, runs, samples, win, W) -> str:
    from sklearn.metrics import roc_auc_score

    cat = scen.set_index("scenario_id")["category"]
    v = samples[samples["is_valid"] == 1]
    lab = win[win["split"].isin(["train", "test"])]
    R = []
    R.append("# Báo cáo chất lượng dữ liệu – Dataset bảo vệ SDN Controller\n")
    R.append(f"Sinh tự động bởi `src/build_dataset.py` lúc {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC. "
             "Nguồn: dữ liệu thô chép nguyên trạng từ vm-controller, vm-client, vm-attacker (19/11/2026). "
             f"Cửa sổ trượt W = {W} mẫu ({W * INTERVAL_S:.0f} s), bước trượt 1 mẫu (2 s).\n")

    # 1
    t = runs.assign(category=runs["scenario_id"].map(cat))[
        ["run_id", "scenario_id", "category", "role", "default_split", "start_utc", "duration_s",
         "n_samples", "n_valid", "n_label0", "n_label1"]].copy()
    t["start_utc"] = t["start_utc"].str[:16].str.replace("T", " ")
    t.columns = ["Lần chạy", "Kịch bản", "Loại", "Vai trò", "Split", "Bắt đầu (UTC)", "Thời lượng (s)",
                 "Mẫu", "Hợp lệ", "Nhãn 0", "Nhãn 1"]
    R.append("## 1. Tổng quan các lần chạy\n")
    R.append(md_table(t, "{:.0f}") + "\n")
    R.append(f"Tổng: **{len(samples)} mẫu**, {len(v)} mẫu hợp lệ; "
             f"{int(v['label'].eq(0).sum())} mẫu nhãn 0, {int(v['label'].eq(1).sum())} mẫu nhãn 1. "
             "Mẫu đầu mỗi lần chạy không hợp lệ vì chưa có khoảng để tính tốc độ.\n")

    # 2
    ct = (win.groupby(["split", "label"], dropna=False).size().rename("n").reset_index())
    ct["label"] = ct["label"].map({0.0: "0 (bình thường)", 1.0: "1 (tấn công)"}).fillna("không nhãn")
    R.append(f"## 2. Cửa sổ thời gian (W = {W})\n")
    R.append(md_table(ct.rename(columns={"split": "Split", "label": "Nhãn", "n": "Số cửa sổ"})) + "\n")
    R.append(f"- Cửa sổ chứa cả mẫu thường lẫn mẫu tấn công (vắt qua thời điểm bắt đầu/kết thúc tấn công): "
             f"**{int(lab['mixed'].sum())}** / {len(lab)}. Ít vì dữ liệu gốc đã bỏ các mẫu nằm vắt qua ranh giới pha.\n"
             "- Nhãn của cửa sổ = nhãn của mẫu cuối cùng (trạng thái tại thời điểm ra quyết định).\n"
             "- Split theo **lần chạy**, không xáo trộn từng cửa sổ, để tránh rò rỉ giữa các cửa sổ chồng lấn.\n")

    # 3
    g = samples.groupby("run_id", sort=False)
    st = pd.DataFrame({
        "dt trung vị (s)": g["dt_s"].median(), "dt lớn nhất (s)": g["dt_s"].max(),
        "Số chỗ hụt mẫu": g["dt_s"].apply(lambda s: int((s > 1.5 * INTERVAL_S).sum())),
        "Số đoạn liên tục": g["segment"].apply(lambda s: int(s[s >= 0].nunique())),
    }).reset_index().rename(columns={"run_id": "Lần chạy"})
    R.append("## 3. Kiểm tra chu kỳ lấy mẫu\n")
    R.append(md_table(st) + "\n")

    # 4
    miss = v[SAMPLE_METRICS].isna().sum()
    R.append("## 4. Giá trị thiếu (trên mẫu hợp lệ)\n")
    R.append("Không có giá trị thiếu ở các cột đo." if miss.sum() == 0 else
             md_table(miss[miss > 0].rename("Số ô thiếu").reset_index().rename(columns={"index": "Cột"})))
    R.append("\n")

    # 5
    nodata = [f for f in FEATURES if lab[f].isna().all()]
    const = [f for f in FEATURES if f not in nodata and lab[f].nunique() <= 1]
    low = [f for f in FEATURES if f not in const + nodata and lab[f].nunique() <= 3]
    R.append("## 5. Đặc trưng hằng số / gần hằng số\n")
    if nodata:
        R.append(f"- Chưa có dữ liệu (lần chạy cũ không thu data-plane): `{', '.join(nodata)}`")
    R.append(f"- Hằng số trên toàn bộ cửa sổ có nhãn: `{', '.join(const) or '(không)'}`\n"
             f"- Chỉ 2–3 giá trị: `{', '.join(low) or '(không)'}`\n"
             f"- Ở mức mẫu: openflow_connections = {sorted(v['openflow_connections'].unique().tolist())}, "
             f"openflow_nodes = {sorted(v['openflow_nodes'].unique().tolist())}, "
             f"api_http_status = {sorted(v['api_http_status'].unique().tolist())}, "
             f"flow_mod_rate lớn nhất = {v['flow_mod_rate'].max():.2f}.\n"
             "- Các đặc trưng này vẫn được giữ trong schema vì sẽ thay đổi khi có Flow Burst (Flow-Mod), failover "
             "(mất kết nối OpenFlow) hay controller quá tải (lỗi HTTP), nhưng **hiện chưa mang thông tin**.\n")

    # 6
    ph = v[v["expected_rate"].notna()].groupby(["run_id", "phase", "expected_rate"], sort=False).agg(
        n=("packet_in_rate", "size"), pin=("packet_in_rate", "mean"), cpu=("controller_cpu_pct", "mean"),
        api=("api_latency_ms", "mean")).reset_index()
    ph["ratio"] = np.where(ph["expected_rate"] > 0, ph["pin"] / ph["expected_rate"], np.nan)
    ph = ph.sort_values(["run_id", "expected_rate"])
    ph.columns = ["Lần chạy", "Pha", "Tốc độ sinh (pps)", "Mẫu", "Packet-In đo (pps)", "CPU TB (%)",
                  "RESTCONF TB (ms)", "Đo/Sinh"]
    R.append("## 6. Kiểm tra nhãn: Packet-In đo được so với tốc độ sinh\n")
    R.append(md_table(ph) + "\n")
    R.append("Tỉ lệ Đo/Sinh ≈ 1 nghĩa là mốc thời gian gán nhãn khớp với lưu lượng thực tế.\n")

    # 7
    dr = v.groupby("run_id", sort=False).agg(
        rss_tb=("controller_rss_mb", "mean"), flows_min=("inventory_flow_count", "min"),
        flows_max=("inventory_flow_count", "max"), cpu_tb=("controller_cpu_pct", "mean"),
        cpu_max=("controller_cpu_pct", "max"), api_tb=("api_latency_ms", "mean")).reset_index()
    dr.columns = ["Lần chạy", "RSS TB (MB)", "Số flow min", "Số flow max", "CPU TB (%)", "CPU max (%)",
                  "RESTCONF TB (ms)"]
    R.append("## 7. Trôi giá trị giữa các phiên (rủi ro rò rỉ)\n")
    R.append(md_table(dr) + "\n")
    spikes = v[v["label"].eq(0) & (v["controller_cpu_pct"] > 30)]
    sp_txt = ", ".join(f"{r.run_id} mẫu {r.seq}: {r.controller_cpu_pct:.0f}%"
                       for r in spikes.nlargest(3, "controller_cpu_pct").itertuples())
    R.append("- RAM (RSS) tuyệt đối và tổng số flow thay đổi theo **phiên chạy/ngày**, không theo tấn công. "
             "Nếu dùng giá trị tuyệt đối, mô hình có thể học thuộc \"ngày nào\" thay vì \"có tấn công không\". "
             "Vì vậy dataset chỉ dùng **độ biến thiên trong cửa sổ** (`rss_delta`, `flows_delta`).\n"
             f"- CPU có **đỉnh đơn lẻ ngay cả khi bình thường** ({len(spikes)} mẫu nhãn 0 vượt 30%; cao nhất: {sp_txt}). "
             "Ngưỡng tĩnh đặt trên CPU từng mẫu sẽ báo động giả → nên dùng giá trị trung bình/cửa sổ (`cpu_mean`).\n")

    # 8
    y = lab["label"].astype(int)
    au = []
    for f in FEATURES:
        m = lab[f].notna()
        x, yy = lab.loc[m, f], y[m]
        if x.nunique() <= 1 or yy.nunique() < 2:
            continue
        a = roc_auc_score(yy, x)
        grp = next(k for k, vs in FEATURE_GROUPS.items() if f in vs)
        au.append((f, grp, max(a, 1 - a), "tấn công cao hơn" if a >= 0.5 else "tấn công thấp hơn"))
    au = pd.DataFrame(au, columns=["Đặc trưng", "Nhóm", "AUC đơn biến", "Chiều"]).sort_values(
        "AUC đơn biến", ascending=False)
    n_max = v.loc[v["label"].eq(0), "packet_in_rate"].max()
    a_min = v.loc[v["label"].eq(1), "packet_in_rate"].min()
    R.append("## 8. Khả năng phân tách của từng đặc trưng (ROC-AUC đơn biến, cửa sổ có nhãn)\n")
    R.append(md_table(au, "{:.3f}") + "\n")
    R.append(f"- Packet-In lớn nhất khi **bình thường**: **{n_max:.2f} pps**; nhỏ nhất khi **tấn công**: "
             f"**{a_min:.2f} pps** → " + ("hai lớp **tách rời hoàn toàn** chỉ bằng 1 ngưỡng."
                                          if n_max < a_min else "hai lớp có vùng chồng lấn.") + "\n")
    top_ctrl = au[au["Nhóm"] == "controller_resource"].head(1)
    if len(top_ctrl):
        R.append(f"- Đặc trưng tài nguyên controller tốt nhất: `{top_ctrl.iloc[0, 0]}` "
                 f"(AUC {top_ctrl.iloc[0, 2]:.3f}) – tấn công hiện tại quá nhẹ để làm controller quá tải rõ rệt.\n")

    # 9
    n_att_runs = runs[runs["scenario_id"].map(cat).eq("attack") & runs["role"].eq("dataset")].shape[0]
    R.append("## 9. Kết luận và việc cần làm với dữ liệu\n")
    R.append(
        "**Dùng được ngay** để dựng đường ống huấn luyện/đánh giá (schema, nhãn theo mốc thời gian, split theo lần "
        "chạy, HTTP QoS cho A03). **Chưa đủ** cho kết luận của chương 4, vì:\n\n"
        f"1. **Quá dễ:** Packet-In một mình tách hoàn toàn hai lớp → Random Forest sẽ không hơn ngưỡng tĩnh "
        "(mục 4.5.1). Cần kịch bản **chồng lấn**: tấn công tốc độ thấp 10–40 pps, lưu lượng hợp lệ đột biến "
        "(flash crowd) 30–80 pps, tấn công dao động/ngắt quãng (pulsing).\n"
        f"2. **Quá ít lần lặp:** {n_att_runs} lần chạy tấn công chính thức, mỗi kịch bản 1 lần. Cần ≥ 5 lần/kịch bản "
        "(run_id `A02-R1..R5`) để báo cáo trung bình ± độ lệch (mục 4.1.1, 4.4.1).\n"
        "3. **Tập kiểm thử A03 không có Packet-In hợp lệ** → tỉ lệ báo động giả (FPR) đo được sẽ lạc quan. "
        "Cần thêm ít nhất 1 lần chạy N03 riêng cho test.\n"
        "4. **Thiếu loại tấn công:** chưa có SYN Flood, Flow Burst (flow_mod_rate luôn 0) như đề cương (mục 2.2.2).\n"
        "5. **Chưa có đặc trưng mặt phẳng dữ liệu** (port stats OVS, số IP nguồn, entropy) → chưa so sánh được "
        "Traffic-only và Controller-aware (mục 4.5.2) và chưa xác định được nguồn tấn công (mục 2.6.2).\n"
        "6. **Tấn công còn nhẹ:** CPU controller chỉ tăng vài %, HTTP vẫn thành công 100% → khó chứng minh tác dụng "
        "bảo vệ (mục 4.4). Cần mức Packet-In cao hơn (vẫn trong VPC, có giới hạn).\n"
        "7. **Lưu lượng bình thường N03 gửi từ IP attacker** → nên chuyển sang vm-client.\n")
    return "\n".join(R)


# ============================================================================================
def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", type=int, action="append", help="kích thước cửa sổ (số mẫu), lặp được")
    args = ap.parse_args()
    windows_sizes = args.window or [5]

    scen = pd.DataFrame(SCENARIOS)
    scen_by_id = {s["scenario_id"]: s for s in SCENARIOS}
    frames, infos = [], []
    for run in RUNS:
        df, info = load_run(run, scen_by_id[run["scenario_id"]])
        frames.append(df)
        infos.append(info)
    samples = pd.concat(frames, ignore_index=True)
    runs = pd.DataFrame(infos)

    wins = {W: build_windows(samples, runs, W) for W in windows_sizes}
    windows_all = pd.concat(wins.values(), ignore_index=True)
    http, ofm = build_http(runs), build_openflow_msgs()

    PROCESSED.mkdir(parents=True, exist_ok=True)
    samples.to_csv(PROCESSED / "samples.csv", index=False)
    for W, w in wins.items():
        w.to_csv(PROCESSED / f"windows_w{W}.csv", index=False)
    (PROCESSED / "features.json").write_text(json.dumps({
        "interval_s": INTERVAL_S, "window_sizes": windows_sizes, "label": "label",
        "group_col": "run_id", "split_col": "split", "features": FEATURES, "groups": FEATURE_GROUPS,
        "notes": {"data_plane_traffic": "Từ dataplane_collector.py (leaf1, VXLAN host<->leaf, trường overlay). "
                                        "NaN ở các lần chạy cũ. Không dùng cột theo cổng (rò rỉ danh tính).",
                  "absolute_rss_flowcount": "Không dùng giá trị tuyệt đối (trôi theo phiên) - chỉ dùng delta."},
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    meta = pd.DataFrame([
        ("built_at_utc", datetime.now(timezone.utc).isoformat()),
        ("builder", "src/build_dataset.py"), ("pandas", pd.__version__),
        ("interval_s", str(INTERVAL_S)), ("max_gap_s", str(MAX_GAP_S)),
        ("window_sizes", ",".join(map(str, windows_sizes))),
        ("label_rule", "1 = pha tấn công (P1..P4/attack), 0 = baseline/recovery/normal, NULL = biên pha/hiệu chuẩn"),
        ("window_label_rule", "nhãn cửa sổ = nhãn mẫu cuối; split theo run_id"),
        ("raw_source", "Chép từ vm-controller, vm-client, vm-attacker ngày 2026-11-19 (bỏ *.pcap)"),
    ], columns=["key", "value"])
    write_db(scen, runs, samples, windows_all, http, ofm, meta)

    W0 = windows_sizes[0]
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(build_report(scen, runs, samples, wins[W0], W0), encoding="utf-8")

    print(f"samples : {len(samples)} dòng ({int(samples['is_valid'].sum())} hợp lệ) -> data/processed/samples.csv")
    for W, w in wins.items():
        print(f"windows : W={W}: {len(w)} cửa sổ, split={w['split'].value_counts().to_dict()}, "
              f"label={w['label'].value_counts(dropna=False).to_dict()}")
    print(f"http_qos: {len(http)} dòng | openflow_messages: {len(ofm)} dòng")
    print(f"DB      : {DB_PATH}")
    print(f"Report  : {REPORT_PATH}")


if __name__ == "__main__":
    main()

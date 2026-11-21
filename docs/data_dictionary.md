# Từ điển dữ liệu – `data/db/sdn_ai.db`

Cơ sở dữ liệu SQLite tập trung toàn bộ telemetry của đồ án. Mọi bảng được sinh lại từ `data/raw` bằng
`src/build_dataset.py`, nên **không sửa tay trong DB**: sửa script hoặc dữ liệu thô rồi build lại.

## Quan hệ giữa các bảng

```
scenarios (1) ──< runs (1) ──< samples      1 dòng = 1 mẫu 2 s
                         └──< windows      1 dòng = 1 cửa sổ trượt W mẫu (đặc trưng cho ML)
                         └──< http_qos     yêu cầu HTTP từ client (run_id rỗng nếu đo ngoài lần chạy)
openflow_messages   đếm bản tin OpenFlow theo giây (chạy thử 17/11)
meta                thông tin lần build (thời điểm, quy tắc nhãn, kích thước cửa sổ...)
```

View sẵn có: `v_run_summary`, `v_windows_train`, `v_windows_test`.

## `scenarios` – danh mục kịch bản

| Cột | Ý nghĩa |
|---|---|
| scenario_id | Mã kịch bản: `N..` bình thường, `A..` tấn công, `E3` hiệu chuẩn |
| category | `normal` / `attack` / `calibration` (hiệu chuẩn, không gán nhãn, không dùng huấn luyện) |
| attack_type | Loại tấn công (`udp_packet_in_flood`; sau này thêm `syn_flood`, `flow_burst`...) |
| traffic_type | Kiểu lưu lượng sinh ra |
| description, rate_profile | Mô tả và biểu đồ tốc độ theo thời gian |
| src_host, dst_host, generator | Máy nguồn/đích trong overlay 192.168.200.0/24 và script sinh lưu lượng |

## `runs` – mỗi lần chạy một kịch bản

| Cột | Ý nghĩa |
|---|---|
| run_id | `<kịch bản>-R<lần lặp>`, ví dụ `A02-R1`. Lần lặp mới thì thêm `A02-R2`, `A02-R3`... |
| role | `dataset` (chính thức), `pilot` (chạy thử 2 phút), `calibration` |
| default_split | `train` / `test` / `excluded`. Split theo **lần chạy**, không theo từng dòng |
| start_utc, end_utc, duration_s | Thời gian lần chạy |
| n_samples, n_valid, n_label0, n_label1 | Số mẫu, số mẫu hợp lệ, số mẫu theo nhãn |
| interval_s | Chu kỳ lấy mẫu thực tế (trung vị) |
| collector, source_file, notes | Cách thu thập, file gốc trong `data/raw`, ghi chú |

## `samples` – telemetry thô đã chuẩn hóa (chu kỳ 2 s)

| Cột | Đơn vị | Nguồn / cách đo |
|---|---|---|
| run_id, seq | – | Lần chạy, số thứ tự mẫu trong file gốc |
| timestamp_utc | ISO-8601 UTC | Thời điểm lấy mẫu (cuối khoảng đo) |
| controller_cpu_pct | % của 1 lõi | CPU tiến trình Java/Karaf của ODL trong khoảng, đọc `/proc/<pid>/stat`. **Có thể > 100%** vì máy có 4 vCPU |
| controller_rss_mb | MB | RAM thường trú (VmRSS) của tiến trình ODL |
| api_latency_ms | ms | Thời gian GET RESTCONF `/rests/data/opendaylight-inventory:nodes?content=nonconfig` |
| api_http_status | mã HTTP | Mã trả về của lần GET trên (200 = bình thường) |
| openflow_connections | kết nối | Số kết nối TCP ESTABLISHED tới cổng 6653 (4 = đủ 4 switch) |
| openflow_nodes | node | Số switch trong inventory ODL |
| inventory_flow_count | flow | Tổng số flow trong inventory (mọi switch, mọi bảng) |
| packet_in_rate | bản tin/s | OFPT_PACKET_IN (type 10) trong khoảng [mẫu trước, mẫu này], đếm từ pcap cổng 6653 bằng tshark |
| flow_mod_rate | bản tin/s | OFPT_FLOW_MOD (type 14), cách đếm như trên |
| of_window_seconds, packet_in_count, flow_mod_count | s, bản tin | Độ dài khoảng đếm và số đếm tuyệt đối |
| traffic_type | – | Kiểu lưu lượng tại thời điểm đó |
| phase | – | `normal`, `baseline`, `P1..P4` / `attack`, `recovery`, `boundary` (vắt qua ranh giới), `calibration` |
| expected_rate | pps | Tốc độ sinh lưu lượng theo kịch bản (rỗng nếu không biết) |
| **label** | 0 / 1 / NULL | **1 = đang bị tấn công**, 0 = bình thường, NULL = không xác định (biên pha, hiệu chuẩn) |
| http_requests … http_latency_max_ms | – | Chỉ có ở A03: tổng hợp yêu cầu HTTP của client trong khoảng |
| t_rel_s, dt_s | s | Thời gian kể từ đầu lần chạy; khoảng cách tới mẫu trước |
| is_valid | 0/1 | 1 = đã có khoảng để tính tốc độ (mẫu đầu mỗi lần chạy = 0) |
| segment | – | Mã đoạn liên tục; mất > 1 mẫu thì sang đoạn mới, cửa sổ không nhảy qua chỗ mất |

## `windows` – đặc trưng cho học máy

Mỗi dòng là một cửa sổ trượt gồm `window_size` mẫu liên tiếp (mặc định 5 mẫu = 10 s), bước trượt 1 mẫu.

| Nhóm | Đặc trưng | Công thức |
|---|---|---|
| control_plane | `pin_last, pin_mean, pin_std, pin_min, pin_max` | Thống kê `packet_in_rate` trong cửa sổ |
| | `pin_slope` | (Packet-In cuối − đầu) / thời gian cửa sổ, tức xu hướng tăng hay giảm |
| | `fmod_mean, fmod_max` | Thống kê `flow_mod_rate` |
| controller_resource | `cpu_last, cpu_mean, cpu_std, cpu_max` | Thống kê CPU controller |
| | `api_last, api_mean, api_std, api_max` | Thống kê độ trễ RESTCONF |
| | `rss_delta` | RAM cuối − RAM đầu cửa sổ (không dùng giá trị tuyệt đối vì trôi theo phiên) |
| | `api_err_cnt` | Số lần RESTCONF trả mã khác 200 |
| | `ofconn_min` | Số kết nối OpenFlow nhỏ nhất (giảm = switch rớt kết nối) |
| topology | `flows_delta` | Số flow cuối − đầu cửa sổ |
| data_plane_traffic | *(chưa có)* | Port stats OVS, số IP nguồn, entropy... cần thu thập thêm |

Cột nhãn và quản lý:

| Cột | Ý nghĩa |
|---|---|
| label | Nhãn của **mẫu cuối** cửa sổ, tức trạng thái tại thời điểm ra quyết định |
| label_frac | Tỉ lệ mẫu tấn công trong cửa sổ |
| mixed | 1 nếu cửa sổ chứa cả mẫu thường lẫn mẫu tấn công (thời điểm bắt đầu/kết thúc tấn công) |
| split | `train` / `test` / `excluded` (lấy theo lần chạy; cửa sổ không nhãn luôn là `excluded`) |
| start_seq, end_seq, start_utc, end_utc, span_s | Vị trí và độ dài thực của cửa sổ |

Danh sách đặc trưng và nhóm cũng có trong `data/processed/features.json`, để script huấn luyện đọc trực tiếp.

## `http_qos` – chất lượng dịch vụ đo từ client

`run_id, source, timestamp_utc, http_status, response_time_ms, curl_exit_code, success`.
Mỗi dòng là một yêu cầu `curl` từ vm-client tới `http://192.168.200.12:8080/`. Bảng này dùng cho mục 4.4.3
(khả năng duy trì dịch vụ hợp lệ).

## Quy tắc gán nhãn (Ground Truth)

- Nhãn được gán theo **mốc thời gian** script tấn công ghi lại (`A0x_START`, các pha 120 s). Không gán dựa
  trên giá trị đo, để tránh nhãn "tự chứng minh".
- Mẫu có khoảng đo nằm vắt qua ranh giới hai pha thì bị loại ở dữ liệu gốc (A02, A03) hoặc để nhãn NULL (A01).
- Kiểm tra chéo: Packet-In đo được / tốc độ sinh ≈ 1.00 ở mọi pha (xem báo cáo chất lượng, mục 6).

## Ví dụ sử dụng

```python
import sqlite3, json, pandas as pd

con = sqlite3.connect(r"D:\sdn-ai-ml\data\db\sdn_ai.db")
feat = json.load(open(r"D:\sdn-ai-ml\data\processed\features.json", encoding="utf-8"))

train = pd.read_sql("SELECT * FROM v_windows_train WHERE window_size = 5", con)
test  = pd.read_sql("SELECT * FROM v_windows_test  WHERE window_size = 5", con)
X_train, y_train = train[feat["features"]], train["label"]
groups = train["run_id"]          # dùng cho GroupKFold / leave-one-run-out
```

```sql
-- Diễn biến Packet-In và CPU của một lần chạy tấn công
SELECT t_rel_s, phase, label, packet_in_rate, controller_cpu_pct, api_latency_ms
FROM samples WHERE run_id = 'A03-R1' AND is_valid = 1 ORDER BY seq;
```

# sdn-ai-ml – Học máy bảo vệ SDN Controller (Nhóm 54)

Phần AI của đồ án *"Ứng dụng học máy phát hiện và giảm thiểu tấn công DoS/DDoS bảo vệ SDN Controller"*.
Hạ tầng gồm OpenDaylight, OVS, Leaf-Spine và VXLAN, chạy trên GCP project `project-d0227366-e7f0-4e39-b78`.

## Cấu trúc

```
data/raw/           dữ liệu thô chép nguyên trạng từ VM (KHÔNG sửa)
  vm-controller/sdn-ai/   telemetry, dataset N02/N03/A02/A03, script collector/merge
  vm-client/sdn-ai/       đo HTTP QoS, script lưu lượng bình thường N02
  vm-attacker/sdn-ai/     script sinh lưu lượng A02 / N03
data/processed/     samples.csv, windows_w5.csv, features.json   (sinh tự động)
data/db/sdn_ai.db   cơ sở dữ liệu SQLite tập trung                (sinh tự động)
src/build_dataset.py  raw -> dataset + DB + báo cáo chất lượng
docs/data_dictionary.md   mô tả bảng, cột, đặc trưng, quy tắc nhãn
reports/data_quality_report.md  báo cáo chất lượng dữ liệu       (sinh tự động)
models/  notebooks/   (bước tiếp theo: huấn luyện)
```

## Cài môi trường (venv)

```powershell
cd D:\sdn-ai-ml
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Build lại dataset & huấn luyện

```powershell
.\.venv\Scripts\python.exe src\build_dataset.py                 # cửa sổ 5 mẫu (10 s)
.\.venv\Scripts\python.exe src\build_dataset.py --window 5 --window 3
.\.venv\Scripts\python.exe src\train.py                         # huấn luyện + báo cáo model
```

## Thêm một lần chạy mới

1. Chép CSV từ VM vào `data/raw/...`. Ví dụ dùng `gcloud compute scp vm-controller:<file> . --zone asia-southeast1-a --tunnel-through-iap`.
2. Thêm 1 dòng vào `RUNS` trong `src/build_dataset.py`, đặt `run_id` theo dạng `<kịch bản>-R<n>`. Nếu là kịch bản mới thì thêm vào `SCENARIOS`.
3. Chạy lại script build. DB và báo cáo sẽ được sinh lại từ đầu.

## Bản đồ mạng (dùng cho module giảm thiểu)

| Host | IP overlay | Nối vào |
|---|---|---|
| client | 192.168.200.11 | leaf1 port 4 (dự phòng leaf2 port 5) |
| web | 192.168.200.12 | leaf2 port 4 (dự phòng leaf1 port 6) |
| attacker | 192.168.200.13 | leaf1 port 5 |

leaf1 = `openflow:86529385323087`, leaf2 = `openflow:103712597946445`.
ODL Aluminium có RESTCONF tại `http://10.10.0.10:8181/rests/...`.

# CLAUDE.md — Nhật ký & bối cảnh dự án (Nhóm 54)

> Ghi cho: **bạn (phụ trách phần AI)**, các bạn cùng nhóm, và các phiên Claude Code sau.
> Mục đích: đọc file này là nắm được dự án đang ở đâu, đã làm gì, còn gì phải làm.
> Cập nhật lần cuối: 2026-11-20.

---

## 1. Đồ án là gì

**Đề tài:** "Ứng dụng học máy phát hiện và giảm thiểu tấn công DoS/DDoS bảo vệ SDN Controller."

- **Hạ tầng (nhóm đã dựng):** OpenDaylight (ODL) + Open vSwitch, kiến trúc Leaf-Spine +
  VXLAN + ECMP + Active-Standby, chạy trên Google Cloud.
- **Phần của bạn (AI):** telemetry → dataset → mô hình học máy → phát hiện & giảm thiểu
  tấn công theo thời gian thực. Đây là nội dung thư mục này.
- **Phần topo/controller:** do bạn cùng nhóm phụ trách (thư mục `/opt/aiguard` trên
  controller thuộc `kunthan231`).
- Chiến lược nhóm: **làm sản phẩm trước, viết lý thuyết (các chương) sau.**

## 2. Hạ tầng GCP

Project: `project-d0227366-e7f0-4e39-b78`, zone `asia-southeast1-a`.

| VM | IP nội bộ | IP overlay | Vai trò |
|---|---|---|---|
| vm-controller | 10.10.0.10 | — | OpenDaylight (e2-standard-4) |
| vm-spine1 / spine2 | 10.10.0.11 / .12 | — | Spine |
| vm-leaf1 / leaf2 | 10.10.0.21 / .22 | — | Leaf (OVS) |
| vm-web | 10.10.0.31 | 192.168.200.12 | Server nạn nhân (HTTP :8080) |
| vm-client | 10.10.0.32 | 192.168.200.11 | Người dùng hợp lệ |
| vm-attacker | 10.10.0.33 | 192.168.200.13 | Máy tấn công |

- OVS bridge trên leaf/spine: **`br-sdn`** (OVS 2.17). leaf1 ports: 4=vx-client,
  5=vx-attacker, 6=vx-web-bak; leaf2 port 4=vx-web. `group:10`=ECMP, `group:20`=failover.
- leaf1 DPID `openflow:86529385323087`, leaf2 `openflow:103712597946445`.
- ODL RESTCONF: `http://10.10.0.10:8181/rests/...`; OpenFlow TCP :6653.

## 3. ⚠️ PHÁT HIỆN QUAN TRỌNG (20/11/2026)

**Topo hiện tại là PROACTIVE thuần → tấn công data-plane KHÔNG chạm tới controller.**

- Cả 4 switch chỉ có flow cài sẵn, **0 rule gửi gói lên controller** (đã kiểm chứng
  trực tiếp trên VM).
- Đã bắn thử UDP từ attacker→web: gói đi qua leaf1 **nhưng controller nhận 0 Packet-In**.
- ODL (`/opt/aiguard/opendaylight`, openflowplugin 0.11.3) **không có app xử lý Packet-In**
  (không l2switch). "aiguard" chỉ là tên thư mục, không phải app riêng.
- Dataset A02/A03 cũ CÓ Packet-In thật → được thu khi còn một cấu hình reactive, sau đó
  bị cài đè thành proactive.

**Hệ quả:** muốn phần "tấn công đè controller" (chương 2.1, 4.4) có dữ liệu thật thì phải
**bật lại đường Packet-In reactive** ở controller. Đây là **Pha B** (xem mục 5).
Chi tiết kỹ thuật: xem phần "topology-proactive-finding" trong bộ nhớ Claude.

## 4. Đã làm được gì (sản phẩm trong repo)

**Dữ liệu (có sẵn từ trước, của nhóm):**
- `data/raw/` dữ liệu thô từ các VM. `src/build_dataset.py` → dataset + SQLite + báo cáo.
- `data/processed/windows_w5.csv` dataset cửa sổ 10s đã gán nhãn. `docs/data_dictionary.md`.

**Pha A — Pipeline huấn luyện (XONG):**
- `src/train.py`: so sánh **ngưỡng tĩnh vs Random Forest**, đủ chỉ số chương 4
  (Confusion, Precision/Recall/F1/FPR, ROC-AUC, PR-AUC, feature importance, tốc độ suy luận).
- Kết quả: `models/rf_w5.joblib`, `models/baseline_w5.json`, `reports/model_report.md`,
  `reports/figures/*.png`.
- **Lưu ý trung thực:** cả hai đều F1=1.000 vì **dữ liệu hiện tại quá dễ** (Packet-In một
  mình tách hết hai lớp). Chưa chứng minh được giá trị học máy → cần dữ liệu khó hơn.

**Kịch bản thu dữ liệu mới (ĐÃ VIẾT, CHƯA CHẠY):** thư mục `scenarios/`
- Mục tiêu: tạo dữ liệu chồng lấn để ML > ngưỡng tĩnh, + bổ sung SYN Flood/Flow Burst.
- `vm-client/flash_crowd_n04.py` (N04), `vm-attacker/lowrate_a04.py` (A04),
  `pulsing_a05.py` (A05), `synflood_a06.py` (A06), `flowburst_a07.py` (A07).
- `vm-leaf/dataplane_collector.py` (đặc trưng mặt phẳng dữ liệu), `preflight.sh`.
- `common/groundtruth.py` (ghi nhãn), `common/finalize_run.py` (gán nhãn tổng quát).
- `scenarios/README.md`, `RUNBOOK.md` (cách chạy), `PROGRESS.md` (checklist).
- `scenarios/restore_point/` — bản chụp flow 4 switch để khôi phục nếu đụng hạ tầng.

## 5. Kế hoạch 2 pha & việc còn lại

- **Pha A (xong):** pipeline huấn luyện chạy trên A02/A03.
- **Pha B (đang dang dở):** bật đường Packet-In reactive ở ODL để thu dữ liệu khó hơn.
  - Đã làm điểm khôi phục. Mốc kết nối tốt (client→web HTTP 200).
  - Full `odl-l2switch` **nguy hiểm** trên VXLAN mesh (loop-remover có thể sập mạng).
  - Hướng an toàn đang cân nhắc: tìm đúng cách nhóm đã bật Packet-In hồi A02/A03 để khôi
    phục, hoặc cài tối thiểu `odl-l2switch-arphandler` + table-miss có giới hạn + kiểm tra
    + rollback. **Cần phối hợp với người dựng controller (kunthan231).**

**Việc còn lại:**
1. Chốt Pha B (bật Packet-In reactive) — cần hỏi kunthan231 hồi A02/A03 bật kiểu gì.
2. Chạy các kịch bản mới theo `scenarios/RUNBOOK.md`, lặp ≥5 lần/kịch bản.
3. Thêm cột `dp_*` (data-plane) vào `window_features()` trong `src/build_dataset.py`.
4. Viết module phát hiện + giảm thiểu thời gian thực (gọi RESTCONF cài flow drop/meter).
5. Chạy thực nghiệm chương 4, vẽ biểu đồ so sánh.

## 6. Cách làm việc / quy ước kỹ thuật

- **Chạy pipeline:** `cd d:\sdn-ai-ml` rồi `python -P src\build_dataset.py` /
  `python -P src\train.py` (dùng gói Python trong user site, nên cần cờ `-P`).
- **gcloud** (đã cài, đã đăng nhập `anh2004zai`, chưa vào PATH):
  `C:\Users\NGOC TU\AppData\Local\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd`
- **SSH vào VM:** `gcloud compute ssh <vm> --zone asia-southeast1-a --tunnel-through-iap`.
  Khi gửi lệnh phức tạp qua PowerShell→gcloud→bash, **mã hóa base64** để tránh lỗi trích dẫn:
  `echo <base64> | base64 -d | bash`.
- **An toàn khi đụng hạ tầng:** luôn chụp trạng thái trước (restore_point), thử phạm vi nhỏ,
  kiểm tra client→web còn thông sau mỗi thay đổi, hỏng là rollback.
- **Các VM giờ có IP ngoài** → tải được gói (apt/maven) nếu cần.
- Tấn công **chỉ** nhắm vm-web trong VPC cô lập, có giới hạn tốc độ — mục đích phòng thủ.

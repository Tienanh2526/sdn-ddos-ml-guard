# sdn-ddos-ml-guard: Học máy bảo vệ SDN Controller (Nhóm 54)

Phần AI của đồ án **"Ứng dụng học máy phát hiện và giảm thiểu tấn công DoS/DDoS bảo vệ SDN Controller"**.
Hạ tầng: OpenDaylight + Open vSwitch, kiến trúc Leaf-Spine + VXLAN + ECMP, chạy trên Google Cloud
(project `project-d0227366-e7f0-4e39-b78`, zone `asia-southeast1-a`).

> **Lấy code:** `git clone -b dev https://github.com/Tienanh2526/sdn-ddos-ml-guard.git`
> Nhánh làm việc là **`dev`**. `main` còn là bản cũ, sẽ cập nhật khi hoàn thiện cả phần web.

---

## 1. Dự án làm gì (đọc 1 phút)

- **Vấn đề:** trong mạng SDN, mỗi khi switch gặp một luồng gói tin mới, nó gửi tin **Packet-In** lên controller để
  hỏi. Kẻ tấn công tạo thật nhiều luồng mới, controller bị dồn hàng trăm Packet-In mỗi giây, CPU và độ trễ tăng vọt,
  cả mạng chậm theo.
- **Giải pháp:** một **module AI chạy trên controller**. Cứ 2 giây nó đọc số liệu của controller và của switch, dùng
  mô hình **Random Forest** đánh giá có đang bị tấn công không. Nếu có, nó tìm ra máy tấn công rồi giảm thiểu **2 cấp**:
  - **cấp 1:** cho gói của máy đó đi thẳng, không hỏi controller;
  - **cấp 2:** chặn hẳn tại switch.

  Hết tấn công thì module tự gỡ chặn.
- **Kết quả chính** (đo thật trên GCP):

| | Kết quả |
|---|---|
| Chất lượng phát hiện (25 lần chạy, kiểm định chéo theo lần chạy) | Random Forest **F1 0,995**, 0 báo động giả/giờ. Cách cũ (ngưỡng Packet-In) chỉ F1 0,853 và báo nhầm 100% lúc người dùng thật tăng đột biến |
| Đổi máy tấn công (học trên R1–R3, thử trên R4–R5) | F1 **0,986**: mô hình học hành vi, không học thuộc máy nào |
| Giảm thiểu SYN flood (pha mạnh nhất) | Packet-In **574 → 2/s**, CPU controller **64% → 9%**, độ trễ web người dùng **62 → 5,6 ms** |
| Thời gian | Phát hiện khoảng **6 s**, cấp 1 có hiệu lực khoảng **9 s** |
| An toàn | **0 lần chặn nhầm** người dùng; tự gỡ chặn khi hết tấn công |

---

## 2. Viết quyển: tư liệu từng chương nằm ở đâu

| Chương / mục | Đọc file này |
|---|---|
| Bối cảnh, mục tiêu, cách tấn công lợi dụng Packet-In | Mục 1 ở trên · `docs/huong-dan-thuc-hanh-gcp.md` (mục 0, 8) |
| Hạ tầng lab, topo, vai trò từng máy | `docs/huong-dan-thuc-hanh-gcp.md` (mục 0) · phần hạ tầng do bạn phụ trách topo viết |
| Đường đi gói tin của từng kịch bản (hop by hop) | `docs/traces/` (A04…A07, N04, BG) |
| Thiết kế dữ liệu, 5 kịch bản, cách gán nhãn | `scenarios/README.md` · `scenarios/RUNBOOK.md` |
| Danh sách đặc trưng, cột dữ liệu, quy tắc nhãn | `docs/data_dictionary.md` · `data/processed/features.json` |
| Chất lượng dữ liệu | `reports/data_quality_report.md` |
| **Thực nghiệm phát hiện** (AI vs ngưỡng, đặc trưng, tráo vai, độ trễ, báo động giả) | `reports/experiment_report.md` |
| **Vì sao chọn Random Forest** (so 8 thuật toán, khoảng tin cậy) | `reports/model_comparison.md` |
| **Thực nghiệm giảm thiểu** (bảng tổng hợp, quá trình sửa lỗi) | `reports/mitigation_summary.md` |
| Chi tiết từng lần chạy có module | `reports/mitigation_<RUN>.md` (A04-M1…M4, A05-M2, A06-M3, A07-M3, N04-M2, A04/A06-S2…) |
| Hình vẽ cho quyển | `reports/figures/` (`exp_*`: phát hiện · `mit_*`: giảm thiểu · `cmp_models.png`: so thuật toán) |
| Thiết kế module thời gian thực (máy trạng thái, quy kết nguồn, 2 cấp) | Đầu file `src/realtime/detector.py` (docstring lớp `Engine`) |
| Kiểm thử hồi quy các lỗi đã gặp | `src/realtime/test_engine.py` |
| Phụ lục: hướng dẫn thao tác lab | `docs/huong-dan-thuc-hanh-gcp.md` |

Mọi số liệu trong `reports/` đều được **sinh tự động từ dữ liệu thật** bởi các script trong `src/`, nên chạy lại là ra
đúng số đó (xem mục 4).

---

## 3. Cấu trúc thư mục

```
data/raw/vm-controller/sdn-ai/dataset/
    attack/A04..A07/<RUN>/      dữ liệu thô từng lần chạy tấn công (CSV 2 s, groundtruth, log module AI)
    normal/N04/<RUN>/           lần chạy lưu lượng hợp lệ (flash crowd)
      R1–R3 = máy mặc định · R4–R5 = tráo vai máy · M = có module · S = có module + tráo vai
data/processed/                 samples.csv, windows_w5.csv (cửa sổ 10 s), features.json   (sinh tự động)
data/db/sdn_ai.db               SQLite tổng hợp                                             (sinh tự động)
src/build_dataset.py            dữ liệu thô → dataset + DB + báo cáo chất lượng
src/evaluate.py                 thực nghiệm phát hiện chương 4 → reports/experiment_report.md + models/detector_rf.joblib
src/compare_models.py           so sánh 8 thuật toán → reports/model_comparison.md
src/analyze_mitigation.py       so 1 cặp lần chạy (không module / có module) → reports/mitigation_<RUN>.md
src/summarize_mitigation.py     bảng tổng hợp giảm thiểu → reports/mitigation_summary.md
src/realtime/detector.py        MODULE AI THỜI GIAN THỰC (chạy trên vm-controller)
src/realtime/odl.py             gọi RESTCONF của OpenDaylight: cài / gỡ / đọc rule
src/realtime/replay.py          kiểm thử offline module bằng dữ liệu đã thu
src/realtime/test_engine.py     kiểm thử hồi quy
src/train.py                    (bản Pha A cũ trên dữ liệu A02/A03 — chỉ để tham khảo lịch sử)
scenarios/                      kịch bản sinh lưu lượng trên từng VM + script điều phối (orchestrate/)
models/detector_rf.joblib       mô hình đang triển khai (Random Forest 30 đặc trưng + mô hình dự phòng)
docs/                           tài liệu: hướng dẫn GCP, từ điển dữ liệu, đường đi gói tin
reports/                        báo cáo + hình (sinh tự động)
```

---

## 4. Cài môi trường và chạy lại

Cần Python 3.11 trở lên.

```powershell
git clone -b dev https://github.com/Tienanh2526/sdn-ddos-ml-guard.git
cd sdn-ddos-ml-guard
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Sinh lại toàn bộ số liệu, báo cáo và hình từ dữ liệu thô (không cần Google Cloud):

```powershell
.\.venv\Scripts\python.exe src\build_dataset.py        # dataset             (~1 phút)
.\.venv\Scripts\python.exe src\evaluate.py             # thực nghiệm phát hiện (~1 phút)
.\.venv\Scripts\python.exe src\compare_models.py       # so sánh thuật toán   (~3 phút)
.\.venv\Scripts\python.exe src\summarize_mitigation.py # tổng hợp giảm thiểu
.\.venv\Scripts\python.exe src\realtime\test_engine.py # kiểm thử module (phải 7/7 OK)
```

(macOS/Linux: thay `.\.venv\Scripts\python.exe` bằng `.venv/bin/python`.)

---

## 5. Thao tác trên Google Cloud

Đọc **`docs/huong-dan-thuc-hanh-gcp.md`**: bật lab, bật chế độ reactive, tự tấn công thử bằng `hping3`, đọc log để
thấy AI phát hiện và chặn, rồi tắt lab. Cần được thêm vào project GCP trước.

Lưu ý: tấn công **chỉ** nhắm vm-web trong mạng lab riêng, có giới hạn tốc độ, cho mục đích phòng thủ.

---

## 6. Quy ước làm việc với Git

- Nhánh chung: **`dev`**. Việc mới tách nhánh riêng từ `dev` (`feat/...`, `exp/...`, `fix/...`, `docs/...`), làm xong
  merge lại vào `dev`.
- Không sửa tay dữ liệu trong `data/raw/` (dữ liệu gốc đo từ VM).
- Không sửa tay file trong `reports/` và `data/processed/`. Muốn đổi thì sửa script trong `src/` rồi chạy lại.
- Viết quyển nên để ngoài repo (Word / Google Docs), chỉ lấy số liệu và hình từ đây.

## 7. Đang làm tiếp

- Giao diện web (dashboard topo + thao tác lab), phần AI phụ trách. Khi xong sẽ merge `dev` vào `main`.

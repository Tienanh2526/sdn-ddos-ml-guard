# ✅ CHECKLIST CÔNG VIỆC — Phần AI (Nhóm 54)

> Đọc file này để biết **đã làm gì** và **còn phải làm gì**.
> Ký hiệu:  `[x]` = xong · `[ ]` = chưa làm · ⏳ = đang làm dở.
> Bối cảnh đầy đủ xem [CLAUDE.md](CLAUDE.md). Cập nhật: 2026-11-21.

---

## 📊 Tiến độ tổng thể

Phần AI có 4 chặng: **Dữ liệu → Dataset → Mô hình → Phát hiện/Giảm thiểu**.
Hiện đang ở cuối chặng 3 (mô hình đã chạy được). Vướng mắc hạ tầng **Pha B** (mạng không
đẩy Packet-In lên controller) **đã gỡ xong 21/11** → sẵn sàng thu dữ liệu mới.

```
[####################------] Chặng 1-2: dữ liệu + dataset   (gần xong)
[######################----] Chặng 3: mô hình               (pipeline xong, chờ data tốt)
[####----------------------] Chặng 4: phát hiện/giảm thiểu  (chưa làm)
[##########################] Pha B: bật Packet-In reactive  (XONG 21/11)
```

---

## 1. Dữ liệu & Dataset (chặng 1–2)

- [x] Thu dữ liệu thô từ các VM (N02, N03, A02, A03…) — *nhóm đã làm*
- [x] `src/build_dataset.py`: ghép dữ liệu → dataset cửa sổ 10s + SQLite + báo cáo
- [x] Báo cáo chất lượng dữ liệu `reports/data_quality_report.md`
- [x] Từ điển dữ liệu `docs/data_dictionary.md`
- [x] Sửa `dataplane_collector.py`: chỉ bắt VXLAN host→leaf, đọc trường overlay (trước đây
      đọc nhầm IP underlay, tỉ lệ SYN luôn 0); thêm `dp_new_flows`; tshark đã cài trên 2 leaf
- [ ] Thêm cột đặc trưng **mặt phẳng dữ liệu** (`dp_*`: số gói, IP nguồn, flow mới, tỉ lệ SYN)
      vào `window_features()` trong `src/build_dataset.py` *(3 chỗ, chờ có dữ liệu mới)*.
      ⚠️ KHÔNG dùng cột theo cổng `rxpps_<port>` làm đặc trưng (gắn danh tính host → rò rỉ nhãn)

## 2. Kịch bản thu dữ liệu khó hơn (thư mục `scenarios/`)

Lý do: dữ liệu hiện tại quá dễ → cần kịch bản chồng lấn để chứng minh ML > ngưỡng tĩnh.

- [x] Viết `flash_crowd_n04.py` (N04 — đột biến hợp lệ)
- [x] Viết `lowrate_a04.py` (A04 — tấn công tốc độ thấp)
- [x] Viết `pulsing_a05.py` (A05 — tấn công ngắt quãng)
- [x] Viết `synflood_a06.py` (A06 — SYN flood có giới hạn)
- [x] Viết `flowburst_a07.py` (A07 — flow burst)
- [x] Viết bộ thu đặc trưng data-plane `dataplane_collector.py` + `preflight.sh`
- [x] Viết thư viện gán nhãn `groundtruth.py` + `finalize_run.py`
- [x] Viết hướng dẫn chạy `RUNBOOK.md`
- [ ] **CHẠY** các kịch bản trên GCP, lặp ≥5 lần/kịch bản
- [ ] Chép dữ liệu mới về `data/raw/` và build lại dataset

## 3. Mô hình học máy (chặng 3) — Pha A

- [x] `src/train.py`: ngưỡng tĩnh vs Random Forest
- [x] Đủ chỉ số chương 4: Confusion, Precision/Recall/F1/FPR, ROC-AUC, PR-AUC, tốc độ
- [x] Độ quan trọng đặc trưng (feature importance) + biểu đồ ROC/PR
- [x] Lưu mô hình `models/rf_w5.joblib` + ngưỡng `models/baseline_w5.json`
- [x] Báo cáo `reports/model_report.md`
- [ ] Chạy LẠI `train.py` sau khi có dữ liệu khó hơn (lúc đó RF mới hơn ngưỡng tĩnh rõ rệt)
- [ ] So sánh **Traffic-only vs Controller-aware** (mục 4.5.2) *(cần cột `dp_*`)*

## 4. Phát hiện & Giảm thiểu thời gian thực (chặng 4) — chưa làm

- [ ] Module phát hiện: đọc telemetry → tính đặc trưng → mô hình chấm điểm theo thời gian thực
- [ ] Xác nhận qua nhiều cửa sổ (temporal confirmation) — chống báo động nhấp nháy
- [ ] Xác định IP/cổng nguồn đáng ngờ
- [ ] Risk/Decision Engine + máy trạng thái (NORMAL→SUSPECT→MITIGATING→RECOVERING)
- [ ] Gọi RESTCONF cài flow **drop/meter** tại leaf để chặn (có whitelist an toàn)
- [ ] Xác minh rule đã hiệu lực (`dump-flows`) + tự khôi phục khi hết tấn công
- [ ] Đo độ trễ toàn chu trình (phát hiện → rule hiệu lực)

## 5. ✅ Pha B — Bật Packet-In reactive (XONG 21/11)

Vướng mắc cũ: **mạng proactive → tấn công không chạm controller (0 Packet-In)**.

- [x] Phát hiện & kiểm chứng vấn đề (bắn thử → controller nhận 0 Packet-In)
- [x] Chụp điểm khôi phục mạng `scenarios/restore_point/` (+ backup tự động trên leaf
      `/var/tmp/aiguard-reactive/` mỗi lần apply)
- [x] Đo mốc kết nối client→web (HTTP 5/5, ping ~2,3 ms)
- [x] kunthan231 giao toàn quyền hạ tầng. Cấu hình hồi A02/A03 không còn dấu vết (lab đã reboot)
- [x] PoC 2 đầu: controller KHÔNG chặn Packet-In (50/50) → chỉ cần rule trên leaf
- [x] Viết + bật `scenarios/vm-leaf/reactive_mode.sh` (reactive mô phỏng: flow mới → Packet-In,
      OVS learn idle 10 s) trên leaf1 + leaf2
- [x] Kiểm chứng: 1 flow → 1 Packet-In; 100 flow mới → 99; HTTP → 2/request;
      client↔web vẫn 5/5; generator per_packet 450 gói → 450 Packet-In
- [x] Generator UDP chuyển sang `flow_mode="per_packet"` (mỗi gói một flow mới)
- [ ] Kiểm tra CPU controller tăng rõ khi tấn công mức cao (sẽ thấy trong lần thu đầu tiên)
- [ ] ⚠️ Rule mất khi leaf reboot → nhớ `reactive_mode.sh status` trước mỗi buổi thu

## 6. Việc tổ chức / bàn giao

- [x] Tạo `CLAUDE.md` (nhật ký dự án)
- [x] Tạo `CHECKLIST.md` (file này)
- [x] Khởi tạo git repo + commit đầu tiên
- [x] Tạo môi trường ảo `.venv` + cài `requirements.txt` (chạy `train.py` OK trong venv)
- [ ] Tạo repo GitHub **private** để sao lưu + chia sẻ nhóm *(cần tài khoản của bạn)*
- [ ] (Khi viết lý thuyết) đối chiếu từng mục với đề cương các chương 2–4

---

## 👉 Việc nên làm TIẾP THEO (ưu tiên)

1. ~~Gỡ Pha B~~ ✅ xong 21/11.
2. Chạy các kịch bản `scenarios/` theo RUNBOOK để có dữ liệu khó hơn
   (trước đó nên sửa lỗi bắt gói VXLAN trong `dataplane_collector.py`).
3. Có dữ liệu mới → chạy lại `train.py` + làm module phát hiện/giảm thiểu (chặng 4).

# Tiến độ — Việc 1: Thu dữ liệu kịch bản bổ sung

> File theo dõi để không mất mạch khi phiên làm việc bị ngắt. Cập nhật mỗi khi
> xong một mục. Bối cảnh: đồ án tốt nghiệp, lab SDN cô lập tự dựng, mục đích
> phòng thủ (ML phát hiện/giảm thiểu DoS/DDoS bảo vệ SDN Controller).

## Mục tiêu việc 1
Làm dataset khó hơn (có vùng chồng lấn Packet-In) để chứng minh ML > ngưỡng tĩnh
(4.5.1), và bổ sung loại tấn công còn thiếu theo đề cương (SYN Flood, Flow Burst —
2.2.2). Chi tiết lý do: xem [README.md](README.md).

## Checklist sản phẩm cần viết

- [x] `README.md` — tổng quan 5 kịch bản + trả lời câu hỏi topo
- [x] `PROGRESS.md` — file này
- [x] `common/groundtruth.py` — thư viện chung: vòng lặp theo pha + ghi `*.groundtruth.json`
- [x] `vm-client/flash_crowd_n04.py` — lưu lượng hợp lệ đột biến, nhiều flow mới (nhãn 0)
- [x] `vm-attacker/lowrate_a04.py` — tấn công tốc độ thấp 10–30 pps (nhãn 1)
- [x] `vm-attacker/pulsing_a05.py` — tấn công bật/tắt ngắt quãng (nhãn 1)
- [x] `vm-attacker/flowburst_a07.py` — nhiều 5-tuple mới để sinh Flow-Mod (nhãn 1)
- [x] `vm-attacker/synflood_a06.py` — SYN flood có giới hạn, chỉ nhắm vm-web (nhãn 1)
- [x] `vm-leaf/dataplane_collector.py` — pps theo cổng, số IP nguồn, tỉ lệ SYN (đặc trưng mới)
- [x] `vm-leaf/preflight.sh` — kiểm tra 3 điều kiện (đường qua OVS, table-miss→controller, cổng TCP của web)
- [x] `common/finalize_run.py` — gán nhãn tổng quát từ groundtruth.json (thay merge_a0X + finalize_a0X)
- [x] `RUNBOOK.md` — quy trình từng lần chạy + đoạn SCENARIOS/RUNS cần thêm vào build_dataset.py
- [~] Tích hợp `src/build_dataset.py`:
      - SCENARIOS/RUNS: đã có snippet sẵn trong RUNBOOK mục 4 (thêm khi có file dữ liệu thật)
      - **CÒN LẠI (chưa làm):** đưa cột `dp_*` vào cửa sổ đặc trưng — sửa 3 chỗ trong
        build_dataset.py: FEATURE_GROUPS["data_plane_traffic"], dict `a` và phần tổng
        hợp trong window_features(). Cần làm để so sánh Traffic-only vs Controller-aware (4.5.2).

## Thứ tự triển khai (khi quay lại cứ theo đây)
1. `common/groundtruth.py` trước (các generator đều import).
2. Lần lượt các generator: N04 → A04 → A05 → A07 → A06.
3. `dataplane_collector.py` + `preflight.sh`.
4. `RUNBOOK.md`.
5. Sửa `build_dataset.py` để nuốt dữ liệu mới.
6. Nhóm chạy thực tế trên GCP, lặp ≥5 lần/kịch bản, chép CSV về `data/raw/...`.

## Ghi chú kỹ thuật cần nhớ
- Khuôn mẫu generator theo `data/raw/vm-attacker/sdn-ai/attack_a02.py`: socket UDP,
  `time.monotonic()`, `next_send += 1/rate`, in `START=`/`END=`/`PHASE=`.
- Thực nghiệm cũ: send_rate ≈ packet_in_rate (1:1) → mỗi datagram = 1 Packet-In.
- Overlay: client 192.168.200.11, web 192.168.200.12, attacker 192.168.200.13.
  leaf1 `openflow:86529385323087`, leaf2 `openflow:103712597946445`.
- An toàn (theo mẫu `a02_watchdog.py`): precheck sức khỏe, giới hạn rate+duration,
  chỉ target 192.168.200.12, tự dừng nếu controller/web mất kết nối.
- Pipeline nhãn hiện có: `merge_a03.py` (ghép pcap OpenFlow) + `finalize_a03.py`
  (gán pha theo attack_start/attack_end). Generator phải in mốc thời gian đúng khuôn.

## Trạng thái hiện tại (cập nhật 20/11)

**Phát hiện lớn:** topo đang chạy là PROACTIVE thuần, ODL (`/opt/aiguard`) không có app
reactive → tấn công data-plane KHÔNG sinh Packet-In (đã kiểm chứng trực tiếp trên VM).
Chi tiết: memory `topology-proactive-finding`. Vì vậy các kịch bản A06/A07 chưa chạy
được đúng ý cho tới khi bật lại đường Packet-In reactive.

**Kế hoạch 2 pha:**
- Pha A (XONG): pipeline huấn luyện `src/train.py` chạy trên A02/A03.
  → models/rf_w5.joblib, models/baseline_w5.json, reports/model_report.md, reports/figures/*.
  Kết quả: RF & ngưỡng tĩnh đều F1=1.000 (dữ liệu quá dễ — đã ghi rõ trong báo cáo).
- Pha B (XONG 21/11): reactive mô phỏng trên leaf bằng `vm-leaf/reactive_mode.sh`
  (flow mới → Packet-In, OVS learn idle 10 s, chuyển tiếp giữ group:20). Đính chính: controller
  không chặn Packet-In (PoC 2 đầu 50/50) — không cần l2switch. Đã kiểm chứng ngữ nghĩa
  reactive + client↔web 5/5. Generator UDP đổi sang `flow_mode="per_packet"` (450/450).
  Rule mất khi leaf reboot → `reactive_mode.sh status` trước mỗi buổi thu.

**Việc còn lại khác:**
1. Nhóm (hoặc mình, sau Pha B) chạy các kịch bản theo RUNBOOK, lặp ≥5 lần/kịch bản.
2. Thêm cột dp_* vào window_features() của build_dataset.py (3 chỗ).
3. Module phát hiện/giảm thiểu thời gian thực (sau khi có dữ liệu khó).

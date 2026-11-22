# Báo cáo thực nghiệm — Phát hiện tấn công bảo vệ SDN Controller (chương 4)

Sinh tự động bởi `src/evaluate.py` lúc 2026-11-22 08:08 UTC.

## 1. Thiết lập

- Dữ liệu: các kịch bản mới (lưu lượng nền luôn chạy, reactive mô phỏng, có đặc trưng mặt phẳng dữ liệu): **4806 cửa sổ 10 s** từ **15 lần chạy** — số lần chạy/kịch bản: {'A04': 3, 'A05': 3, 'A06': 3, 'A07': 3, 'N04': 3}.
- Nhãn 0: 2139 cửa sổ, nhãn 1: 2667. Bỏ 194 cửa sổ trong **12 s ngay sau khi tấn công dừng** (dư chấn: SYN-ACK gửi lại, cửa sổ còn mẫu tấn công).
- **Kiểm định chéo leave-one-run-out (LORO):** mỗi lần chạy lần lượt làm tập kiểm thử, huấn luyện trên các lần chạy còn lại → đánh giá trên lần chạy *chưa từng thấy*, không rò rỉ giữa các cửa sổ chồng lấn.
- Bộ đặc trưng: traffic-only 10, controller-aware 20, kết hợp 30 (danh sách trong `data/processed/features.json`).
- Đường cơ sở: **Ngưỡng Packet-In** (`pin_mean`, chọn ngưỡng F1 tốt nhất trên tập huấn luyện mỗi fold) và **Ngưỡng tốt nhất** (thử mọi đặc trưng, mọi chiều, lấy cái tốt nhất trên tập huấn luyện — đường cơ sở mạnh).
- Random Forest: 300 cây, `min_samples_leaf=2`, `class_weight=balanced`, ngưỡng xác suất 0,5.

## 2. Kết quả chính (gộp mọi fold LORO, theo từng cửa sổ)

| Phương pháp | Precision | Recall | F1 | FPR | ROC-AUC | PR-AUC |
|---|---|---|---|---|---|---|
| Ngưỡng Packet-In | 0.747 | 0.998 | 0.854 | 0.421 | 0.995 | 0.997 |
| Ngưỡng tốt nhất | 0.992 | 0.991 | 0.991 | 0.010 | 0.992 | 0.994 |
| RF traffic-only | 0.994 | 0.992 | 0.993 | 0.007 | 0.996 | 0.997 |
| RF controller-aware | 0.944 | 0.979 | 0.961 | 0.072 | 0.999 | 1.000 |
| RF kết hợp | 0.999 | 0.998 | 0.998 | 0.001 | 1.000 | 1.000 |

Chênh lệch F1: RF kết hợp − đường cơ sở tốt nhất = **+0.007**.

![So sánh phương pháp](figures/exp_methods.png)

## 3. Theo loại lưu lượng

| Phương pháp | Recall Low-rate | Recall Pulsing | Recall SYN flood | Recall Flow burst | FPR flash crowd |
|---|---|---|---|---|---|
| Ngưỡng Packet-In | 0.993 | 1.000 | 1.000 | 1.000 | 1.000 |
| Ngưỡng tốt nhất | 0.993 | 0.944 | 0.998 | 0.998 | 0.000 |
| RF traffic-only | 0.995 | 0.948 | 0.999 | 0.998 | 0.000 |
| RF controller-aware | 0.940 | 1.000 | 1.000 | 0.989 | 0.169 |
| RF kết hợp | 0.995 | 0.996 | 1.000 | 1.000 | 0.000 |

![Recall theo loại tấn công](figures/exp_recall_by_attack.png)

## 4. F1 theo từng lần chạy (lần chạy có cả hai lớp)

| Phương pháp | A04-R1 | A04-R2 | A04-R3 | A05-R1 | A05-R2 | A05-R3 | A06-R1 | A06-R2 | A06-R3 | A07-R1 | A07-R2 | A07-R3 | TB ± ĐLC |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Ngưỡng Packet-In | 0.996 | 0.994 | 0.996 | 1.000 | 0.994 | 1.000 | 0.982 | 0.985 | 0.991 | 1.000 | 0.998 | 1.000 | 0.995 ± 0.006 |
| Ngưỡng tốt nhất | 0.996 | 0.994 | 0.998 | 0.960 | 0.965 | 0.977 | 0.985 | 0.989 | 0.989 | 1.000 | 0.998 | 0.996 | 0.987 ± 0.013 |
| RF traffic-only | 0.998 | 0.994 | 1.000 | 0.960 | 0.965 | 0.977 | 0.987 | 0.994 | 0.996 | 1.000 | 0.998 | 0.996 | 0.989 ± 0.013 |
| RF controller-aware | 0.991 | 0.926 | 0.983 | 1.000 | 1.000 | 1.000 | 0.994 | 1.000 | 1.000 | 0.994 | 1.000 | 0.989 | 0.990 ± 0.020 |
| RF kết hợp | 0.998 | 0.996 | 0.998 | 0.994 | 0.994 | 1.000 | 0.996 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 | 0.998 ± 0.002 |

## 5. Xác nhận nhiều cửa sổ (3/4) — chống báo động nhấp nháy (mục 2.6.1)

Báo động khi ≥ 3 trong 4 cửa sổ gần nhất (cửa sổ trượt 2 s) bị phân loại là tấn công. Độ trễ tính từ cửa sổ đầu tiên của đợt tấn công tới khi báo động.

| Phương pháp | F1 sau xác nhận | FPR sau xác nhận | Đợt tấn công | Bỏ sót đợt | Độ trễ TB (s) | Độ trễ P90 (s) | Báo động giả / giờ |
|---|---|---|---|---|---|---|---|
| Ngưỡng Packet-In | 0.84 | 0.44 | 39 | 0 | 4.28 | 4.05 | 2.52 |
| Ngưỡng tốt nhất | 0.97 | 0.03 | 39 | 0 | 5.32 | 6.07 | 0.00 |
| RF traffic-only | 0.97 | 0.02 | 39 | 0 | 5.11 | 6.05 | 0.00 |
| RF controller-aware | 0.94 | 0.07 | 39 | 0 | 4.23 | 4.05 | 14.31 |
| RF kết hợp | 0.98 | 0.02 | 39 | 0 | 4.28 | 4.44 | 0.00 |

## 6. Độ quan trọng đặc trưng (RF kết hợp, toàn bộ dữ liệu)

| Đặc trưng | Importance |
|---|---|
| `dp_reply_mean` | 0.2609 |
| `dp_ppf_mean` | 0.1881 |
| `dp_flows_max` | 0.0990 |
| `dp_flows_mean` | 0.0789 |
| `pin_mean` | 0.0472 |
| `dp_dport_h_mean` | 0.0448 |
| `pin_last` | 0.0421 |
| `dp_udp_frac_mean` | 0.0393 |
| `pin_max` | 0.0333 |
| `dp_size_mean` | 0.0282 |
| `api_mean` | 0.0238 |
| `api_last` | 0.0234 |
| `api_max` | 0.0154 |
| `pin_min` | 0.0153 |
| `cpu_mean` | 0.0144 |

![Độ quan trọng](figures/exp_feature_importance.png)

## 7. Diễn biến theo thời gian (lần chạy R1)

![A04-R1](figures/exp_timeline_A04-R1.png)
![A05-R1](figures/exp_timeline_A05-R1.png)
![A06-R1](figures/exp_timeline_A06-R1.png)
![A07-R1](figures/exp_timeline_A07-R1.png)
![N04-R1](figures/exp_timeline_N04-R1.png)

## 8. Ngưỡng mà đường cơ sở chọn ở từng fold

- **Ngưỡng Packet-In**: A04-R1: `pin_mean` > 11.344; A04-R2: `pin_mean` > 11.464; A04-R3: `pin_mean` > 11.344; A05-R1: `pin_mean` > 11.344; A05-R2: `pin_mean` > 11.344; A05-R3: `pin_mean` > 11.344; A06-R1: `pin_mean` > 11.344; A06-R2: `pin_mean` > 11.288; A06-R3: `pin_mean` > 11.344; A07-R1: `pin_mean` > 11.344; A07-R2: `pin_mean` > 11.344; A07-R3: `pin_mean` > 11.344; N04-R1: `pin_mean` > 11.344; N04-R2: `pin_mean` > 11.344; N04-R3: `pin_mean` > 11.344
- **Ngưỡng tốt nhất**: A04-R1: `dp_reply_mean` < 0.965; A04-R2: `dp_reply_mean` < 0.965; A04-R3: `dp_reply_mean` < 0.965; A05-R1: `dp_reply_mean` < 0.965; A05-R2: `dp_reply_mean` < 0.965; A05-R3: `dp_reply_mean` < 0.964; A06-R1: `dp_reply_mean` < 0.966; A06-R2: `dp_reply_mean` < 0.965; A06-R3: `dp_reply_mean` < 0.965; A07-R1: `dp_reply_mean` < 0.965; A07-R2: `dp_reply_mean` < 0.965; A07-R3: `dp_reply_mean` < 0.965; N04-R1: `dp_reply_mean` < 0.965; N04-R2: `dp_reply_mean` < 0.965; N04-R3: `dp_reply_mean` < 0.965

## 9. Mô hình xuất cho module phát hiện thời gian thực

`models/detector_rf.joblib` — RF kết hợp huấn luyện trên toàn bộ 15 lần chạy, 30 đặc trưng, xác nhận 3/4. Suy luận ~41.46 ms/cửa sổ.

## 10. Ghi chú trung thực

- Kết quả sẽ ổn định hơn khi đủ 5 lần chạy/kịch bản (R4–R5 tráo vai máy nguồn). Với ít lần chạy, một fold LORO có thể thiếu hẳn một loại lưu lượng trong tập huấn luyện (ví dụ bỏ N04 ra làm test thì mô hình chưa từng thấy flash crowd) — đây là kiểm thử tổng quát hóa khắt khe, không phải lỗi.
- Đặc trưng theo cổng/máy (`rxpps_<port>`) KHÔNG được dùng (tránh học thuộc danh tính máy).
- Flow-Mod luôn = 0 (switch tự học flow trong chế độ reactive mô phỏng) → `fmod_*` không mang thông tin.
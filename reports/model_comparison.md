# So sánh thuật toán học máy (chương 4)

Sinh bởi `src/compare_models.py` lúc 2026-11-23 04:32 UTC. **8011 cửa sổ 10 s** từ **25 lần chạy thật**, cùng **30 đặc trưng kết hợp**, cùng giao thức với `evaluate.py` (LORO; bỏ 324 cửa sổ đệm 12 s sau tấn công). Các mô hình nhạy với thang đo (LR, k-NN, SVM, MLP) được chuẩn hóa bằng `StandardScaler` *trong* pipeline (học trên fold huấn luyện, không rò rỉ). Siêu tham số để mặc định hợp lý, KHÔNG tinh chỉnh trên tập kiểm thử.

## 1. Chất lượng phát hiện (LORO, theo cửa sổ)

| Phương pháp | Precision | Recall | F1 | FPR | ROC-AUC | Recall Low-rate | Recall Pulsing | Recall SYN flood | Recall Flow burst | FPR flash crowd |
|---|---|---|---|---|---|---|---|---|---|---|
| Ngưỡng tốt nhất (cơ sở) | 0.992 | 0.985 | **0.989** | 0.010 | 0.992 | 0.992 | 0.955 | 0.976 | 0.998 | 0.001 |
| Logistic Regression | 0.998 | 0.988 | **0.993** | 0.002 | 0.996 | 0.992 | 0.982 | 0.975 | 0.999 | 0.001 |
| Decision Tree | 0.994 | 0.988 | **0.991** | 0.008 | 0.989 | 0.991 | 0.984 | 0.974 | 0.999 | 0.001 |
| k-NN (k=5) | 0.995 | 0.989 | **0.992** | 0.006 | 0.996 | 0.988 | 0.998 | 0.977 | 1.000 | 0.011 |
| SVM (RBF) | 0.990 | 0.991 | **0.991** | 0.012 | 0.997 | 0.994 | 1.000 | 0.978 | 0.999 | 0.026 |
| MLP (64-32) | 0.998 | 0.989 | **0.993** | 0.003 | 0.996 | 0.994 | 0.993 | 0.971 | 0.999 | 0.004 |
| Random Forest (đang dùng) | 0.998 | 0.993 | **0.995** | 0.002 | 0.998 | 0.996 | 1.000 | 0.979 | 1.000 | 0.000 |
| XGBoost | 0.997 | 0.992 | **0.994** | 0.004 | 0.999 | 0.996 | 0.996 | 0.977 | 1.000 | 0.000 |

## 2. Khi chạy như module thật (xác nhận 3/4) và khi tráo vai máy

Tráo vai: huấn luyện R1–R3 (4806 cửa sổ, tấn công luôn từ .13) → kiểm thử R4–R5 (3205 cửa sổ, tấn công từ .11).

| Phương pháp | Độ trễ phát hiện TB (s) | Bỏ sót đợt | Báo động giả / giờ | F1 tráo vai | FPR flash crowd tráo vai |
|---|---|---|---|---|---|
| Ngưỡng tốt nhất (cơ sở) | 5.14 | 0/65 | 1.01 | 0.985 | 0.002 |
| Logistic Regression | 4.65 | 0/65 | 0.00 | 0.983 | 0.005 |
| Decision Tree | 4.49 | 0/65 | 1.01 | 0.977 | 0.002 |
| k-NN (k=5) | 4.37 | 0/65 | 1.52 | 0.985 | 0.000 |
| SVM (RBF) | 4.30 | 0/65 | 3.54 | 0.989 | 0.002 |
| MLP (64-32) | 4.40 | 0/65 | 0.51 | 0.981 | 0.002 |
| Random Forest (đang dùng) | 4.18 | 0/65 | 0.00 | 0.986 | 0.000 |
| XGBoost | 4.24 | 0/65 | 0.51 | 0.984 | 0.000 |

## 3. Chi phí triển khai

Suy luận đo đúng cách module gọi: DataFrame 1 dòng, 200 cửa sổ, trung vị (máy phát triển, Windows; cùng điều kiện cho mọi mô hình). Ngân sách thời gian thực: 1 quyết định / 2 s.

| Phương pháp | Huấn luyện toàn bộ (s) | Suy luận trung vị (ms) | Suy luận P95 (ms) | Kích thước (KB) |
|---|---|---|---|---|
| Ngưỡng tốt nhất (cơ sở) | 0.10 | 1.83 | 3.74 | 0 |
| Logistic Regression | 0.12 | 6.95 | 14.97 | 3 |
| Decision Tree | 0.49 | 7.37 | 12.77 | 11 |
| k-NN (k=5) | 0.02 | 5.29 | 6.88 | 1,943 |
| SVM (RBF) | 3.58 | 9.62 | 15.56 | 92 |
| MLP (64-32) | 2.77 | 8.72 | 17.00 | 106 |
| Random Forest (đang dùng) | 2.54 | 64.95 | 113.34 | 2,445 |
| XGBoost | 0.83 | 9.33 | 17.55 | 354 |
## 5. Nhận xét

1. **Mọi mô hình học máy đều vượt đường cơ sở mạnh** (F1 0.991–0.995 so với 0.989). Ngay cả Logistic Regression (tuyến tính) cũng đạt 0.993: phần lớn sức phân biệt đến từ **thiết kế đặc trưng** (tỉ lệ gói trả lời, gói/flow, entropy cổng — đo 2 chiều trên mặt phẳng dữ liệu) chứ không riêng từ thuật toán. Đây là kết luận trung thực cần nêu trong chương 4.
2. **Random Forest đứng đầu về F1 (0.9955)** và là mô hình duy nhất đồng thời có 0 báo động giả/giờ, FPR flash crowd = 0 và độ trễ phát hiện nhỏ nhất (4.18 s). Bootstrap theo lần chạy: RF tốt hơn có ý nghĩa so với Ngưỡng tốt nhất (cơ sở), Logistic Regression, Decision Tree, k-NN (k=5), MLP (64-32); **không khác biệt có ý nghĩa** với SVM (RBF), XGBoost (k-NN nằm sát biên: cận dưới ≈ 0).
3. **XGBoost tương đương RF** (F1 0.9943, khác biệt không có ý nghĩa) với chi phí thấp hơn nhiều (suy luận 9.3 ms so với 65.0 ms, 354 KB so với 2,445 KB) — là phương án thay thế tốt nếu controller hạn chế tài nguyên.
4. **Tráo vai**: mọi mô hình giữ F1 ≥ 0.977 — không mô hình nào sụp khi nguồn tấn công đổi máy. SVM có F1 tráo vai cao nhất (0.989) nhưng nhiều báo động giả nhất (3.5/giờ, FPR flash crowd 0.026) → không phù hợp cho module giảm thiểu tự động (báo động giả = cài rule nhầm).
5. **Chi phí**: RF chậm và lớn nhất (65 ms/cửa sổ, 2.4 MB) nhưng chỉ chiếm ~3.2% ngân sách 2 s mỗi chu kỳ — không phải điểm nghẽn. (Lúc đầu đo 60 ms do RF huấn luyện với `n_jobs=-1`: suy luận 1 dòng tốn chi phí tạo luồng → module nay đặt `n_jobs=1` khi nạp. Đo trực tiếp trên vm-controller (e2-standard-4, 23/11): 92,5 ms → 23,4 ms/cửa sổ.)
6. **Lý do giữ Random Forest**: F1 cao nhất và 0 báo động giả (quan trọng nhất khi mỗi báo động dẫn tới cài rule lên switch); không cần chuẩn hóa đặc trưng (ít rủi ro lệch phân phối khi đo online); có độ quan trọng đặc trưng để giải thích; chỉ cần scikit-learn trên controller.

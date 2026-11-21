# Báo cáo mô hình – Phát hiện tấn công bảo vệ SDN Controller

Sinh tự động bởi `src/train.py`. Cửa sổ W = 5 mẫu. Split theo lần chạy (train/test), không xáo trộn cửa sổ.

## 1. Dữ liệu

- Train: 1440 cửa sổ (1195 bình thường, 245 tấn công), từ: ['A01-R1', 'A02-R1', 'N00-R1', 'N01-R1', 'N02-R1', 'N03-R1']
- Test : 440 cửa sổ (204 bình thường, 236 tấn công), từ: ['A03-R1']

## 2. Đường cơ sở ngưỡng tĩnh

Đặc trưng `pin_last`, ngưỡng chọn trên train = **35.02** (F1 train 1.000). Trên tập test:

- Confusion [TN FP / FN TP]: [204 0 / 0 236]
- Precision=1.000  Recall=1.000  F1=1.000  FPR=0.000
- ROC-AUC=1.000  PR-AUC=1.000

## 3. Random Forest

Fit thẳng (không CV): chỉ 2 lần chạy có nhãn tấn công - quá ít nhóm để GroupKFold có ý nghĩa.

Trên tập test:

- Confusion [TN FP / FN TP]: [204 0 / 0 236]
- Precision=1.000  Recall=1.000  F1=1.000  FPR=0.000
- ROC-AUC=1.000  PR-AUC=1.000
- Tốc độ suy luận: 0.108 ms/cửa sổ

## 4. So sánh (mục 4.5.1)

| Mô hình | F1 | FPR | ROC-AUC |
|---|---|---|---|
| Ngưỡng tĩnh `pin_last` | 1.000 | 0.000 | 1.000 |
| Random Forest | 1.000 | 0.000 | 1.000 |

## 5. Tầm quan trọng đặc trưng (mục 4.3.3)

| Đặc trưng | Importance |
|---|---|
| pin_last | 0.2347 |
| pin_max | 0.1944 |
| pin_mean | 0.1709 |
| pin_min | 0.1321 |
| cpu_mean | 0.0770 |
| pin_std | 0.0764 |
| cpu_max | 0.0488 |
| cpu_last | 0.0374 |
| pin_slope | 0.0128 |
| api_mean | 0.0066 |
| api_last | 0.0026 |
| api_max | 0.0023 |

Đã lưu 3 hình vào reports\figures

## 6. Ghi chú trung thực về giới hạn hiện tại

- Chênh lệch F1 giữa Random Forest và ngưỡng tĩnh = **+0.000**. Nếu quá nhỏ (≈0), nghĩa là dữ liệu hiện tại quá dễ: một ngưỡng Packet-In đã tách gần hết hai lớp, nên chưa chứng minh được giá trị của học máy (mục 4.5.1).
- Nguyên nhân: hai lớp chưa chồng lấn, và tập test chỉ gồm 1 lần chạy (A03).
- Việc cần làm để báo cáo chương 4 thuyết phục: thu thêm các kịch bản chồng lấn (flash crowd, low-rate, pulsing, SYN flood, flow burst — xem `scenarios/`), lặp ≥5 lần/kịch bản, và bổ sung đặc trưng mặt phẳng dữ liệu.
- Lưu ý: dữ liệu controller-aware (Packet-In, CPU) chỉ có ý nghĩa khi controller thật sự chịu tải tấn công — cần bật đường Packet-In reactive trên ODL (Pha B).

# Hiệu quả giảm thiểu — A04-S2 so với A04-R4

Sinh bởi `src/analyze_mitigation.py`. Cùng kịch bản A04, lưu lượng nền như nhau; A04-R4 chạy không có module, A04-S2 chạy có module thời gian thực.

## Dòng thời gian của module (tính từ lúc tấn công bắt đầu)

| Mốc | Giây sau khi tấn công bắt đầu |
|---|---|
| Nghi ngờ (cửa sổ đầu dương tính) | 9.8 |
| Xác nhận tấn công (k/n) | 13.8 |
| Cài rule cấp 1 xong (RESTCONF) | 15.8 |
| Rule cấp 1 nằm trên switch | 15.8 |
| Cài rule cấp 2 (drop) | 70.0 |

Rule cấp 1: RESTCONF 16.2 ms, lên switch sau 2.02 s.

## So sánh theo pha (trung bình)

| Pha | Chỉ số | Không giảm thiểu | Có module | Thay đổi |
|---|---|---|---|---|
| baseline | Packet-In (pps) | 5.66 | 3.13 | -45% |
| baseline | CPU controller (%) | 7.84 | 9.14 | +17% |
| baseline | Độ trễ RESTCONF (ms) | 40.57 | 37.18 | -8% |
| baseline | Độ trễ HTTP người dùng (ms) | 5.78 | 5.77 | -0% |
| baseline | HTTP thành công | 1.00 | 1.00 | +0% |
| P1 | Packet-In (pps) | 14.09 | 2.14 | -85% |
| P1 | CPU controller (%) | 8.98 | 9.47 | +5% |
| P1 | Độ trễ RESTCONF (ms) | 48.78 | 43.40 | -11% |
| P1 | Độ trễ HTTP người dùng (ms) | 5.88 | 5.86 | -0% |
| P1 | HTTP thành công | 1.00 | 1.00 | +0% |
| P2 | Packet-In (pps) | 23.30 | 1.03 | -96% |
| P2 | CPU controller (%) | 10.37 | 8.95 | -14% |
| P2 | Độ trễ RESTCONF (ms) | 57.81 | 38.30 | -34% |
| P2 | Độ trễ HTTP người dùng (ms) | 6.03 | 6.06 | +0% |
| P2 | HTTP thành công | 1.00 | 1.00 | +0% |
| P3 | Packet-In (pps) | 35.38 | 1.00 | -97% |
| P3 | CPU controller (%) | 12.27 | 8.83 | -28% |
| P3 | Độ trễ RESTCONF (ms) | 69.33 | 34.33 | -50% |
| P3 | Độ trễ HTTP người dùng (ms) | 5.62 | 5.88 | +4% |
| P3 | HTTP thành công | 1.00 | 1.00 | +0% |
| recovery | Packet-In (pps) | 3.21 | 1.95 | -39% |
| recovery | CPU controller (%) | 7.54 | 9.03 | +20% |
| recovery | Độ trễ RESTCONF (ms) | 37.92 | 34.78 | -8% |
| recovery | Độ trễ HTTP người dùng (ms) | 5.79 | 5.73 | -1% |
| recovery | HTTP thành công | 1.00 | 1.00 | +0% |

![Hiệu quả giảm thiểu](figures/mit_A04-S2.png)

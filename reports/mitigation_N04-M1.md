# Hiệu quả giảm thiểu — N04-M1 so với N04-R1

Sinh bởi `src/analyze_mitigation.py`. Cùng kịch bản N04, lưu lượng nền như nhau; N04-R1 chạy không có module, N04-M1 chạy có module thời gian thực.

## Dòng thời gian của module (tính từ lúc tấn công bắt đầu)

| Mốc | Giây sau khi tấn công bắt đầu |
|---|---|



## So sánh theo pha (trung bình)

| Pha | Chỉ số | Không giảm thiểu | Có module | Thay đổi |
|---|---|---|---|---|
| warmup | Packet-In (pps) | 31.16 | 36.13 | +16% |
| warmup | CPU controller (%) | 18.08 | 13.48 | -25% |
| warmup | Độ trễ RESTCONF (ms) | 60.93 | 59.55 | -2% |
| warmup | Độ trễ HTTP người dùng (ms) | 5.26 | 5.47 | +4% |
| warmup | HTTP thành công | 1.00 | 1.00 | +0% |
| surge1 | Packet-In (pps) | 115.98 | 114.98 | -1% |
| surge1 | CPU controller (%) | 20.60 | 21.28 | +3% |
| surge1 | Độ trễ RESTCONF (ms) | 93.77 | 92.66 | -1% |
| surge1 | Độ trễ HTTP người dùng (ms) | 5.29 | 5.25 | -1% |
| surge1 | HTTP thành công | 1.00 | 1.00 | +0% |
| surge2 | Packet-In (pps) | 191.47 | 192.19 | +0% |
| surge2 | CPU controller (%) | 29.70 | 28.11 | -5% |
| surge2 | Độ trễ RESTCONF (ms) | 124.18 | 119.97 | -3% |
| surge2 | Độ trễ HTTP người dùng (ms) | 5.13 | 5.11 | -1% |
| surge2 | HTTP thành công | 1.00 | 1.00 | +0% |
| cooldown | Packet-In (pps) | 66.73 | 63.49 | -5% |
| cooldown | CPU controller (%) | 20.26 | 17.06 | -16% |
| cooldown | Độ trễ RESTCONF (ms) | 80.99 | 73.61 | -9% |
| cooldown | Độ trễ HTTP người dùng (ms) | 5.25 | 5.18 | -1% |
| cooldown | HTTP thành công | 1.00 | 1.00 | +0% |

![Hiệu quả giảm thiểu](figures/mit_N04-M1.png)

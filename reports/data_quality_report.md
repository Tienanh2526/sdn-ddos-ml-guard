# Báo cáo chất lượng dữ liệu – Dataset bảo vệ SDN Controller

Sinh tự động bởi `src/build_dataset.py` lúc 2026-11-19 15:37 UTC. Nguồn: dữ liệu thô chép nguyên trạng từ vm-controller, vm-client, vm-attacker (19/11/2026). Cửa sổ trượt W = 5 mẫu (10 s), bước trượt 1 mẫu (2 s).

## 1. Tổng quan các lần chạy

| Lần chạy | Kịch bản | Loại | Vai trò | Split | Bắt đầu (UTC) | Thời lượng (s) | Mẫu | Hợp lệ | Nhãn 0 | Nhãn 1 |
|---|---|---|---|---|---|---|---|---|---|---|
| N00-R1 | N00 | normal | pilot | train | 2026-11-17 06:53 | 118 | 60 | 58 | 58 | 0 |
| N01-R1 | N01 | normal | pilot | train | 2026-11-17 08:22 | 118 | 60 | 59 | 59 | 0 |
| E3-LOW | E3 | calibration | calibration | excluded | 2026-11-17 07:36 | 118 | 60 | 59 | 0 | 0 |
| E3-MED | E3 | calibration | calibration | excluded | 2026-11-17 07:49 | 118 | 60 | 59 | 0 | 0 |
| E3-HIGH | E3 | calibration | calibration | excluded | 2026-11-17 08:08 | 118 | 60 | 59 | 0 | 0 |
| A01-R1 | A01 | attack | pilot | train | 2026-11-17 08:31 | 118 | 60 | 59 | 48 | 9 |
| N02-R1 | N02 | normal | dataset | train | 2026-11-18 03:14 | 997 | 499 | 499 | 499 | 0 |
| N03-R1 | N03 | normal | dataset | train | 2026-11-19 02:22 | 801 | 397 | 397 | 397 | 0 |
| A02-R1 | A02 | attack | dataset | train | 2026-11-19 06:53 | 797 | 394 | 394 | 158 | 236 |
| A03-R1 | A03 | attack | dataset | test | 2026-11-19 09:06 | 897 | 444 | 444 | 208 | 236 |

Tổng: **2094 mẫu**, 2087 mẫu hợp lệ; 1427 mẫu nhãn 0, 481 mẫu nhãn 1. Mẫu đầu mỗi lần chạy không hợp lệ vì chưa có khoảng để tính tốc độ.

## 2. Cửa sổ thời gian (W = 5)

| Split | Nhãn | Số cửa sổ |
|---|---|---|
| excluded | không nhãn | 167 |
| test | 0 (bình thường) | 204 |
| test | 1 (tấn công) | 236 |
| train | 0 (bình thường) | 1195 |
| train | 1 (tấn công) | 245 |

- Cửa sổ chứa cả mẫu thường lẫn mẫu tấn công (vắt qua thời điểm bắt đầu/kết thúc tấn công): **22** / 1880. Ít vì dữ liệu gốc đã bỏ các mẫu nằm vắt qua ranh giới pha.
- Nhãn của cửa sổ = nhãn của mẫu cuối cùng (trạng thái tại thời điểm ra quyết định).
- Split theo **lần chạy**, không xáo trộn từng cửa sổ, để tránh rò rỉ giữa các cửa sổ chồng lấn.

## 3. Kiểm tra chu kỳ lấy mẫu

| Lần chạy | dt trung vị (s) | dt lớn nhất (s) | Số chỗ hụt mẫu | Số đoạn liên tục |
|---|---|---|---|---|
| N00-R1 | 2.00 | 2.02 | 0 | 1 |
| N01-R1 | 2.00 | 2.01 | 0 | 1 |
| E3-LOW | 2.00 | 2.02 | 0 | 1 |
| E3-MED | 2.00 | 2.01 | 0 | 1 |
| E3-HIGH | 2.00 | 2.02 | 0 | 1 |
| A01-R1 | 2.00 | 2.02 | 0 | 1 |
| N02-R1 | 2.00 | 2.04 | 0 | 1 |
| N03-R1 | 2.00 | 4.02 | 4 | 1 |
| A02-R1 | 2.00 | 4.01 | 5 | 1 |
| A03-R1 | 2.00 | 4.01 | 5 | 1 |

## 4. Giá trị thiếu (trên mẫu hợp lệ)

Không có giá trị thiếu ở các cột đo.


## 5. Đặc trưng hằng số / gần hằng số

- Hằng số trên toàn bộ cửa sổ có nhãn: `fmod_mean, fmod_max, api_err_cnt, ofconn_min`
- Chỉ 2–3 giá trị: `flows_delta`
- Ở mức mẫu: openflow_connections = [4], openflow_nodes = [4], api_http_status = [200], flow_mod_rate lớn nhất = 0.00.
- Các đặc trưng này vẫn được giữ trong schema vì sẽ thay đổi khi có Flow Burst (Flow-Mod), failover (mất kết nối OpenFlow) hay controller quá tải (lỗi HTTP), nhưng **hiện chưa mang thông tin**.

## 6. Kiểm tra nhãn: Packet-In đo được so với tốc độ sinh

| Lần chạy | Pha | Tốc độ sinh (pps) | Mẫu | Packet-In đo (pps) | CPU TB (%) | RESTCONF TB (ms) | Đo/Sinh |
|---|---|---|---|---|---|---|---|
| A02-R1 | baseline | 0.00 | 9 | 0.00 | 11.15 | 42.43 |  |
| A02-R1 | recovery | 0.00 | 149 | 0.00 | 8.32 | 32.95 |  |
| A02-R1 | P1 | 40.00 | 59 | 40.00 | 14.69 | 33.74 | 1.00 |
| A02-R1 | P2 | 60.00 | 59 | 59.99 | 14.39 | 35.37 | 1.00 |
| A02-R1 | P3 | 80.00 | 59 | 80.00 | 15.09 | 34.61 | 1.00 |
| A02-R1 | P4 | 100.00 | 59 | 100.00 | 15.39 | 34.57 | 1.00 |
| A03-R1 | baseline | 0.00 | 19 | 0.00 | 7.28 | 31.30 |  |
| A03-R1 | recovery | 0.00 | 189 | 0.00 | 7.33 | 31.65 |  |
| A03-R1 | P1 | 40.00 | 59 | 40.00 | 10.63 | 31.54 | 1.00 |
| A03-R1 | P2 | 60.00 | 59 | 60.00 | 16.94 | 59.40 | 1.00 |
| A03-R1 | P3 | 80.00 | 59 | 79.98 | 13.36 | 34.03 | 1.00 |
| A03-R1 | P4 | 100.00 | 59 | 100.00 | 14.10 | 32.55 | 1.00 |
| N03-R1 | normal | 2.00 | 1 | 2.25 | 117.97 | 71.32 | 1.12 |
| N03-R1 | normal | 5.00 | 99 | 5.00 | 11.00 | 35.11 | 1.00 |
| N03-R1 | normal | 10.00 | 99 | 10.00 | 8.93 | 32.47 | 1.00 |
| N03-R1 | normal | 20.00 | 99 | 20.00 | 9.99 | 32.78 | 1.00 |
| N03-R1 | normal | 30.00 | 99 | 30.00 | 9.39 | 31.57 | 1.00 |

Tỉ lệ Đo/Sinh ≈ 1 nghĩa là mốc thời gian gán nhãn khớp với lưu lượng thực tế.

## 7. Trôi giá trị giữa các phiên (rủi ro rò rỉ)

| Lần chạy | RSS TB (MB) | Số flow min | Số flow max | CPU TB (%) | CPU max (%) | RESTCONF TB (ms) |
|---|---|---|---|---|---|---|
| N00-R1 | 1498.63 | 53 | 53 | 7.75 | 15.48 | 36.08 |
| N01-R1 | 1505.96 | 53 | 53 | 7.85 | 29.47 | 34.34 |
| E3-LOW | 1501.55 | 53 | 54 | 8.10 | 22.98 | 36.25 |
| E3-MED | 1503.95 | 53 | 54 | 8.52 | 15.99 | 35.82 |
| E3-HIGH | 1504.76 | 53 | 54 | 7.86 | 12.99 | 33.65 |
| A01-R1 | 1506.99 | 53 | 54 | 8.57 | 14.48 | 34.91 |
| N02-R1 | 1562.33 | 53 | 53 | 8.18 | 54.94 | 31.80 |
| N03-R1 | 1530.41 | 54 | 54 | 10.10 | 117.97 | 33.08 |
| A02-R1 | 1438.99 | 53 | 54 | 12.32 | 63.43 | 34.14 |
| A03-R1 | 1443.82 | 53 | 54 | 10.74 | 36.96 | 35.74 |

- RAM (RSS) tuyệt đối và tổng số flow thay đổi theo **phiên chạy/ngày**, không theo tấn công. Nếu dùng giá trị tuyệt đối, mô hình có thể học thuộc "ngày nào" thay vì "có tấn công không". Vì vậy dataset chỉ dùng **độ biến thiên trong cửa sổ** (`rss_delta`, `flows_delta`).
- CPU có **đỉnh đơn lẻ ngay cả khi bình thường** (14 mẫu nhãn 0 vượt 30%; cao nhất: N03-R1 mẫu 0: 118%, N02-R1 mẫu 302: 55%, N02-R1 mẫu 271: 37%). Ngưỡng tĩnh đặt trên CPU từng mẫu sẽ báo động giả → nên dùng giá trị trung bình/cửa sổ (`cpu_mean`).

## 8. Khả năng phân tách của từng đặc trưng (ROC-AUC đơn biến, cửa sổ có nhãn)

| Đặc trưng | Nhóm | AUC đơn biến | Chiều |
|---|---|---|---|
| pin_last | control_plane | 1.000 | tấn công cao hơn |
| pin_mean | control_plane | 0.996 | tấn công cao hơn |
| pin_max | control_plane | 0.995 | tấn công cao hơn |
| pin_min | control_plane | 0.985 | tấn công cao hơn |
| cpu_mean | controller_resource | 0.958 | tấn công cao hơn |
| pin_std | control_plane | 0.950 | tấn công cao hơn |
| cpu_last | controller_resource | 0.919 | tấn công cao hơn |
| cpu_max | controller_resource | 0.892 | tấn công cao hơn |
| cpu_std | controller_resource | 0.702 | tấn công cao hơn |
| api_mean | controller_resource | 0.694 | tấn công cao hơn |
| api_max | controller_resource | 0.660 | tấn công cao hơn |
| api_last | controller_resource | 0.643 | tấn công cao hơn |
| api_std | controller_resource | 0.616 | tấn công cao hơn |
| pin_slope | control_plane | 0.537 | tấn công cao hơn |
| rss_delta | controller_resource | 0.531 | tấn công cao hơn |
| flows_delta | topology | 0.501 | tấn công thấp hơn |

- Packet-In lớn nhất khi **bình thường**: **30.48 pps**; nhỏ nhất khi **tấn công**: **39.52 pps** → hai lớp **tách rời hoàn toàn** chỉ bằng 1 ngưỡng.

- Đặc trưng tài nguyên controller tốt nhất: `cpu_mean` (AUC 0.958) – tấn công hiện tại quá nhẹ để làm controller quá tải rõ rệt.

## 9. Kết luận và việc cần làm với dữ liệu

**Dùng được ngay** để dựng đường ống huấn luyện/đánh giá (schema, nhãn theo mốc thời gian, split theo lần chạy, HTTP QoS cho A03). **Chưa đủ** cho kết luận của chương 4, vì:

1. **Quá dễ:** Packet-In một mình tách hoàn toàn hai lớp → Random Forest sẽ không hơn ngưỡng tĩnh (mục 4.5.1). Cần kịch bản **chồng lấn**: tấn công tốc độ thấp 10–40 pps, lưu lượng hợp lệ đột biến (flash crowd) 30–80 pps, tấn công dao động/ngắt quãng (pulsing).
2. **Quá ít lần lặp:** 2 lần chạy tấn công chính thức, mỗi kịch bản 1 lần. Cần ≥ 5 lần/kịch bản (run_id `A02-R1..R5`) để báo cáo trung bình ± độ lệch (mục 4.1.1, 4.4.1).
3. **Tập kiểm thử A03 không có Packet-In hợp lệ** → tỉ lệ báo động giả (FPR) đo được sẽ lạc quan. Cần thêm ít nhất 1 lần chạy N03 riêng cho test.
4. **Thiếu loại tấn công:** chưa có SYN Flood, Flow Burst (flow_mod_rate luôn 0) như đề cương (mục 2.2.2).
5. **Chưa có đặc trưng mặt phẳng dữ liệu** (port stats OVS, số IP nguồn, entropy) → chưa so sánh được Traffic-only và Controller-aware (mục 4.5.2) và chưa xác định được nguồn tấn công (mục 2.6.2).
6. **Tấn công còn nhẹ:** CPU controller chỉ tăng vài %, HTTP vẫn thành công 100% → khó chứng minh tác dụng bảo vệ (mục 4.4). Cần mức Packet-In cao hơn (vẫn trong VPC, có giới hạn).
7. **Lưu lượng bình thường N03 gửi từ IP attacker** → nên chuyển sang vm-client.

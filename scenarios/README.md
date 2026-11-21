# Kịch bản thu dữ liệu bổ sung (Nhóm 54)

Các kịch bản này bổ sung cho N00–N03 / A01–A03 đã có, nhằm **làm dataset khó hơn**
để chứng minh học máy hơn ngưỡng tĩnh (mục 4.5.1) và bổ sung loại tấn công còn
thiếu theo đề cương (SYN Flood, Flow Burst – mục 2.2.2).

Toàn bộ chạy **trong VPC cô lập của nhóm**, chỉ nhắm tới VM của nhóm
(`vm-web` overlay `192.168.200.12`), có giới hạn tốc độ và thời lượng, có kiểm tra
sức khỏe trước/trong khi chạy. Mục đích là sinh dữ liệu có nhãn để huấn luyện bộ
phát hiện phòng thủ.

## Vì sao cần thêm (tóm tắt phân tích dữ liệu hiện tại)

Dataset hiện tại hai lớp **không chồng lấn** theo Packet-In: bình thường ≤ 30 pps,
tấn công ≥ 40 pps. Một ngưỡng `pin_mean > 30` đã đạt F1 ≈ 0,987 → Random Forest
(F1 = 1,000) gần như không hơn. Để học máy có giá trị, cần tạo **vùng chồng lấn**:

| Mã | Tên | Loại | Nhãn | Vai trò trong dataset |
|---|---|---|---|---|
| N04 | Flash crowd | normal | 0 | Packet-In **cao** nhưng hợp lệ → ngưỡng tĩnh báo nhầm |
| A04 | Low-rate | attack | 1 | Packet-In **thấp** (10–30 pps) → ngưỡng tĩnh bỏ sót |
| A05 | Pulsing | attack | 1 | Bật/tắt ngắt quãng → kiểm tra xác nhận đa cửa sổ (2.6.1) |
| A06 | SYN Flood | attack | 1 | Loại tấn công theo đề cương; sinh Packet-In + Flow-Mod |
| A07 | Flow Burst | attack | 1 | Nhiều flow mới → làm `flow_mod_rate` khác 0 |

Ngoài ra bổ sung **đặc trưng mặt phẳng dữ liệu** (pps theo cổng leaf, số IP nguồn,
tỉ lệ SYN) để so sánh Traffic-only vs Controller-aware (4.5.2) và xác định nguồn
tấn công (2.6.2).

## Có cần đổi topo đã dựng không?

**Gần như không.** Bảng tóm tắt:

| Thành phần | Thay đổi topo? | Việc cần làm |
|---|---|---|
| N04, A04, A05 (UDP) | **Không** | Dùng đúng đường `attacker/client → web` sẵn có, chỉ khác tốc độ/timing |
| A06 SYN Flood | Không đổi topo | `vm-web` cần 1 cổng TCP đang mở (đã có HTTP :8080). Cần `hping3` trên vm-attacker |
| A07 Flow Burst | Không đổi topo | Cần chế độ reactive (table-miss → controller) để sinh Flow-Mod. Xác minh, không đổi topo |
| Đặc trưng data-plane | Không đổi topo | Thêm thu thập `ovs-ofctl dump-ports/flows` trên leaf (chỉ đọc). Tùy chọn bật sFlow (config, không phải topo) |
| VM thu thập ML | Không bắt buộc | Nên thêm 1 `vm-monitor` để không ảnh hưởng số đo CPU controller. Chạy tạm trên VM cũ cũng được |

**3 điều cần xác minh trước khi chạy** (chạy `scenarios/vm-leaf/preflight.sh`):
1. Đường `attacker → web` đi qua OVS leaf (lưu lượng phải qua switch thì controller mới thấy).
2. Có rule table-miss gửi gói lên controller không → quyết định A07/A06 có sinh được Packet-In/Flow-Mod.
3. `vm-web` có dịch vụ TCP lắng nghe (cho A06).

## Cách chạy 1 kịch bản (ví dụ A04, lặp 5 lần)

Trên vm-controller, mỗi lần chạy mở thu thập telemetry + capture OpenFlow
(dùng lại `run_n02_capture.sh` đổi SESSION). Trên máy sinh lưu lượng:

```bash
# vm-attacker, lần chạy R1..R5
python3 scenarios/vm-attacker/lowrate_a04.py --run A04-R1
```

Mỗi generator tự ghi file `*.groundtruth.json` (start/end/phase/target/nhãn) để
`src/build_dataset.py` gán nhãn tự động. Sau khi chép CSV về
`data/raw/...`, thêm dòng vào `RUNS` và chạy lại build.

Xem `scenarios/RUNBOOK.md` để biết quy trình đầy đủ từng lần chạy.

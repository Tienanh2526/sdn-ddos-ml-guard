# Cách chạy một lần thu dữ liệu và log được lưu ở đâu

## Tóm tắt dễ hiểu

Mỗi **lần thu dữ liệu** (ví dụ `A04-R1` = kịch bản A04, lần lặp thứ 1) cần 5 máy chạy cùng
lúc: controller ghi lại tình trạng ODL, leaf1 ghi lại lưu lượng đi qua switch, client vừa làm
"người dùng bình thường" vừa đo xem web còn truy cập được không, còn máy attacker (hoặc client)
phát lưu lượng của kịch bản. Làm tay thì phải mở 5 cửa sổ SSH và bấm đúng giây, rất dễ lệch.

Vì vậy chỉ cần chạy **một lệnh** trên máy Windows có gcloud:

```powershell
.\scenarios\orchestrate\run_scenario.ps1 -RunId A04-R1 -GenVm vm-attacker `
  -GenCmd "python3 ~/scenarios/vm-attacker/lowrate_a04.py" -GenDuration 780
```

Script sẽ **hẹn giờ** cho từng máy: chọn một mốc T0 (khoảng 2,5 phút sau), rồi dặn mỗi máy
"đến giờ này thì bắt đầu". Các máy GCP có đồng hồ đồng bộ nên tất cả khởi động cùng lúc.
Việc hẹn giờ nằm ngay trên VM (`systemd-run`), nên kể cả mất mạng giữa chừng thì lần thu vẫn
chạy tiếp.

| Thời điểm | Máy | Việc |
|---|---|---|
| T0 − 40 s | vm-controller | bắt gói OpenFlow (đếm Packet-In) + đo CPU/RAM/độ trễ API của ODL mỗi 2 s |
| T0 − 40 s | vm-leaf1 | đo lưu lượng đi qua switch: số flow, gói/flow, có chiều về không... |
| T0 − 40 s | vm-client | đo HTTP tới web mỗi 2 s (web còn sống không, nhanh hay chậm) |
| T0 − 30 s | vm-client | lưu lượng nền hợp lệ: duyệt web + phiên UDP (như người dùng thật) |
| T0 | máy sinh lưu lượng | chạy kịch bản (tấn công hoặc flash crowd), tự ghi mốc thời gian từng pha |

Hết giờ (~17 phút/lần), script tự **gom file** về controller, **gán nhãn** từng mẫu 2 s theo mốc
thời gian mà generator đã ghi (pha tấn công = 1, bình thường = 0, mẫu vắt qua ranh giới hai
pha thì loại), rồi **tải toàn bộ về repo**. `src/build_dataset.py` tự nhận lần thu mới.

## Log và dữ liệu của mỗi lần thu

Thư mục: `data/raw/vm-controller/sdn-ai/dataset/<attack|normal>/<SCN>/<RUN>/`

| File | Là gì | Xem khi nào |
|---|---|---|
| `orchestrate.log` | Nhật ký của script điều phối: T0, từng bước hẹn giờ, gom file, gán nhãn | Lần thu có chạy trọn không |
| `generator.log` | Kịch bản: giờ bắt đầu/kết thúc, từng pha, số gói đã gửi | Tấn công có chạy đúng tốc độ không |
| `background.log` | Lưu lượng nền mỗi 10 s: số request HTTP thành công/lỗi, số phiên UDP | Người dùng thật có bị ảnh hưởng không |
| `telemetry.log` | ODL mỗi 2 s: CPU, RAM, độ trễ API, số switch còn kết nối | Controller có bị quá tải không |
| `dataplane.log` | Switch mỗi 2 s: flow, gói/flow, tỉ lệ chiều về, SYN | Lưu lượng có đúng như thiết kế không |
| `http.log` | Bộ đo HTTP QoS (dữ liệu chính nằm ở `http.csv`) | Web có lúc nào không truy cập được |
| `finalize.log` | Kết quả gán nhãn: bao nhiêu mẫu, loại bao nhiêu và vì sao, bao nhiêu Packet-In | Nhãn có hợp lý không |
| `<RUN>.groundtruth.json` | Mốc thời gian và bảng pha (căn cứ gán nhãn) | Đối chiếu nhãn |
| `<RUN>.csv` | **Dữ liệu cuối cùng đã gán nhãn** (đầu vào của build_dataset) | Huấn luyện |
| `telemetry.csv`, `dataplane.csv`, `http.csv` | Dữ liệu thô từng nguồn | Phân tích lại |

Ngoài repo vẫn còn trên VM: bản gốc ở `~/aiguard-runs/<RUN>/` trên từng máy, và file bắt gói
OpenFlow `/var/tmp/aiguard-runs/<RUN>/openflow.pcap` trên controller (lớn nên không đưa lên
git, giữ để gán nhãn lại khi cần).

## Bật / tắt lab

```powershell
.\scenarios\orchestrate\lab_up.ps1     # bật 8 VM + chuẩn bị, chờ dòng LAB_READY
.\scenarios\orchestrate\lab_down.ps1   # tắt 8 VM khi xong (TERMINATED = đã tắt, đĩa vẫn giữ)
```

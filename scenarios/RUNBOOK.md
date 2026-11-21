# RUNBOOK — Chạy kịch bản và thu dữ liệu

Quy trình chuẩn cho mỗi lần chạy. Lặp **≥ 5 lần mỗi kịch bản** (R1..R5) để báo cáo
trung bình ± độ lệch (mục 4.1.1). Mọi thứ chạy trong VPC cô lập của nhóm.

## 0. Chuẩn bị một lần

```bash
# Chép thư mục scenarios/ lên các VM liên quan (từ máy có gcloud).
# Trên Windows (pscp) KHÔNG dùng được "~/" — phải ghi đường dẫn tuyệt đối /home/<user>/
gcloud compute scp --recurse scenarios vm-attacker:/home/<user>/ --zone asia-southeast1-a --tunnel-through-iap
gcloud compute scp --recurse scenarios vm-client:/home/<user>/   --zone asia-southeast1-a --tunnel-through-iap
gcloud compute scp --recurse scenarios vm-leaf1:/home/<user>/    --zone asia-southeast1-a --tunnel-through-iap
gcloud compute scp --recurse scenarios vm-leaf2:/home/<user>/    --zone asia-southeast1-a --tunnel-through-iap

# Cài công cụ còn thiếu (xem preflight báo gì):
#   vm-attacker: sudo apt-get install -y hping3
#   vm-leaf1/2 : sudo apt-get install -y tshark   (chọn "No" khi hỏi non-root capture, sẽ chạy sudo)
```

Chạy preflight trên leaf, ghi lại `--bridge` và `--iface` để dùng cho collector:
```bash
bash ~/scenarios/vm-leaf/preflight.sh
```

### 0.1 Bật chế độ REACTIVE trên 2 leaf (Pha B — BẮT BUỘC)

Topo gốc là proactive thuần → tấn công không tạo Packet-In. `reactive_mode.sh` gửi **gói đầu
của mỗi flow mới** từ cổng host lên controller (OVS learn, idle 10 s), vẫn chuyển tiếp bằng
action proactive cũ. Rule **mất khi leaf reboot** → kiểm tra `status` trước mỗi buổi thu.

```bash
# trên vm-leaf2 rồi vm-leaf1
sudo bash ~/scenarios/vm-leaf/reactive_mode.sh status     # có 5 rule/cổng host cookie 0xa1a1 là OK
sudo bash ~/scenarios/vm-leaf/reactive_mode.sh apply      # nếu chưa có
# kiểm tra client->web còn 200 (trên vm-client):
curl -s -o /dev/null -w '%{http_code}\n' http://192.168.200.12:8080/
# hoàn tác (về proactive thuần):
sudo bash ~/scenarios/vm-leaf/reactive_mode.sh rollback
```

Ngữ nghĩa đã kiểm chứng (21/11): 100 gói cùng 1 flow → 1 Packet-In; 100 flow mới → 99–100;
1 request HTTP → 2 (chiều đi ở leaf1 + chiều về ở leaf2); Flow-Mod = 0 (switch tự học).
Generator UDP (N04/A04/A05) mặc định `flow_mode="per_packet"` (mỗi gói đổi cổng nguồn)
→ Packet-In ≈ tốc độ gửi (đo được 450/450). A06 (hping3 tự tăng cổng nguồn) và A07
(đổi cổng đích) vốn đã là flow mới mỗi gói.

### 0.2 Bật dịch vụ UDP echo trên vm-web (BẮT BUỘC)

Đầu bên kia của phiên UDP hợp lệ trong lưu lượng nền. Thiếu nó, UDP hợp lệ không có chiều
về → mô hình học tắt "cứ UDP là tấn công". Mất khi vm-web reboot.
```bash
sudo systemd-run --unit=aiguard-udp-echo --collect python3 ~/scenarios/vm-web/udp_echo.py
systemctl is-active aiguard-udp-echo      # active
```

### 0.3 Vì sao dữ liệu mới "khó đúng cách" (đã kiểm chứng 21/11 trên GCP)

| Đoạn (TB mỗi mẫu 2 s, leaf1) | flows | gói/flow | reply_ratio | udp_frac |
|---|---|---|---|---|
| Chỉ lưu lượng nền | 2,5 | 11,7 | 0,94 | 0,38 |
| Tấn công low-rate (+nền) | 41 | **1,1** | **0,19** | 0,90 |
| Flash crowd HTTP (+nền) | 46 | **5,8** | **1,01** | 0,15 |

Số flow (≈ Packet-In) của tấn công và flash crowd **chồng lấn** → ngưỡng tĩnh thất bại;
**hành vi** (gói/flow, chiều về) khác → mô hình phân biệt được mà không cần biết máy nào gửi.

### 0.4 Kế hoạch lần chạy (TRÁO VAI để chống học thuộc danh tính máy)

| Kịch bản | R1–R3 chạy trên | R4–R5 chạy trên (tráo vai) | Thời lượng |
|---|---|---|---|
| N04 flash crowd | vm-client (mặc định) | vm-attacker `--src 192.168.200.13` | 600 s |
| A04 low-rate | vm-attacker (mặc định) | vm-client `--src 192.168.200.11` | 780 s |
| A05 pulsing | vm-attacker | vm-client `--src 192.168.200.11` | 520 s |
| A06 SYN flood | vm-attacker | vm-client `--src 192.168.200.11` | 780 s |
| A07 flow burst | vm-attacker | vm-client `--src 192.168.200.11` | 780 s |

Đặt ít nhất 1 lần chạy/kịch bản (gợi ý R5 — lần tráo vai) làm `split="test"`.
**Lưu lượng nền LUÔN chạy trên vm-client** ở mọi lần (kể cả khi vm-client là máy tấn công).

## ⚡ Cách nhanh: 1 lệnh cho cả lần chạy (khuyên dùng)

`scenarios/orchestrate/run_scenario.ps1` (chạy trên máy Windows có gcloud) tự làm toàn bộ
mục 1–3 bên dưới: hẹn giờ đồng bộ 5 VM, chờ, gom file, gán nhãn trên controller, tải về
`data/raw/vm-controller/sdn-ai/dataset/<attack|normal>/<SCN>/<RUN>/`. `build_dataset.py` tự
nhận các thư mục này (không cần sửa `RUNS`; R5 = test).

```powershell
cd D:\sdn-ai-ml
# lần thường
.\scenarios\orchestrate\run_scenario.ps1 -RunId A04-R1 -GenVm vm-attacker `
  -GenCmd "python3 ~/scenarios/vm-attacker/lowrate_a04.py" -GenDuration 780
# lần tráo vai
.\scenarios\orchestrate\run_scenario.ps1 -RunId A04-R4 -GenVm vm-client `
  -GenCmd "python3 ~/scenarios/vm-attacker/lowrate_a04.py --src 192.168.200.11" -GenDuration 780
```
Mật khẩu ODL: biến môi trường `ODL_PASSWORD` (mặc định `admin`). Mỗi lần ~17 phút.

## 1. Trình tự mỗi lần chạy thủ công (ví dụ A04-R1)

Mở song song các phiên SSH. **Bật telemetry + capture TRƯỚC, rồi lưu lượng nền, generator
SAU CÙNG**, để cửa sổ baseline có dữ liệu.

**(a) vm-controller** — telemetry + bắt gói OpenFlow (dùng lại mẫu run_n02_capture):
```bash
SESS=A04-R1
mkdir -p ~/sdn-ai/dataset/attack/A04
sudo timeout --signal=INT 900 tcpdump -i ens4 -nn -s0 -w ~/sdn-ai/dataset/attack/A04/openflow-$SESS.pcap 'tcp port 6653' &
SDN_SAMPLES=450 SDN_INTERVAL=2 \
A03_TELEMETRY_OUTPUT=~/sdn-ai/dataset/attack/A04/telemetry-$SESS.csv \
SDN_AUTH_STDIN=1 python3 ~/sdn-ai/telemetry/telemetry_collector_a03.py <<<"$ODL_PASSWORD"
```

**(b) vm-leaf1** — đặc trưng mặt phẳng dữ liệu (mặc định br-sdn/ens4, VTEP host tự dò;
chỉ đếm gói VXLAN host→leaf, đọc trường overlay — đã kiểm chứng 21/11):
```bash
sudo python3 ~/scenarios/vm-leaf/dataplane_collector.py \
  --out ~/dataplane-A04-R1.csv --samples 450
```

**(b2) vm-client** — lưu lượng NỀN hợp lệ (HTTP Poisson 0,5–3 kết nối/s + phiên UDP có
echo), chạy dài hơn kịch bản ≥ 60 s, bật TRƯỚC generator ~30 s:
```bash
python3 ~/scenarios/vm-client/background_mix.py --duration 900 > ~/bg-A04-R1.log 2>&1 &
```

**(c) máy sinh lưu lượng** — tùy kịch bản (lần tráo vai: chạy trên VM kia + thêm `--src`):
```bash
# A04 (vm-attacker)
python3 ~/scenarios/vm-attacker/lowrate_a04.py --run A04-R1
# A04-R4 tráo vai (chạy trên vm-client)
python3 ~/scenarios/vm-attacker/lowrate_a04.py --run A04-R4 --src 192.168.200.11
# N04 (vm-client) — phiên HTTP thật 10→25→35→15 kết nối/s (Packet-In ≈ 2×)
python3 ~/scenarios/vm-client/flash_crowd_n04.py --run N04-R1
# A05 (vm-attacker)
python3 ~/scenarios/vm-attacker/pulsing_a05.py --run A05-R1
# A07 (vm-attacker)
python3 ~/scenarios/vm-attacker/flowburst_a07.py --run A07-R1
# A06 SYN flood (vm-attacker, cần sudo + --yes)
sudo python3 ~/scenarios/vm-attacker/synflood_a06.py --run A06-R1 --yes
```

**(d) vm-client** — nếu muốn đo QoS (khuyến nghị cho mọi kịch bản tấn công):
```bash
A03_HTTP_OUTPUT=~/http-A04-R1.csv A03_HTTP_SAMPLES=450 \
python3 ~/sdn-ai/monitor/http_qos_monitor_a03.py
```

Generator tự in `..._START=` / `..._END=` và ghi
`~/sdn-ai/groundtruth/A04-R1.groundtruth.json`.

## 2. Gán nhãn (trên vm-controller)

Chép groundtruth.json + dataplane + http về controller (nếu ở VM khác), rồi:
```bash
python3 ~/scenarios/common/finalize_run.py --run A04-R1 \
  --groundtruth ~/sdn-ai/groundtruth/A04-R1.groundtruth.json \
  --telemetry   ~/sdn-ai/dataset/attack/A04/telemetry-A04-R1.csv \
  --pcap        ~/sdn-ai/dataset/attack/A04/openflow-A04-R1.pcap \
  --http        ~/http-A04-R1.csv \
  --dataplane   ~/dataplane-A04-R1.csv \
  --out         ~/sdn-ai/dataset/attack/A04/A04-R1.csv
```
Kiểm tra dòng `LABELS = {0: ..., 1: ...}` hợp lý (có cả baseline và pha tấn công).

## 3. Chép về repo

```bash
gcloud compute scp vm-controller:~/sdn-ai/dataset/attack/A04/A04-R1.csv \
  data/raw/vm-controller/sdn-ai/dataset/attack/A04/ --zone asia-southeast1-a --tunnel-through-iap
```
(Tạo thư mục đích trước nếu chưa có.)

## 4. Khai báo trong build_dataset.py

Thêm vào `SCENARIOS` (một lần cho mỗi mã kịch bản):
```python
dict(scenario_id="N04", category="normal", attack_type=None, traffic_type="benign_flash_crowd",
     description="Flash crowd HTTP thật 10->25->35->15 kết nối/s (Packet-In ~2x), Poisson, có response",
     src_host=HOSTS["client"], dst_host=HOSTS["web"], generator="vm-client/flash_crowd_n04.py",
     rate_profile="10,25,35,15 conn/s"),
dict(scenario_id="A04", category="attack", attack_type="udp_low_rate", traffic_type="udp_low_rate",
     description="Tấn công tốc độ thấp 10->20->30 pps, chồng lấn vùng bình thường (cổng 45461)",
     src_host=HOSTS["attacker"], dst_host=HOSTS["web"], generator="vm-attacker/lowrate_a04.py",
     rate_profile="10,20,30 pps x 180 s"),
dict(scenario_id="A05", category="attack", attack_type="udp_pulsing", traffic_type="udp_pulsing",
     description="Tấn công ngắt quãng 40–80 pps (ngẫu nhiên mỗi đợt) bật/tắt 20s x 10 chu kỳ (cổng 45462)",
     src_host=HOSTS["attacker"], dst_host=HOSTS["web"], generator="vm-attacker/pulsing_a05.py",
     rate_profile="40-80 pps, 20s on/off"),
dict(scenario_id="A06", category="attack", attack_type="syn_flood", traffic_type="tcp_syn_flood",
     description="SYN flood tới web:8080, 50->100->200 pps có giới hạn (hping3)",
     src_host=HOSTS["attacker"], dst_host=HOSTS["web"], generator="vm-attacker/synflood_a06.py",
     rate_profile="50,100,200 pps x 180 s"),
dict(scenario_id="A07", category="attack", attack_type="flow_burst", traffic_type="flow_burst",
     description="Flow burst 50->100->200 flow mới/s (cổng đích quay vòng 20000-39999)",
     src_host=HOSTS["attacker"], dst_host=HOSTS["web"], generator="vm-attacker/flowburst_a07.py",
     rate_profile="50,100,200 newflow/s x 180 s"),
```

Thêm vào `RUNS` (một dòng mỗi lần chạy; đặt ≥1 lần chạy làm split="test"):
```python
dict(run_id="A04-R1", scenario_id="A04",
     file=CTRL / "dataset/attack/A04/A04-R1.csv", role="dataset", split="train", notes=""),
# ... A04-R2..R5, N04-R1..R5, A05-*, A06-*, A07-* tương tự
```

Chạy lại: `.\.venv\Scripts\python.exe src\build_dataset.py`. DB + báo cáo sinh lại từ đầu.

## 5. Dùng đặc trưng mặt phẳng dữ liệu (data-plane)

Cột `dp_*` đã có trong CSV sau finalize, nhưng `build_dataset.py` **chưa** đưa chúng
vào cửa sổ đặc trưng. Đây là việc cần làm tiếp (xem `PROGRESS.md`): bổ sung các cột
`dp_*` vào `FEATURE_GROUPS["data_plane_traffic"]`, vào dict `a` và phần tổng hợp
trong `window_features()`. Khi đó mới so sánh được Traffic-only vs Controller-aware
(mục 4.5.2).

## An toàn (nhắc lại)
- Chỉ nhắm vm-web (192.168.200.12). Generator tự chặn đích khác.
- A06 có giới hạn tốc độ ≤ 200 pps, precheck sức khỏe, tự dừng nếu web/controller chết.
- Tắt các VM sau khi chạy xong để tiết kiệm credit.

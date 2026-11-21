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

## 1. Trình tự mỗi lần chạy (ví dụ A04-R1)

Mở song song các phiên SSH. **Bật telemetry + capture TRƯỚC, generator SAU**, để
cửa sổ baseline có dữ liệu.

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

**(c) máy sinh lưu lượng** — tùy kịch bản:
```bash
# A04 (vm-attacker)
python3 ~/scenarios/vm-attacker/lowrate_a04.py --run A04-R1
# N04 (vm-client)
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
     description="Flash crowd hợp lệ 20->50->70->30 pps, nhiều phiên (cổng 45460)",
     src_host=HOSTS["client"], dst_host=HOSTS["web"], generator="vm-client/flash_crowd_n04.py",
     rate_profile="20,50,70,30 pps"),
dict(scenario_id="A04", category="attack", attack_type="udp_low_rate", traffic_type="udp_low_rate",
     description="Tấn công tốc độ thấp 10->20->30 pps, chồng lấn vùng bình thường (cổng 45461)",
     src_host=HOSTS["attacker"], dst_host=HOSTS["web"], generator="vm-attacker/lowrate_a04.py",
     rate_profile="10,20,30 pps x 180 s"),
dict(scenario_id="A05", category="attack", attack_type="udp_pulsing", traffic_type="udp_pulsing",
     description="Tấn công ngắt quãng 80 pps bật/tắt 20s x 10 chu kỳ (cổng 45462)",
     src_host=HOSTS["attacker"], dst_host=HOSTS["web"], generator="vm-attacker/pulsing_a05.py",
     rate_profile="80 pps, 20s on/off"),
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

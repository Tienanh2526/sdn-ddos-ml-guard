# Hướng dẫn thực hành trên Google Cloud: tự tấn công và xem AI phát hiện, chặn

> Dành cho: các bạn trong nhóm 54 lần đầu thao tác lab trên Google Cloud.
> Đọc xong và làm theo, bạn sẽ tự: bật lab → chạy module AI → tấn công thử từ máy attacker →
> đọc hiểu AI phát hiện và chặn ra sao → tắt lab.
> Mọi kết quả mẫu bên dưới là kết quả **thật** ghi lại ngày 23/11/2026.

---

## 0. Lab gồm những gì

```
                    ┌─────────────────────────────┐
                    │  vm-controller 10.10.0.10   │   OpenDaylight (bộ não của mạng SDN)
                    │  + module AI (detector.py)  │
                    └──────────────┬──────────────┘
                 kênh OpenFlow (switch hỏi controller = "Packet-In")
          ┌────────────────┬───────┴────────┬────────────────┐
     vm-spine1        vm-spine2         vm-leaf1          vm-leaf2      ← 4 switch Open vSwitch
          └──── fabric VXLAN + ECMP ────────┤                 │
                                   ┌────────┴───────┐         │
                              vm-client      vm-attacker    vm-web
                           192.168.200.11  192.168.200.13  192.168.200.12
                           (người dùng)    (máy tấn công)  (web nạn nhân, cổng 8080)
```

| Máy | Việc của nó trong thí nghiệm |
|---|---|
| vm-controller | Chạy OpenDaylight và **module AI**: cứ 2 giây hỏi mô hình "có đang bị tấn công không", nếu có thì cài lệnh chặn lên switch |
| vm-leaf1 | Switch nối client và attacker. Chạy **bộ thu đặc trưng lưu lượng** gửi về cho AI |
| vm-client | Người dùng thật: liên tục mở web để xem có bị ảnh hưởng không |
| vm-attacker | Máy tấn công: gửi SYN flood vào vm-web |
| vm-web | Web server bị tấn công |

**Ý tưởng tấn công:** mỗi khi switch gặp một luồng gói tin *mới*, nó gửi một tin **Packet-In** lên controller để hỏi.
Kẻ tấn công tạo thật nhiều luồng mới → controller bị dồn hàng trăm Packet-In mỗi giây → CPU tăng, phản hồi chậm →
cả mạng chậm theo. Module AI phải nhận ra điều này và chặn đúng máy tấn công, không chặn nhầm người dùng.

---

## 1. Chuẩn bị (làm một lần)

1. Được thêm vào project Google Cloud `project-d0227366-e7f0-4e39-b78` (nhờ bạn quản lý project thêm email của bạn).
2. Chọn một trong hai cách vào máy:
   - **Cách dễ (khuyên dùng):** trình duyệt → <https://console.cloud.google.com> → chọn project → **Compute Engine →
     VM instances** → bấm nút **SSH** cạnh tên máy. Mỗi lần bấm mở một cửa sổ dòng lệnh riêng.
   - **Cách dùng máy tính:** cài [Google Cloud CLI](https://cloud.google.com/sdk/docs/install), đăng nhập
     `gcloud auth login`, rồi mở mỗi máy bằng:
     ```bash
     gcloud compute ssh vm-controller --zone asia-southeast1-a --tunnel-through-iap
     ```
     (đổi `vm-controller` thành tên máy khác).

> Mọi lệnh có `sudo` sẽ chạy với quyền quản trị trên máy ảo. Không cần nhập mật khẩu.

---

## 2. Bật lab

### Bước 2.1 — Bật 8 máy ảo

**Trên Console:** tích chọn cả 8 máy → bấm **START / RESUME**.
**Hoặc bằng lệnh** (trên máy tính đã cài gcloud):
```bash
gcloud compute instances start vm-controller vm-spine1 vm-spine2 vm-leaf1 vm-leaf2 vm-web vm-client vm-attacker --zone asia-southeast1-a
gcloud compute instances list --zones asia-southeast1-a --format="table(name,status)"
```

**Kết quả thật:**
```
NAME           STATUS
vm-attacker    RUNNING
vm-client      RUNNING
vm-controller  RUNNING
...
vm-web         RUNNING
```

**Giải thích:**
- `RUNNING` = máy đang chạy (đang bị tính tiền CPU/RAM).
- `TERMINATED` = máy đã tắt (chỉ tính tiền ổ đĩa, rất ít). Tắt **không mất dữ liệu**.
- Sau khi bật, đợi **1–2 phút** để OpenDaylight và các switch khởi động xong rồi mới làm bước tiếp.

### Bước 2.2 — Bật "chế độ reactive" trên 2 leaf (BẮT BUỘC sau mỗi lần bật máy)

**Vì sao cần:** mặc định các switch đã được cài sẵn đường đi nên **không bao giờ hỏi controller** → tấn công không chạm
tới controller, thí nghiệm vô nghĩa. Chế độ reactive làm cho **gói đầu tiên của mỗi luồng mới** được gửi lên controller
(Packet-In), giống mạng SDN thật. Chế độ này **mất mỗi khi leaf khởi động lại**, nên lần nào bật máy cũng phải làm lại.

Mở SSH vào **vm-leaf1**, gõ:
```bash
sudo bash ~/scenarios/vm-leaf/reactive_mode.sh apply
sudo ovs-ofctl -O OpenFlow13 dump-flows br-sdn | grep -c cookie=0xa1a1
```
Làm y hệt trên **vm-leaf2**.

**Kết quả thật (vm-leaf1):**
```
 cookie=0xa1a1, duration=0.053s, table=2, n_packets=0, ... priority=100,tcp,reg0=0,in_port=4
   actions=learn(table=1,idle_timeout=10,...),CONTROLLER:128,group:20
-- số flow đã học (cookie 0xa1a2): 0
10
```

**Giải thích từng phần:**
- Dòng `cookie=0xa1a1 ...` là một rule vừa cài. Đọc theo thứ tự:
  - `cookie=0xa1a1`: "dấu" của nhóm mình để sau này gỡ đúng rule của mình.
  - `in_port=4`: áp cho gói đi vào từ cổng 4 (cổng nối vm-client).
  - `learn(...)`: switch tự ghi nhớ luồng này 10 giây, các gói sau của cùng luồng không hỏi controller nữa.
  - `CONTROLLER:128`: **gửi gói này lên controller**, chính là Packet-In.
  - `group:20`: vẫn chuyển gói đi tiếp như bình thường, người dùng không bị ảnh hưởng.
- `số flow đã học: 0`: chưa có luồng nào đi qua, bình thường.
- Con số cuối cùng là số rule reactive: **leaf1 = 10, leaf2 = 5** là đúng.

**Nếu gặp:** `Không tìm thấy rule proactive 'priority=450,in_port=4' — dừng.`
→ Leaf vừa khởi động, chưa cài xong đường đi mặc định. **Đợi 30 giây rồi chạy lại** lệnh `apply`.

### Bước 2.3 — Bật dịch vụ echo trên vm-web

SSH vào **vm-web**:
```bash
sudo systemd-run --unit=aiguard-udp-echo --collect python3 ~/scenarios/vm-web/udp_echo.py
echo echo=$(systemctl is-active aiguard-udp-echo) web=$(systemctl is-active sdn-web)
```
**Kết quả đúng:** `echo=active web=active`

- `web=active`: web server (cổng 8080) đang chạy.
- `echo=active`: dịch vụ trả lời gói UDP, dùng cho lưu lượng nền hợp lệ của các lần thu dữ liệu. Dịch vụ này cũng
  **mất khi máy khởi động lại**, nên phải bật lại.

### Bước 2.4 — Kiểm tra mạng thông

Trên **vm-client**:
```bash
curl -s -o /dev/null -w "%{http_code} %{time_total}s\n" --interface 192.168.200.11 http://192.168.200.12:8080/
```
**Kết quả thật:** `200 0.006830s`

- `200` = mở web thành công. Nếu ra `000` là không kết nối được → xem mục 6.
- `0.0068s` = 6,8 mili giây, mạng nội bộ nhanh là đúng. (Lần đầu có thể chậm hơn, khoảng 50 ms, vì switch đang học luồng mới.)

> **Mẹo cho người có repo trên máy tính:** script `scenarios/orchestrate/lab_up.ps1` làm sẵn bước 2.2–2.4.

---

## 3. Thí nghiệm: tự tấn công và xem AI chặn

Mở **4 cửa sổ SSH** cùng lúc và chạy **đúng thứ tự** 3.1 → 3.4. Nên xếp các cửa sổ cạnh nhau để quan sát.

### 3.1 — Cửa sổ 1: vm-leaf1 — bộ thu đặc trưng lưu lượng

```bash
sudo python3 ~/scenarios/vm-leaf/dataplane_collector.py --out /tmp/dp-$(date +%s).csv --samples 100000 --push 10.10.0.10:47000
```

| Phần lệnh | Ý nghĩa |
|---|---|
| `dataplane_collector.py` | Đếm lưu lượng trên switch mỗi 2 giây: số gói, số luồng, tỉ lệ gói được trả lời, cổng đích… |
| `--out /tmp/dp-....csv` | Ghi lại ra file (tên có giờ để không trùng file cũ) |
| `--samples 100000` | Chạy rất lâu (tới khi bấm Ctrl+C) |
| `--push 10.10.0.10:47000` | **Gửi từng mẫu về controller** cho module AI |

Cửa sổ này cứ để chạy, không cần nhìn. **Nếu quên bước này**, AI vẫn chạy nhưng phải dùng *mô hình dự phòng* chỉ có số
liệu phía controller (kém chính xác hơn), xem mục 6.

### 3.2 — Cửa sổ 2: vm-controller — module AI (cửa sổ quan trọng nhất)

```bash
cd ~/aiguard-app/realtime
sudo ~/aiguard-venv/bin/python detector.py --model ~/aiguard-app/models/detector_rf.joblib --telemetry-module ~/aiguard-tools/telemetry_collector_a03.py --mode mitigate --allow-drop --password admin
```

| Phần lệnh | Ý nghĩa |
|---|---|
| `detector.py` | Module AI thời gian thực |
| `--model .../detector_rf.joblib` | Mô hình Random Forest đã huấn luyện trên 25 lần chạy thật |
| `--mode mitigate` | Phát hiện **và** giảm thiểu (cài rule lên switch). `--mode detect` = chỉ báo, không chặn |
| `--allow-drop` | Cho phép **cấp 2** (chặn hẳn). Bỏ đi thì module chỉ dùng cấp 1 |
| `--password admin` | Mật khẩu RESTCONF của OpenDaylight (mặc định của lab) |

**Kết quả thật khi mạng bình thường** (mỗi 2 giây một dòng):
```
05:38:29 NORMAL     p=0.13 pin=   0.0 cpu=  9.0 api=31.52 top=None(0.00) primary
05:38:31 NORMAL     p=0.26 pin=   0.0 cpu=  6.0 api=89.88 top=None(0.00) primary
```

**Cách đọc một dòng:**

| Cột | Ví dụ | Ý nghĩa |
|---|---|---|
| Giờ | `05:38:29` | Giờ **UTC** (giờ Việt Nam = UTC + 7, tức 12:38:29) |
| Trạng thái | `NORMAL` | Trạng thái của module, xem bảng dưới |
| `p=` | `0.13` | **Xác suất bị tấn công** theo AI (0 → 1). Trên **0,5** là AI cho là tấn công |
| `pin=` | `0.0` | Số **Packet-In mỗi giây** lên controller. Càng cao controller càng bị dồn |
| `cpu=` | `9.0` | % CPU của OpenDaylight |
| `api=` | `31.52` | Thời gian controller trả lời một yêu cầu (ms). Bị tấn công sẽ tăng vọt |
| `top=` | `None(0.00)` | Máy gửi nhiều Packet-In nhất và tỉ lệ của nó |
| cột cuối | `primary` | Đang dùng mô hình chính (30 đặc trưng). `fallback` = mô hình dự phòng; `-` = chưa đủ dữ liệu |

**4 trạng thái của module:**

| Trạng thái | Nghĩa |
|---|---|
| `NORMAL` | Bình thường |
| `SUSPECT` | Nghi ngờ: có cửa sổ bị AI chấm là tấn công, đang chờ xác nhận (3 trong 4 cửa sổ gần nhất) |
| `MITIGATING` | Đã xác nhận tấn công, **đang giảm thiểu** |
| `RECOVERING` | AI thấy hết tấn công, chờ 30 giây cho chắc rồi gỡ chặn |

⏳ **Đợi khoảng 2 phút trước khi tấn công.** Trong thời gian này module học "mức bình thường" của từng máy (mỗi máy hay
gửi bao nhiêu Packet-In). Nhờ vậy khi có tấn công, nó so được máy nào **tăng bất thường** để chặn đúng máy.

### 3.3 — Cửa sổ 3: vm-client — người dùng thật

```bash
while true; do curl -s -o /dev/null -w "%{http_code}  %{time_total}s\n" --interface 192.168.200.11 http://192.168.200.12:8080/; sleep 1; done
```
Mỗi giây mở web một lần và in kết quả, ví dụ `200  0.006s`.

- `200` = vào web được. **Mục tiêu: suốt thí nghiệm vẫn luôn là 200.**
- Số giây phía sau là thời gian tải. Nếu controller bị đè mà không có AI, con số này có thể tăng lên hàng chục ms.

### 3.4 — Cửa sổ 4: vm-attacker — tấn công

```bash
sudo hping3 -S -p 8080 -i u5000 -q 192.168.200.12
```

| Phần lệnh | Ý nghĩa |
|---|---|
| `hping3` | Công cụ tạo gói tin |
| `-S` | Gói **SYN** (gói mở kết nối TCP), mỗi gói là một luồng mới → mỗi gói gây một Packet-In |
| `-p 8080` | Nhắm cổng web 8080 |
| `-i u5000` | Cứ 5000 micro giây gửi 1 gói = **200 gói/giây** |
| `-q` | Chạy im lặng, chỉ in thống kê khi dừng |
| `192.168.200.12` | Địa chỉ vm-web |

Bấm **Ctrl+C** để dừng tấn công. hping3 sẽ in thống kê dạng:
```
--- 192.168.200.12 hping statistic ---
N packets transmitted, M packets received, X% packet loss
```
- `transmitted` = số gói đã gửi; `received` = số gói web trả lời.
- Khi AI đã **chặn cấp 2**, gói của attacker bị switch bỏ đi nên `packet loss` gần **100%**. Đây là dấu hiệu chặn thành công.

> Bỏ `-q` thì mỗi gói được trả lời in ra 1 dòng `len=44 ip=192.168.200.12 ... flags=SA ...` (SA = web trả lời SYN-ACK).
> Khi bị chặn cấp 2, các dòng này **ngừng xuất hiện**.

---

## 4. Đọc kết quả: chuyện gì xảy ra sau khi bấm tấn công

Theo dõi **cửa sổ 2**. Ngoài các dòng 2 giây, module in thêm **dòng sự kiện** (tên sự kiện + dữ liệu JSON).
Dưới đây là diễn biến điển hình (số liệu lấy từ lần chạy thật A06-M3):

| Thời điểm (sau khi tấn công) | Dòng sự kiện | Giải thích |
|---|---|---|
| ~2 s | `state {"frm": "NORMAL", "to": "SUSPECT", "prob": 0.65}` | AI bắt đầu nghi ngờ: xác suất vượt 0,5 |
| ~6 s | `state {"frm": "SUSPECT", "to": "MITIGATING", "prob": 0.94, "top_src": "192.168.200.13", ...}` | Đủ 3/4 cửa sổ xác nhận → **chính thức là tấn công** |
| ~6 s | `target {"ip": "192.168.200.13", "share": 0.99, "excess_pin": 195, "primary": true, ...}` | **Quy kết nguồn**: máy .13 gây 99% phần Packet-In vượt mức bình thường (195 Packet-In thừa trong 10 s) |
| ~9 s | `rule_install {"level": 1, "flow_id": "aig-l1-192-168-200-13", "http": 201, "rest_ms": 11.4, "on_switch_s": 3.53, ...}` | Cài **cấp 1** cho .13. `http 201` = OpenDaylight nhận lệnh; `rest_ms` = gửi lệnh mất 11 ms; `on_switch_s` = 3,5 s sau rule có mặt trên switch |
| ~30 s | `rule_install {"level": 2, "flow_id": "aig-l2-192-168-200-13", ...}` | Tấn công vẫn tiếp → nâng **cấp 2: chặn hẳn** |

**Cấp 1 và cấp 2 khác nhau thế nào?**
- **Cấp 1 (bypass):** gói của máy nghi ngờ được chuyển thẳng đi, **không hỏi controller nữa**. Controller hết bị đè
  ngay, nhưng gói vẫn tới được web. Đây là bước an toàn: lỡ nhắm nhầm thì người dùng cũng không mất mạng.
- **Cấp 2 (drop):** switch **bỏ hẳn** gói của máy đó. Chỉ dùng khi chắc chắn là tấn công (sau 20 giây và nguồn không bị nghi
  nhầm).

**Các dòng 2 giây lúc này sẽ như sau** (minh họa bằng số liệu thật của A06):
```
... MITIGATING p=0.97 pin=   3.4 cpu=  9.1 api=36.1 top=None(0.00) primary
```
`pin` từ hàng trăm **giảm về vài Packet-In/giây**, `cpu` và `api` về như lúc bình thường: controller đã được bảo vệ.
`p` vẫn cao vì AI vẫn thấy lưu lượng tấn công trên switch, chỉ là nó không còn đè được controller.

**Cửa sổ 3 (người dùng)** vẫn ra `200` đều đặn: AI không chặn nhầm người dùng thật.

### Xem lệnh chặn trên switch (tùy chọn)

Mở thêm 1 cửa sổ **vm-leaf1**:
```bash
sudo ovs-ofctl -O OpenFlow13 dump-flows br-sdn | grep 0xa1b
```
Sẽ thấy 2 dòng dạng:
```
cookie=0xa1b0..., duration=25s, n_packets=..., priority=700,ip,in_port=5,nw_src=192.168.200.13 actions=output:2
cookie=0xa1b0..., duration=4s,  n_packets=..., priority=800,ip,in_port=5,nw_src=192.168.200.13,nw_dst=192.168.200.12 actions=drop
```
- `cookie=0xa1b0...`: dấu của rule do **module AI** cài (khác `0xa1a1` của chế độ reactive).
- `in_port=5, nw_src=192.168.200.13`: áp cho gói từ máy attacker.
- `priority=700 ... actions=output:2` = **cấp 1**: chuyển thẳng ra cổng 2 (lên spine), bỏ qua bước hỏi controller.
- `priority=800 ... actions=drop` = **cấp 2**: bỏ gói. Ưu tiên 800 cao hơn 700 nên khi có cấp 2, gói bị bỏ trước.
- `n_packets`: số gói đã khớp rule, chạy lệnh lại sẽ thấy tăng, chứng tỏ attacker vẫn đang gửi.

### Sau khi bấm Ctrl+C ở cửa sổ 4 (dừng tấn công)

| Dòng | Giải thích |
|---|---|
| `state {"frm": "MITIGATING", "to": "RECOVERING"}` | AI thấy 5 chu kỳ liên tiếp bình thường |
| ~30 s sau: `rule_remove {"flow_id": "aig-l1-...", "http": 204, "why": "hết tấn công (rule khớp 0.0 gói/s)"}` | Trước khi gỡ, module **đọc bộ đếm gói của rule**: 0 gói/s nghĩa là attacker đã thật sự dừng → gỡ. `http 204` = gỡ thành công |
| `state {"frm": "RECOVERING", "to": "NORMAL"}` | Về bình thường |

Có thể gặp thêm:
- `hold {"why": "rule vẫn bị khớp — nguồn còn gửi", "hit_pps": 22.4}`: AI thấy bình thường nhưng rule vẫn bị khớp
  22 gói/s, tức máy đó vẫn đang gửi → **giữ rule**, chưa gỡ, để tránh vừa gỡ đã bị tấn công lại.
- `step_down {"ips": ["..."]}` (**hạ bậc**): bỏ cấp 2, giữ cấp 1, để máy đó dùng mạng lại được nếu lưu lượng còn lại
  là hợp lệ.

---

## 5. Kết thúc và tắt lab

1. Bấm **Ctrl+C** lần lượt ở cửa sổ **4 → 2 → 3 → 1**.
   - Khi dừng module (cửa sổ 2), nó **tự gỡ mọi rule đã cài** (dòng `rule_remove ... "why": "detector dừng"`) rồi in `stop {}`.
2. Không dùng nữa thì **tắt 8 máy** để đỡ tốn tiền: Console → chọn cả 8 máy → **STOP**, hoặc:
   ```bash
   gcloud compute instances stop vm-controller vm-spine1 vm-spine2 vm-leaf1 vm-leaf2 vm-web vm-client vm-attacker --zone asia-southeast1-a
   ```
   Lần sau bật lại nhớ làm lại **bước 2.2 và 2.3**.

---

## 6. Sự cố thường gặp

| Hiện tượng | Nguyên nhân | Cách xử lý |
|---|---|---|
| `Không tìm thấy rule proactive ...` khi apply | Leaf mới khởi động | Đợi 30 giây, chạy lại |
| Cửa sổ 2 có cột cuối là `fallback` hoặc `-`, `p=nan` | Chưa chạy bộ thu ở cửa sổ 1, hoặc chạy thiếu `--push` | Chạy lại bước 3.1 đúng lệnh |
| Cửa sổ 2 có `pin=0.0` mãi dù đang tấn công | Chưa bật chế độ reactive (bước 2.2) | Chạy `reactive_mode.sh apply` trên cả 2 leaf |
| Tấn công nhưng module không chuyển trạng thái | Tấn công quá yếu, hoặc chưa đợi 2 phút học mức nền | Đợi đủ 2 phút; thử `-i u5000` (200 gói/s) |
| Cửa sổ 3 ra `000` | Mạng chưa thông (máy vừa bật) hoặc web chưa chạy | Kiểm tra bước 2.3–2.4 |
| `hping3: command not found` | Máy chưa cài | `sudo apt-get install -y hping3` |
| SSH báo lỗi IAP / không kết nối | Máy chưa bật xong, hoặc tài khoản chưa có quyền | Đợi 1 phút; nhờ người quản lý project cấp quyền |

---

## 7. Lưu ý an toàn

- Chỉ tấn công **vm-web** trong mạng lab riêng của nhóm, với tốc độ giới hạn (200 gói/giây). **Không** nhắm địa chỉ nào khác.
- Không chạy thí nghiệm này khi đang có lô thu dữ liệu tự động (`run_batch.ps1`): hai bên sẽ làm nhiễu nhau.
- Mật khẩu `admin` của OpenDaylight chỉ dùng trong lab, không mở controller ra Internet.

---

## 8. Thuật ngữ nhanh

| Từ | Nghĩa |
|---|---|
| SDN | Mạng mà một **controller** trung tâm điều khiển mọi switch |
| Controller (OpenDaylight) | Bộ não của mạng SDN |
| Packet-In | Tin switch gửi lên controller khi gặp luồng gói tin mới |
| Flow / luồng | Một chuỗi gói cùng nguồn, đích, cổng |
| Rule / flow rule | Lệnh cài trên switch: gói thế nào thì xử lý ra sao |
| Reactive | Switch hỏi controller khi gặp luồng mới (cái tấn công lợi dụng) |
| SYN flood | Gửi ồ ạt gói mở kết nối TCP để tạo thật nhiều luồng mới |
| Random Forest | Thuật toán học máy dùng để phân biệt tấn công / bình thường |
| RESTCONF | Cách module AI gửi lệnh cài/gỡ rule cho OpenDaylight qua HTTP |
| Cấp 1 / Cấp 2 | Bypass (không hỏi controller) / Drop (bỏ gói) |

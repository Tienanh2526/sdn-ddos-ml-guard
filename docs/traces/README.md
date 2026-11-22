# Đường đi gói tin của từng kịch bản (minh họa)

Sinh ngày 22/11/2026 bằng `scenarios/vm-leaf/trace_paths.sh` (chạy `ovs-appctl ofproto/trace`
trên vm-leaf1, vm-spine1, vm-leaf2). OVS **mô phỏng** một gói đại diện đi qua bảng flow và in ra
gói khớp rule nào, làm action gì, ra cổng nào — không gửi gói thật. Mỗi file `<KỊCH BẢN>.txt`
gồm 3 đoạn theo đúng thứ tự đường đi: leaf1 → spine1 → leaf2.

## Đường đi chung (mọi kịch bản)

```
máy nguồn ──VXLAN──▶ leaf1 ──VXLAN VNI 1001──▶ spine1 ──VXLAN VNI 1001──▶ leaf2 ──VXLAN VNI 2002──▶ vm-web
(.13 attacker      10.10.0.21     group:20         10.10.0.11   output:2     10.10.0.22   output:4    10.10.0.31
 hoặc .11 client)  [REACTIVE]     (fast-failover,                                [REACTIVE cho
                    │              bucket 0 = spine1)                             chiều về]
                    ▼
            Packet-In lên ODL (chỉ gói ĐẦU của flow mới)
```

## Cơ chế reactive ở leaf (Pha B) — đọc trong đoạn leaf1 của mỗi file

| | Gói ĐẦU của flow mới | Gói TIẾP THEO cùng flow |
|---|---|---|
| table 0 (prio 600) | ghi reg0=0, tra table 1 rồi table 2 | như bên trái |
| table 1 | *No match* (chưa học) | khớp flow đã học (cookie 0xa1a2) → reg0=1 |
| table 2 | `learn(...)` + **`CONTROLLER:128`** + `group:20` | `group:20` (đi thẳng, **không** Packet-In) |
| Datapath | `userspace(...controller...)` = gửi Packet-In, rồi vào tunnel | chỉ vào tunnel |

Spine1: một rule tĩnh `in_port=1 → output:2` (trung chuyển). Leaf2: rule proactive
`ip,nw_dst=192.168.200.12 → output:4` giao tới web.

## Từng kịch bản

| File | Gói đại diện | Điểm đáng chú ý |
|---|---|---|
| `A04.txt` | UDP attacker → web:45461 | Mỗi gói tấn công đổi cổng nguồn → luôn là "gói đầu của flow mới" → mỗi gói 1 Packet-In. Cổng đóng nên không có chiều về |
| `A05.txt` | UDP attacker → web:45462 | Giống A04 nhưng bật/tắt theo đợt |
| `A06.txt` | TCP **SYN** attacker → web:8080 | Leaf1: Packet-In cho SYN. Leaf2: **chiều về SYN-ACK** của web cũng là flow mới → thêm Packet-In |
| `A07.txt` | UDP attacker → web, cổng **đích** đổi | Cổng đích rải rác → mỗi gói một flow mới |
| `N04.txt` | TCP client → web:8080 (HTTP) | 1 kết nối = Packet-In ở leaf1 (chiều đi) + ở leaf2 (chiều về) = **2 Packet-In/request**; các gói sau của phiên đi thẳng |
| `BG.txt` | UDP client → echo web:45470 | Phiên UDP hợp lệ: chỉ gói đầu mỗi chiều gây Packet-In, hàng trăm gói sau đi thẳng |

→ Đây là lý do tấn công (mọi gói là flow mới) đè được controller, còn lưu lượng hợp lệ cùng
số gói thì gần như không: **controller chịu tải theo số flow mới, không theo số gói.**

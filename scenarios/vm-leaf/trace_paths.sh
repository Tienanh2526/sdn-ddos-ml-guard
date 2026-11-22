#!/usr/bin/env bash
# trace_paths.sh — Minh họa ĐƯỜNG ĐI của một gói đại diện cho từng kịch bản qua fabric
# (dùng cho hình minh họa trong luận văn). Dựa trên `ovs-appctl ofproto/trace`: OVS mô phỏng
# gói đi qua bảng flow và in ra khớp rule nào, làm action gì, ra cổng nào. KHÔNG gửi gói thật.
#
# Chạy trên vm-leaf1 (switch nguồn), vm-spine1 (trung chuyển), vm-leaf2 (switch đích):
#   sudo bash trace_paths.sh <KỊCH_BẢN>        KỊCH_BẢN: A04 A05 A06 A07 N04 BG
# Trên leaf nguồn, gói được trace 2 lần để thấy cơ chế reactive (Pha B):
#   lần 1 (-generate): gói ĐẦU của flow mới -> table2: learn + CONTROLLER (Packet-In) + group:20
#                      (-generate cho phép action learn thực thi -> cài flow học, idle 10 s)
#   lần 2           : gói TIẾP THEO cùng flow -> table1 khớp flow vừa học -> chỉ group:20
# Với N04 (HTTP) còn trace chiều về (web -> client) ở leaf2: đây là Packet-In thứ 2 mỗi request.
# Cổng nguồn 15001-15006: ngoài dải generator (20000-29999) và ephemeral (32768+) để flow học
# tạm thời của -generate không trùng flow thật nếu trace trong lúc đang thu dữ liệu.
set -euo pipefail
SCN="${1:?Dùng: $0 <A04|A05|A06|A07|N04|BG>}"
BR=br-sdn
H=$(hostname -s)
CLIENT=192.168.200.11; WEB=192.168.200.12; ATT=192.168.200.13

case "$SCN" in
  A04) SRC=$ATT; SIF=vx-attacker; PROTO=udp; SP=15001; DP=45461; DESC="A04 low-rate: UDP 1 gói/flow tới cổng đóng" ;;
  A05) SRC=$ATT; SIF=vx-attacker; PROTO=udp; SP=15002; DP=45462; DESC="A05 pulsing: UDP 1 gói/flow (đợt bật)" ;;
  A06) SRC=$ATT; SIF=vx-attacker; PROTO=tcp; SP=15003; DP=8080;  DESC="A06 SYN flood: TCP SYN tới web:8080, cổng nguồn đổi mỗi gói" ;;
  A07) SRC=$ATT; SIF=vx-attacker; PROTO=udp; SP=15004; DP=21234; DESC="A07 flow burst: UDP, cổng ĐÍCH đổi mỗi gói" ;;
  N04) SRC=$CLIENT; SIF=vx-client; PROTO=tcp; SP=15005; DP=8080; DESC="N04 flash crowd: phiên HTTP client -> web:8080" ;;
  BG)  SRC=$CLIENT; SIF=vx-client; PROTO=udp; SP=15006; DP=45470; DESC="Lưu lượng nền: phiên UDP client -> echo web:45470" ;;
  *) echo "Kịch bản lạ: $SCN" >&2; exit 1 ;;
esac
ofp() { ovs-vsctl get Interface "$1" ofport; }
flow() {  # $1=in_port $2=src $3=dst $4=sport $5=dport
  # ofproto/trace cần udp_src/udp_dst cho UDP (tp_src báo "prerequisites not met")
  local f="in_port=$1,$PROTO,nw_src=$2,nw_dst=$3,${PROTO}_src=$4,${PROTO}_dst=$5"
  if [[ "$SCN" == A06 && "$2" == "$SRC" ]]; then f="$f,tcp_flags=0x002"; fi   # chỉ cờ SYN
  echo "$f"
}
trace() {  # $1=tiêu đề $2=flow $3=cờ thêm
  echo; echo "----- $1"; echo "flow: $2 $3"
  ovs-appctl ofproto/trace "$BR" "$2" $3 | grep -vE '^\s*$|^Flow:|^bridge\(|^-{5,}' | sed 's/^/  /'
}

echo "===== $DESC"
echo "===== switch: $H  ($(date -u +%Y-%m-%dT%H:%M:%SZ))"
case "$H" in
  vm-leaf1)
    P=$(ofp $SIF); F=$(flow "$P" "$SRC" "$WEB" "$SP" "$DP")
    trace "[leaf1] gói ĐẦU của flow mới (vào từ $SIF=port $P)" "$F" "-generate"
    trace "[leaf1] gói TIẾP THEO cùng flow (đã học)" "$F" ""
    ;;
  vm-spine1)
    P=$(ofp vx-leaf1); trace "[spine1] trung chuyển leaf1 -> leaf2 (vào từ vx-leaf1=port $P)" "$(flow "$P" "$SRC" "$WEB" "$SP" "$DP")" ""
    ;;
  vm-leaf2)
    P=$(ofp vx-spine1); trace "[leaf2] giao tới web (vào từ vx-spine1=port $P)" "$(flow "$P" "$SRC" "$WEB" "$SP" "$DP")" ""
    if [[ "$PROTO" == tcp || "$SCN" == BG ]]; then
      W=$(ofp vx-web)
      trace "[leaf2] CHIỀU VỀ web -> $SRC, gói đầu (vào từ vx-web=port $W)" "$(flow "$W" "$WEB" "$SRC" "$DP" "$SP")" "-generate"
    fi
    ;;
  *) echo "Chạy trên vm-leaf1 / vm-spine1 / vm-leaf2" >&2; exit 1 ;;
esac

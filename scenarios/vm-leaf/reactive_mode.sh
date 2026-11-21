#!/usr/bin/env bash
# reactive_mode.sh — Bật/tắt chế độ REACTIVE mô phỏng trên OVS leaf (Pha B).
#
# Vì sao: topo gốc là proactive thuần (flow cài sẵn bằng restore-sdn-flows.sh) nên tấn công
# mặt phẳng dữ liệu không tạo Packet-In nào → controller không chịu tải. Script này dựng lại
# hành vi của mạng SDN reactive: GÓI ĐẦU của mỗi flow mới (5-tuple) đi vào từ cổng host
# được gửi lên controller (OFPT_PACKET_IN), switch học flow đó (OVS learn, idle_timeout)
# để các gói sau đi thẳng. Việc chuyển tiếp vẫn dùng đúng action proactive cũ (group:20...)
# nên không đổi topo/ECMP/failover.
#
# Pipeline (chỉ gói IPv4 vào từ cổng host; ARP và flow cũ giữ nguyên):
#   table 0  prio 600  ip,in_port=H  -> reg0=0, resubmit(,1), resubmit(,2)
#   table 1  (flow đã học, prio 100, idle_timeout)  -> reg0=1
#   table 2  prio 200  reg0=1,in_port=H             -> <action cũ>
#            prio 100  reg0=0,tcp/udp,in_port=H      -> learn(5-tuple), CONTROLLER, <action cũ>
#            prio  50  reg0=0,ip,in_port=H           -> learn(src,dst,proto), CONTROLLER, <action cũ>
#
# Giới hạn trung thực: việc "cài flow" do switch tự học (learn), không phải ODL gửi Flow-Mod,
# nên flow_mod_rate vẫn = 0. Tải Packet-In lên controller là THẬT (ODL nhận + xử lý).
#
# Dùng (trên vm-leaf1 và vm-leaf2, cần sudo):
#   sudo bash reactive_mode.sh status
#   sudo bash reactive_mode.sh apply  [--idle 10]
#   sudo bash reactive_mode.sh rollback
# Rule mất khi reboot (restore-sdn-flows.service chỉ khôi phục proactive) → apply lại.
set -euo pipefail

BR="br-sdn"
CK_BASE="0xa1a1"     # rule tĩnh của chế độ reactive
CK_LEARN="0xa1a2"    # flow do switch tự học
IDLE=10
PIN_LEN=128          # số byte gói gửi kèm Packet-In
BACKUP_DIR="/var/tmp/aiguard-reactive"

host_ifaces() {
  case "$(hostname -s)" in
    vm-leaf1) echo "vx-client vx-attacker" ;;
    vm-leaf2) echo "vx-web" ;;
    *) echo "Chỉ chạy trên vm-leaf1 / vm-leaf2" >&2; exit 1 ;;
  esac
}

ofport() { ovs-vsctl get Interface "$1" ofport; }

# Action proactive hiện có cho gói vào từ cổng host (vd group:20).
fwd_action() {
  local p="$1" act
  act=$(ovs-ofctl -O OpenFlow13 dump-flows "$BR" "table=0,in_port=$p" \
        | grep -E "priority=450,in_port=$p actions=" | sed -E 's/.*actions=//' | head -1)
  if [[ -z "$act" ]]; then
    echo "Không tìm thấy rule proactive 'priority=450,in_port=$p' — dừng." >&2; exit 1
  fi
  echo "$act"
}

status() {
  echo "== $(hostname -s) bridge=$BR"
  echo "-- rule reactive (cookie $CK_BASE):"
  ovs-ofctl -O OpenFlow13 dump-flows "$BR" "cookie=$CK_BASE/-1" | grep -v OFPST || true
  echo "-- số flow đã học (cookie $CK_LEARN): $(ovs-ofctl -O OpenFlow13 dump-flows "$BR" "cookie=$CK_LEARN/-1" | grep -vc OFPST || true)"
}

rollback() {
  ovs-ofctl -O OpenFlow13 del-flows "$BR" "cookie=$CK_BASE/-1"
  ovs-ofctl -O OpenFlow13 del-flows "$BR" "cookie=$CK_LEARN/-1"
  echo "ROLLBACK_DONE $(hostname -s)"
  status
}

apply() {
  mkdir -p "$BACKUP_DIR"
  local ts; ts=$(date -u +%Y%m%dT%H%M%SZ)
  ovs-ofctl -O OpenFlow13 dump-flows "$BR"  > "$BACKUP_DIR/flows-$ts.txt"
  ovs-ofctl -O OpenFlow13 dump-groups "$BR" > "$BACKUP_DIR/groups-$ts.txt"
  echo "BACKUP=$BACKUP_DIR/flows-$ts.txt"

  # Làm sạch phiên trước (idempotent).
  ovs-ofctl -O OpenFlow13 del-flows "$BR" "cookie=$CK_BASE/-1"
  ovs-ofctl -O OpenFlow13 del-flows "$BR" "cookie=$CK_LEARN/-1"

  local L="table=1,idle_timeout=$IDLE,priority=100,cookie=$CK_LEARN,eth_type=0x800,NXM_OF_IN_PORT[],NXM_OF_IP_SRC[],NXM_OF_IP_DST[]"
  local SET="load:1->NXM_NX_REG0[0]"
  local CTL="controller(max_len=$PIN_LEN)"
  local f
  f=$(mktemp)
  for ifc in $(host_ifaces); do
    local p act
    p=$(ofport "$ifc"); act=$(fwd_action "$p")
    echo "HOST_PORT $ifc=$p forward=$act"
    cat >> "$f" <<EOF
cookie=$CK_BASE,table=0,priority=600,ip,in_port=$p,actions=load:0->NXM_NX_REG0[0],resubmit(,1),resubmit(,2)
cookie=$CK_BASE,table=2,priority=200,reg0=1,ip,in_port=$p,actions=$act
cookie=$CK_BASE,table=2,priority=100,reg0=0,tcp,in_port=$p,actions=learn($L,nw_proto=6,NXM_OF_TCP_SRC[],NXM_OF_TCP_DST[],$SET),$CTL,$act
cookie=$CK_BASE,table=2,priority=100,reg0=0,udp,in_port=$p,actions=learn($L,nw_proto=17,NXM_OF_UDP_SRC[],NXM_OF_UDP_DST[],$SET),$CTL,$act
cookie=$CK_BASE,table=2,priority=50,reg0=0,ip,in_port=$p,actions=learn($L,NXM_OF_IP_PROTO[],$SET),$CTL,$act
EOF
  done
  # Gửi theo thứ tự table 2 trước, table 0 sau để không có khoảnh khắc gói rơi.
  grep 'table=2' "$f" > "$f.2"; grep 'table=0' "$f" > "$f.0"
  ovs-ofctl -O OpenFlow13 add-flows "$BR" "$f.2"
  ovs-ofctl -O OpenFlow13 add-flows "$BR" "$f.0"
  rm -f "$f" "$f.0" "$f.2"
  echo "APPLY_DONE $(hostname -s) idle_timeout=${IDLE}s"
  status
}

cmd="${1:-status}"; shift || true
while [[ $# -gt 0 ]]; do
  case "$1" in --idle) IDLE="$2"; shift 2 ;; *) echo "Tham số lạ: $1" >&2; exit 1 ;; esac
done
[[ $EUID -eq 0 ]] || { echo "Cần sudo" >&2; exit 1; }
case "$cmd" in
  apply) apply ;; rollback) rollback ;; status) status ;;
  *) echo "Dùng: $0 {apply|rollback|status} [--idle N]" >&2; exit 1 ;;
esac

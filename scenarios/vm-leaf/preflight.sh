#!/usr/bin/env bash
# Kiểm tra 3 điều kiện trước khi chạy kịch bản mới. Chỉ ĐỌC trạng thái, không đổi gì.
# Chạy trên vm-leaf1 (và lặp lại trên vm-leaf2). Một số lệnh cần sudo.
set -u

echo "=== 1. OVS bridge & cổng (để điền --bridge/--iface cho dataplane_collector) ==="
sudo ovs-vsctl show 2>/dev/null || echo "KHONG_CO_OVS"
echo
echo "Danh sách bridge:"
for b in $(sudo ovs-vsctl list-br 2>/dev/null); do
  echo "  bridge=$b"
  echo "  -- cổng:"; sudo ovs-ofctl -O OpenFlow13 dump-ports-desc "$b" 2>/dev/null | sed 's/^/    /'
done

echo
echo "=== 2. Chế độ reactive (Pha B) — có rule gửi flow mới lên CONTROLLER không ==="
echo "Cần thấy rule cookie 0xa1a1 có 'CONTROLLER'. Nếu KHÔNG: chạy"
echo "  sudo bash ~/scenarios/vm-leaf/reactive_mode.sh apply"
echo "(rule mất khi leaf reboot). Không có thì tấn công KHÔNG tạo Packet-In."
for b in $(sudo ovs-vsctl list-br 2>/dev/null); do
  n=$(sudo ovs-ofctl -O OpenFlow13 dump-flows "$b" 'cookie=0xa1a1/-1' 2>/dev/null | grep -c CONTROLLER || true)
  echo "  bridge=$b rule_reactive_CONTROLLER=$n $([ "$n" -gt 0 ] && echo OK || echo 'THIEU -> apply')"
done

echo
echo "=== 3. Đường attacker->web có qua switch này không + web có cổng TCP mở ==="
echo "Kiểm tra reachability overlay (chạy từ leaf có thể không thấy overlay; xem trên host)."
echo "Trên vm-web chạy:  ss -ltnp | grep :8080     (phải có dịch vụ lắng nghe cho A06)"
echo "Trên vm-attacker:  ip route get 192.168.200.12   (phải đi ra iface overlay)"

echo
echo "=== 4. Công cụ cần cho kịch bản ==="
for t in tshark hping3 ovs-ofctl python3; do
  if command -v "$t" >/dev/null 2>&1; then
    echo "  OK   $t -> $(command -v $t)"
  else
    echo "  THIEU $t  (cài: sudo apt-get install -y $t)"
  fi
done

echo
echo "PREFLIGHT_DONE"

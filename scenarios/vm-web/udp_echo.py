#!/usr/bin/env python3
"""Dịch vụ UDP echo trên vm-web — đầu bên kia của các phiên UDP HỢP LỆ (VoIP/game giả lập).

Có dịch vụ này thì lưu lượng UDP hợp lệ có chiều về (reply_ratio ≈ 1, nhiều gói/flow), khác
hẳn UDP tấn công (1 gói/flow tới cổng đóng, không chiều về). Thiếu nó, mô hình sẽ học tắt
"cứ UDP là tấn công".

Chỉ lắng nghe trên IP overlay 192.168.200.12, cổng 45470. Giới hạn tốc độ trả lời để không
bao giờ thành công cụ khuếch đại. Chạy tạm thời (mất khi reboot):
  sudo systemd-run --unit=aiguard-udp-echo --collect python3 ~/scenarios/vm-web/udp_echo.py
Dừng: sudo systemctl stop aiguard-udp-echo
"""
import socket
import time

BIND = ("192.168.200.12", 45470)
MAX_REPLY_PPS = 2000


def main():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(BIND)
    print(f"UDP_ECHO_LISTEN={BIND}", flush=True)
    window, count, total = int(time.time()), 0, 0
    while True:
        data, addr = s.recvfrom(4096)
        now = int(time.time())
        if now != window:
            window, count = now, 0
        if count >= MAX_REPLY_PPS:
            continue
        s.sendto(data, addr)
        count += 1
        total += 1
        if total % 10000 == 0:
            print(f"ECHOED={total}", flush=True)


if __name__ == "__main__":
    main()

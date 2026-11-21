#!/usr/bin/env python3
import socket
import time
import sys
from datetime import datetime, timezone

DURATION = int(sys.argv[1]) if len(sys.argv) > 1 else 1000

# Các giai đoạn Packet-In hợp lệ, mức thấp đến cao
PHASES = [
    (0, 200, 2),
    (200, 400, 5),
    (400, 600, 10),
    (600, 800, 20),
    (800, 1000, 30),
]

SRC = "192.168.200.13"
DST = ("192.168.200.12", 45456)

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((SRC, 0))

start = time.monotonic()
sent = 0

print("N03_START=", datetime.now(timezone.utc).isoformat(), flush=True)

try:
    for begin, end, rate in PHASES:
        if begin >= DURATION:
            break

        phase_end = min(end, DURATION)

        while time.monotonic() - start < begin:
            time.sleep(0.05)

        next_send = time.monotonic()
        phase_count = 0

        while time.monotonic() - start < phase_end:
            now = time.monotonic()

            if now >= next_send:
                sock.sendto(b"N03_BENIGN_UDP", DST)
                sent += 1
                phase_count += 1
                next_send += 1.0 / rate
            else:
                time.sleep(min(0.01, next_send - now))

        print(
            f"PHASE={begin}-{phase_end}s RATE={rate}/s "
            f"SENT={phase_count}",
            flush=True
        )
finally:
    sock.close()

print("N03_END=", datetime.now(timezone.utc).isoformat(), flush=True)
print("TOTAL_SENT=", sent, flush=True)

#!/usr/bin/env python3
import socket
import time
import sys
from datetime import datetime, timezone

DURATION = int(sys.argv[1]) if len(sys.argv) > 1 else 480

PHASES = [
    (0, 120, 40),
    (120, 240, 60),
    (240, 360, 80),
    (360, 480, 100),
]

SRC = "192.168.200.13"
DST = ("192.168.200.12", 45457)

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((SRC, 0))

start = time.monotonic()
total_sent = 0

print(
    "A02_START=",
    datetime.now(timezone.utc).isoformat(),
    flush=True
)

try:
    for begin, end, rate in PHASES:
        if begin >= DURATION:
            break

        phase_end = min(end, DURATION)

        while time.monotonic() - start < begin:
            time.sleep(0.01)

        next_send = time.monotonic()
        phase_sent = 0

        while time.monotonic() - start < phase_end:
            now = time.monotonic()

            if now >= next_send:
                sock.sendto(b"A02_SYNTHETIC_BURST", DST)
                phase_sent += 1
                total_sent += 1
                next_send += 1.0 / rate
            else:
                time.sleep(min(0.005, next_send - now))

        print(
            f"PHASE={begin}-{phase_end}s "
            f"RATE={rate}/s SENT={phase_sent}",
            flush=True
        )

finally:
    sock.close()

print(
    "A02_END=",
    datetime.now(timezone.utc).isoformat(),
    flush=True
)
print("TOTAL_SENT=", total_sent, flush=True)

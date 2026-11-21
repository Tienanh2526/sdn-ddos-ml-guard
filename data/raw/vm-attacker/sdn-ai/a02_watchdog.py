#!/usr/bin/env python3
import subprocess
import urllib.request
import urllib.error
import time
import sys
from datetime import datetime, timezone

CONTROLLER = "http://10.10.0.10:8181/rests/data/opendaylight-inventory:nodes"
WEB = "http://192.168.200.12:8080/"
GENERATOR = "/home/kunthan231_gmail_com/sdn-ai/attack_a02.py"

def log(message):
    print(
        datetime.now(timezone.utc).isoformat(),
        message,
        flush=True
    )

def check_controller():
    try:
        urllib.request.urlopen(CONTROLLER, timeout=3)
        return True
    except urllib.error.HTTPError as e:
        return e.code == 401
    except Exception:
        return False

def check_web():
    try:
        with urllib.request.urlopen(WEB, timeout=3) as r:
            return r.status == 200
    except Exception:
        return False

def health():
    controller = check_controller()
    web = check_web()
    log(f"CONTROLLER={controller} WEB={web}")
    return controller and web

if "--check-only" in sys.argv:
    for _ in range(3):
        if not health():
            sys.exit(1)
        time.sleep(2)
    log("WATCHDOG_PRECHECK_PASS")
    sys.exit(0)

if not health():
    log("ABORT: Initial health check failed")
    sys.exit(1)

process = subprocess.Popen(
    [sys.executable, "-u", GENERATOR, "480"]
)

failures = 0

try:
    while process.poll() is None:
        time.sleep(5)

        if health():
            failures = 0
        else:
            failures += 1
            log(f"HEALTH_FAILURES={failures}")

        if failures >= 3:
            log("EMERGENCY_STOP")
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            sys.exit(2)

    log(f"GENERATOR_EXIT={process.returncode}")
    sys.exit(process.returncode)

finally:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

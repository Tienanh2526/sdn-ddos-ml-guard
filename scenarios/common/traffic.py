#!/usr/bin/env python3
"""Sinh lưu lượng HỢP LỆ có hành vi giống thật (dùng cho N04 và lưu lượng nền).

Vì sao cần: nếu lưu lượng bình thường và tấn công chỉ khác nhau ở MÁY GỬI (cùng là UDP
1 gói/flow) thì mô hình hoặc không học được gì, hoặc học thuộc danh tính máy (rò rỉ nhãn).
Lưu lượng hợp lệ thật khác tấn công ở HÀNH VI:
  - phiên HTTP: bắt tay TCP đủ, request + response, nhiều gói mỗi flow, có chiều về;
  - phiên UDP ứng dụng (kiểu VoIP/game): một 5-tuple kéo dài nhiều giây, có gói trả lời
    (dịch vụ echo trên vm-web, xem scenarios/vm-web/udp_echo.py);
  - thời điểm đến ngẫu nhiên (Poisson), không đều tăm tắp như máy phát.
Tấn công (A04–A07) thì 1 gói/flow, không chiều về, SYN dở dang, cổng đích rải rác.

Chỉ nhắm vm-web (192.168.200.12). Nguồn chỉ được là client hoặc attacker của lab.
"""
import http.client
import random
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor

TARGET = "192.168.200.12"
HTTP_PORT = 8080
UDP_ECHO_PORT = 45470
ALLOWED_SOURCES = {"192.168.200.11", "192.168.200.13"}   # client, attacker
HTTP_PATHS = ["/", "/", "/", "/index.html", "/static/a.txt", "/missing"]


def assert_source_allowed(ip):
    if ip not in ALLOWED_SOURCES:
        raise SystemExit(f"SOURCE_NOT_ALLOWED={ip} (chỉ {sorted(ALLOWED_SOURCES)})")


class Stats:
    def __init__(self):
        self.lock = threading.Lock()
        self.http_ok = self.http_fail = 0
        self.http_lat = []
        self.udp_sessions = self.udp_sent = self.udp_echo = 0

    def http(self, ok, lat=None):
        with self.lock:
            if ok:
                self.http_ok += 1
                self.http_lat.append(lat)
            else:
                self.http_fail += 1

    def summary(self):
        with self.lock:
            lat = sorted(self.http_lat)
            p50 = lat[len(lat) // 2] if lat else None
            return (f"HTTP_OK={self.http_ok} HTTP_FAIL={self.http_fail} "
                    f"HTTP_P50_MS={p50 and round(p50 * 1000, 1)} "
                    f"UDP_SESSIONS={self.udp_sessions} UDP_SENT={self.udp_sent} "
                    f"UDP_ECHO={self.udp_echo}")


def http_request(src_ip, stats, timeout=5.0):
    """Một kết nối HTTP/1.0 = một flow TCP mới (request + response)."""
    t0 = time.monotonic()
    try:
        c = http.client.HTTPConnection(TARGET, HTTP_PORT, timeout=timeout,
                                       source_address=(src_ip, 0))
        c.request("GET", random.choice(HTTP_PATHS), headers={"User-Agent": "lab-user"})
        r = c.getresponse()
        r.read()
        c.close()
        stats.http(r.status < 500, time.monotonic() - t0)
    except Exception:
        stats.http(False)


def udp_session(src_ip, stats, duration_s, pps, stop_evt):
    """Phiên UDP hai chiều kiểu VoIP/game: 1 cổng nguồn, nhiều gói, chờ echo."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind((src_ip, 0))
    s.settimeout(0.001)
    with stats.lock:
        stats.udp_sessions += 1
    end = time.monotonic() + duration_s
    nxt = time.monotonic()
    try:
        while time.monotonic() < end and not stop_evt.is_set():
            now = time.monotonic()
            if now >= nxt:
                s.sendto(random.randbytes(random.randint(60, 300)), (TARGET, UDP_ECHO_PORT))
                with stats.lock:
                    stats.udp_sent += 1
                nxt += random.expovariate(pps)
            try:
                s.recvfrom(2048)
                with stats.lock:
                    stats.udp_echo += 1
            except (socket.timeout, BlockingIOError):
                time.sleep(0.002)
    finally:
        s.close()


class PoissonHttp:
    """Bắn kết nối HTTP theo quá trình Poisson với tốc độ (kết nối/s) đổi được lúc chạy."""

    def __init__(self, src_ip, stats, workers=96):
        assert_source_allowed(src_ip)
        self.src, self.stats = src_ip, stats
        self.pool = ThreadPoolExecutor(max_workers=workers)
        self.rate = 0.0
        self.stop_evt = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True)

    def start(self):
        self.thread.start()
        return self

    def _loop(self):
        while not self.stop_evt.is_set():
            r = self.rate
            if r <= 0:
                time.sleep(0.1)
                continue
            self.pool.submit(http_request, self.src, self.stats)
            self.stop_evt.wait(random.expovariate(r))

    def stop(self):
        self.stop_evt.set()
        self.thread.join(timeout=2)
        self.pool.shutdown(wait=True, cancel_futures=True)


class UdpSessions:
    """Phiên UDP đến theo Poisson (trung bình 1 phiên mỗi `mean_gap_s`), chạy song song."""

    def __init__(self, src_ip, stats, mean_gap_s=20.0, dur_range=(5, 30), pps_range=(10, 40)):
        assert_source_allowed(src_ip)
        self.src, self.stats = src_ip, stats
        self.mean_gap, self.dur_range, self.pps_range = mean_gap_s, dur_range, pps_range
        self.stop_evt = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.workers = []

    def start(self):
        self.thread.start()
        return self

    def _loop(self):
        while not self.stop_evt.wait(random.expovariate(1.0 / self.mean_gap)):
            t = threading.Thread(target=udp_session, daemon=True, args=(
                self.src, self.stats, random.uniform(*self.dur_range),
                random.uniform(*self.pps_range), self.stop_evt))
            t.start()
            self.workers.append(t)

    def stop(self):
        self.stop_evt.set()
        self.thread.join(timeout=2)
        for t in self.workers:
            t.join(timeout=2)

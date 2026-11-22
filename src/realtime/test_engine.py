#!/usr/bin/env python3
"""test_engine.py — Kiểm thử máy trạng thái Engine trên các tình huống ĐÃ GẶP khi chạy thật.

  .\\.venv\\Scripts\\python.exe src\\realtime\\test_engine.py

Mỗi test dựng lại một lỗi đã thấy trên GCP (A04-M1..M4) để lỗi đó không quay lại khi sửa code.
"""
from __future__ import annotations

import collections
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))
from detector import Engine  # noqa: E402

ATT, LEGIT = "192.168.200.13", "192.168.200.11"


class FakeODL:
    def __init__(self):
        self.flows, self.calls, self.pkts = {}, [], collections.Counter()

    def put_flow(self, node, flow, table=0):
        self.flows[flow["id"]] = flow; self.calls.append(("PUT", flow["id"]))
        return 201, 10.0, {}

    def wait_on_switch(self, *a, **k):
        return 1.0

    def delete_flow(self, node, fid, table=0):
        self.flows.pop(fid, None); self.calls.append(("DELETE", fid))
        return 204, 5.0

    def flow_packets(self, node, fid, table=0):
        if fid not in self.flows:
            return None
        if fid.startswith("aig-l1-") and fid.replace("aig-l1-", "aig-l2-") in self.flows:
            return None                      # cấp 2 hứng hết gói -> cấp 1 bị idle-timeout xóa (A04-M3)
        return self.pkts[fid.split("-", 2)[-1]]


def make(**kw):
    a = SimpleNamespace(mode="mitigate", allow_drop=True, threshold=0.5, k=3, n=4, min_share=0.5,
                        escalate_after=20.0, clear_cycles=5, recover_s=30.0, rule_idle=60, rule_hard=900,
                        attr_window=5, min_evidence=15, base_alpha=0.05, hit_pps=2.0)
    a.__dict__.update(kw)
    clock = {"t": 0.0}
    odl, ev = FakeODL(), []
    eng = Engine(a, odl, lambda k, **kw: ev.append((clock["t"], k, kw)), clock=lambda: clock["t"])
    return eng, odl, ev, clock


def run(eng, odl, clock, seconds, prob, src, sending=None):
    """Chạy `seconds` giây (chu kỳ 2 s); sending = {ip: gói/s} cộng vào bộ đếm rule của ip."""
    for _ in range(int(seconds / 2)):
        clock["t"] += 2
        for ip, r in (sending or {}).items():
            odl.pkts[ip.replace(".", "-")] += r * 2
        s = collections.Counter({ip: c for ip, c in src.items() if ip not in eng.targets})
        eng.step(prob, s)


def test_lowrate_does_not_target_legit():
    """A04-M1: tấn công ~10 pps cùng cỡ Packet-In nền của người dùng -> phải nhắm .13, không .11."""
    eng, odl, ev, clock = make()
    run(eng, odl, clock, 120, 0.1, {LEGIT: 11})                       # nền: .11 ~5,5 pps, .13 im lặng
    run(eng, odl, clock, 30, 0.9, {LEGIT: 12, ATT: 20}, {ATT: 10})    # tấn công 10 pps
    targets = [kw["ip"] for _, k, kw in ev if k == "target"]
    assert targets and targets[0] == ATT, targets
    assert not any(c[1].endswith(LEGIT.replace(".", "-")) and c[1].startswith("aig-l2") for c in odl.calls)


def test_hold_while_attacker_sends_then_release():
    """A04-M2/M3: mô hình thấy bình thường (rule làm tốt việc) nhưng nguồn còn gửi -> hold; ngừng -> gỡ."""
    eng, odl, ev, clock = make()
    run(eng, odl, clock, 120, 0.1, {LEGIT: 11})
    run(eng, odl, clock, 40, 0.9, {LEGIT: 11, ATT: 40}, {ATT: 10})     # phát hiện + cấp 1 + cấp 2
    run(eng, odl, clock, 120, 0.1, {LEGIT: 11}, {ATT: 10})             # vẫn gửi, mô hình "bình thường"
    assert any(k == "hold" for _, k, _ in ev), "phải hold khi rule còn bị khớp"
    assert any(f.endswith(ATT.replace(".", "-")) for f in odl.flows), "rule tấn công phải còn"
    run(eng, odl, clock, 120, 0.1, {LEGIT: 11})                        # ngừng gửi
    assert not odl.flows, f"phải gỡ hết khi nguồn ngừng: {list(odl.flows)}"


def test_secondary_legit_target_released():
    """A04-M4: .11 bị nhắm bổ sung (cấp 1) — lưu lượng hợp lệ khớp rule mãi -> không được giữ mãi."""
    eng, odl, ev, clock = make()
    run(eng, odl, clock, 120, 0.1, {LEGIT: 11})
    run(eng, odl, clock, 30, 0.9, {LEGIT: 11, ATT: 40}, {ATT: 10})
    run(eng, odl, clock, 20, 0.9, {LEGIT: 60}, {ATT: 10, LEGIT: 6})    # .11 tăng đột biến -> nhắm bổ sung
    assert LEGIT in eng.targets and eng.targets[LEGIT]["l1_only"]
    assert not any(c[1] == "aig-l2-" + LEGIT.replace(".", "-") for c in odl.calls), "nguồn phụ không được drop"
    run(eng, odl, clock, 200, 0.1, {LEGIT: 11}, {LEGIT: 6})            # tấn công dừng, .11 vẫn gửi hợp lệ
    assert not odl.flows, f"phải gỡ hết sau khi tấn công dừng: {list(odl.flows)}"


def test_no_action_on_normal():
    eng, odl, ev, clock = make()
    run(eng, odl, clock, 600, 0.1, {LEGIT: 60})
    assert not odl.calls


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            try:
                fn(); print(f"OK   {name}")
            except AssertionError as e:
                fails += 1; print(f"FAIL {name}: {e}")
    sys.exit(1 if fails else 0)

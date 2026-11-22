"""odl.py — Client RESTCONF (RFC 8040) tối giản cho OpenDaylight: cài / xóa / kiểm tra flow.

Chỉ dùng thư viện chuẩn. Flow được ghi vào CONFIG datastore của node switch; FRM của ODL đẩy
xuống switch qua OpenFlow (FLOW_MOD). Kiểm tra hiệu lực bằng cách đọc OPERATIONAL datastore
(ODL thu từ thống kê switch) — tức rule đã thật sự nằm trên switch.

URL: http://<odl>:8181/rests/data/opendaylight-inventory:nodes/node=<id>/flow-node-inventory:table=<t>/flow=<fid>
"""
from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request

COOKIE_BASE = 0xA1B00000          # mọi flow do module giảm thiểu cài: cookie 0xA1B0xxxx


class ODL:
    def __init__(self, base="http://127.0.0.1:8181", user="admin", password="admin", timeout=5.0):
        self.base = base.rstrip("/")
        self.auth = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
        self.timeout = timeout

    # ------------------------------------------------------------------ HTTP
    def _req(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers={
            "Authorization": self.auth, "Accept": "application/yang-data+json",
            "Content-Type": "application/yang-data+json"})
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                txt = r.read().decode() or "{}"
                return r.status, (json.loads(txt) if txt.strip() else {}), (time.monotonic() - t0) * 1000
        except urllib.error.HTTPError as e:
            return e.code, {"error": e.read().decode(errors="replace")[:500]}, (time.monotonic() - t0) * 1000
        except Exception as e:  # mạng / timeout
            return None, {"error": str(e)}, (time.monotonic() - t0) * 1000

    @staticmethod
    def _flow_path(node, table, flow_id, operational=False):
        n = urllib.parse.quote(node, safe="")
        p = f"/rests/data/opendaylight-inventory:nodes/node={n}/flow-node-inventory:table={table}/flow={flow_id}"
        return p + ("?content=nonconfig" if operational else "")

    # ------------------------------------------------------------------ flow
    def put_flow(self, node, flow: dict, table=0):
        """Ghi flow vào config datastore (tạo mới hoặc thay thế). Trả về (http_status, ms)."""
        st, body, ms = self._req("PUT", self._flow_path(node, table, flow["id"]),
                                 {"flow-node-inventory:flow": [flow]})
        return st, ms, body

    def delete_flow(self, node, flow_id, table=0):
        st, body, ms = self._req("DELETE", self._flow_path(node, table, flow_id))
        return st, ms

    def flow_on_switch(self, node, flow_id, table=0):
        """True nếu flow đã xuất hiện trong operational datastore (đã nằm trên switch)."""
        st, body, _ = self._req("GET", self._flow_path(node, table, flow_id, operational=True))
        return st == 200

    def wait_on_switch(self, node, flow_id, table=0, timeout_s=15.0, poll_s=0.5):
        """Chờ flow lên switch; trả về số giây đã chờ hoặc None nếu quá hạn."""
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout_s:
            if self.flow_on_switch(node, flow_id, table):
                return time.monotonic() - t0
            time.sleep(poll_s)
        return None


# ---------------------------------------------------------------------- dựng flow
def _match(node, in_port, src_ip, dst_ip=None, proto=None):
    m = {"in-port": f"{node}:{in_port}",
         "ethernet-match": {"ethernet-type": {"type": 2048}},
         "ipv4-source": f"{src_ip}/32"}
    if dst_ip:
        m["ipv4-destination"] = f"{dst_ip}/32"
    if proto:
        m["ip-match"] = {"ip-protocol": proto}
    return m


def bypass_flow(flow_id, node, in_port, src_ip, out_port, priority=700, idle_timeout=60, hard_timeout=900, seq=1):
    """CẤP 1 — Bảo vệ controller, KHÔNG cắt dịch vụ: lưu lượng từ src_ip (cổng in_port) được chuyển
    tiếp thẳng ra out_port (cổng uplink lên spine) ở priority cao hơn rule reactive (600) → không còn
    gói nào bị gửi lên controller (dừng bão Packet-In) nhưng gói vẫn tới đích.
    idle_timeout: rule sống chừng nào nguồn còn gửi, tự gỡ khi ngừng; hard_timeout: trần an toàn."""
    return {"id": flow_id, "table_id": 0, "priority": priority, "hard-timeout": hard_timeout,
            "idle-timeout": idle_timeout, "cookie": COOKIE_BASE + seq, "flow-name": flow_id,
            "match": _match(node, in_port, src_ip),
            "instructions": {"instruction": [{"order": 0, "apply-actions": {"action": [
                {"order": 0, "output-action": {"output-node-connector": str(out_port), "max-length": 0}}]}}]}}


def drop_flow(flow_id, node, in_port, src_ip, dst_ip, priority=800, idle_timeout=60, hard_timeout=900, seq=2):
    """CẤP 2 — Chặn: bỏ mọi gói IPv4 src_ip -> dst_ip vào từ in_port (không có instruction = drop).
    Hết hạn kép: idle_timeout (tự gỡ khi nguồn ngừng gửi) + hard_timeout (trần an toàn tuyệt đối)."""
    return {"id": flow_id, "table_id": 0, "priority": priority, "hard-timeout": hard_timeout,
            "idle-timeout": idle_timeout, "cookie": COOKIE_BASE + seq, "flow-name": flow_id,
            "match": _match(node, in_port, src_ip, dst_ip)}

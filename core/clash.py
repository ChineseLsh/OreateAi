"""Clash API — 切换代理节点实现 IP 轮换"""

import logging
import httpx

log = logging.getLogger(__name__)

CLASH_API = "http://127.0.0.1:9097"
CLASH_SECRET = "lsh"
CLASH_GROUP = "GLOBAL"

_headers = {"Authorization": f"Bearer {CLASH_SECRET}"}


def _get(path: str) -> dict:
    return httpx.get(f"{CLASH_API}{path}", headers=_headers, timeout=5).json()


def _put(path: str, data: dict):
    return httpx.put(f"{CLASH_API}{path}", headers=_headers, json=data, timeout=5)


def list_nodes(group: str = CLASH_GROUP) -> list[str]:
    d = _get(f"/proxies/{group}")
    all_nodes = d.get("all", [])
    return [n for n in all_nodes if not any(k in n for k in ["剩余", "到期", "官网", "DIRECT", "REJECT", "自动选择", "故障转移"])]


def current_node(group: str = CLASH_GROUP) -> str:
    d = _get(f"/proxies/{group}")
    return d.get("now", "")


def switch_node(node: str, group: str = CLASH_GROUP) -> bool:
    resp = _put(f"/proxies/{group}", {"name": node})
    ok = resp.status_code == 204
    if ok:
        log.info(f"switched to: {node}")
    else:
        log.warning(f"switch failed: {resp.status_code} {resp.text}")
    return ok


def get_current_ip() -> str:
    import ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    transport = httpx.HTTPTransport(proxy="http://127.0.0.1:7897", verify=ctx)
    c = httpx.Client(transport=transport, verify=ctx, timeout=8)
    for url in ["https://httpbin.org/ip", "https://ifconfig.me/ip", "https://icanhazip.com"]:
        try:
            r = c.get(url)
            text = r.text.strip()
            if "origin" in text:
                return r.json().get("origin", "").split(",")[0].strip()
            return text.split("\n")[0].strip()
        except Exception:
            continue
    c.close()
    return "?"


class NodeRotator:
    def __init__(self, group: str = CLASH_GROUP):
        self.group = group
        self.nodes = list_nodes(group)
        self.index = 0
        self.original = current_node(group)
        log.info(f"rotator: {len(self.nodes)} usable nodes, current={self.original}")

    def next(self) -> str:
        if not self.nodes:
            return ""
        node = self.nodes[self.index % len(self.nodes)]
        self.index += 1
        switch_node(node, self.group)
        return node

    def restore(self):
        if self.original:
            switch_node(self.original, self.group)

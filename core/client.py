"""HTTP 客户端 — 封装 session、cookie、请求重试"""

import ssl
import httpx
from config import BASE_URL, DEFAULT_HEADERS, DEFAULT_PROXY
from core.fingerprint import generate_ouid

_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE


class OreateClient:
    def __init__(self, proxy: str | None = DEFAULT_PROXY):
        self.ouid = generate_ouid()
        transport = httpx.HTTPTransport(proxy=proxy, verify=_ssl_ctx) if proxy else None
        self.session = httpx.Client(
            headers=DEFAULT_HEADERS,
            cookies={"OUID": self.ouid},
            timeout=30,
            follow_redirects=True,
            transport=transport,
            verify=_ssl_ctx,
        )

    def get(self, url: str, **kwargs) -> dict:
        resp = self.session.get(url, **kwargs)
        return resp.json()

    def post(self, url: str, **kwargs) -> dict:
        resp = self.session.post(url, **kwargs)
        return resp.json()

    def set_referer(self, ref: str):
        self.session.headers["Referer"] = ref

    def close(self):
        self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

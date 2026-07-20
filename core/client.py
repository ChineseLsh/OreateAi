"""HTTP client with an optional official-page browser transport."""

import ssl
import httpx
from config import BASE_URL, DEFAULT_HEADERS, DEFAULT_PROXY
from core.fingerprint import generate_ouid

_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE


class OreateClient:
    def __init__(
        self,
        proxy: str | None = DEFAULT_PROXY,
        *,
        browser: bool = False,
        cookies: dict[str, str] | None = None,
    ):
        self.proxy = proxy
        self.ouid = (cookies or {}).get("OUID") or generate_ouid()
        self.bid = (cookies or {}).get("__bid_n", "")
        self.browser = None
        transport = httpx.HTTPTransport(proxy=proxy, verify=_ssl_ctx, retries=1) if proxy else None
        self.session = httpx.Client(
            headers=DEFAULT_HEADERS,
            cookies=cookies or {"OUID": self.ouid},
            timeout=30,
            follow_redirects=True,
            transport=transport,
            verify=_ssl_ctx,
        )
        if browser:
            from core.browser_runtime import BrowserRuntime

            self.browser = BrowserRuntime(proxy=proxy, cookies=cookies or {"OUID": self.ouid})
            self._sync_browser()

    @property
    def user_agent(self) -> str:
        return self.session.headers.get("User-Agent", "")

    def _sync_browser(self) -> None:
        if self.browser is None:
            return
        browser_cookies = self.browser.cookies()
        values = {item["name"]: item["value"] for item in browser_cookies}
        self.session.cookies.clear()
        for item in browser_cookies:
            self.session.cookies.set(
                item["name"],
                item["value"],
                domain=item.get("domain") or ".oreateai.com",
                path=item.get("path") or "/",
            )
        self.ouid = values.get("OUID", self.ouid)
        self.bid = values.get("__bid_n", self.bid)
        self.session.headers["User-Agent"] = self.browser.user_agent()

    @staticmethod
    def _json_response(resp: httpx.Response) -> dict:
        resp.raise_for_status()
        try:
            return resp.json()
        except Exception as exc:
            raise RuntimeError(f"non-JSON response from {resp.request.url}") from exc

    def get(self, url: str, **kwargs) -> dict:
        if self.browser is not None and url.startswith(BASE_URL):
            result = self.browser.request_json("GET", url)
            self._sync_browser()
            return result
        resp = self.session.get(url, **kwargs)
        return self._json_response(resp)

    def post(self, url: str, **kwargs) -> dict:
        if self.browser is not None and url.startswith(BASE_URL):
            result = self.browser.request_json("POST", url, body=kwargs.get("json"))
            self._sync_browser()
            return result
        resp = self.session.post(url, **kwargs)
        return self._json_response(resp)

    def risk_post(self, url: str, body: dict) -> dict:
        if self.browser is None:
            raise RuntimeError("risk request requires browser=True")
        result = self.browser.request_json("POST", url, body=body, risk=True)
        self._sync_browser()
        return result

    def stream_sse(self, url: str, body: dict) -> list[dict]:
        if self.browser is None:
            raise RuntimeError("SSE risk request requires browser=True")
        events = self.browser.stream_sse(url, body)
        self._sync_browser()
        return events

    def set_referer(self, ref: str):
        self.session.headers["Referer"] = ref
        if self.browser is not None:
            self.browser.set_url(ref)

    def export_cookies(self) -> dict[str, str]:
        if self.browser is not None:
            self._sync_browser()
        return {cookie.name: cookie.value for cookie in self.session.cookies.jar}

    def close(self):
        if self.browser is not None:
            self.browser.close()
            self.browser = None
        self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

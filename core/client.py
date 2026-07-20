"""Protocol-first HTTP client with a lazy browser risk transport."""

import ssl
import httpx
from curl_cffi import requests as curl_requests
from config import DEFAULT_HEADERS, DEFAULT_PROXY
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
        self._browser_enabled = browser
        self.browser = None
        initial_cookies = cookies or {"OUID": self.ouid}
        proxies = {"http": proxy, "https": proxy} if proxy else {}
        self.protocol = curl_requests.Session(
            impersonate="chrome120",
            headers=dict(DEFAULT_HEADERS),
            proxies=proxies,
            timeout=30,
            verify=False,
        )
        for name, value in initial_cookies.items():
            self.protocol.cookies.set(
                name, str(value), domain=".oreateai.com", path="/"
            )
        transport = httpx.HTTPTransport(proxy=proxy, verify=_ssl_ctx, retries=1) if proxy else None
        self.session = httpx.Client(
            headers=DEFAULT_HEADERS,
            cookies=initial_cookies,
            timeout=30,
            follow_redirects=True,
            transport=transport,
            verify=_ssl_ctx,
        )

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
        user_agent = self.browser.user_agent()
        self.session.headers["User-Agent"] = user_agent
        self.protocol.headers["User-Agent"] = user_agent
        self._replace_protocol_cookies(values)

    def _replace_protocol_cookies(self, cookies: dict[str, str]) -> None:
        self.protocol.cookies.clear()
        for name, value in cookies.items():
            self.protocol.cookies.set(
                name, str(value), domain=".oreateai.com", path="/"
            )

    def _sync_protocol(self) -> None:
        values = self.protocol.cookies.get_dict()
        self.session.cookies.clear()
        for name, value in values.items():
            self.session.cookies.set(
                name, value, domain=".oreateai.com", path="/"
            )
        self.ouid = values.get("OUID", self.ouid)
        self.bid = values.get("__bid_n", self.bid)

    def _cookie_values(self) -> dict[str, str]:
        return {cookie.name: cookie.value for cookie in self.session.cookies.jar}

    def _sync_to_browser(self) -> None:
        if self.browser is None:
            return
        cookies = self._cookie_values()
        self.ouid = cookies.get("OUID", self.ouid)
        self.bid = cookies.get("__bid_n", self.bid)
        self.browser.set_cookies(cookies)
        referer = self.session.headers.get("Referer", "")
        if referer:
            self.browser.set_url(referer)

    def prepare_risk_context(self):
        if not self._browser_enabled:
            raise RuntimeError("risk request requires browser=True")
        if self.browser is None:
            from core.browser_runtime import BrowserRuntime

            self.browser = BrowserRuntime(
                proxy=self.proxy,
                cookies=self._cookie_values() or {"OUID": self.ouid},
            )
            self._sync_browser()
            referer = self.session.headers.get("Referer", "")
            if referer:
                self.browser.set_url(referer)
        else:
            self._sync_to_browser()
        return self.browser

    @staticmethod
    def _json_response(resp) -> dict:
        resp.raise_for_status()
        try:
            return resp.json()
        except Exception as exc:
            raise RuntimeError(f"non-JSON response from {resp.url}") from exc

    def get(self, url: str, **kwargs) -> dict:
        resp = self.protocol.get(url, **kwargs)
        self._sync_protocol()
        return self._json_response(resp)

    def post(self, url: str, **kwargs) -> dict:
        resp = self.protocol.post(url, **kwargs)
        self._sync_protocol()
        return self._json_response(resp)

    def risk_post(self, url: str, body: dict) -> dict:
        browser = self.prepare_risk_context()
        result = browser.request_json("POST", url, body=body, risk=True)
        self._sync_browser()
        return result

    def stream_sse(self, url: str, body: dict) -> list[dict]:
        browser = self.prepare_risk_context()
        events = browser.stream_sse(url, body)
        self._sync_browser()
        return events

    def set_referer(self, ref: str):
        self.session.headers["Referer"] = ref
        self.protocol.headers["Referer"] = ref
        if self.browser is not None:
            self.browser.set_url(ref)

    def export_cookies(self) -> dict[str, str]:
        return self._cookie_values()

    def close(self):
        if self.browser is not None:
            self.browser.close()
            self.browser = None
        self.protocol.close()
        self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

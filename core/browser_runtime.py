"""Official-page browser transport for OreateAI risk-checked requests."""

from __future__ import annotations

import json
from urllib.parse import unquote, urlparse

from config import (
    BASE_URL,
    BROWSER_CHANNEL,
    BROWSER_HEADLESS,
    BROWSER_RISK_TIMEOUT_MS,
    BROWSER_TIMEOUT_MS,
)


class BrowserRuntimeError(RuntimeError):
    pass


def _proxy_config(proxy_url: str | None) -> dict | None:
    if not proxy_url:
        return None
    parsed = urlparse(proxy_url if "://" in proxy_url else f"http://{proxy_url}")
    if not parsed.hostname:
        raise ValueError(f"invalid proxy: {proxy_url}")
    config = {"server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port or 80}"}
    if parsed.username:
        config["username"] = unquote(parsed.username)
    if parsed.password:
        config["password"] = unquote(parsed.password)
    return config


class BrowserRuntime:
    def __init__(self, proxy: str | None = None, cookies: dict[str, str] | None = None):
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        self._browser = None
        self._context = None
        self._page = None
        try:
            self._browser = self._playwright.chromium.launch(
                channel=BROWSER_CHANNEL,
                headless=BROWSER_HEADLESS,
                proxy=_proxy_config(proxy),
            )
            self._context = self._browser.new_context(locale="zh-CN")
            if cookies:
                self._context.add_cookies(
                    [
                        {
                            "name": name,
                            "value": str(value),
                            "domain": ".oreateai.com",
                            "path": "/",
                            "secure": True,
                        }
                        for name, value in cookies.items()
                        if name and value is not None
                    ]
                )
            self._page = self._context.new_page()
            self._page.set_default_timeout(BROWSER_TIMEOUT_MS)
            self._page.goto(
                f"{BASE_URL}/home/index/zh",
                wait_until="commit",
                timeout=BROWSER_TIMEOUT_MS,
            )
        except Exception:
            self.close()
            raise

    @property
    def page(self):
        if self._page is None:
            raise BrowserRuntimeError("browser runtime is closed")
        return self._page

    def _wait_for_risk_runtime(self) -> None:
        self.page.wait_for_function(
            """() => Object.values(window.PARIS_INSTANCE_CACHE || {})
                .some(value => typeof value?.sendBantiReport === 'function')""",
            timeout=BROWSER_TIMEOUT_MS,
        )

    def request_json(
        self,
        method: str,
        url: str,
        *,
        body: dict | None = None,
        risk: bool = False,
    ) -> dict:
        if risk:
            self._wait_for_risk_runtime()
        result = self.page.evaluate(
            """async ({method, url, body, risk, riskTimeout}) => {
                const getJt = async () => {
                    const instance = Object.values(window.PARIS_INSTANCE_CACHE || {})
                        .find(value => typeof value?.sendBantiReport === 'function');
                    if (!instance) throw new Error('risk runtime is not ready');
                    return await new Promise((resolve, reject) => {
                        const timer = setTimeout(() => reject(new Error('risk token timeout')), riskTimeout);
                        instance.sendBantiReport({subid: ''}, (_error, response) => {
                            clearTimeout(timer);
                            const jt = response?.htj?.jt || '';
                            if (jt) resolve(jt);
                            else reject(new Error('risk token is empty'));
                        });
                    });
                };
                const payload = body ? {...body} : undefined;
                if (risk) payload.jt = await getJt();
                const response = await fetch(url, {
                    method,
                    credentials: 'include',
                    headers: {
                        'Accept': 'application/json, text/plain, */*',
                        'Content-Type': 'application/json',
                        'client-type': 'pc',
                        'locale': 'zh-CN'
                    },
                    body: method === 'GET' || payload === undefined ? undefined : JSON.stringify(payload)
                });
                return {status: response.status, ok: response.ok, text: await response.text()};
            }""",
            {
                "method": method.upper(),
                "url": url,
                "body": body,
                "risk": risk,
                "riskTimeout": BROWSER_RISK_TIMEOUT_MS,
            },
        )
        try:
            payload = json.loads(result["text"])
        except Exception as exc:
            raise BrowserRuntimeError(
                f"browser request returned non-JSON HTTP {result['status']}"
            ) from exc
        if not result["ok"]:
            raise BrowserRuntimeError(f"browser request failed with HTTP {result['status']}")
        return payload

    def stream_sse(self, url: str, body: dict) -> list[dict]:
        self._wait_for_risk_runtime()
        result = self.page.evaluate(
            """async ({url, body, riskTimeout}) => {
                const instance = Object.values(window.PARIS_INSTANCE_CACHE || {})
                    .find(value => typeof value?.sendBantiReport === 'function');
                if (!instance) throw new Error('risk runtime is not ready');
                const jt = await new Promise((resolve, reject) => {
                    const timer = setTimeout(() => reject(new Error('risk token timeout')), riskTimeout);
                    instance.sendBantiReport({subid: ''}, (_error, response) => {
                        clearTimeout(timer);
                        const value = response?.htj?.jt || '';
                        if (value) resolve(value);
                        else reject(new Error('risk token is empty'));
                    });
                });
                const response = await fetch(url, {
                    method: 'POST',
                    credentials: 'include',
                    headers: {
                        'Accept': 'text/event-stream',
                        'Content-Type': 'application/json',
                        'client-type': 'pc',
                        'locale': 'zh-CN'
                    },
                    body: JSON.stringify({...body, jt})
                });
                if (!response.ok || !response.body) {
                    throw new Error(`SSE HTTP ${response.status}`);
                }
                const reader = response.body.getReader();
                const decoder = new TextDecoder();
                const events = [];
                let buffer = '';
                const consume = (line) => {
                    if (!line.startsWith('data:')) return;
                    try { events.push(JSON.parse(line.slice(5).trim())); } catch (_error) {}
                };
                while (true) {
                    const {done, value} = await reader.read();
                    buffer += decoder.decode(value || new Uint8Array(), {stream: !done});
                    const lines = buffer.split(/\r?\n/);
                    buffer = lines.pop() || '';
                    lines.forEach(consume);
                    if (done) break;
                }
                if (buffer) consume(buffer);
                return events;
            }""",
            {"url": url, "body": body, "riskTimeout": BROWSER_RISK_TIMEOUT_MS},
        )
        if not isinstance(result, list):
            raise BrowserRuntimeError("SSE returned an invalid event collection")
        return result

    def set_url(self, url: str) -> None:
        self._wait_for_risk_runtime()
        self.page.evaluate("url => history.replaceState(null, '', url)", url)

    def set_cookies(self, cookies: dict[str, str]) -> None:
        if self._context is None:
            raise BrowserRuntimeError("browser runtime is closed")
        self._context.clear_cookies()
        if cookies:
            self._context.add_cookies(
                [
                    {
                        "name": name,
                        "value": str(value),
                        "domain": ".oreateai.com",
                        "path": "/",
                        "secure": True,
                    }
                    for name, value in cookies.items()
                    if name and value is not None
                ]
            )

    def cookies(self) -> list[dict]:
        return self._context.cookies(BASE_URL) if self._context else []

    def user_agent(self) -> str:
        return str(self.page.evaluate("() => navigator.userAgent"))

    def close(self) -> None:
        for obj in (self._context, self._browser):
            if obj is not None:
                try:
                    obj.close()
                except Exception:
                    pass
        self._context = None
        self._browser = None
        self._page = None
        if getattr(self, "_playwright", None) is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

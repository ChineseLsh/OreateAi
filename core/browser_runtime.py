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

_RISK_TOKEN_ERROR_PREFIX = "THREADAI_RISK_TOKEN:"
_RISK_TOKEN_ATTEMPTS = 2


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
        try:
            self.page.wait_for_function(
                """() => Object.values(window.PARIS_INSTANCE_CACHE || {})
                    .some(value => typeof value?.sendBantiReport === 'function')""",
                timeout=BROWSER_TIMEOUT_MS,
            )
        except Exception as exc:
            raise BrowserRuntimeError(
                f"risk runtime was not ready within {BROWSER_TIMEOUT_MS} ms"
            ) from exc

    def _evaluate_risk(self, script: str, payload: dict):
        self._wait_for_risk_runtime()
        args = {
            **payload,
            "riskTimeout": BROWSER_RISK_TIMEOUT_MS,
            "riskErrorPrefix": _RISK_TOKEN_ERROR_PREFIX,
        }
        last_error = None
        for _ in range(_RISK_TOKEN_ATTEMPTS):
            try:
                return self.page.evaluate(script, args)
            except Exception as exc:
                if _RISK_TOKEN_ERROR_PREFIX not in str(exc):
                    raise BrowserRuntimeError(f"browser request failed: {exc}") from exc
                last_error = exc
        raise BrowserRuntimeError(
            f"risk token failed after {_RISK_TOKEN_ATTEMPTS} attempts "
            f"({BROWSER_RISK_TIMEOUT_MS} ms each)"
        ) from last_error

    def request_json(
        self,
        method: str,
        url: str,
        *,
        body: dict | None = None,
        risk: bool = False,
    ) -> dict:
        script = """async ({method, url, body, risk, riskTimeout, riskErrorPrefix}) => {
                const getJt = async () => {
                    const instance = Object.values(window.PARIS_INSTANCE_CACHE || {})
                        .find(value => typeof value?.sendBantiReport === 'function');
                    if (!instance) throw new Error(`${riskErrorPrefix}runtime unavailable`);
                    return await new Promise((resolve, reject) => {
                        const fail = reason => reject(new Error(`${riskErrorPrefix}${reason}`));
                        const timer = setTimeout(() => fail('timeout'), riskTimeout);
                        try {
                            instance.sendBantiReport({subid: ''}, (error, response) => {
                                clearTimeout(timer);
                                if (error) return fail('callback error');
                                const jt = response?.htj?.jt || '';
                                if (jt) resolve(jt);
                                else fail('empty');
                            });
                        } catch (_error) {
                            clearTimeout(timer);
                            fail('callback threw');
                        }
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
            }"""
        args = {
            "method": method.upper(),
            "url": url,
            "body": body,
            "risk": risk,
            "riskTimeout": BROWSER_RISK_TIMEOUT_MS,
            "riskErrorPrefix": _RISK_TOKEN_ERROR_PREFIX,
        }
        result = (
            self._evaluate_risk(script, args)
            if risk
            else self.page.evaluate(script, args)
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
        result = self._evaluate_risk(
            """async ({url, body, riskTimeout, riskErrorPrefix}) => {
                const instance = Object.values(window.PARIS_INSTANCE_CACHE || {})
                    .find(value => typeof value?.sendBantiReport === 'function');
                if (!instance) throw new Error(`${riskErrorPrefix}runtime unavailable`);
                const jt = await new Promise((resolve, reject) => {
                    const fail = reason => reject(new Error(`${riskErrorPrefix}${reason}`));
                    const timer = setTimeout(() => fail('timeout'), riskTimeout);
                    try {
                        instance.sendBantiReport({subid: ''}, (error, response) => {
                            clearTimeout(timer);
                            if (error) return fail('callback error');
                            const value = response?.htj?.jt || '';
                            if (value) resolve(value);
                            else fail('empty');
                        });
                    } catch (_error) {
                        clearTimeout(timer);
                        fail('callback threw');
                    }
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
            {"url": url, "body": body},
        )
        if not isinstance(result, list):
            raise BrowserRuntimeError("SSE returned an invalid event collection")
        return result

    def set_url(self, url: str) -> None:
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

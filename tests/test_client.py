import unittest
from unittest.mock import Mock, patch

import httpx

from config import BASE_URL
from core.client import OreateClient


def _response(url: str, payload: dict) -> httpx.Response:
    return httpx.Response(200, json=payload, request=httpx.Request("GET", url))


class ClientTests(unittest.TestCase):
    def test_ordinary_request_does_not_start_browser(self):
        with patch("core.browser_runtime.BrowserRuntime") as runtime_class:
            client = OreateClient(proxy=None, browser=True)
            client.protocol.get = Mock(
                return_value=_response(
                    f"{BASE_URL}/oreate/user/getuserinfo",
                    {"status": {"code": 0}, "data": {}},
                )
            )

            result = client.get(f"{BASE_URL}/oreate/user/getuserinfo")

        self.assertEqual(result["status"]["code"], 0)
        runtime_class.assert_not_called()
        client.close()

    def test_protocol_response_cookies_flow_into_download_session(self):
        client = OreateClient(proxy=None, browser=True)
        client.protocol.cookies.set(
            "ouss", "protocol-session", domain=".oreateai.com", path="/"
        )
        client.protocol.get = Mock(
            return_value=_response(
                f"{BASE_URL}/oreate/user/getuserinfo",
                {"status": {"code": 0}, "data": {}},
            )
        )

        client.get(f"{BASE_URL}/oreate/user/getuserinfo")

        self.assertEqual(client.export_cookies()["ouss"], "protocol-session")
        self.assertIsNone(client.browser)
        client.close()

    def test_first_risk_request_starts_browser_with_protocol_cookies(self):
        browser = Mock()
        browser.cookies.return_value = [
            {"name": "OUID", "value": "device", "domain": ".oreateai.com", "path": "/"},
            {"name": "__bid_n", "value": "bid", "domain": ".oreateai.com", "path": "/"},
            {"name": "ouss", "value": "session", "domain": ".oreateai.com", "path": "/"},
        ]
        browser.user_agent.return_value = "Chrome/Test"
        browser.request_json.return_value = {"status": {"code": 0}, "data": {}}
        with patch(
            "core.browser_runtime.BrowserRuntime", return_value=browser
        ) as runtime_class:
            client = OreateClient(
                proxy=None,
                browser=True,
                cookies={"OUID": "device", "ouss": "protocol-session"},
            )

            result = client.risk_post(
                f"{BASE_URL}/passport/api/emaillogin", {"email": "base@example.com"}
            )

        self.assertEqual(result["status"]["code"], 0)
        self.assertEqual(
            runtime_class.call_args.kwargs["cookies"]["ouss"], "protocol-session"
        )
        browser.request_json.assert_called_once()
        self.assertEqual(client.bid, "bid")
        client.close()

    def test_protocol_cookie_update_flows_into_existing_browser(self):
        browser = Mock()
        browser.cookies.return_value = [
            {"name": "OUID", "value": "device", "domain": ".oreateai.com", "path": "/"},
            {"name": "ouss", "value": "browser-session", "domain": ".oreateai.com", "path": "/"},
        ]
        browser.user_agent.return_value = "Chrome/Test"
        browser.request_json.return_value = {"status": {"code": 0}, "data": {}}
        with patch("core.browser_runtime.BrowserRuntime", return_value=browser):
            client = OreateClient(proxy=None, browser=True, cookies={"OUID": "device"})
            client.risk_post(f"{BASE_URL}/passport/api/emaillogin", {})
            client.session.cookies.clear()
            client.session.cookies.set("OUID", "device")
            client.session.cookies.set("ouss", "protocol-confirmed")

            client.risk_post(f"{BASE_URL}/passport/api/emailsignupin", {})

        self.assertEqual(
            browser.set_cookies.call_args.args[0]["ouss"], "protocol-confirmed"
        )
        client.close()


if __name__ == "__main__":
    unittest.main()

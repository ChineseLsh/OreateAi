import unittest
from unittest.mock import Mock

from core.browser_runtime import (
    BrowserRuntime,
    BrowserRuntimeError,
    _RISK_TOKEN_ERROR_PREFIX,
)


class BrowserRuntimeTests(unittest.TestCase):
    @staticmethod
    def _runtime():
        runtime = BrowserRuntime.__new__(BrowserRuntime)
        runtime._page = Mock()
        runtime._wait_for_risk_runtime = Mock()
        return runtime

    def test_set_url_does_not_wait_for_risk_runtime(self):
        runtime = self._runtime()

        runtime.set_url("https://www.oreateai.com/home/index/zh?tokenID=test")

        runtime._wait_for_risk_runtime.assert_not_called()
        runtime.page.evaluate.assert_called_once_with(
            "url => history.replaceState(null, '', url)",
            "https://www.oreateai.com/home/index/zh?tokenID=test",
        )

    def test_risk_request_retries_token_failure_once(self):
        runtime = self._runtime()
        runtime.page.evaluate.side_effect = [
            RuntimeError(f"{_RISK_TOKEN_ERROR_PREFIX}timeout"),
            {
                "status": 200,
                "ok": True,
                "text": '{"status":{"code":0},"data":{"isRegister":true}}',
            },
        ]

        result = runtime.request_json(
            "POST",
            "https://www.oreateai.com/passport/api/emailsignupin",
            body={"email": "base@example.invalid"},
            risk=True,
        )

        self.assertEqual(result["status"]["code"], 0)
        self.assertEqual(runtime.page.evaluate.call_count, 2)
        runtime._wait_for_risk_runtime.assert_called_once_with()
        script = runtime.page.evaluate.call_args_list[0].args[0]
        self.assertEqual(script.count("fetch(url"), 1)
        self.assertLess(
            script.index("payload.jt = await getJt()"),
            script.index("const response = await fetch"),
        )

    def test_risk_request_does_not_retry_request_failure(self):
        runtime = self._runtime()
        runtime.page.evaluate.side_effect = RuntimeError("fetch failed")

        with self.assertRaisesRegex(BrowserRuntimeError, "browser request failed"):
            runtime.request_json(
                "POST",
                "https://www.oreateai.com/passport/api/emailsignupin",
                body={},
                risk=True,
            )

        runtime.page.evaluate.assert_called_once()

    def test_risk_request_reports_exhausted_token_attempts(self):
        runtime = self._runtime()
        runtime.page.evaluate.side_effect = RuntimeError(
            f"{_RISK_TOKEN_ERROR_PREFIX}empty"
        )

        with self.assertRaisesRegex(BrowserRuntimeError, "risk token failed after 2 attempts"):
            runtime.request_json(
                "POST",
                "https://www.oreateai.com/passport/api/emailsignupin",
                body={},
                risk=True,
            )

        self.assertEqual(runtime.page.evaluate.call_count, 2)

    def test_risk_runtime_timeout_reports_stage(self):
        runtime = BrowserRuntime.__new__(BrowserRuntime)
        runtime._page = Mock()
        runtime.page.wait_for_function.side_effect = TimeoutError("slow page")

        with self.assertRaisesRegex(
            BrowserRuntimeError, "risk runtime was not ready"
        ):
            runtime._wait_for_risk_runtime()


if __name__ == "__main__":
    unittest.main()

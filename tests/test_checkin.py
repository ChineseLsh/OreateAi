import unittest
from unittest.mock import Mock, patch

from modules import login


class _CheckinClient:
    def __init__(self, before, after, claim_code):
        self.points = iter((before, after))
        self.claim_code = claim_code
        self.closed = False

    def get(self, url):
        if url.endswith("/point/getrestpoints"):
            return {"status": {"code": 0}, "data": {"restPoint": next(self.points)}}
        if url.endswith("/account/getfirstusepoint"):
            return {
                "status": {
                    "code": self.claim_code,
                    "msg": "success" if self.claim_code == 0 else "claim failed",
                },
                "data": {},
            }
        raise AssertionError(f"unexpected URL: {url}")

    def export_cookies(self):
        return {"OUID": "device", "ouss": "session"}

    def close(self):
        self.closed = True


class CheckinTests(unittest.TestCase):
    def _run_checkin(self, before, after, claim_code=0):
        client = _CheckinClient(before, after, claim_code)
        unlock = Mock()
        with (
            patch.object(
                login,
                "list_accounts",
                return_value=[
                    {
                        "email": "base@example.com",
                        "password": "Password1!",
                        "cookies": "{}",
                        "status": "active",
                    }
                ],
            ),
            patch.object(login, "try_lock_account", return_value=True),
            patch.object(login, "unlock_account", unlock),
            patch.object(login, "OreateClient", return_value=client),
            patch("core.pool.restore_session", return_value=True),
            patch.object(login, "update_account_cookies") as update_cookies,
            patch.object(login, "update_account_points") as update_points,
            patch.object(login, "set_account_status") as set_status,
        ):
            result = login.daily_checkin_all()
        return result, client, unlock, update_cookies, update_points, set_status

    def test_claimed_state_reports_delta_and_persists_new_balance(self):
        result, client, unlock, update_cookies, update_points, set_status = self._run_checkin(50, 80)

        self.assertEqual(
            result,
            [
                {
                    "email": "base@example.com",
                    "ok": True,
                    "status": "claimed",
                    "points_before": 50,
                    "points_after": 80,
                    "earned": 30,
                    "error": "",
                }
            ],
        )
        update_cookies.assert_called_once_with(
            "base@example.com", {"OUID": "device", "ouss": "session"}
        )
        update_points.assert_called_once_with("base@example.com", 80)
        set_status.assert_called_once_with("base@example.com", "active")
        unlock.assert_called_once_with("base@example.com")
        self.assertTrue(client.closed)

    def test_zero_delta_with_success_code_is_already_claimed(self):
        result, *_ = self._run_checkin(80, 80)

        self.assertEqual(result[0]["status"], "already_claimed")
        self.assertEqual(result[0]["earned"], 0)
        self.assertTrue(result[0]["ok"])

    def test_busy_account_has_explicit_state_without_opening_browser(self):
        with (
            patch.object(login, "list_accounts", return_value=[{"email": "busy@example.com"}]),
            patch.object(login, "try_lock_account", return_value=False),
            patch.object(login, "OreateClient") as client_class,
        ):
            result = login.daily_checkin_all()

        self.assertEqual(
            result,
            [
                {
                    "email": "busy@example.com",
                    "ok": False,
                    "status": "busy",
                    "error": "account is busy",
                }
            ],
        )
        client_class.assert_not_called()

    def test_browser_close_failure_still_unlocks_and_returns_result(self):
        client = _CheckinClient(50, 80, 0)
        client.close = Mock(side_effect=RuntimeError("close failed"))
        unlock = Mock()
        with (
            patch.object(
                login,
                "list_accounts",
                return_value=[
                    {
                        "email": "base@example.com",
                        "password": "Password1!",
                        "cookies": "{}",
                        "status": "active",
                    }
                ],
            ),
            patch.object(login, "try_lock_account", return_value=True),
            patch.object(login, "unlock_account", unlock),
            patch.object(login, "OreateClient", return_value=client),
            patch("core.pool.restore_session", return_value=True),
            patch.object(login, "update_account_cookies"),
            patch.object(login, "update_account_points"),
            patch.object(login, "set_account_status"),
        ):
            result = login.daily_checkin_all()

        self.assertTrue(result[0]["ok"])
        unlock.assert_called_once_with("base@example.com")


if __name__ == "__main__":
    unittest.main()

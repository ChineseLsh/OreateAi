import unittest
from unittest.mock import Mock, patch

from core import pool


class PoolTests(unittest.TestCase):
    def test_browser_startup_failure_releases_account_lock(self):
        account = {
            "email": "base@example.com",
            "password": "Password1!",
            "points": 80,
            "cookies": "{}",
        }
        with (
            patch.object(pool, "acquire_best_account", return_value=account),
            patch.object(pool, "OreateClient", side_effect=RuntimeError("chrome failed")) as client,
            patch.object(pool, "unlock_account") as unlock,
            patch.object(pool.time, "sleep"),
        ):
            result = pool.acquire_account(20)

        self.assertIsNone(result)
        self.assertEqual(client.call_count, 2)
        unlock.assert_called_once_with("base@example.com")

    def test_successful_acquisition_transfers_lock_to_caller(self):
        account = {
            "email": "base@example.com",
            "password": "Password1!",
            "points": 80,
            "cookies": "{}",
        }
        client = Mock()
        with (
            patch.object(pool, "acquire_best_account", return_value=account),
            patch.object(pool, "OreateClient", return_value=client),
            patch.object(pool, "restore_session", return_value=True),
            patch.object(pool, "_fresh_points", return_value=80),
            patch.object(pool, "unlock_account") as unlock,
        ):
            result = pool.acquire_account(20)

        self.assertEqual(result, (client, account))
        unlock.assert_not_called()

    def test_refresh_keeps_low_positive_account_available(self):
        client = Mock()
        client.get.return_value = {"data": {"restPoint": 10}}
        with (
            patch.object(pool, "update_account_points") as update_points,
            patch.object(pool, "set_account_status") as set_status,
        ):
            points = pool.refresh_points(client, "base@example.com")

        self.assertEqual(points, 10)
        update_points.assert_called_once_with("base@example.com", 10)
        set_status.assert_called_once_with("base@example.com", "active")


if __name__ == "__main__":
    unittest.main()

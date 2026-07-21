import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from core import db


class MailboxPoolTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_path = db.DB_PATH
        self.original_connection = db._conn_cache
        with db._lock:
            db.DB_PATH = Path(self.temp_dir.name) / "test.db"
            db._conn_cache = None
        db.init_db()

    def tearDown(self):
        with db._lock:
            temporary_connection = db._conn_cache
            db._conn_cache = self.original_connection
            db.DB_PATH = self.original_path
        if temporary_connection is not None:
            temporary_connection.close()
        self.temp_dir.cleanup()

    @staticmethod
    def _line(address="mailbox@example.com"):
        return f"{address}----mail-password----client-id----refresh-token"

    def test_import_parses_imap_format_without_returning_secrets(self):
        from modules.self_pool import import_mailboxes

        result = import_mailboxes(
            self._line() + "\ninvalid-line\n" + self._line("second@example.com")
        )

        self.assertEqual(result["imported"], 2)
        self.assertEqual(result["updated"], 0)
        self.assertEqual(result["invalid_lines"], [2])
        self.assertNotIn("mail-password", str(result))
        self.assertNotIn("refresh-token", str(result))

    def test_acquire_mailbox_has_single_winner_under_concurrency(self):
        from modules.self_pool import import_mailboxes

        import_mailboxes(self._line())
        barrier = threading.Barrier(3)
        results = []

        def acquire():
            barrier.wait()
            results.append(db.acquire_mailbox())

        threads = [threading.Thread(target=acquire) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=5)

        winners = [record for record in results if record is not None]
        self.assertEqual(len(winners), 1)
        self.assertEqual(winners[0]["address"], "mailbox@example.com")
        self.assertEqual(db.get_mailbox("mailbox@example.com")["status"], "reserved")

    def test_reimport_preserves_used_status_and_reset_is_explicit(self):
        from modules.self_pool import import_mailboxes

        import_mailboxes(self._line())
        record = db.acquire_mailbox()
        self.assertIsNotNone(record)
        db.finalize_mailbox(record["address"])

        result = import_mailboxes(
            "mailbox@example.com----new-password----new-client----new-refresh"
        )

        self.assertEqual(result["updated"], 1)
        self.assertEqual(db.get_mailbox("mailbox@example.com")["status"], "used")
        self.assertTrue(db.reset_mailbox("mailbox@example.com"))
        self.assertEqual(db.get_mailbox("mailbox@example.com")["status"], "available")

    def test_mailbox_list_never_returns_credentials(self):
        from modules.self_pool import import_mailboxes

        import_mailboxes(self._line())

        result = db.list_mailboxes()

        self.assertEqual(len(result), 1)
        self.assertNotIn("password", result[0])
        self.assertNotIn("client_id", result[0])
        self.assertNotIn("refresh_token", result[0])

    def test_provider_marks_failed_attempt_used_and_success_registered(self):
        from modules.self_pool import SelfPoolEmailProvider, import_mailboxes

        import_mailboxes(self._line("failed@example.com"))
        failed = SelfPoolEmailProvider()
        self.assertEqual(failed.create(), "failed@example.com")
        failed.close()
        self.assertEqual(db.get_mailbox("failed@example.com")["status"], "used")

        import_mailboxes(self._line("success@example.com"))
        success = SelfPoolEmailProvider()
        self.assertEqual(success.create(), "success@example.com")
        db.save_account("success@example.com", "Password1!", 50, "", {})
        success.close()
        self.assertEqual(db.get_mailbox("success@example.com")["status"], "registered")

    def test_provider_reuses_microsoft_oauth_imap_reader(self):
        from modules.self_pool import SelfPoolEmailProvider, import_mailboxes

        token_id = "12345678-1234-4abc-8def-1234567890ab"
        import_mailboxes(self._line())
        provider = SelfPoolEmailProvider()
        provider.create()

        with (
            patch.object(provider, "_refresh_access_token", return_value="access-token"),
            patch.object(provider, "_find_token", return_value=token_id),
        ):
            self.assertEqual(
                provider.wait_for_verification_link(timeout=1),
                ("mailbox@example.com", token_id),
            )
        provider.close()

    def test_provider_persists_rotated_refresh_token(self):
        from modules.luckmail import LuckMailProvider
        from modules.self_pool import SelfPoolEmailProvider, import_mailboxes

        import_mailboxes(self._line())
        provider = SelfPoolEmailProvider()
        provider.create()

        def refresh(_provider):
            provider._account["refresh_token"] = "rotated-refresh-token"
            return "access-token"

        with patch.object(LuckMailProvider, "_refresh_access_token", refresh):
            self.assertEqual(provider._refresh_access_token(), "access-token")

        self.assertEqual(
            db.get_mailbox("mailbox@example.com")["refresh_token"],
            "rotated-refresh-token",
        )
        provider.close()

    def test_provider_reports_reachable_inbox_without_oreate_mail(self):
        from modules import self_pool
        from modules.self_pool import SelfPoolEmailProvider, import_mailboxes

        import_mailboxes(self._line())
        provider = SelfPoolEmailProvider()
        provider.create()

        with (
            patch.object(provider, "_refresh_access_token", return_value="access-token"),
            patch.object(provider, "_find_token", return_value=None),
            patch.object(self_pool.time, "time", side_effect=(0, 0, 2)),
            patch.object(self_pool.time, "sleep"),
        ):
            with self.assertRaisesRegex(
                TimeoutError,
                "self mailbox received no OreateAI verification link",
            ):
                provider.wait_for_verification_link(timeout=1)
        provider.close()

    def test_factory_builds_self_pool_provider(self):
        from modules.email_provider import build_email_provider
        from modules.self_pool import SelfPoolEmailProvider

        self.assertIsInstance(build_email_provider("self_pool"), SelfPoolEmailProvider)


if __name__ == "__main__":
    unittest.main()

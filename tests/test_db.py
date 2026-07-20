import tempfile
import threading
import unittest
from pathlib import Path

from core import db


class DatabaseTests(unittest.TestCase):
    def test_acquire_best_account_has_single_winner_under_concurrency(self):
        original_path = db.DB_PATH
        original_connection = db._conn_cache
        temporary_connection = None

        with tempfile.TemporaryDirectory() as directory:
            try:
                with db._lock:
                    db.DB_PATH = Path(directory) / "test.db"
                    db._conn_cache = None
                db.init_db()
                db.save_account(
                    "one@example.com",
                    "Password1!",
                    80,
                    "",
                    {"OUID": "device", "ouss": "session"},
                )

                barrier = threading.Barrier(3)
                results = []
                errors = []

                def acquire():
                    try:
                        barrier.wait()
                        results.append(db.acquire_best_account(20))
                    except Exception as exc:
                        errors.append(exc)

                threads = [threading.Thread(target=acquire) for _ in range(2)]
                for thread in threads:
                    thread.start()
                barrier.wait()
                for thread in threads:
                    thread.join(timeout=5)

                self.assertFalse(errors)
                self.assertTrue(all(not thread.is_alive() for thread in threads))
                winners = [account for account in results if account is not None]
                self.assertEqual(len(winners), 1)
                self.assertEqual(winners[0]["email"], "one@example.com")
                self.assertEqual(db.get_account("one@example.com")["locked"], 1)
            finally:
                with db._lock:
                    temporary_connection = db._conn_cache
                    db._conn_cache = original_connection
                    db.DB_PATH = original_path
                if temporary_connection is not None:
                    temporary_connection.close()


if __name__ == "__main__":
    unittest.main()

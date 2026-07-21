import tempfile
import unittest
from pathlib import Path

from dotenv import dotenv_values

from core.settings import read_settings, update_settings


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.env_path = Path(self.temp_dir.name) / ".env"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_read_settings_masks_secret_values(self):
        self.env_path.write_text(
            "LUCKMAIL_API_KEY=private-key\n"
            "THREADAI_BROWSER_HEADLESS=false\n"
            "THREADAI_BROWSER_TIMEOUT_MS=45000\n",
            encoding="utf-8",
        )

        result = read_settings(self.env_path)

        self.assertNotIn("private-key", str(result))
        self.assertTrue(result["secret_status"]["LUCKMAIL_API_KEY"])
        self.assertFalse(result["values"]["THREADAI_BROWSER_HEADLESS"])
        self.assertEqual(result["values"]["THREADAI_BROWSER_TIMEOUT_MS"], 45000)
        self.assertEqual(result["values"]["LUCKMAIL_MODE"], "project_purchase")
        self.assertEqual(result["values"]["LUCKMAIL_PROJECT_CODE"], "grok")
        self.assertEqual(result["values"]["LUCKMAIL_EMAIL_TYPE"], "ms_imap")
        self.assertEqual(result["values"]["LUCKMAIL_DOMAIN"], "outlook.com")
        self.assertEqual(result["values"]["LUCKMAIL_ORDER_ALLOCATION_ATTEMPTS"], 10)

    def test_update_preserves_blank_secret_and_supports_explicit_clear(self):
        self.env_path.write_text("LUCKMAIL_API_KEY=private-key\n", encoding="utf-8")

        update_settings(
            {
                "THREADAI_EMAIL_PROVIDER": "auto",
                "THREADAI_BROWSER_HEADLESS": True,
            },
            {"LUCKMAIL_API_KEY": ""},
            [],
            self.env_path,
        )

        values = dotenv_values(self.env_path)
        self.assertEqual(values["LUCKMAIL_API_KEY"], "private-key")
        self.assertEqual(values["THREADAI_BROWSER_HEADLESS"], "true")

        result = update_settings(
            {},
            {},
            ["LUCKMAIL_API_KEY"],
            self.env_path,
        )
        self.assertFalse(result["secret_status"]["LUCKMAIL_API_KEY"])

        result = update_settings(
            {"THREADAI_EMAIL_PROVIDER": "self_pool"},
            {},
            [],
            self.env_path,
        )
        self.assertEqual(result["values"]["THREADAI_EMAIL_PROVIDER"], "self_pool")

    def test_update_rejects_unknown_and_invalid_values(self):
        with self.assertRaisesRegex(ValueError, "unsupported setting"):
            update_settings({"UNKNOWN_SETTING": "value"}, {}, [], self.env_path)

        with self.assertRaisesRegex(ValueError, "THREADAI_BROWSER_TIMEOUT_MS"):
            update_settings(
                {"THREADAI_BROWSER_TIMEOUT_MS": 100},
                {},
                [],
                self.env_path,
            )

        with self.assertRaisesRegex(ValueError, "THREADAI_EMAIL_PROVIDER"):
            update_settings(
                {"THREADAI_EMAIL_PROVIDER": "unknown"},
                {},
                [],
                self.env_path,
            )

        with self.assertRaisesRegex(ValueError, "LUCKMAIL_MODE"):
            update_settings(
                {"LUCKMAIL_MODE": "unknown"},
                {},
                [],
                self.env_path,
            )

        with self.assertRaisesRegex(ValueError, "LUCKMAIL_EMAIL_TYPE"):
            update_settings(
                {"LUCKMAIL_EMAIL_TYPE": "unknown"},
                {},
                [],
                self.env_path,
            )

        with self.assertRaisesRegex(ValueError, "LUCKMAIL_PROJECT_CODE"):
            update_settings(
                {"LUCKMAIL_PROJECT_CODE": ""},
                {},
                [],
                self.env_path,
            )

        with self.assertRaisesRegex(ValueError, "LUCKMAIL_API_KEY"):
            update_settings(
                {},
                {"LUCKMAIL_API_KEY": "line-one\nline-two"},
                [],
                self.env_path,
            )

        with self.assertRaisesRegex(ValueError, "LUCKMAIL_API_KEY"):
            update_settings(
                {},
                {},
                ["LUCKMAIL_API_KEY", "UNSUPPORTED_SECRET"],
                self.env_path,
            )


if __name__ == "__main__":
    unittest.main()

import unittest
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import format_datetime
from unittest.mock import patch

from modules import luckmail


class _FakeIMAP:
    def __init__(self, messages):
        self.messages = messages
        self.authenticated = False
        self.closed = False
        self.logged_out = False

    def authenticate(self, mechanism, callback):
        self.authenticated = mechanism == "XOAUTH2" and bool(callback(None))
        return "OK", [b""]

    def select(self, mailbox):
        return "OK", [b""]

    def search(self, *_):
        ids = b" ".join(str(index).encode() for index in sorted(self.messages))
        return "OK", [ids]

    def fetch(self, message_id, _query):
        raw = self.messages[int(message_id)]
        return "OK", [(b"RFC822", raw)]

    def close(self):
        self.closed = True

    def logout(self):
        self.logged_out = True


def _mail_bytes(to_address, body):
    message = EmailMessage()
    message["Date"] = format_datetime(datetime.now(timezone.utc))
    message["To"] = to_address
    message["From"] = "Oreate AI <verify@example.invalid>"
    message["Subject"] = "Verify your Oreate account"
    message.set_content(body)
    return message.as_bytes()


class LuckMailTests(unittest.TestCase):
    def setUp(self):
        luckmail._rotated_refresh_tokens.clear()

    def test_normalize_record_keeps_only_usable_base_ms_imap_mailboxes(self):
        valid = {
            "type": "ms_imap",
            "status": "active",
            "address": "base@example.com",
            "password": "mail-password",
            "client_id": "client-id",
            "refresh_token": "refresh-token",
        }

        self.assertEqual(
            luckmail._normalize_record(valid),
            {
                "address": "base@example.com",
                "password": "mail-password",
                "client_id": "client-id",
                "refresh_token": "refresh-token",
            },
        )

        rejected = (
            {**valid, "address": "base+alias@example.com"},
            {**valid, "status": 0},
            {**valid, "type": "password"},
            {**valid, "client_id": ""},
            {**valid, "refresh_token": ""},
        )
        for record in rejected:
            with self.subTest(record=record):
                self.assertIsNone(luckmail._normalize_record(record))

    def test_find_token_filters_recipient_and_extracts_oreate_token(self):
        expected = "12345678-1234-4abc-8def-1234567890ab"
        unrelated = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        fake_imap = _FakeIMAP(
            {
                1: _mail_bytes(
                    "base@example.com",
                    f"Open the link: https://example.invalid/?tokenID={expected}&amp;from=mail",
                ),
                2: _mail_bytes(
                    "someone-else@example.com",
                    f"https://example.invalid/?tokenID={unrelated}",
                ),
            }
        )
        provider = luckmail.LuckMailProvider()
        provider.address = "base@example.com"

        with (
            patch.object(luckmail, "LUCKMAIL_IMAP_PROXY", "direct"),
            patch.object(luckmail, "LUCKMAIL_REQUIRE_RECIPIENT_MATCH", True),
            patch.object(luckmail.imaplib, "IMAP4_SSL", return_value=fake_imap),
        ):
            token = provider._find_token("imap.example.invalid", "access-token")

        self.assertEqual(token, expected)
        self.assertTrue(fake_imap.authenticated)
        self.assertTrue(fake_imap.closed)
        self.assertTrue(fake_imap.logged_out)


if __name__ == "__main__":
    unittest.main()

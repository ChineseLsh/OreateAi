import unittest
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import format_datetime
from unittest.mock import patch

import httpx

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

    def test_project_order_client_creates_and_polls_order(self):
        requests = []

        def handler(request):
            requests.append(request)
            if request.url.path.endswith("/order/create"):
                return httpx.Response(
                    200,
                    json={
                        "code": 0,
                        "data": {
                            "order_no": "ORD-1",
                            "email_address": "user@outlook.com",
                        },
                    },
                )
            return httpx.Response(
                200,
                json={"code": 0, "data": {"status": "success", "verification_code": "token-id"}},
            )

        client = luckmail.LuckMailClient(
            "api-key",
            transport=httpx.MockTransport(handler),
        )
        created = client.create_order(
            project_code="grok",
            email_type="ms_imap",
            domain="outlook.com",
        )
        code = client.get_order_code("ORD-1")
        client.close()

        self.assertEqual(created["email_address"], "user@outlook.com")
        self.assertEqual(code["verification_code"], "token-id")
        self.assertEqual(requests[0].method, "POST")
        self.assertEqual(
            requests[0].read(),
            b'{"project_code":"grok","email_type":"ms_imap","domain":"outlook.com"}',
        )

    def test_project_order_creation_is_not_retried(self):
        attempts = 0

        def handler(_request):
            nonlocal attempts
            attempts += 1
            raise httpx.ReadTimeout("response lost")

        client = luckmail.LuckMailClient(
            "api-key",
            retries=3,
            transport=httpx.MockTransport(handler),
        )
        try:
            with self.assertRaisesRegex(RuntimeError, "after 1 attempts"):
                client.create_order(
                    project_code="grok",
                    email_type="ms_imap",
                    domain="outlook.com",
                )
        finally:
            client.close()

        self.assertEqual(attempts, 1)

    def test_project_provider_polls_and_cancels_pending_order(self):
        token_id = "12345678-1234-4abc-8def-1234567890ab"

        class FakeClient:
            def __init__(self):
                self.cancelled = []
                self.polls = 0

            def create_order(self, **kwargs):
                self.create_kwargs = kwargs
                return {"order_no": "ORD-2", "email_address": "user@outlook.com"}

            def get_order_code(self, order_no):
                self.polls += 1
                return {"status": "success", "verification_code": token_id}

            def cancel_order(self, order_no):
                self.cancelled.append(order_no)

            def close(self):
                pass

        fake = FakeClient()
        with (
            patch.object(luckmail, "LUCKMAIL_MODE", "project_order"),
            patch.object(luckmail, "LuckMailClient", return_value=fake),
        ):
            provider = luckmail.LuckMailProvider()
            self.assertEqual(provider.create(), "user@outlook.com")
            self.assertEqual(provider.wait_for_verification_link(timeout=1), ("user@outlook.com", token_id))
            provider.close()

        self.assertEqual(fake.create_kwargs, {
            "project_code": "grok",
            "email_type": "ms_imap",
            "domain": "outlook.com",
        })
        self.assertEqual(fake.cancelled, [])

        pending = FakeClient()
        pending.get_order_code = lambda order_no: {"status": "pending"}
        with (
            patch.object(luckmail, "LUCKMAIL_MODE", "project_order"),
            patch.object(luckmail, "LuckMailClient", return_value=pending),
            patch.object(luckmail.time, "sleep"),
        ):
            provider = luckmail.LuckMailProvider()
            provider.create()
            with self.assertRaises(TimeoutError):
                provider.wait_for_verification_link(timeout=0)
            provider.close()
        self.assertEqual(pending.cancelled, ["ORD-2"])

    def test_project_provider_rejects_and_cancels_wrong_domain(self):
        class FakeClient:
            def __init__(self):
                self.cancelled = []

            def create_order(self, **kwargs):
                return {"order_no": "ORD-3", "email_address": "user@hotmail.com"}

            def cancel_order(self, order_no):
                self.cancelled.append(order_no)

            def close(self):
                pass

        fake = FakeClient()
        with (
            patch.object(luckmail, "LUCKMAIL_MODE", "project_order"),
            patch.object(luckmail, "LuckMailClient", return_value=fake),
        ):
            provider = luckmail.LuckMailProvider()
            with self.assertRaisesRegex(RuntimeError, "outlook.com"):
                provider.create()

        self.assertEqual(fake.cancelled, ["ORD-3"])

    def test_project_provider_holds_existing_accounts_until_fresh_mailbox(self):
        class FakeClient:
            def __init__(self):
                self.orders = iter(
                    (
                        {"order_no": "ORD-old-1", "email_address": "old1@outlook.com"},
                        {"order_no": "ORD-old-2", "email_address": "old2@outlook.com"},
                        {"order_no": "ORD-new", "email_address": "fresh@outlook.com"},
                    )
                )
                self.cancelled = []

            def create_order(self, **kwargs):
                return next(self.orders)

            def cancel_order(self, order_no):
                self.cancelled.append(order_no)

            def close(self):
                pass

        fake = FakeClient()
        with (
            patch.object(luckmail, "LUCKMAIL_MODE", "project_order"),
            patch.object(luckmail, "LUCKMAIL_ORDER_ALLOCATION_ATTEMPTS", 5),
            patch.object(luckmail, "LuckMailClient", return_value=fake),
            patch.object(
                luckmail,
                "get_account",
                side_effect=lambda address: {"email": address}
                if address.startswith("old")
                else None,
            ),
        ):
            provider = luckmail.LuckMailProvider()
            self.assertEqual(provider.create(), "fresh@outlook.com")
            self.assertEqual(fake.cancelled, ["ORD-old-1", "ORD-old-2"])
            provider.close()

        self.assertEqual(
            fake.cancelled,
            ["ORD-old-1", "ORD-old-2", "ORD-new"],
        )


if __name__ == "__main__":
    unittest.main()

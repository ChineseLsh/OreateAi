import unittest
from unittest.mock import patch

from core import pool
from modules import register


class _SignupClient:
    def __init__(self):
        self.calls = []

    def risk_post(self, url, body):
        self.calls.append((url, body))
        return {
            "status": {"code": 0, "msg": "success"},
            "data": {"isRegister": True},
        }


class _Provider:
    address = "base@example.com"

    def wait_for_verification_link(self):
        return self.address, "12345678-1234-4abc-8def-1234567890ab"


class _UnauthenticatedClient:
    def __init__(self):
        self.urls = []
        self.referer = ""

    def get(self, url):
        self.urls.append(url)
        return {
            "status": {"code": 200001, "msg": "user not login"},
            "data": {},
        }

    def set_referer(self, value):
        self.referer = value


class RegistrationTests(unittest.TestCase):
    def test_main_signup_uses_exact_successful_har_schema(self):
        client = _SignupClient()

        response = register.email_signup(
            client,
            "base@example.com",
            "encrypted-password",
            "ticket-id",
            fr="main",
            fission_code="must-not-leak",
            invite_code="must-not-leak",
        )

        self.assertEqual(response["status"]["code"], 0)
        self.assertEqual(len(client.calls), 1)
        url, body = client.calls[0]
        self.assertTrue(url.endswith("/passport/api/emailsignupin"))
        self.assertEqual(
            body,
            {
                "fr": "main",
                "email": "base@example.com",
                "ticketID": "ticket-id",
                "password": "encrypted-password",
            },
        )

    def test_registration_rejects_confirmed_but_unauthenticated_session(self):
        client = _UnauthenticatedClient()
        with (
            patch.object(register, "get_ticket", return_value=("ticket-id", "public-key")),
            patch.object(register, "encrypt_password", return_value="encrypted-password"),
            patch.object(
                register,
                "email_signup",
                return_value={
                    "status": {"code": 0, "msg": "success"},
                    "data": {"isRegister": True},
                },
            ),
            patch.object(
                register,
                "email_register_confirm",
                return_value={"status": {"code": 0}, "data": {"isLogin": True}},
            ),
        ):
            result = register.register(client, _Provider(), password="Password1!")

        self.assertFalse(result.success)
        self.assertEqual(result.error, "confirmed session is not logged in")
        self.assertEqual(len(client.urls), 1)
        self.assertTrue(client.urls[0].endswith("/oreate/user/getuserinfo"))

    def test_session_restore_does_not_treat_anonymous_points_as_authenticated(self):
        client = _UnauthenticatedClient()
        account = {
            "email": "base@example.com",
            "password": "",
            "cookies": "{}",
        }

        restored = pool.restore_session(client, account)

        self.assertFalse(restored)
        self.assertEqual(len(client.urls), 1)
        self.assertTrue(client.urls[0].endswith("/oreate/user/getuserinfo"))


if __name__ == "__main__":
    unittest.main()

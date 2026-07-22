import asyncio
import io
import unittest
from unittest.mock import Mock, patch

from fastapi import UploadFile

from api import server
from modules.video import VideoResult, VideoSpec


class ApiTests(unittest.TestCase):
    def setUp(self):
        server.tasks_status.clear()

    def test_accounts_response_omits_credentials_and_cookies(self):
        rows = [
            {
                "email": "base@example.invalid",
                "password": "secret",
                "cookies": '{"ouss":"session"}',
                "points": 80,
            }
        ]
        with patch.object(server, "list_accounts", return_value=rows):
            result = asyncio.run(server.api_accounts())

        self.assertEqual(
            result,
            [{"email": "base@example.invalid", "points": 80}],
        )

    def test_register_task_passes_configured_provider(self):
        with patch.object(
            server,
            "register_and_add_to_pool",
            return_value={"email": "base@example.invalid", "points": 80},
        ) as register_account:
            server._bg_register("register-task", "luckmail")

        register_account.assert_called_once_with("luckmail")
        self.assertEqual(server.tasks_status["register-task"]["status"], "done")

    def test_video_upload_uses_video_source_and_returns_file_metadata(self):
        client = Mock()
        upload = UploadFile(file=io.BytesIO(b"png-data"), filename="cute.png")
        with (
            patch.object(
                server,
                "auto_acquire",
                return_value=(client, {"email": "base@example.invalid"}),
            ),
            patch.object(server, "upload_image", return_value="aivideo/upload/cute.png") as upload_image,
            patch.object(server, "release_account"),
        ):
            result = server.api_upload(upload, "cute.png")

        upload_image.assert_called_once_with(
            client,
            b"png-data",
            "cute",
            "png",
            source="aiVideo",
        )
        self.assertEqual(result["filename"], "cute.png")
        self.assertEqual(result["size"], 8)

    def test_mailbox_import_returns_counts_without_submitted_secrets(self):
        request = server.MailboxImportReq(
            text="mailbox@example.com----secret----client-id----refresh-token"
        )
        expected = {"imported": 1, "updated": 0, "invalid_lines": []}
        with patch.object(server, "import_mailboxes", return_value=expected):
            result = asyncio.run(server.api_import_mailboxes(request))

        self.assertEqual(result, expected)
        self.assertNotIn("secret", str(result))
        self.assertNotIn("refresh-token", str(result))

    def test_mailbox_list_uses_safe_database_projection(self):
        expected = [{
            "address": "mailbox@example.com",
            "type": "ms_imap",
            "status": "available",
            "created_at": 1,
            "updated_at": 1,
        }]
        with patch.object(server, "list_mailboxes", return_value=expected):
            result = asyncio.run(server.api_mailboxes())

        self.assertEqual(result, expected)

    def test_mailbox_reset_rejects_non_resettable_address(self):
        with patch.object(server, "reset_mailbox", return_value=False):
            with self.assertRaises(server.HTTPException) as caught:
                asyncio.run(server.api_reset_mailbox("mailbox@example.com"))

        self.assertEqual(caught.exception.status_code, 409)

    def test_config_response_uses_secret_safe_settings_service(self):
        expected = {
            "values": {"THREADAI_BROWSER_HEADLESS": True},
            "secret_status": {"LUCKMAIL_API_KEY": True},
            "restart_required": False,
        }
        with patch.object(server, "read_settings", return_value=expected):
            result = asyncio.run(server.api_config())

        self.assertEqual(result, expected)

    def test_config_update_maps_validation_errors_to_422(self):
        request = server.ConfigReq(values={"UNKNOWN": "value"})
        with patch.object(
            server,
            "update_settings",
            side_effect=ValueError("unsupported setting: UNKNOWN"),
        ):
            with self.assertRaises(server.HTTPException) as caught:
                asyncio.run(server.api_config_update(request))

        self.assertEqual(caught.exception.status_code, 422)
        self.assertIn("UNKNOWN", caught.exception.detail)

    def test_register_task_returns_underlying_failure_reason(self):
        with patch.object(
            server,
            "register_and_add_to_pool",
            side_effect=RuntimeError("risk token failed after 2 attempts"),
        ):
            server._bg_register("register-task", "luckmail")

        task = server.tasks_status["register-task"]
        self.assertEqual(task["status"], "error")
        self.assertEqual(
            task["result"]["error"], "risk token failed after 2 attempts"
        )

    def test_checkin_task_keeps_per_account_results_and_earned_total(self):
        results = [
            {
                "email": "base@example.invalid",
                "ok": True,
                "status": "claimed",
                "points_after": 80,
                "earned": 30,
            },
            {
                "email": "busy@example.invalid",
                "ok": False,
                "status": "busy",
                "error": "account is busy",
            },
        ]
        with patch.object(server, "daily_checkin_all", return_value=results):
            server._bg_checkin("checkin-task")

        payload = server.tasks_status["checkin-task"]["result"]
        self.assertEqual(payload["total"], 2)
        self.assertEqual(payload["success"], 1)
        self.assertEqual(payload["earned_points"], 30)
        self.assertEqual(payload["accounts"], results)

    def test_fission_task_passes_configured_provider(self):
        result = Mock(success=True, email="base@example.invalid", points=80)
        with patch.object(server, "chain_fission", return_value=[result]) as chain:
            server._bg_fission("fission-task", "invite-code", 2, "luckmail")

        chain.assert_called_once_with(
            "invite-code", depth=2, provider_name="luckmail"
        )
        self.assertEqual(server.tasks_status["fission-task"]["status"], "done")

    def test_video_resolves_cost_before_acquire_and_keeps_remote_success(self):
        spec = VideoSpec(
            model_name="Seedance 2.0 Mini",
            duration=5,
            resolution="720",
            is_audio=True,
            ai_type=14201,
            point=68,
            scene="text_or_image",
        )
        config_context = Mock()
        config_context.__enter__ = Mock(return_value=object())
        config_context.__exit__ = Mock(return_value=False)
        account_client = Mock()
        remote = VideoResult(
            True,
            video_url="https://cdn.oreateai.com/result.mp4",
            log_id="log-1",
            downloaded=False,
            error="remote video generated but local download failed",
        )
        request = server.VideoReq(
            prompt="test prompt",
            resolution="720",
            is_audio=True,
            image_url="aivideo/upload/cute.png",
            image_name="cute.png",
            image_size=12345,
        )

        with (
            patch.object(server, "OreateClient", return_value=config_context),
            patch.object(server, "resolve_video_spec", return_value=spec),
            patch.object(
                server,
                "auto_acquire",
                return_value=(
                    account_client,
                    {"email": "base@example.invalid", "points": 100},
                ),
            ) as acquire,
            patch.object(server, "save_video"),
            patch.object(
                server,
                "get_remaining_points",
                side_effect=[100, RuntimeError("points unavailable")],
            ),
            patch.object(server, "generate_video", return_value=remote) as generate,
            patch.object(server, "update_account_points"),
            patch.object(server, "update_video"),
            patch.object(server, "release_account"),
        ):
            server._bg_video("video-task", request)

        acquire.assert_called_once_with(min_points=68)
        self.assertEqual(generate.call_args.kwargs["ai_type"], 14201)
        self.assertEqual(generate.call_args.kwargs["resolution"], "720")
        self.assertEqual(generate.call_args.kwargs["image_name"], "cute.png")
        self.assertEqual(generate.call_args.kwargs["image_size"], 12345)
        result = server.tasks_status["video-task"]
        self.assertEqual(result["status"], "done")
        self.assertEqual(result["result"]["local_path"], "")
        self.assertIsNone(result["result"]["points_after"])
        self.assertEqual(result["result"]["points_cost"], 68)
        self.assertEqual(
            result["result"]["video_url"],
            "https://cdn.oreateai.com/result.mp4",
        )
        self.assertIn("warning", result["result"])
        self.assertIn("point refresh failed", result["result"]["warning"])

    def test_video_quarantines_account_rejected_as_spam(self):
        spec = VideoSpec(
            model_name="Seedance 2.0 Mini",
            duration=5,
            resolution="480",
            is_audio=False,
            ai_type=14198,
            point=30,
            scene="text_or_image",
        )
        config_context = Mock()
        config_context.__enter__ = Mock(return_value=object())
        config_context.__exit__ = Mock(return_value=False)
        account_client = Mock()
        request = server.VideoReq(prompt="test", resolution="480", is_audio=False)
        rejected = VideoResult(
            False,
            error="upstream video error 212361: spam user",
            error_code=212361,
        )
        lifecycle = []

        with (
            patch.object(server, "OreateClient", return_value=config_context),
            patch.object(server, "resolve_video_spec", return_value=spec),
            patch.object(
                server,
                "auto_acquire",
                return_value=(
                    account_client,
                    {"email": "spam@example.invalid", "points": 80},
                ),
            ),
            patch.object(server, "save_video"),
            patch.object(server, "get_remaining_points", return_value=80),
            patch.object(server, "generate_video", return_value=rejected),
            patch.object(server, "update_video"),
            patch.object(
                server,
                "set_account_status",
                side_effect=lambda *_: lifecycle.append("quarantine"),
            ) as set_status,
            patch.object(
                server,
                "release_account",
                side_effect=lambda *_: lifecycle.append("release"),
            ),
        ):
            server._bg_video("video-task", request)

        set_status.assert_called_once_with("spam@example.invalid", "error")
        self.assertEqual(lifecycle, ["release", "quarantine"])
        self.assertEqual(server.tasks_status["video-task"]["status"], "error")


if __name__ == "__main__":
    unittest.main()

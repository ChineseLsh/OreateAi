import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from modules import video


MODEL_CONFIG = {
    "models": [
        {
            "modelName": "Seedance 2.0 Mini",
            "pointCostImage": [
                {
                    "audio": True,
                    "duration": 5,
                    "point": 68,
                    "resolution": "720",
                    "aiType": 14201,
                }
            ],
            "pointCostReference": [
                {
                    "duration": 5,
                    "point": 105,
                    "resolution": "720",
                    "aiType": 14210,
                }
            ],
        }
    ]
}


class _ModelClient:
    def get(self, _url):
        return {"status": {"code": 0}, "data": MODEL_CONFIG}


class _SSEClient:
    def __init__(self):
        self.session = SimpleNamespace(headers={"User-Agent": "Chrome/Test"})
        self.ouid = "DEVICE:FG=1"
        self.bid = "browser-id"
        self.body = None
        self.referer = ""
        self.risk_prepared = False

    def get(self, url):
        if url.endswith("/aivideo/getmodelconfigv3"):
            return {"status": {"code": 0}, "data": MODEL_CONFIG}
        if url.endswith("/oreate/user/getuserinfo"):
            return {
                "status": {"code": 0},
                "data": {
                    "basicInfo": {
                        "email": "base@example.com",
                        "createTime": 1234567890,
                        "isLogin": True,
                    },
                    "vipInfo": {"vipType": 0},
                },
            }
        raise AssertionError(f"unexpected URL: {url}")

    def set_referer(self, value):
        self.referer = value

    def prepare_risk_context(self):
        self.risk_prepared = True

    def stream_sse(self, url, body):
        self.body = body
        self.stream_url = url
        return [
            {"event": "start", "logId": "log-1"},
            {"event": "ping", "logId": "log-1"},
            {
                "event": "generating",
                "logId": "log-1",
                "data": {
                    "equery_id": "query-1",
                    "is_end": True,
                    "result": "<video controls src='https://cdn.oreateai.com/result.mp4'>",
                },
            },
            {"event": "end", "logId": "log-1"},
        ]


class _StatusResponse:
    status_code = 503

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def iter_bytes(self):
        raise AssertionError("a failed response must not be written")


class VideoTests(unittest.TestCase):
    def test_resolve_video_spec_returns_exact_ai_type_and_cost(self):
        spec = video.resolve_video_spec(
            _ModelClient(),
            "Seedance 2.0 Mini",
            duration=5,
            resolution="720",
            is_audio=True,
        )

        self.assertEqual(spec.ai_type, 14201)
        self.assertEqual(spec.point, 68)
        self.assertEqual(spec.scene, "text_or_image")

    def test_resolve_video_spec_rejects_unsupported_combination_without_fallback(self):
        with self.assertRaisesRegex(ValueError, "unsupported video configuration"):
            video.resolve_video_spec(
                _ModelClient(),
                "Seedance 2.0 Mini",
                duration=5,
                resolution="480",
                is_audio=True,
            )

        with self.assertRaisesRegex(ValueError, "unknown video model"):
            video.resolve_video_spec(_ModelClient(), "Unknown Model")

    def test_submit_video_sse_parses_url_and_builds_profile_bound_payload(self):
        client = _SSEClient()

        result = video.submit_video_sse(
            client,
            "chat-1",
            "make a test video",
            resolution="720",
            duration=5,
            is_audio=True,
        )

        self.assertTrue(result.success)
        self.assertEqual(result.video_url, "https://cdn.oreateai.com/result.mp4")
        self.assertEqual(result.log_id, "log-1")
        self.assertEqual(result.query_id, "query-1")
        self.assertEqual(result.cost_points, 68)
        self.assertTrue(client.risk_prepared)
        self.assertTrue(client.stream_url.endswith("/oreate/sse/stream"))
        self.assertEqual(
            client.body["extra"],
            {
                "email": "base@example.com",
                "vip": "0",
                "reg_ts": 1234567890,
                "deviceID": "DEVICE:FG=1",
                "bid": "browser-id",
                "doc_name": "",
                "module_name": "gpt4o",
            },
        )
        self.assertEqual(
            client.body["videoConfig"],
            {
                "modelName": "Seedance 2.0 Mini",
                "ratio": "16:9",
                "resolution": "720",
                "duration": 5,
                "isAudio": True,
                "aiType": 14201,
                "scene": "text_or_image",
                "textOrImage": {"image": ""},
            },
        )

    def test_submit_video_sse_preserves_uploaded_image_metadata(self):
        client = _SSEClient()

        result = video.submit_video_sse(
            client,
            "chat-1",
            "make a test video",
            resolution="720",
            duration=5,
            is_audio=True,
            image_url="aivideo/upload/cute.png",
            image_name="cute.png",
            image_size=12345,
        )

        self.assertTrue(result.success)
        self.assertEqual(
            client.body["messages"][0]["attachments"],
            [{
                "bos_url": "aivideo/upload/cute.png",
                "bosUrl": "aivideo/upload/cute.png",
                "doc_title": "cute.png",
                "doc_type": "png",
                "size": 12345,
                "flag": "upload",
                "type": "file",
                "status": 1,
            }],
        )

    def test_submit_video_sse_preserves_upstream_error_code_and_message(self):
        client = _SSEClient()
        with patch.object(
            client,
            "stream_sse",
            return_value=[{
                "event": "error",
                "logId": "log-2",
                "data": {"code": 212361, "msg": "spam user"},
            }],
        ):
            result = video.submit_video_sse(client, "chat-1", "test")

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, 212361)
        self.assertEqual(result.error, "upstream video error 212361: spam user")

    def test_download_http_failure_leaves_no_local_or_partial_file(self):
        client = SimpleNamespace(
            session=SimpleNamespace(stream=lambda *_args, **_kwargs: _StatusResponse())
        )
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "video.mp4"

            downloaded = video.download_video(client, "https://cdn.example.invalid/video.mp4", str(target))

            self.assertFalse(downloaded)
            self.assertFalse(target.exists())
            self.assertFalse(target.with_suffix(".mp4.part").exists())

    def test_generate_video_preserves_remote_success_when_local_download_fails(self):
        remote = video.VideoResult(
            True,
            video_url="https://cdn.oreateai.com/result.mp4",
            log_id="log-1",
            chat_id="chat-1",
        )
        with (
            patch.object(video, "create_chat", return_value="chat-1"),
            patch.object(video, "submit_video_sse", return_value=remote),
            patch.object(video, "download_video", return_value=False),
        ):
            result = video.generate_video(object(), "prompt", save_path="missing/video.mp4")

        self.assertTrue(result.success)
        self.assertFalse(result.downloaded)
        self.assertEqual(result.local_path, "")
        self.assertEqual(result.video_url, "https://cdn.oreateai.com/result.mp4")
        self.assertEqual(result.error, "remote video generated but local download failed")


if __name__ == "__main__":
    unittest.main()

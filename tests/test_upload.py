import unittest
from unittest.mock import patch

from modules import upload


class _Client:
    proxy = "socks5://127.0.0.1:1080"


class UploadTests(unittest.TestCase):
    def test_image_upload_inherits_selected_account_proxy(self):
        token = {
            "bucket": "bucket",
            "objectPath": "object/path.webp",
            "sessionkey": "session",
        }
        with (
            patch.object(upload, "get_upload_token", return_value=token),
            patch.object(
                upload,
                "upload_to_gcs",
                return_value="https://cdn.oreateai.com/object/path.webp",
            ) as upload_to_gcs,
        ):
            result = upload.upload_image_bytes(
                _Client(), b"image", filename="reference", ext="webp"
            )

        self.assertEqual(result, "object/path.webp")
        upload_to_gcs.assert_called_once_with(
            token,
            b"image",
            "image/webp",
            proxy="socks5://127.0.0.1:1080",
        )


if __name__ == "__main__":
    unittest.main()

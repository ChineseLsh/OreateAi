"""图片上传模块 — 获取 BOS token → GCS resumable upload → 返回 CDN URL"""

import logging
import mimetypes
import os
import ssl
from pathlib import Path

import httpx

from config import OREATE_API, CDN_URL, DEFAULT_PROXY

log = logging.getLogger(__name__)

_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE


def get_upload_token(client, filename: str, file_ext: str, file_size: int, source: str = "aiVideo") -> dict:
    resp = client.post(f"{OREATE_API}/convert/getuploadbostoken", json={
        "mFileList": [{"filename": filename, "fileExt": file_ext, "size": file_size}],
        "source": source,
    })
    key = f"{filename}.{file_ext}"
    info = resp["data"]["KeyList"][key]
    log.info(f"upload token: bucket={info['bucket']}, path={info['objectPath'][:60]}...")
    return info


def _make_gcs_client(proxy: str | None = DEFAULT_PROXY) -> httpx.Client:
    if proxy:
        transport = httpx.HTTPTransport(proxy=proxy, verify=_ssl_ctx, retries=2)
    else:
        transport = httpx.HTTPTransport(verify=_ssl_ctx, retries=2)
    return httpx.Client(timeout=httpx.Timeout(connect=15, read=120, write=120, pool=15),
                        transport=transport, verify=_ssl_ctx)


def upload_to_gcs(
    token_info: dict,
    file_data: bytes,
    content_type: str = "image/webp",
    proxy: str | None = DEFAULT_PROXY,
) -> str:
    bucket = token_info["bucket"]
    object_path = token_info["objectPath"]
    session_key = token_info["sessionkey"]
    init_url = f"https://storage.googleapis.com/upload/storage/v1/b/{bucket}/o"

    http = _make_gcs_client(proxy)
    try:
        init_resp = http.post(
            init_url,
            params={"uploadType": "resumable", "name": object_path},
            headers={
                "Authorization": f"Bearer {session_key}",
                "Content-Type": "application/json",
                "X-Upload-Content-Type": content_type,
                "X-Upload-Content-Length": str(len(file_data)),
            },
            json={},
        )
        upload_url = init_resp.headers.get("Location") or init_resp.headers.get("location")
        if not upload_url:
            log.warning("no upload URL: %s", init_resp.status_code)
            return ""

        log.info("uploading %s bytes...", len(file_data))
        put_resp = http.put(upload_url, content=file_data, headers={"Content-Type": content_type})
        if put_resp.status_code == 200:
            cdn_url = f"https://{CDN_URL.replace('https://','')}/{object_path}"
            log.info(f"upload done: {cdn_url}")
            return cdn_url
        log.warning("GCS PUT failed: %s", put_resp.status_code)
    except Exception as e:
        log.warning("GCS upload error: %s", type(e).__name__)
    finally:
        http.close()

    log.error("GCS upload failed")
    return ""


def upload_image(client, file_path: str, source: str = "aiVideo") -> str:
    """上传本地图片，返回 CDN URL"""
    p = Path(file_path)
    if not p.exists():
        raise FileNotFoundError(f"{file_path} not found")

    file_data = p.read_bytes()
    name = p.stem.replace(" ", "_")
    ext = p.suffix.lstrip(".")
    if ext in ("jpg", "jpeg"):
        ct = "image/jpeg"
    elif ext == "png":
        ct = "image/png"
    elif ext == "webp":
        ct = "image/webp"
    else:
        ct = mimetypes.guess_type(file_path)[0] or "application/octet-stream"

    token = get_upload_token(client, f"_upload_{name}", ext, len(file_data), source)
    return upload_to_gcs(token, file_data, ct, proxy=client.proxy)


def upload_image_bytes(client, data: bytes, filename: str = "upload", ext: str = "webp", source: str = "aiImage") -> str:
    """上传内存中的图片字节，返回 objectPath（给 videoConfig.textOrImage.image 用）"""
    ct = {"webp": "image/webp", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(ext, "image/webp")
    token = get_upload_token(client, f"_upload_{filename}", ext, len(data), source)
    cdn_url = upload_to_gcs(token, data, ct, proxy=client.proxy)
    if cdn_url:
        return token["objectPath"]
    return ""

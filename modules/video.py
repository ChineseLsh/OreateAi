"""视频生成模块 — SSE 流式提交 + 轮询 + 下载"""

import json
import logging
import time
from dataclasses import dataclass

import httpx

from config import OREATE_API, BIZ_API, CDN_URL
from core.client import OreateClient

log = logging.getLogger(__name__)


@dataclass
class VideoResult:
    success: bool
    video_url: str = ""
    log_id: str = ""
    chat_id: str = ""
    query_id: str = ""
    error: str = ""
    cost_points: int = 20


def get_scene_config(client: OreateClient) -> dict:
    resp = client.get(f"{OREATE_API}/aivideo/getsceneconfig")
    return resp["data"]


def get_model_config(client: OreateClient) -> dict:
    resp = client.get(f"{OREATE_API}/aivideo/getmodelconfigv3")
    return resp["data"]


def resolve_ai_type(client: OreateClient, model_name: str, duration: int = 5,
                    resolution: str = "480", is_audio: bool = True, has_image: bool = False) -> int:
    mc = get_model_config(client)
    for m in mc["models"]:
        if m["modelName"] != model_name:
            continue
        cost_key = "pointCostImage" if has_image else "pointCostImage"
        entries = m.get(cost_key, [])
        for e in entries:
            if e.get("duration") == duration and e.get("resolution") == resolution and e.get("audio", False) == is_audio:
                return e["aiType"]
        if entries:
            for e in entries:
                if e.get("duration") == duration and e.get("audio", False) == is_audio:
                    return e["aiType"]
            return entries[0]["aiType"]
    return 14199


def create_chat(client: OreateClient, chat_type: str = "aiVideo") -> str:
    client.set_referer("https://www.oreateai.com/home/chat/aiVideo")
    resp = client.post(f"{OREATE_API}/create/chat", json={
        "type": chat_type,
        "docId": "",
    })
    chat_id = resp["data"]["chatId"]
    log.info(f"chat created: {chat_id}")
    return chat_id


def get_remaining_points(client: OreateClient) -> int:
    resp = client.get(f"{BIZ_API}/point/getrestpoints")
    return resp["data"]["restPoint"]


def _build_attachments(image_url: str) -> list:
    if not image_url:
        return []
    ext = image_url.rsplit(".", 1)[-1] if "." in image_url else "webp"
    name = image_url.rsplit("/", 1)[-1].rsplit(".", 1)[0] if "/" in image_url else "_upload"
    return [{
        "bos_url": image_url,
        "bosUrl": image_url,
        "doc_title": name,
        "doc_type": ext,
        "size": 0,
        "flag": "upload",
        "type": "file",
        "status": 1,
    }]


def submit_video_sse(
    client: OreateClient,
    chat_id: str,
    prompt: str,
    model_name: str = "Seedance 2.0 Mini",
    ratio: str = "16:9",
    resolution: str = "480",
    duration: int = 5,
    is_audio: bool = True,
    ai_type: int | None = None,
    scene: str = "text_or_image",
    image_url: str = "",
) -> VideoResult:
    """提交视频生成任务，通过 SSE 流式接收结果"""

    if ai_type is None:
        ai_type = resolve_ai_type(client, model_name, duration, resolution, is_audio, has_image=bool(image_url))

    client.set_referer(f"https://www.oreateai.com/home/chat/aiVideo/{chat_id}")

    body = {
        "jt": "",
        "ua": client.session.headers.get("User-Agent", ""),
        "js_env": "h5",
        "extra": {
            "email": "",
            "vip": "0",
            "reg_ts": int(time.time()),
            "deviceID": client.ouid,
            "bid": "",
            "doc_name": "",
            "module_name": "gpt4o",
        },
        "clientType": "pc",
        "type": "chat",
        "chatType": "aiVideo",
        "chatTitle": "Unnamed Session",
        "focusId": chat_id,
        "chatId": chat_id,
        "from": "home",
        "messages": [
            {"role": "user", "content": prompt, "attachments": _build_attachments(image_url)}
        ],
        "videoConfig": {
            "modelName": model_name,
            "ratio": ratio,
            "resolution": resolution,
            "duration": duration,
            "isAudio": is_audio,
            "aiType": ai_type,
            "scene": scene,
            "textOrImage": {"image": image_url},
        },
        "isFirst": True,
    }

    log.info(f"submitting video: prompt='{prompt}', model={model_name}, {duration}s")
    if image_url:
        log.info(f"  with image: {image_url[-60:]}")

    import ssl as _ssl
    _ctx = _ssl.create_default_context()
    _ctx.check_hostname = False
    _ctx.verify_mode = _ssl.CERT_NONE
    from config import DEFAULT_PROXY
    cookies = {c.name: c.value for c in client.session.cookies.jar}
    sse_headers = {
        **client.session.headers,
        "Accept": "text/event-stream",
        "Content-Type": "application/json",
        "locale": "zh-CN",
        "client-type": "pc",
    }

    for attempt in range(2):
        use_proxy = (attempt == 0) and bool(DEFAULT_PROXY)
        label = "proxy" if use_proxy else "direct"
        transport = httpx.HTTPTransport(proxy=DEFAULT_PROXY if use_proxy else None, verify=_ctx, retries=1)
        sse_client = httpx.Client(
            headers=sse_headers, cookies=cookies,
            transport=transport, verify=_ctx, timeout=300,
        )
        try:
            with sse_client.stream("POST", f"{OREATE_API}/sse/stream", json=body) as resp:
                video_url_result = ""
                log_id = ""
                query_id = ""

                for line in resp.iter_lines():
                    if not line.startswith("data: "):
                        continue
                    payload = json.loads(line[6:])
                    event = payload.get("event", "")
                    log_id = payload.get("logId", log_id)

                    if event == "start":
                        log.info(f"[{label}] task started, logId={log_id}")
                    elif event == "ping":
                        pass
                    elif event == "generating":
                        data = payload.get("data", {})
                        query_id = data.get("equery_id", "")
                        result_html = data.get("result", "")
                        if "src=" in result_html:
                            start = result_html.find('src="') + 5
                            end = result_html.find('"', start)
                            video_url_result = result_html[start:end]
                            log.info(f"video ready: {video_url_result}")
                    elif event == "end":
                        log.info("stream ended")
                        break
                    elif event == "error":
                        err_msg = str(payload)
                        log.error(f"SSE error: {err_msg}")
                        return VideoResult(False, error=err_msg, log_id=log_id, chat_id=chat_id)

                if video_url_result:
                    return VideoResult(True, video_url=video_url_result, log_id=log_id,
                                       chat_id=chat_id, query_id=query_id)
                return VideoResult(False, error="no video url in stream", log_id=log_id, chat_id=chat_id)

        except Exception as e:
            log.warning(f"[{label}] SSE failed: {type(e).__name__}: {e}")
            if attempt == 0:
                log.info("retrying SSE without proxy...")
            else:
                return VideoResult(False, error=str(e), chat_id=chat_id)
        finally:
            sse_client.close()

    return VideoResult(False, error="SSE failed on all attempts", chat_id=chat_id)


def download_video(client: OreateClient, video_url: str, save_path: str) -> bool:
    import ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    from config import DEFAULT_PROXY
    transport = httpx.HTTPTransport(proxy=DEFAULT_PROXY, verify=ctx) if DEFAULT_PROXY else httpx.HTTPTransport(verify=ctx)
    dl = httpx.Client(transport=transport, timeout=120, follow_redirects=True)
    try:
        resp = dl.get(video_url)
        if resp.status_code in (200, 206):
            with open(save_path, "wb") as f:
                f.write(resp.content)
            log.info(f"downloaded {len(resp.content)} bytes -> {save_path}")
            return True
        log.error(f"download failed: {resp.status_code}")
        return False
    finally:
        dl.close()


def generate_video(
    client: OreateClient,
    prompt: str,
    save_path: str | None = None,
    **kwargs,
) -> VideoResult:
    """一键生成：建会话 → 提交 SSE → 下载"""
    chat_id = create_chat(client)
    result = submit_video_sse(client, chat_id, prompt, **kwargs)

    if result.success and save_path:
        download_video(client, result.video_url, save_path)

    return result

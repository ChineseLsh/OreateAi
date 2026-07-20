"""视频生成模块 — SSE 流式提交 + 轮询 + 下载"""

import logging
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

from config import BASE_URL, OREATE_API, BIZ_API
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
    cost_points: int = 0
    downloaded: bool = False
    local_path: str = ""


@dataclass(frozen=True)
class VideoSpec:
    model_name: str
    duration: int
    resolution: str
    is_audio: bool
    ai_type: int
    point: int
    scene: str


class _VideoSourceParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.src = ""

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "video" or self.src:
            return
        self.src = dict(attrs).get("src", "")


def get_scene_config(client: OreateClient) -> dict:
    resp = client.get(f"{OREATE_API}/aivideo/getsceneconfig")
    return resp["data"]


def get_model_config(client: OreateClient) -> dict:
    resp = client.get(f"{OREATE_API}/aivideo/getmodelconfigv3")
    return resp["data"]


def resolve_video_spec(
    client: OreateClient,
    model_name: str,
    duration: int = 5,
    resolution: str = "720",
    is_audio: bool = True,
    scene: str = "text_or_image",
) -> VideoSpec:
    mc = get_model_config(client)
    for m in mc["models"]:
        if m["modelName"] != model_name:
            continue
        cost_key = {
            "text_or_image": "pointCostImage",
            "frame_based": "pointCostImage",
            "reference": "pointCostReference",
            "motion": "pointCostMotion",
        }.get(scene)
        if not cost_key:
            raise ValueError(f"unsupported video scene: {scene}")
        entries = m.get(cost_key) or []
        for e in entries:
            if (
                int(e.get("duration", -1)) == int(duration)
                and str(e.get("resolution", "")) == str(resolution)
                and bool(e.get("audio", False)) is bool(is_audio)
            ):
                return VideoSpec(
                    model_name=model_name,
                    duration=int(duration),
                    resolution=str(resolution),
                    is_audio=bool(is_audio),
                    ai_type=int(e["aiType"]),
                    point=int(e["point"]),
                    scene=scene,
                )
        raise ValueError(
            f"unsupported video configuration: {model_name}/{duration}s/{resolution}/audio={is_audio}"
        )
    raise ValueError(f"unknown video model: {model_name}")


def resolve_ai_type(client: OreateClient, model_name: str, duration: int = 5,
                    resolution: str = "720", is_audio: bool = True, has_image: bool = False) -> int:
    return resolve_video_spec(
        client, model_name, duration, resolution, is_audio, scene="text_or_image"
    ).ai_type


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
    resolution: str = "720",
    duration: int = 5,
    is_audio: bool = True,
    ai_type: int | None = None,
    scene: str = "text_or_image",
    image_url: str = "",
) -> VideoResult:
    """提交视频生成任务，通过 SSE 流式接收结果"""

    spec = None
    if ai_type is None:
        spec = resolve_video_spec(
            client, model_name, duration, resolution, is_audio, scene=scene
        )
        ai_type = spec.ai_type

    userinfo = client.get(f"{BASE_URL}/oreate/user/getuserinfo")
    basic_info = userinfo.get("data", {}).get("basicInfo", {})
    vip_info = userinfo.get("data", {}).get("vipInfo", {})
    if userinfo.get("status", {}).get("code") != 0 or not basic_info.get("isLogin"):
        return VideoResult(False, error="video account is not logged in", chat_id=chat_id)

    client.set_referer(f"https://www.oreateai.com/home/chat/aiVideo/{chat_id}")
    client.prepare_risk_context()

    body = {
        "ua": client.session.headers.get("User-Agent", ""),
        "js_env": "h5",
        "extra": {
            "email": basic_info.get("email", ""),
            "vip": str(vip_info.get("vipType", 0)),
            "reg_ts": basic_info.get("createTime", 0),
            "deviceID": client.ouid,
            "bid": client.bid,
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

    try:
        events = client.stream_sse(f"{OREATE_API}/sse/stream", body)
    except Exception as exc:
        return VideoResult(False, error=str(exc), chat_id=chat_id)

    video_url_result = ""
    log_id = ""
    query_id = ""
    for payload in events:
        event = payload.get("event", "")
        log_id = str(payload.get("logId", log_id))
        if event == "start":
            log.info("video task started, logId=%s", log_id)
        elif event == "generating":
            data = payload.get("data", {})
            query_id = str(data.get("equery_id", ""))
            parser = _VideoSourceParser()
            parser.feed(str(data.get("result", "")))
            video_url_result = parser.src or video_url_result
        elif event == "error":
            return VideoResult(False, error=str(payload), log_id=log_id, chat_id=chat_id)
        elif event == "end":
            break

    if video_url_result:
        return VideoResult(
            True,
            video_url=video_url_result,
            log_id=log_id,
            chat_id=chat_id,
            query_id=query_id,
            cost_points=spec.point if spec else 0,
        )
    return VideoResult(False, error="no video url in stream", log_id=log_id, chat_id=chat_id)


def download_video(client: OreateClient, video_url: str, save_path: str) -> bool:
    target = Path(save_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".part")
    try:
        with client.session.stream("GET", video_url, timeout=120) as resp:
            if resp.status_code not in (200, 206):
                log.error("download failed: %s", resp.status_code)
                return False
            with temporary.open("wb") as output:
                for chunk in resp.iter_bytes():
                    output.write(chunk)
        temporary.replace(target)
        log.info("downloaded video -> %s", target)
        return True
    except Exception as exc:
        log.error("download failed: %s", type(exc).__name__)
        return False
    finally:
        temporary.unlink(missing_ok=True)


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
        result.downloaded = download_video(client, result.video_url, save_path)
        if result.downloaded:
            result.local_path = save_path
        else:
            result.error = "remote video generated but local download failed"

    return result

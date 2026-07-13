"""FastAPI 服务 — 账号池 + 视频历史 + 调度"""

import json
import logging
import sys
import uuid
from pathlib import Path

import fastapi
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.client import OreateClient
from core.db import (
    init_db, migrate_jsonl, list_accounts, list_videos, get_stats,
    save_video, update_video, update_account_points, set_account_status,
    get_account,
)
from core.pool import (
    auto_acquire, release_account, refresh_points,
    register_and_add_to_pool, restore_session,
)
from modules.register import register
from modules.login import daily_checkin_all
from modules.email_provider import LinshiyouxiangProvider
from modules.fission import chain_fission, seed_and_fission
from modules.video import generate_video, get_remaining_points
from modules.upload import upload_image_bytes as upload_image

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("api")

VIDEOS_DIR = Path(__file__).resolve().parent.parent / "videos"
VIDEOS_DIR.mkdir(exist_ok=True)
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title="ThreadAI")
tasks_status: dict[str, dict] = {}


@app.on_event("startup")
def startup():
    init_db()
    n = migrate_jsonl()
    if n:
        log.info(f"migrated {n} accounts from jsonl")


# --- Models ---

class FissionReq(BaseModel):
    invite_code: str = ""
    depth: int = 3

class VideoReq(BaseModel):
    prompt: str
    model_name: str = "Seedance 2.0 Mini"
    duration: int = 5
    ratio: str = "16:9"
    image_url: str = ""


# --- Pages ---

@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


# --- API: Stats ---

@app.get("/api/stats")
async def api_stats():
    return get_stats()


# --- API: Accounts ---

@app.get("/api/accounts")
async def api_accounts():
    accs = list_accounts()
    for a in accs:
        a.pop("cookies", None)
    return accs


@app.post("/api/register")
async def api_register(bg: BackgroundTasks):
    tid = uuid.uuid4().hex[:8]
    tasks_status[tid] = {"status": "running", "type": "register"}
    bg.add_task(_bg_register, tid)
    return {"task_id": tid, "status": "started"}


def _bg_register(tid: str):
    try:
        acc = register_and_add_to_pool()
        if acc:
            tasks_status[tid] = {"status": "done", "type": "register", "result": {
                "success": True, "email": acc["email"], "points": acc["points"],
            }}
        else:
            tasks_status[tid] = {"status": "error", "type": "register", "result": {"error": "register failed"}}
    except Exception as e:
        tasks_status[tid] = {"status": "error", "type": "register", "result": {"error": str(e)}}


@app.post("/api/accounts/{email}/refresh")
async def api_refresh_account(email: str):
    acc = get_account(email)
    if not acc:
        raise HTTPException(404, "account not found")
    client = OreateClient()
    result = restore_session(client, acc)
    if result is True:
        pts = refresh_points(client, email)
        set_account_status(email, "active")
        client.close()
        return {"email": email, "points": pts, "status": "active"}
    if result is False:
        set_account_status(email, "expired")
        client.close()
        return {"email": email, "points": acc["points"], "status": "expired"}
    # network error — don't change status
    client.close()
    return {"email": email, "points": acc["points"], "status": acc.get("status", "unknown"), "note": "network error, status unchanged"}


# --- API: Daily Checkin ---

@app.post("/api/checkin")
async def api_checkin(bg: BackgroundTasks):
    tid = uuid.uuid4().hex[:8]
    tasks_status[tid] = {"status": "running", "type": "checkin"}
    bg.add_task(_bg_checkin, tid)
    return {"task_id": tid, "status": "started"}


def _bg_checkin(tid: str):
    try:
        results = daily_checkin_all()
        ok = [r for r in results if r.get("ok")]
        total_pts = sum(r.get("points", 0) for r in ok)
        tasks_status[tid] = {"status": "done", "type": "checkin", "result": {
            "total": len(results), "success": len(ok), "total_points": total_pts,
            "accounts": [{"email": r["email"], "points": r.get("points", 0)} for r in ok],
        }}
    except Exception as e:
        tasks_status[tid] = {"status": "error", "type": "checkin", "result": {"error": str(e)}}


# --- API: Fission ---

@app.post("/api/fission")
async def api_fission(req: FissionReq, bg: BackgroundTasks):
    tid = uuid.uuid4().hex[:8]
    tasks_status[tid] = {"status": "running", "type": "fission"}
    bg.add_task(_bg_fission, tid, req.invite_code, req.depth)
    return {"task_id": tid, "status": "started"}


def _bg_fission(tid: str, invite_code: str, depth: int):
    try:
        if invite_code:
            results = chain_fission(invite_code, depth=depth)
        else:
            results = seed_and_fission(depth=depth)
        ok = [r for r in results if r.success]
        tasks_status[tid] = {"status": "done", "type": "fission", "result": {
            "total": len(results), "success": len(ok),
            "accounts": [{"email": r.email, "points": r.points} for r in ok],
        }}
    except Exception as e:
        tasks_status[tid] = {"status": "error", "type": "fission", "result": {"error": str(e)}}


# --- API: Video ---

@app.post("/api/upload")
def api_upload(
    file: fastapi.UploadFile = fastapi.File(...),
    filename: str = fastapi.Form("image.webp"),
):
    result = auto_acquire(min_points=0)
    if not result:
        log.info("upload: pool empty, registering new account...")
        acc = register_and_add_to_pool()
        if not acc:
            return JSONResponse(status_code=500, content={"error": "no account available, register failed"})
        result = auto_acquire(min_points=0)
    if not result:
        return JSONResponse(status_code=500, content={"error": "no account available"})
    client, account = result
    try:
        data = file.file.read()
        fname = filename or file.filename or "image.webp"
        ext = fname.rsplit(".", 1)[-1] if "." in fname else "webp"
        name = fname.rsplit(".", 1)[0] if "." in fname else "image"
        object_path = upload_image(client, data, name, ext)
        if not object_path:
            return JSONResponse(status_code=500, content={"error": "CDN upload failed"})
        cdn_preview = f"https://cdn.oreateai.com/{object_path}"
        return {"url": object_path, "preview": cdn_preview}
    except Exception as e:
        log.exception("upload failed")
        return JSONResponse(status_code=500, content={"error": str(e)})
    finally:
        release_account(client, account["email"])


@app.get("/api/videos")
async def api_videos():
    return list_videos(limit=100)


@app.post("/api/video")
async def api_video(req: VideoReq, bg: BackgroundTasks):
    tid = uuid.uuid4().hex[:8]
    tasks_status[tid] = {"status": "running", "type": "video", "prompt": req.prompt}
    bg.add_task(_bg_video, tid, req)
    return {"task_id": tid, "status": "started"}


def _bg_video(tid: str, req: VideoReq):
    client = None
    email = None
    try:
        result = auto_acquire(min_points=20)

        if not result:
            log.info("pool empty, registering new account...")
            acc = register_and_add_to_pool()
            if not acc:
                tasks_status[tid] = {"status": "error", "type": "video", "result": {"error": "no accounts and register failed"}}
                return
            result = auto_acquire(min_points=20)
            if not result:
                tasks_status[tid] = {"status": "error", "type": "video", "result": {"error": "freshly registered account not usable"}}
                return

        client, account = result
        email = account["email"]
        log.info(f"using account: {email} (pts={account['points']})")

        save_video(tid, email, req.prompt, req.model_name, req.duration, req.ratio, status="generating")

        pts_before = get_remaining_points(client)
        save_path = str(VIDEOS_DIR / f"{tid}.mp4")
        video = generate_video(
            client, req.prompt,
            save_path=save_path,
            model_name=req.model_name,
            duration=req.duration,
            ratio=req.ratio,
            image_url=req.image_url,
        )

        if video.success:
            new_pts = get_remaining_points(client)
            actual_cost = max(pts_before - new_pts, 0)
            update_account_points(email, new_pts)
            if new_pts < 20:
                set_account_status(email, "exhausted")

            update_video(tid, video_url=video.video_url, local_path=f"/videos/{tid}.mp4",
                         log_id=video.log_id, points_cost=actual_cost, status="done")
            tasks_status[tid] = {"status": "done", "type": "video", "result": {
                "video_url": video.video_url,
                "local_path": f"/videos/{tid}.mp4",
                "log_id": video.log_id,
                "account": email,
                "points_after": new_pts,
            }}
        else:
            update_video(tid, status="error")
            tasks_status[tid] = {"status": "error", "type": "video", "result": {"error": video.error}}

    except Exception as e:
        log.exception("video task failed")
        tasks_status[tid] = {"status": "error", "type": "video", "result": {"error": str(e)}}
        try:
            update_video(tid, status="error")
        except Exception:
            pass
    finally:
        if client and email:
            release_account(client, email)


# --- API: Task polling ---

@app.get("/api/task/{task_id}")
async def api_task(task_id: str):
    if task_id not in tasks_status:
        raise HTTPException(404, "task not found")
    return tasks_status[task_id]


# --- Static ---

app.mount("/videos", StaticFiles(directory=str(VIDEOS_DIR)), name="videos")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

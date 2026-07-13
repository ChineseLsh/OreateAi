"""登录模块 — emaillogin 接口 + 批量每日签到"""

import logging
from config import PASSPORT_API, BIZ_API
from core.client import OreateClient
from core.crypto import encrypt_password
from core.fingerprint import generate_jt
from core.db import list_accounts, update_account_points, update_account_cookies, set_account_status
from modules.register import get_ticket

log = logging.getLogger(__name__)


def email_login(client: OreateClient, email: str, password: str) -> dict:
    ticket_id, pk = get_ticket(client)
    encrypted_pwd = encrypt_password(password, pk)

    resp = client.post(f"{PASSPORT_API}/emaillogin", json={
        "fr": "main",
        "ticketID": ticket_id,
        "email": email,
        "password": encrypted_pwd,
        "jt": generate_jt(),
    })

    data = resp.get("data", {})
    if data.get("isLogin"):
        log.info(f"login OK: {email}")
        return {"success": True, "email": email}

    log.warning(f"login failed: {resp}")
    return {"success": False, "email": email, "error": resp.get("status", {}).get("msg", "unknown")}


def daily_checkin_all() -> list[dict]:
    """批量登录所有账号触发每日签到，返回 [{email, pts_before, pts_after, ok}]"""
    accs = list_accounts()
    results = []

    for acc in accs:
        email = acc["email"]
        password = acc.get("password", "")
        if not password:
            continue

        client = OreateClient()
        try:
            result = email_login(client, email, password)
            if not result.get("success"):
                results.append({"email": email, "ok": False, "error": result.get("error", "")})
                continue

            cookies = {c.name: c.value for c in client.session.cookies.jar}
            update_account_cookies(email, cookies)

            resp = client.get(f"{BIZ_API}/point/getrestpoints")
            pts = resp["data"]["restPoint"]
            update_account_points(email, pts)
            set_account_status(email, "active")

            results.append({"email": email, "ok": True, "points": pts})
            log.info(f"checkin: {email} -> {pts} pts")

        except Exception as e:
            results.append({"email": email, "ok": False, "error": str(e)})
            log.warning(f"checkin error {email}: {e}")
        finally:
            client.close()

    ok = sum(1 for r in results if r.get("ok"))
    total_pts = sum(r.get("points", 0) for r in results if r.get("ok"))
    log.info(f"checkin done: {ok}/{len(results)} accounts, total {total_pts} pts")
    return results

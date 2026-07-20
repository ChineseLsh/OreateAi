"""登录模块 — emaillogin 接口 + 批量每日签到"""

import json
import logging
from config import PASSPORT_API, BIZ_API, OREATE_API
from core.client import OreateClient
from core.crypto import encrypt_password
from core.db import (
    list_accounts,
    set_account_status,
    try_lock_account,
    unlock_account,
    update_account_cookies,
    update_account_points,
)
from modules.register import get_ticket

log = logging.getLogger(__name__)


def email_login(client: OreateClient, email: str, password: str) -> dict:
    ticket_id, pk = get_ticket(client)
    encrypted_pwd = encrypt_password(password, pk)

    resp = client.risk_post(f"{PASSPORT_API}/emaillogin", {
        "fr": "main",
        "ticketID": ticket_id,
        "email": email,
        "password": encrypted_pwd,
    })

    data = resp.get("data", {})
    if data.get("isLogin"):
        log.info(f"login OK: {email}")
        return {"success": True, "email": email}

    log.warning(f"login failed: {resp}")
    return {"success": False, "email": email, "error": resp.get("status", {}).get("msg", "unknown")}


def daily_checkin_all() -> list[dict]:
    """Restore each account, claim first-use points, and report the delta."""
    from core.pool import restore_session

    accs = list_accounts()
    results = []

    for acc in accs:
        email = acc["email"]
        if not try_lock_account(email):
            results.append({"email": email, "ok": False, "status": "busy", "error": "account is busy"})
            continue

        client = None
        try:
            cookies = json.loads(acc.get("cookies", "{}") or "{}")
            client = OreateClient(browser=True, cookies=cookies)
            restored = restore_session(client, acc)
            if restored is not True:
                set_account_status(email, "expired" if restored is False else acc.get("status", "active"))
                results.append({"email": email, "ok": False, "status": "login_failed", "error": "session restore failed"})
                continue

            before = client.get(f"{BIZ_API}/point/getrestpoints")["data"]["restPoint"]
            claim = client.get(f"{OREATE_API}/account/getfirstusepoint")
            after = client.get(f"{BIZ_API}/point/getrestpoints")["data"]["restPoint"]
            earned = max(after - before, 0)
            claim_code = claim.get("status", {}).get("code", -1)
            status = "claimed" if earned > 0 else "already_claimed" if claim_code == 0 else "claim_failed"
            ok = claim_code == 0

            update_account_cookies(email, client.export_cookies())
            update_account_points(email, after)
            set_account_status(email, "active")

            results.append({
                "email": email,
                "ok": ok,
                "status": status,
                "points_before": before,
                "points_after": after,
                "earned": earned,
                "error": "" if ok else claim.get("status", {}).get("msg", "claim failed"),
            })
            log.info("checkin: %s %s +%s", email, status, earned)

        except Exception as e:
            results.append({"email": email, "ok": False, "status": "error", "error": str(e)})
            log.warning(f"checkin error {email}: {e}")
        finally:
            try:
                if client:
                    try:
                        client.close()
                    except Exception as exc:
                        log.warning(
                            "checkin browser close failed: %s", type(exc).__name__
                        )
            finally:
                unlock_account(email)

    ok = sum(1 for r in results if r.get("ok"))
    earned = sum(r.get("earned", 0) for r in results if r.get("ok"))
    log.info(f"checkin done: {ok}/{len(results)} accounts, earned {earned} pts")
    return results

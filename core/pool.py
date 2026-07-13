"""账号池调度 — 获取/释放/刷新积分 + cookie 恢复"""

import json
import logging
import time

from core.client import OreateClient
from core.db import (
    get_best_account, lock_account, unlock_account,
    update_account_points, update_account_cookies,
    set_account_status, save_account, count_active_accounts,
)
from config import BIZ_API

log = logging.getLogger(__name__)


def restore_session(client: OreateClient, account: dict) -> bool | None:
    """恢复 session：先试 cookie，失败则 emaillogin。返回 True/False/None"""
    # 1. 尝试 cookie 恢复
    cookies = json.loads(account.get("cookies", "{}"))
    if cookies:
        for name, value in cookies.items():
            client.session.cookies.set(name, value, domain=".oreateai.com")
        try:
            resp = client.get(f"{BIZ_API}/point/getrestpoints")
            code = resp.get("status", {}).get("code", -1)
            if code == 0:
                pts = resp["data"]["restPoint"]
                update_account_points(account["email"], pts)
                log.info(f"cookie restored: {account['email']} pts={pts}")
                return True
        except Exception as e:
            log.warning(f"cookie restore error: {type(e).__name__}")

    # 2. Cookie 失败，尝试 emaillogin
    email = account.get("email", "")
    password = account.get("password", "")
    if not password:
        log.info(f"no password for {email}, can't login")
        return False

    try:
        from modules.login import email_login
        result = email_login(client, email, password)
        if result.get("success"):
            new_cookies = {c.name: c.value for c in client.session.cookies.jar}
            update_account_cookies(email, new_cookies)
            resp = client.get(f"{BIZ_API}/point/getrestpoints")
            if resp.get("status", {}).get("code") == 0:
                pts = resp["data"]["restPoint"]
                update_account_points(email, pts)
                set_account_status(email, "active")
                log.info(f"login restored: {email} pts={pts}")
                return True
        log.info(f"login failed for {email}")
        return False
    except Exception as e:
        log.warning(f"login error for {email}: {type(e).__name__}")
        return None


def refresh_points(client: OreateClient, email: str) -> int:
    try:
        resp = client.get(f"{BIZ_API}/point/getrestpoints")
        pts = resp["data"]["restPoint"]
        update_account_points(email, pts)
        if pts < 20:
            set_account_status(email, "exhausted")
        return pts
    except Exception:
        return -1


def acquire_account(min_points: int = 20) -> tuple[OreateClient, dict] | None:
    account = get_best_account(min_points)
    if not account:
        log.warning("no available account in pool")
        return None

    lock_account(account["email"])

    for attempt in range(2):
        client = OreateClient()
        result = restore_session(client, account)

        if result is True:
            account["points"] = _fresh_points(client, account)
            if account["points"] >= min_points:
                log.info(f"acquired: {account['email']} pts={account['points']}")
                return client, account
            log.info(f"{account['email']} pts={account['points']} < {min_points}, skip")
            break

        if result is False:
            log.info(f"session expired: {account['email']}")
            set_account_status(account["email"], "expired")
            break

        # result is None = network error, retry once
        log.info(f"retry {attempt+1} for {account['email']}...")
        client.close()
        time.sleep(1)

    unlock_account(account["email"])
    try:
        client.close()
    except Exception:
        pass
    return None


def _fresh_points(client, account):
    try:
        resp = client.get(f"{BIZ_API}/point/getrestpoints")
        pts = resp["data"]["restPoint"]
        update_account_points(account["email"], pts)
        return pts
    except Exception:
        return account.get("points", 0)


def release_account(client: OreateClient, email: str):
    try:
        pts = refresh_points(client, email)
        log.info(f"released: {email} pts={pts}")
    except Exception:
        pass
    unlock_account(email)
    client.close()


def auto_acquire(min_points: int = 20) -> tuple[OreateClient, dict] | None:
    attempts = count_active_accounts(min_points)
    for _ in range(max(attempts, 1)):
        result = acquire_account(min_points)
        if result:
            return result
    return None


def register_and_add_to_pool() -> dict | None:
    from modules.email_provider import LinshiyouxiangProvider
    from modules.register import register

    provider = LinshiyouxiangProvider()
    try:
        provider.create()
        with OreateClient() as client:
            result = register(client, provider)
            if not result.success:
                log.error(f"pool register failed: {result.error}")
                return None
            cookies = {c.name: c.value for c in client.session.cookies.jar}
            save_account(
                result.email, result.password, result.points,
                result.invite_code, cookies,
            )
            log.info(f"new account added to pool: {result.email} pts={result.points}")
            return {
                "email": result.email,
                "password": result.password,
                "points": result.points,
                "invite_code": result.invite_code,
                "cookies": json.dumps(cookies),
                "status": "active",
            }
    finally:
        provider.close()

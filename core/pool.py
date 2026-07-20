"""账号池调度 — 获取/释放/刷新积分 + cookie 恢复"""

import json
import logging
import time

from core.client import OreateClient
from core.db import (
    acquire_best_account, unlock_account,
    update_account_points, update_account_cookies,
    set_account_status, save_account, count_active_accounts,
)
from config import BASE_URL, BIZ_API

log = logging.getLogger(__name__)


def restore_session(client: OreateClient, account: dict) -> bool | None:
    """恢复 session：先试 cookie，失败则 emaillogin。返回 True/False/None"""
    try:
        resp = client.get(f"{BASE_URL}/oreate/user/getuserinfo")
        basic_info = resp.get("data", {}).get("basicInfo", {})
        if resp.get("status", {}).get("code") == 0 and basic_info.get("isLogin"):
            pts = client.get(f"{BIZ_API}/point/getrestpoints")["data"]["restPoint"]
            update_account_points(account["email"], pts)
            update_account_cookies(account["email"], client.export_cookies())
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
            verify = client.get(f"{BASE_URL}/oreate/user/getuserinfo")
            basic_info = verify.get("data", {}).get("basicInfo", {})
            if verify.get("status", {}).get("code") != 0 or not basic_info.get("isLogin"):
                return False
            new_cookies = client.export_cookies()
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
        set_account_status(email, "exhausted" if pts <= 0 else "active")
        return pts
    except Exception:
        return -1


def acquire_account(min_points: int = 20) -> tuple[OreateClient, dict] | None:
    account = acquire_best_account(min_points)
    if not account:
        log.warning("no available account in pool")
        return None

    client = None
    acquired = False
    try:
        for attempt in range(2):
            try:
                cookies = json.loads(account.get("cookies", "{}") or "{}")
            except (TypeError, ValueError):
                log.warning(f"invalid saved cookies for {account['email']}")
                cookies = {}

            try:
                client = OreateClient(browser=True, cookies=cookies)
                result = restore_session(client, account)
            except Exception as exc:
                log.warning(
                    f"session startup error for {account['email']}: "
                    f"{type(exc).__name__}"
                )
                result = None

            if result is True:
                account["points"] = _fresh_points(client, account)
                if account["points"] >= min_points:
                    acquired = True
                    log.info(
                        f"acquired: {account['email']} pts={account['points']}"
                    )
                    return client, account
                log.info(
                    f"{account['email']} pts={account['points']} < "
                    f"{min_points}, skip"
                )
                set_account_status(
                    account["email"],
                    "exhausted" if account["points"] <= 0 else "active",
                )
                break

            if result is False:
                log.info(f"session expired: {account['email']}")
                set_account_status(account["email"], "expired")
                break

            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass
                client = None
            if attempt == 0:
                log.info(f"retry 1 for {account['email']}...")
                time.sleep(1)
        return None
    finally:
        if not acquired:
            unlock_account(account["email"])
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass


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


def register_and_add_to_pool(provider_name: str | None = None) -> dict | None:
    from modules.email_provider import build_email_provider
    from modules.register import register

    provider = build_email_provider(provider_name)
    try:
        provider.create()
        with OreateClient(browser=True) as client:
            result = register(client, provider)
            if not result.success:
                log.error(f"pool register failed: {result.error}")
                return None
            cookies = client.export_cookies()
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

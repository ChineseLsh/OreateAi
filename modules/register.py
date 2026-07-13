"""注册模块 — 完整注册流程 getticket → emailsignupin → poll verify → confirm"""

import time
import logging

from config import PASSPORT_API, BASE_URL
from core.client import OreateClient
from core.crypto import encrypt_password
from core.fingerprint import generate_jt, random_password

log = logging.getLogger(__name__)


class RegisterResult:
    def __init__(self, email: str, password: str, success: bool, points: int = 0, invite_code: str = "", error: str = ""):
        self.email = email
        self.password = password
        self.success = success
        self.points = points
        self.invite_code = invite_code
        self.error = error

    def __repr__(self):
        if self.success:
            return f"<RegisterOK email={self.email} points={self.points} invite={self.invite_code}>"
        return f"<RegisterFail email={self.email} error={self.error}>"


def get_ticket(client: OreateClient) -> tuple[str, str]:
    data = client.get(f"{PASSPORT_API}/getticket")
    ticket = data["data"]["ticketID"]
    pk = data["data"]["pk"]
    log.info(f"ticket={ticket[:16]}...")
    return ticket, pk


def email_signup(
    client: OreateClient,
    email: str,
    encrypted_pwd: str,
    ticket_id: str,
    fr: str = "main",
    fission_code: str = "",
    invite_code: str = "",
) -> dict:
    body = {
        "fr": fr,
        "email": email,
        "ticketID": ticket_id,
        "password": encrypted_pwd,
        "fissionCode": fission_code,
        "inviteCode": invite_code,
        "jt": generate_jt(),
    }
    if fr != "main":
        body["plat"] = "wap"
    resp = client.post(f"{PASSPORT_API}/emailsignupin", json=body)
    log.info(f"signup resp: code={resp['status']['code']}, isRegister={resp['data'].get('isRegister')}")
    return resp


def poll_email_verified(
    client: OreateClient,
    email: str,
    ticket_id: str,
    encrypted_pwd: str,
    fr: str = "",
    timeout: int = 120,
    interval: int = 3,
) -> bool:
    body = {
        "email": email,
        "ticketID": ticket_id,
        "password": encrypted_pwd,
        "fr": fr,
    }
    start = time.time()
    while time.time() - start < timeout:
        resp = client.post(f"{PASSPORT_API}/checkemailverified", json=body)
        d = resp.get("data", {})
        if d.get("isLogin"):
            log.info("邮箱验证通过 ✓")
            return True
        if not d.get("isNeedRetry"):
            log.warning(f"不再需要重试: {d}")
            return False
        time.sleep(interval)
    log.error("邮箱验证超时")
    return False


def email_register_confirm(
    client: OreateClient,
    email: str,
    token_id: str,
    fr: str = "main",
    fission_code: str = "",
    invite_code: str = "",
) -> dict:
    body = {
        "email": email,
        "tokenID": token_id,
        "plat": "pc",
        "fr": fr,
        "fissionCode": fission_code,
        "inviteCode": invite_code,
        "jt": generate_jt(),
    }
    resp = client.post(f"{PASSPORT_API}/emailregisterconfirm", json=body)
    log.info(f"confirm resp: isLogin={resp['data'].get('isLogin')}")
    return resp


def get_invite_code(client: OreateClient) -> str:
    resp = client.get(f"{BASE_URL}/oreate/activity/getinviteurl")
    code = resp["data"]["inviteCode"]
    log.info(f"invite code: {code}")
    return code


def get_points(client: OreateClient) -> int:
    resp = client.get(f"{BASE_URL}/bizapi/point/getrestpoints")
    pts = resp["data"]["restPoint"]
    log.info(f"当前积分: {pts}")
    return pts


def register(
    client: OreateClient,
    email_provider,
    password: str | None = None,
    fr: str = "main",
    invite_code: str = "",
) -> RegisterResult:
    """完整注册流程"""
    password = password or random_password()
    email = email_provider.address

    try:
        ticket_id, pk = get_ticket(client)
        encrypted_pwd = encrypt_password(password, pk)
        signup_resp = email_signup(client, email, encrypted_pwd, ticket_id, fr=fr, invite_code=invite_code)

        if signup_resp["status"]["code"] != 0:
            return RegisterResult(email, password, False, error=signup_resp["status"]["msg"])

        if not signup_resp["data"].get("isRegister"):
            return RegisterResult(email, password, False, error="账号已存在或注册被拒")

        log.info("等待邮箱验证...")
        email_addr, token_id = email_provider.wait_for_verification_link()

        confirm_ref = f"{BASE_URL}/home/index/zh?email={email}"
        if fr != "main":
            confirm_ref += f"&fr={fr}&inviteCode={invite_code}"
        confirm_ref += f"&tokenID={token_id}"
        client.set_referer(confirm_ref)

        confirm_resp = email_register_confirm(
            client, email, token_id, fr=fr, invite_code=invite_code,
        )
        if not confirm_resp["data"].get("isLogin"):
            return RegisterResult(email, password, False, error="confirm 失败")

        time.sleep(2)
        points = get_points(client)
        inv_code = get_invite_code(client)

        cookies = {c.name: c.value for c in client.session.cookies.jar}
        try:
            from core.db import save_account
            save_account(email, password, points, inv_code, cookies)
        except Exception:
            pass

        return RegisterResult(email, password, True, points=points, invite_code=inv_code)

    except Exception as e:
        log.exception("注册异常")
        return RegisterResult(email, password, False, error=str(e))

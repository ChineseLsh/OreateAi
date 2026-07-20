"""裂变模块 — A 号注册 → 拿邀请码 → B 号通过邀请链接注册（每号换 IP）"""

import json
import logging
import time

from core.client import OreateClient
from modules.register import register, RegisterResult
from modules.email_provider import build_email_provider

log = logging.getLogger(__name__)

try:
    from core.clash import NodeRotator, get_current_ip
    HAS_CLASH = True
except Exception:
    HAS_CLASH = False

ACCOUNTS_FILE = "accounts.jsonl"


def _save_result(result: RegisterResult):
    with open(ACCOUNTS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps({
            "email": result.email,
            "password": result.password,
            "points": result.points,
            "invite_code": result.invite_code,
            "ts": int(time.time()),
        }, ensure_ascii=False) + "\n")


def _retry_create_provider(provider_name: str | None = None, max_retries: int = 3):
    for i in range(max_retries):
        try:
            provider = build_email_provider(provider_name)
            provider.create()
            return provider
        except Exception as e:
            log.warning(f"provider create attempt {i+1} failed: {e}")
            time.sleep(3)
    raise RuntimeError("failed to create email provider after retries")


def fission_register(
    parent_invite_code: str,
    provider_name: str | None = None,
) -> RegisterResult:
    provider = _retry_create_provider(provider_name)
    try:
        with OreateClient(browser=True) as client:
            client.set_referer(
                f"https://www.oreateai.com/userlogin/fissionregister/zh"
                f"?fr=inviteFriend&inviteCode={parent_invite_code}"
            )
            return register(
                client, provider,
                fr="inviteFriend",
                invite_code=parent_invite_code,
            )
    finally:
        provider.close()


def chain_fission(
    seed_invite_code: str,
    depth: int = 3,
    rotate_ip: bool = True,
    provider_name: str | None = None,
    _rotator=None,
) -> list[RegisterResult]:
    rotator = _rotator
    own_rotator = False
    if rotator is None and rotate_ip and HAS_CLASH:
        rotator = NodeRotator()
        own_rotator = True

    results = []
    current_code = seed_invite_code

    try:
        for i in range(depth):
            if rotator:
                node = rotator.next()
                time.sleep(5)
                log.info(f"=== fission #{i+1}/{depth} node={node} ===")
            else:
                log.info(f"=== fission #{i+1}/{depth} ===")

            result = fission_register(current_code, provider_name)
            results.append(result)

            if not result.success:
                log.error(f"fission stopped: {result.error}")
                break

            _save_result(result)
            current_code = result.invite_code
            log.info(f"#{i+1} OK: {result.email} pts={result.points}")
    finally:
        if own_rotator and rotator:
            rotator.restore()

    return results


def seed_and_fission(
    depth: int = 3,
    rotate_ip: bool = True,
    provider_name: str | None = None,
) -> list[RegisterResult]:
    """从零开始：先注册种子号，再链式裂变（每号换 IP）"""
    rotator = NodeRotator() if (rotate_ip and HAS_CLASH) else None

    try:
        if rotator:
            node = rotator.next()
            time.sleep(5)
            log.info(f"=== seed account, node={node} ===")
        else:
            log.info("=== registering seed account ===")

        provider = _retry_create_provider(provider_name)
        try:
            with OreateClient(browser=True) as client:
                seed = register(client, provider)
        finally:
            provider.close()

        if not seed.success:
            log.error(f"seed failed: {seed.error}")
            return [seed]

        _save_result(seed)
        log.info(f"seed OK: {seed.email} pts={seed.points} invite={seed.invite_code[:24]}...")

        children = chain_fission(
            seed.invite_code,
            depth=depth,
            rotate_ip=rotate_ip,
            provider_name=provider_name,
            _rotator=rotator,
        )
        return [seed] + children
    finally:
        if rotator:
            rotator.restore()

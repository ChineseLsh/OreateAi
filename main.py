"""ThreadAI command-line entry point."""

import argparse
import json
import logging

from config import DEFAULT_PROXY
from core.client import OreateClient
from core.db import (
    acquire_best_account,
    get_account,
    init_db,
    list_accounts,
    set_account_status,
    try_lock_account,
    unlock_account,
)
from core.pool import release_account, restore_session
from modules.email_provider import build_email_provider
from modules.fission import chain_fission
from modules.login import daily_checkin_all
from modules.register import register
from modules.video import generate_video, get_remaining_points, resolve_video_spec

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("main")


def _effective_proxy(args) -> str | None:
    proxy = getattr(args, "proxy", None)
    return DEFAULT_PROXY if proxy is None else proxy or None


def _account_from_selector(selector: str) -> dict:
    if selector.isdigit():
        accounts = list_accounts()
        index = int(selector)
        if index >= len(accounts):
            raise ValueError(f"account index out of range: {index}")
        return accounts[index]
    account = get_account(selector)
    if not account:
        raise ValueError(f"account not found: {selector}")
    return account


def _account_cookies(account: dict) -> dict[str, str]:
    cookies = account.get("cookies") or "{}"
    if isinstance(cookies, str):
        cookies = json.loads(cookies)
    if not isinstance(cookies, dict):
        raise ValueError("stored account cookies are invalid")
    return {str(name): str(value) for name, value in cookies.items()}


def _acquire_cli_account(selector: str | None, proxy: str | None) -> tuple[OreateClient, dict]:
    if selector:
        account = _account_from_selector(selector)
        if not try_lock_account(account["email"]):
            raise RuntimeError(f"account is busy: {account['email']}")
    else:
        account = acquire_best_account(min_points=0)
        if not account:
            raise RuntimeError("no active account available")

    client = None
    try:
        client = OreateClient(
            proxy=proxy,
            browser=True,
            cookies=_account_cookies(account),
        )
        restored = restore_session(client, account)
        if restored is not True:
            if restored is False:
                set_account_status(account["email"], "expired")
            raise RuntimeError(f"session restore failed: {account['email']}")
        set_account_status(account["email"], "active")
        return client, account
    except Exception:
        try:
            if client:
                try:
                    client.close()
                except Exception:
                    pass
        finally:
            unlock_account(account["email"])
        raise


def cmd_register(args):
    """Register one account with the configured mailbox and browser runtime."""
    provider = build_email_provider(args.provider, address=args.email or "")
    try:
        provider.create()
        with OreateClient(proxy=_effective_proxy(args), browser=True) as client:
            result = register(client, provider, password=args.password)
    finally:
        provider.close()

    print(f"\n{'=' * 50}")
    print(result)
    if not result.success:
        return

    print(f"  邮箱: {result.email}")
    print(f"  密码: {result.password}")
    print(f"  积分: {result.points}")
    print(f"  邀请码: {result.invite_code}")

    if args.output:
        with open(args.output, "a", encoding="utf-8") as output:
            output.write(json.dumps({
                "email": result.email,
                "password": result.password,
                "points": result.points,
                "invite_code": result.invite_code,
            }, ensure_ascii=False) + "\n")
        print(f"  已写入 {args.output}")


def cmd_fission(args):
    """Run the existing invitation chain command."""
    if not args.invite_code:
        log.info("未提供邀请码，先注册种子账号...")
        provider = build_email_provider(args.provider)
        try:
            provider.create()
            with OreateClient(proxy=_effective_proxy(args), browser=True) as client:
                seed = register(client, provider)
        finally:
            provider.close()
        if not seed.success:
            log.error("种子注册失败: %s", seed.error)
            return
        args.invite_code = seed.invite_code
        log.info("种子账号: %s", seed.email)

    results = chain_fission(
        args.invite_code,
        depth=args.depth,
        provider_name=args.provider,
    )

    print(f"\n{'=' * 50}")
    print(f"裂变结果: {sum(1 for result in results if result.success)}/{len(results)} 成功")
    for result in results:
        print(f"  {result}")

    if args.output:
        with open(args.output, "a", encoding="utf-8") as output:
            for result in results:
                if result.success:
                    output.write(json.dumps({
                        "email": result.email,
                        "password": result.password,
                        "points": result.points,
                        "invite_code": result.invite_code,
                    }, ensure_ascii=False) + "\n")


def cmd_check(args):
    """Restore one stored account and print its authenticated point balance."""
    client, account = _acquire_cli_account(args.account, _effective_proxy(args))
    try:
        points = get_remaining_points(client)
        print(f"邮箱: {account['email']}")
        print(f"积分: {points}")
    finally:
        release_account(client, account["email"])


def cmd_checkin(_args):
    """Claim the daily first-use points for all stored accounts."""
    results = daily_checkin_all()
    successful = sum(1 for result in results if result.get("ok"))
    earned = sum(result.get("earned", 0) for result in results if result.get("ok"))
    print(f"签到完成: {successful}/{len(results)}，新增积分: {earned}")
    for result in results:
        if result.get("ok"):
            print(
                f"  {result['email']}: {result.get('status', 'ok')} "
                f"{result.get('points_before', 0)} -> {result.get('points_after', 0)} "
                f"(+{result.get('earned', 0)})"
            )
        else:
            print(f"  {result['email']}: 失败 ({result.get('error', 'unknown error')})")


def cmd_video(args):
    """Generate a video with a restored browser-backed account."""
    client, account = _acquire_cli_account(args.account, _effective_proxy(args))
    try:
        is_audio = not args.no_audio
        spec = resolve_video_spec(
            client,
            args.model,
            duration=args.duration,
            resolution=args.resolution,
            is_audio=is_audio,
        )
        points = get_remaining_points(client)
        log.info("账号 %s 当前积分 %s，本次需要 %s", account["email"], points, spec.point)
        if points < spec.point:
            log.error("积分不足: %s < %s", points, spec.point)
            return

        result = generate_video(
            client,
            prompt=args.prompt,
            save_path=args.save,
            model_name=args.model,
            duration=args.duration,
            ratio=args.ratio,
            resolution=args.resolution,
            is_audio=is_audio,
            image_url=args.image_url,
        )
        print(f"\n{'=' * 50}")
        if result.success:
            print(f"  video: {result.video_url}")
            print(f"  logId: {result.log_id}")
            print(f"  cost: {result.cost_points}")
            if args.save and result.downloaded:
                print(f"  saved: {result.local_path}")
            elif args.save and result.error:
                print(f"  warning: {result.error}")
        else:
            print(f"  error: {result.error}")
    finally:
        release_account(client, account["email"])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="ThreadAI OreateAI browser protocol tool")
    sub = parser.add_subparsers(dest="command")

    register_parser = sub.add_parser("register", help="注册单个账号")
    register_parser.add_argument("--email", help="指定邮箱并手动输入验证 tokenID")
    register_parser.add_argument("--provider", help="邮箱 provider；省略时读取 THREADAI_EMAIL_PROVIDER")
    register_parser.add_argument("--password", help="指定账号密码")
    register_parser.add_argument("--proxy", help="本次代理；省略时读取 THREADAI_PROXY")
    register_parser.add_argument("--output", "-o", default="accounts.jsonl", help="额外输出 JSONL")

    fission_parser = sub.add_parser("fission", help="裂变注册")
    fission_parser.add_argument("--invite-code", help="种子邀请码")
    fission_parser.add_argument("--depth", type=int, default=3, help="裂变层数")
    fission_parser.add_argument("--provider", help="种子账号邮箱 provider")
    fission_parser.add_argument("--proxy", help="种子账号代理；省略时读取 THREADAI_PROXY")
    fission_parser.add_argument("--output", "-o", default="accounts.jsonl", help="额外输出 JSONL")

    check_parser = sub.add_parser("check", help="恢复账号并查询真实积分")
    check_parser.add_argument("account", help="账号邮箱或账号列表下标（从 0 开始）")
    check_parser.add_argument("--proxy", help="本次代理；省略时读取 THREADAI_PROXY")

    sub.add_parser("checkin", help="为账号库中的全部账号执行每日签到")

    video_parser = sub.add_parser("video", help="用账号库中的登录账号生成视频")
    video_parser.add_argument("prompt", help="视频描述")
    video_parser.add_argument("--model", default="Seedance 2.0 Mini", help="模型名称")
    video_parser.add_argument("--duration", type=int, default=5, choices=[5, 10], help="时长")
    video_parser.add_argument("--ratio", default="16:9", help="画面比例")
    video_parser.add_argument("--resolution", default="720", help="分辨率，例如 480 或 720")
    video_parser.add_argument("--no-audio", action="store_true", help="关闭生成音频")
    video_parser.add_argument("--image-url", default="", help="已上传参考图的 objectPath")
    video_parser.add_argument("--save", "-s", help="本地保存路径")
    video_parser.add_argument("--proxy", help="本次代理；省略时读取 THREADAI_PROXY")
    video_parser.add_argument("--account", help="账号邮箱或账号列表下标；省略时自动选择")

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    init_db()

    commands = {
        "register": cmd_register,
        "fission": cmd_fission,
        "check": cmd_check,
        "checkin": cmd_checkin,
        "video": cmd_video,
    }
    command = commands.get(args.command)
    if command:
        command(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()

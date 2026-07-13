"""Oreate AI 协议工具 — 主入口"""

import argparse
import logging
import json
import sys

from core.client import OreateClient
from modules.register import register, get_ticket, get_points, get_invite_code
from modules.email_provider import TempMailProvider, ManualEmailProvider
from modules.fission import fission_register, chain_fission
from modules.video import generate_video, get_remaining_points

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("main")


def cmd_register(args):
    """单号注册"""
    if args.email:
        provider = ManualEmailProvider(args.email)
    else:
        provider = TempMailProvider()
        provider.create()

    with OreateClient(proxy=args.proxy) as client:
        result = register(client, provider, password=args.password)

    print(f"\n{'='*50}")
    print(result)
    if result.success:
        print(f"  邮箱: {result.email}")
        print(f"  密码: {result.password}")
        print(f"  积分: {result.points}")
        print(f"  邀请码: {result.invite_code}")

        if args.output:
            with open(args.output, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "email": result.email,
                    "password": result.password,
                    "points": result.points,
                    "invite_code": result.invite_code,
                }, ensure_ascii=False) + "\n")
            print(f"  已写入 {args.output}")

    provider.close()


def cmd_fission(args):
    """裂变注册"""
    if not args.invite_code:
        log.info("未提供邀请码，先注册种子账号...")
        provider = TempMailProvider()
        provider.create()
        with OreateClient(proxy=args.proxy) as client:
            seed = register(client, provider)
        provider.close()
        if not seed.success:
            log.error(f"种子注册失败: {seed.error}")
            return
        args.invite_code = seed.invite_code
        log.info(f"种子账号: {seed.email}, 邀请码: {seed.invite_code}")

    results = chain_fission(args.invite_code, depth=args.depth, proxy=args.proxy)

    print(f"\n{'='*50}")
    print(f"裂变结果: {sum(1 for r in results if r.success)}/{len(results)} 成功")
    for r in results:
        print(f"  {r}")

    if args.output:
        with open(args.output, "a", encoding="utf-8") as f:
            for r in results:
                if r.success:
                    f.write(json.dumps({
                        "email": r.email,
                        "password": r.password,
                        "points": r.points,
                        "invite_code": r.invite_code,
                    }, ensure_ascii=False) + "\n")


def cmd_check(args):
    """检查账号状态"""
    provider = ManualEmailProvider(args.email)
    with OreateClient(proxy=args.proxy) as client:
        pts = get_remaining_points(client)
        print(f"积分: {pts}")


def cmd_video(args):
    """生成视频"""
    with OreateClient(proxy=args.proxy) as client:
        pts = get_remaining_points(client)
        log.info(f"当前积分: {pts}")
        if pts < 20:
            log.error("积分不足")
            return

        result = generate_video(
            client,
            prompt=args.prompt,
            save_path=args.save,
            model_name=args.model,
            duration=args.duration,
            ratio=args.ratio,
        )
        print(f"\n{'='*50}")
        if result.success:
            print(f"  video: {result.video_url}")
            print(f"  logId: {result.log_id}")
            if args.save:
                print(f"  saved: {args.save}")
        else:
            print(f"  error: {result.error}")


def main():
    parser = argparse.ArgumentParser(description="Oreate AI Protocol Tool")
    sub = parser.add_subparsers(dest="command")

    # register
    p_reg = sub.add_parser("register", help="注册单号")
    p_reg.add_argument("--email", help="指定邮箱（手动验证模式）")
    p_reg.add_argument("--password", help="指定密码")
    p_reg.add_argument("--proxy", help="代理 http://host:port")
    p_reg.add_argument("--output", "-o", default="accounts.jsonl", help="输出文件")

    # fission
    p_fis = sub.add_parser("fission", help="裂变注册")
    p_fis.add_argument("--invite-code", help="种子邀请码（不提供则先注册种子号）")
    p_fis.add_argument("--depth", type=int, default=3, help="裂变层数")
    p_fis.add_argument("--proxy", help="代理")
    p_fis.add_argument("--output", "-o", default="accounts.jsonl", help="输出文件")

    # check
    p_chk = sub.add_parser("check", help="检查账号")
    p_chk.add_argument("email")
    p_chk.add_argument("--proxy", help="代理")

    # video
    p_vid = sub.add_parser("video", help="生成视频")
    p_vid.add_argument("prompt", help="视频描述")
    p_vid.add_argument("--model", default="Seedance 2.0 Mini", help="模型名称")
    p_vid.add_argument("--duration", type=int, default=5, choices=[5, 10], help="时长")
    p_vid.add_argument("--ratio", default="16:9", help="画面比例")
    p_vid.add_argument("--save", "-s", help="保存路径（不指定则不下载）")
    p_vid.add_argument("--proxy", help="代理")
    p_vid.add_argument("--account", help="accounts.jsonl 中的行号（从0开始），用该账号生成")

    args = parser.parse_args()
    if args.command == "register":
        cmd_register(args)
    elif args.command == "fission":
        cmd_fission(args)
    elif args.command == "check":
        cmd_check(args)
    elif args.command == "video":
        cmd_video(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()

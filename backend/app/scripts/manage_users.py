"""建账号 / 改密码脚本 —— 邀请制没有注册接口，账号由你手动开。

用法：
    # 建账号（密码交互式输入，不进 shell 历史）
    backend/.venv/bin/python -m app.scripts.manage_users create alice --nickname 小爱

    # 改密码
    backend/.venv/bin/python -m app.scripts.manage_users passwd alice

    # 列出所有账号
    backend/.venv/bin/python -m app.scripts.manage_users list

    # 调整每日配额
    backend/.venv/bin/python -m app.scripts.manage_users limits alice --new 30 --review 150

⚠️ 密码用 getpass 交互输入，**不接受命令行参数** ——
   命令行参数会留在 shell 历史和进程列表里。
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from sqlalchemy import select

from app.core.database import AsyncSessionLocal, engine
from app.core.security import hash_password
from app.models import User


def log(msg: str) -> None:
    print(msg, flush=True)


def prompt_password(confirm: bool = True) -> str:
    """交互式读密码，两次确认。"""
    pw = getpass.getpass("密码: ")
    if not pw:
        log("❌ 密码不能为空")
        sys.exit(1)
    if confirm:
        again = getpass.getpass("再输一次: ")
        if pw != again:
            log("❌ 两次输入不一致")
            sys.exit(1)
    if len(pw) < 6:
        log("⚠️  密码短于 6 位。邀请制小范围使用可以接受，但建议长一些。")
    return pw


async def cmd_create(username: str, nickname: str | None) -> int:
    async with AsyncSessionLocal() as db:
        exists = (
            await db.execute(select(User).where(User.username == username))
        ).scalar_one_or_none()
        if exists:
            log(f"❌ 用户名「{username}」已存在。改密码用：passwd {username}")
            return 1

        password = prompt_password()
        user = User(
            username=username,
            nickname=nickname or username,
            password_hash=hash_password(password),
        )
        db.add(user)
        await db.commit()
        log(f"✅ 已创建账号「{username}」（昵称 {user.nickname}）")
        log(f"   每日新词 {user.daily_new_limit} / 复习上限 {user.daily_review_limit}")
        return 0


async def cmd_passwd(username: str) -> int:
    async with AsyncSessionLocal() as db:
        user = (
            await db.execute(select(User).where(User.username == username))
        ).scalar_one_or_none()
        if user is None:
            log(f"❌ 找不到用户「{username}」")
            return 1

        password = prompt_password()
        user.password_hash = hash_password(password)
        await db.commit()
        log(f"✅ 已更新「{username}」的密码")
        log("   注意：已签发的 token 仍然有效直到过期（当前设计未做主动失效）")
        return 0


async def cmd_list() -> int:
    async with AsyncSessionLocal() as db:
        users = (await db.execute(select(User).order_by(User.created_at))).scalars().all()

    if not users:
        log("还没有任何账号。建一个：create <用户名>")
        return 0

    log(f"\n共 {len(users)} 个账号：\n")
    log(f"  {'用户名':<16} {'昵称':<14} {'新词':>5} {'复习':>5}  创建时间")
    log("  " + "─" * 62)
    for u in users:
        log(f"  {u.username:<16} {u.nickname:<14} "
            f"{u.daily_new_limit:>5} {u.daily_review_limit:>5}  "
            f"{u.created_at:%Y-%m-%d %H:%M}")
    return 0


async def cmd_limits(username: str, new_limit: int | None, review_limit: int | None) -> int:
    if new_limit is None and review_limit is None:
        log("❌ 至少要指定 --new 或 --review 之一")
        return 1

    async with AsyncSessionLocal() as db:
        user = (
            await db.execute(select(User).where(User.username == username))
        ).scalar_one_or_none()
        if user is None:
            log(f"❌ 找不到用户「{username}」")
            return 1

        if new_limit is not None:
            if new_limit < 0:
                log("❌ 每日新词数不能为负")
                return 1
            user.daily_new_limit = new_limit
        if review_limit is not None:
            if review_limit < 0:
                log("❌ 每日复习上限不能为负")
                return 1
            user.daily_review_limit = review_limit

        await db.commit()
        log(f"✅ 已更新「{username}」：新词 {user.daily_new_limit} / "
            f"复习上限 {user.daily_review_limit}")
        return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="manage_users",
        description="建账号 / 改密码 / 调配额（邀请制，无注册接口）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("create", help="创建账号")
    c.add_argument("username")
    c.add_argument("--nickname", help="昵称，默认与用户名相同")

    pw = sub.add_parser("passwd", help="修改密码")
    pw.add_argument("username")

    sub.add_parser("list", help="列出所有账号")

    lim = sub.add_parser("limits", help="调整每日配额")
    lim.add_argument("username")
    lim.add_argument("--new", type=int, dest="new_limit", help="每日新词数")
    lim.add_argument("--review", type=int, dest="review_limit", help="每日复习上限")

    return p


async def main_async(args: argparse.Namespace) -> int:
    try:
        match args.command:
            case "create":
                return await cmd_create(args.username, args.nickname)
            case "passwd":
                return await cmd_passwd(args.username)
            case "list":
                return await cmd_list()
            case "limits":
                return await cmd_limits(args.username, args.new_limit, args.review_limit)
            case _:
                return 1
    finally:
        # engine 必须在本循环内销毁（见 BUG-006）
        await engine.dispose()


def main() -> int:
    args = build_parser().parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())

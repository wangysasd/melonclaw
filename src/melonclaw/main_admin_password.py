"""交互设置 admin 密码，不从参数接收明文。"""
import asyncio
import getpass

from dotenv import load_dotenv

from melonclaw.core.config import load_settings
from melonclaw.database import Database
from melonclaw.repository import BusinessRepository
from melonclaw.services.accounts import AccountService


async def set_password(password):
    database = Database(load_settings().database_url)
    try:
        await database.open()
        await database.verify_schema(require_checkpointer=False)
        await AccountService(BusinessRepository(database)).reset_password("admin", password)
    finally:
        await database.close()


def main():
    load_dotenv()
    password = getpass.getpass("admin 新密码（5～128 字符）：")
    if password != getpass.getpass("再次输入："):
        raise SystemExit("两次密码不一致。")
    try:
        asyncio.run(set_password(password))
    except Exception:
        raise SystemExit("密码设置失败，请检查数据库初始化及密码长度。") from None
    print("admin 密码已设置，原有相关会话已失效。")


if __name__ == "__main__":
    main()

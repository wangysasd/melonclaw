"""扩展短信验证请求用途，允许手机验证码找回密码（reset）。

只替换 sms_challenges.purpose 的检查约束。不改已有用户、手机号绑定或历史验证记录。
"""

from sqlalchemy import text

from melonclaw.database.errors import DatabaseSchemaError


async def upgrade(connection):
    present = await connection.scalar(text("SELECT to_regclass('sms_challenges')"))
    if present is None:
        raise DatabaseSchemaError("缺少 sms_challenges；请先完成短信认证表升级。")
    await connection.execute(text(
        "ALTER TABLE sms_challenges DROP CONSTRAINT IF EXISTS ck_sms_challenges_purpose"
    ))
    await connection.execute(text(
        "ALTER TABLE sms_challenges ADD CONSTRAINT ck_sms_challenges_purpose "
        "CHECK (purpose IN ('register', 'login', 'bind', 'reset'))"
    ))

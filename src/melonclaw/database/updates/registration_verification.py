"""自助注册的手机号核验留痕。

短期验证码请求可以清理，这条记录按账号和验证编号保留。
不改已有用户、密码、租户或手机号绑定。
"""

from sqlalchemy import text


async def upgrade(connection):
    await connection.execute(text("""
        CREATE TABLE IF NOT EXISTS registration_verifications (
            user_id VARCHAR(64) PRIMARY KEY REFERENCES users(user_id) ON DELETE RESTRICT,
            verification_id VARCHAR(32) NOT NULL,
            method VARCHAR(16) NOT NULL,
            phone_tail VARCHAR(4) NOT NULL,
            verified_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT ck_registration_verifications_method CHECK (method = 'sms_code'),
            CONSTRAINT uq_registration_verifications_id UNIQUE (verification_id)
        )
    """))

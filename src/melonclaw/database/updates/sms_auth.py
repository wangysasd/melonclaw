"""增加手机号绑定、短信验证请求和发送配额。

不改 users、tenants 或已有业务表。重复执行只补齐缺失对象。
"""

from sqlalchemy import text


async def upgrade(connection):
    await connection.execute(text("""
        CREATE TABLE IF NOT EXISTS user_phones (
            user_id VARCHAR(64) PRIMARY KEY REFERENCES users(user_id) ON DELETE RESTRICT,
            phone_hmac VARCHAR(64) NOT NULL,
            phone_tail VARCHAR(4) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_user_phones_hmac UNIQUE (phone_hmac)
        )
    """))
    await connection.execute(text("""
        CREATE TABLE IF NOT EXISTS sms_challenges (
            challenge_id VARCHAR(32) PRIMARY KEY,
            purpose VARCHAR(16) NOT NULL,
            phone_hmac VARCHAR(64) NOT NULL,
            user_id VARCHAR(64) REFERENCES users(user_id) ON DELETE RESTRICT,
            generation INTEGER NOT NULL,
            status VARCHAR(16) NOT NULL,
            out_id VARCHAR(32) NOT NULL,
            scheme_name VARCHAR(20) NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            expires_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            CONSTRAINT ck_sms_challenges_purpose CHECK (purpose IN ('register', 'login', 'bind')),
            CONSTRAINT ck_sms_challenges_status CHECK (
                status IN ('reserved', 'sent', 'verifying', 'consumed', 'replaced', 'failed', 'uncertain')
            ),
            CONSTRAINT uq_sms_challenges_out_id UNIQUE (out_id)
        )
    """))
    await connection.execute(text("""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_sms_challenges_active
        ON sms_challenges (phone_hmac, purpose)
        WHERE status IN ('reserved', 'sent', 'verifying')
    """))
    await connection.execute(text("""
        CREATE TABLE IF NOT EXISTS sms_quotas (
            bucket VARCHAR(96) PRIMARY KEY,
            window_start TIMESTAMPTZ NOT NULL,
            count INTEGER NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL
        )
    """))

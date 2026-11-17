"""按本地库补上 knowledge_bases.parser_model_id。

列为可空 UUID，外键指向 model_configs，并带同样的索引。已有该列时不改数据。
"""

from sqlalchemy import text


async def upgrade(connection):
    present = await connection.scalar(text(
        "SELECT EXISTS ("
        "SELECT 1 FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name='knowledge_bases' "
        "AND column_name='parser_model_id')"
    ))
    if not present:
        await connection.execute(text(
            "ALTER TABLE knowledge_bases ADD COLUMN parser_model_id UUID "
            "REFERENCES model_configs(id) ON DELETE RESTRICT"
        ))
    await connection.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_knowledge_bases_parser_model "
        "ON knowledge_bases (parser_model_id)"
    ))

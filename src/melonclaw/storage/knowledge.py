"""知识库原件/派生文件的私有存储，路径只由内部 UUID 生成。"""
from pathlib import Path
from uuid import UUID


class KnowledgeFiles:
    def __init__(self, data_root):
        self.root = Path(data_root) / "knowledge"

    def path(self, *parts):
        current = self.root
        # 同时拒绝数据根任一级父路径中的符号链接。
        for parent in [*current.parents, current]:
            if parent.is_symlink():
                raise ValueError("知识库存储路径无效。")
        for part in parts:
            if str(part) in {"source", "ocr.json"}:
                token = str(part)
            else:
                token = str(UUID(str(part)))
            current = current / token
            if current.is_symlink():
                raise ValueError("知识库存储路径无效。")
        return current

    def source(self, kb_id, document_id, revision_id):
        return self.path(kb_id, document_id, revision_id, "source")

    def work(self, job_id):
        return self.path(job_id)

"""独立解析进程；仅接收受控路径/限制，不加载应用或部署凭据。"""
import json
import sys
from pathlib import Path


class ParseError(ValueError):
    """固定错误分类与真实页码；不含路径、正文或凭据。"""

    def __init__(self, code, page=None):
        self.code, self.page = code, page
        super().__init__(code)


def parse(spec):
    source = Path(spec["source"])
    extension = spec["extension"]
    if extension not in {".txt", ".md"}:
        raise ParseError("unsupported")
    content = source.read_text(encoding="utf-8-sig")
    if len(content) > spec["max_chars"]:
        raise ParseError("text_limit", 1)
    return [{"page": 1, "content": content}]


def main():
    try:
        # CPU 限额是故障隔离，不宣称为安全沙箱。
        import resource
        spec = json.loads(sys.stdin.read(8192))
        resource.setrlimit(resource.RLIMIT_CPU, (spec["cpu_seconds"], spec["cpu_seconds"]))
        resource.setrlimit(resource.RLIMIT_FSIZE, (spec["max_file_bytes"], spec["max_file_bytes"]))
        print(json.dumps({"pages": parse(spec)}, ensure_ascii=False))
    except ParseError as exc:
        print(json.dumps({"error": {"code": exc.code, "page": exc.page}}))
        sys.exit(1)
    except Exception:
        print('{"error":{"code":"parse_failed"}}')
        sys.exit(1)


if __name__ == "__main__":
    main()

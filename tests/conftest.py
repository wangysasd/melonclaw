"""测试进程默认具备启动所需的签名密钥，不写入仓库配置。"""

import os

os.environ.setdefault("MELONCLAW_JWT_SECRET", "pytest-jwt-secret-must-be-32-characters")

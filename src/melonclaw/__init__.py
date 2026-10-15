"""Deep Agents 累计学习应用。"""

import os
from pathlib import Path

from dotenv import load_dotenv

configured_env_file = os.environ.get("MELONCLAW_ENV_FILE")
if configured_env_file:
    load_dotenv(dotenv_path=configured_env_file)
elif Path("/etc/melonclaw/melonclaw.env").is_file():
    # Match the standard Linux deployment config used by systemd so maintenance
    # commands run manually by the service account use the same settings.
    load_dotenv(dotenv_path="/etc/melonclaw/melonclaw.env")
else:
    load_dotenv()

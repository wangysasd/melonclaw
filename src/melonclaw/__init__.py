"""Deep Agents 累计学习应用。"""

import os

from dotenv import load_dotenv

env_file = os.environ.get("MELONCLAW_ENV_FILE")
if env_file:
    load_dotenv(dotenv_path=env_file)
else:
    load_dotenv()

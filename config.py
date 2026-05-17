"""
买房助手 - 统一配置模块
所有脚本从这里读取配置，敏感信息从 .env 文件加载
"""

import os
from pathlib import Path

def load_env():
    """手动加载.env文件，不依赖python-dotenv"""
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        print(f"⚠️  找不到 .env 文件，请复制 .env.example 并填入配置")
        print(f"   期望路径: {env_path}")
        return

    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                os.environ.setdefault(key.strip(), value.strip())

# 加载.env
load_env()

def get(key, required=True):
    val = os.environ.get(key)
    if required and not val:
        raise ValueError(f"❌ 缺少环境变量: {key}，请检查 .env 文件")
    return val

# ── 所有配置 ──────────────────────────────────────────────────────────
NOTION_TOKEN         = get("NOTION_TOKEN")
NOTION_PROPERTY_DB   = get("NOTION_PROPERTY_DB_ID")
NOTION_EMAIL_DB      = get("NOTION_EMAIL_DB_ID")
ANTHROPIC_KEY        = get("ANTHROPIC_API_KEY", required=False) or ""
GMAIL_USER           = get("GMAIL_USER")
GMAIL_PASSWORD       = get("GMAIL_APP_PASSWORD")
TELEGRAM_TOKEN       = get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID     = get("TELEGRAM_CHAT_ID")
CHECK_INTERVAL       = int(get("CHECK_INTERVAL_SECONDS", required=False) or 300)

# ── Notion请求头 ──────────────────────────────────────────────────────
NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28"
}

ANTHROPIC_HEADERS = {
    "x-api-key": ANTHROPIC_KEY,
    "anthropic-version": "2023-06-01",
    "content-type": "application/json"
}

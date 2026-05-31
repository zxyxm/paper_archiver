import os
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
DEFAULT_ARCHIVE_ROOT = APP_DIR / "archive"
CONFIG_FILE = APP_DIR / "config.json"

PROVIDER_PRESETS = {
    "deepseek": {
        "name": "DeepSeek API",
        "base_url": "https://api.deepseek.com/chat/completions",
        "model": "deepseek-chat",
        "api_key_env": "DEEPSEEK_API_KEY",
        "model_env": "DEEPSEEK_MODEL",
        "base_url_env": "DEEPSEEK_BASE_URL",
        "auth_header": "Authorization",
    },
    "xiaomi": {
        "name": "?? MiMo Reasoning",
        "base_url": os.environ.get(
            "XIAOMI_BASE_URL", "https://token-plan-cn.xiaomimimo.com/v1"
        ),
        "model": os.environ.get("XIAOMI_MODEL", "mimo-v2.5-pro"),
        "api_key_env": "XIAOMI_API_KEY",
        "model_env": "XIAOMI_MODEL",
        "base_url_env": "XIAOMI_BASE_URL",
        "auth_header": os.environ.get("XIAOMI_AUTH_HEADER", "api-key"),
    },
    "custom": {
        "name": "??? OpenAI ??",
        "base_url": os.environ.get("OPENAI_COMPAT_BASE_URL", ""),
        "model": os.environ.get("OPENAI_COMPAT_MODEL", ""),
        "api_key_env": "OPENAI_COMPAT_API_KEY",
        "model_env": "OPENAI_COMPAT_MODEL",
        "base_url_env": "OPENAI_COMPAT_BASE_URL",
        "auth_header": os.environ.get("OPENAI_COMPAT_AUTH_HEADER", "Authorization"),
    },
}

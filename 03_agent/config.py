"""模型接入配置：仅使用标准库，公开结果绝不包含明文 API Key。"""

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BASE_URL = "https://api.openai.com/v1"
CONFIG_KEYS = (
    "AGENT_API_KEY",
    "AGENT_BASE_URL",
    "AGENT_MODEL",
    "AGENT_LLM",
    "AGENT_OLLAMA_URL",
    "AGENT_EMBED_MODEL",
    "AGENT_DATA_DIR",
    "AGENT_TOP_K",
    "AGENT_MODEL_TIMEOUT",
)
DEFAULT_OLLAMA_URL = "http://localhost:11434"


def _read_dotenv():
    """读取项目根 .env；支持空行、注释、export 和成对引号。"""
    path = PROJECT_ROOT / ".env"
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        return {}

    values = {}
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or key not in CONFIG_KEYS:
            continue
        value = value.strip()
        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in {"'", '"'}
        ):
            value = value[1:-1]
        values[key] = value
    return values


def _load_settings():
    """在运行时加载原始配置；环境变量优先，返回值不得记录到日志。"""
    file_values = _read_dotenv()
    settings = {}
    for key in CONFIG_KEYS:
        value = os.environ[key] if key in os.environ else file_values.get(key, "")
        settings[key] = value.strip()
    return settings


def _mask_api_key(api_key):
    if not api_key:
        return ""
    if len(api_key) <= 4:
        return "***"
    return api_key[:4] + "***"


def _data_directory(value):
    path = Path(value).expanduser() if value else PROJECT_ROOT / ".pyagent-data"
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return str(path.resolve())


def get_runtime_config():
    """返回模型连接所需的原始配置；调用方严禁记录或展示 api_key。"""
    settings = _load_settings()
    api_key = settings["AGENT_API_KEY"]
    llm = settings["AGENT_LLM"].lower() or "openai"
    try:
        top_k = int(settings["AGENT_TOP_K"] or "4")
        model_timeout = float(settings["AGENT_MODEL_TIMEOUT"] or "180")
    except ValueError:
        raise ValueError("AGENT_TOP_K 必须是整数，AGENT_MODEL_TIMEOUT 必须是数字") from None
    if not 1 <= top_k <= 10:
        raise ValueError("AGENT_TOP_K 必须在 1 到 10 之间")
    if model_timeout <= 0:
        raise ValueError("AGENT_MODEL_TIMEOUT 必须大于 0")
    return {
        "api_key": api_key,
        "base_url": settings["AGENT_BASE_URL"] or DEFAULT_BASE_URL,
        "model": settings["AGENT_MODEL"],
        "llm": llm,
        "has_model": bool(api_key) or llm in {"ollama", "mock"},
        "ollama_url": settings["AGENT_OLLAMA_URL"] or DEFAULT_OLLAMA_URL,
        "embed_model": settings["AGENT_EMBED_MODEL"],
        "data_dir": _data_directory(settings["AGENT_DATA_DIR"]),
        "top_k": top_k,
        "model_timeout": model_timeout,
    }


def get_config():
    """返回可安全展示的配置快照，api_key 始终为空字符串或掩码。"""
    settings = get_runtime_config()
    settings["api_key"] = _mask_api_key(settings["api_key"])
    return settings

"""config 的隔离测试；所有密钥值均为无效的测试占位符。"""

from pathlib import Path
import sys

import pytest


AGENT_DIR = Path(__file__).resolve().parent
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

import config  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch, tmp_path):
    for key in config.CONFIG_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    return tmp_path


def test_defaults_without_env_or_dotenv(isolated_config):
    assert config.get_config() == {
        "api_key": "",
        "base_url": "https://api.openai.com/v1",
        "model": "",
        "llm": "openai",
        "has_model": False,
        "ollama_url": "http://localhost:11434",
        "embed_model": "",
        "data_dir": str((isolated_config / ".pyagent-data").resolve()),
        "top_k": 4,
        "model_timeout": 180.0,
    }


def test_dotenv_loading_and_environment_precedence(isolated_config, monkeypatch):
    (isolated_config / ".env").write_text(
        "# 测试配置\n"
        "export AGENT_API_KEY='dotenv-test-secret'\n"
        "AGENT_BASE_URL=https://dotenv.invalid/v1\n"
        'AGENT_MODEL="local-test-model"\n'
        "AGENT_LLM=ollama\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_BASE_URL", "https://environment.invalid/v1")

    result = config.get_config()

    assert result == {
        "api_key": "dote***",
        "base_url": "https://environment.invalid/v1",
        "model": "local-test-model",
        "llm": "ollama",
        "has_model": True,
        "ollama_url": "http://localhost:11434",
        "embed_model": "",
        "data_dir": str((isolated_config / ".pyagent-data").resolve()),
        "top_k": 4,
        "model_timeout": 180.0,
    }
    assert "dotenv-test-secret" not in repr(result)


def test_environment_key_is_masked_and_enables_model(monkeypatch):
    monkeypatch.setenv("AGENT_API_KEY", "environment-test-secret")
    monkeypatch.setenv("AGENT_MODEL", "test-model")

    result = config.get_config()

    assert result["api_key"] == "envi***"
    assert result["has_model"] is True
    assert "environment-test-secret" not in repr(result)


def test_ollama_is_available_without_key(monkeypatch):
    monkeypatch.setenv("AGENT_LLM", "OLLAMA")

    result = config.get_config()

    assert result["api_key"] == ""
    assert result["llm"] == "ollama"
    assert result["has_model"] is True


def test_mock_is_available_without_key(monkeypatch):
    monkeypatch.setenv("AGENT_LLM", "MOCK")

    result = config.get_config()

    assert result["api_key"] == ""
    assert result["llm"] == "mock"
    assert result["has_model"] is True


def test_short_key_is_not_partially_exposed(monkeypatch):
    monkeypatch.setenv("AGENT_API_KEY", "tiny")

    result = config.get_config()

    assert result["api_key"] == "***"
    assert "tiny" not in repr(result)

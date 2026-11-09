"""LLM 调用链测试；所有 HTTP 都由替身响应，不访问网络。"""

import json
from pathlib import Path
import sys

import pytest


AGENT_DIR = Path(__file__).resolve().parent
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

import llm  # noqa: E402


MESSAGES = [{"role": "user", "content": "请检查项目"}]


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self):
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


def install_fake_urlopen(monkeypatch, payload):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append({"request": request, "timeout": timeout})
        return FakeResponse(payload)

    monkeypatch.setattr(llm.urllib.request, "urlopen", fake_urlopen)
    return calls


def test_mock_llm_returns_deterministic_text_and_tool_call():
    tool_call = {
        "id": "call-test",
        "type": "function",
        "function": {"name": "check_project", "arguments": "{}"},
    }
    client = llm.MockLLM(
        responses={
            "检查": {"tool_calls": [tool_call]},
            "你好": "固定的问候",
        },
        default_response="固定默认响应",
    )

    tool_result = client.chat(MESSAGES)
    assert json.loads(tool_result)["tool_calls"] == [tool_call]
    assert client.chat([{"role": "user", "content": "你好"}]) == "固定的问候"
    assert client.chat([{"role": "user", "content": "其他"}]) == "固定默认响应"
    assert client.chat(MESSAGES) == tool_result
    assert client.received_messages[0] == MESSAGES


def test_openai_text_request_and_v1_url(monkeypatch):
    calls = install_fake_urlopen(
        monkeypatch,
        {"choices": [{"message": {"content": "真实后端的模拟文本"}}]},
    )
    client = llm.LLMClient(
        backend="openai",
        model="test-model",
        api_key="test-only-key",
        base_url="https://openai.invalid/v1/",
        timeout=12,
    )

    assert client.chat(MESSAGES) == "真实后端的模拟文本"
    request = calls[0]["request"]
    assert request.full_url == "https://openai.invalid/v1/chat/completions"
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == "Bearer test-only-key"
    assert json.loads(request.data.decode("utf-8")) == {
        "model": "test-model",
        "messages": MESSAGES,
    }
    assert calls[0]["timeout"] == 12


def test_openai_tool_calls_are_preserved(monkeypatch):
    tool_calls = [
        {
            "id": "call-1",
            "type": "function",
            "function": {
                "name": "run_check",
                "arguments": '{"path":"demo"}',
            },
        }
    ]
    install_fake_urlopen(
        monkeypatch,
        {
            "choices": [
                {"message": {"content": None, "tool_calls": tool_calls}}
            ]
        },
    )
    client = llm.LLMClient(api_key="test-only-key", model="test-model")

    result = json.loads(client.chat(MESSAGES))

    assert result == {"content": "", "tool_calls": tool_calls}


def test_ollama_uses_local_protocol_without_authorization(monkeypatch):
    calls = install_fake_urlopen(
        monkeypatch,
        {"message": {"role": "assistant", "content": "本地模拟响应"}},
    )
    client = llm.LLMClient(backend="ollama", model="local-test-model")

    assert client.chat(MESSAGES) == "本地模拟响应"
    request = calls[0]["request"]
    assert request.full_url == "http://localhost:11434/api/chat"
    assert request.get_header("Authorization") is None
    assert json.loads(request.data.decode("utf-8")) == {
        "model": "local-test-model",
        "messages": MESSAGES,
        "stream": False,
    }


def test_get_llm_selects_openai_and_ollama(monkeypatch):
    settings = {
        "api_key": "test-only-key",
        "base_url": "https://openai.invalid/v1",
        "model": "test-model",
        "llm": "openai",
        "has_model": True,
    }
    monkeypatch.setattr(llm.config, "get_runtime_config", lambda: settings)
    openai_client = llm.get_llm()
    assert isinstance(openai_client, llm.LLMClient)
    assert openai_client.backend == "openai"

    settings["llm"] = "ollama"
    settings["api_key"] = ""
    ollama_client = llm.get_llm()
    assert isinstance(ollama_client, llm.LLMClient)
    assert ollama_client.backend == "ollama"

    settings["llm"] = "mock"
    mock_client = llm.get_llm()
    assert isinstance(mock_client, llm.MockLLM)
    assert mock_client.chat(MESSAGES) == "MockLLM：未匹配预设响应"


def test_unconfigured_chat_is_friendly_and_never_connects(monkeypatch):
    monkeypatch.setattr(
        llm.config,
        "get_runtime_config",
        lambda: {
            "api_key": "",
            "base_url": llm.config.DEFAULT_BASE_URL,
            "model": "",
            "llm": "openai",
            "has_model": False,
        },
    )

    def forbidden_network(*args, **kwargs):
        raise AssertionError("未配置时不允许联网")

    monkeypatch.setattr(llm.urllib.request, "urlopen", forbidden_network)
    client = llm.get_llm()

    assert isinstance(client, llm.UnconfiguredLLM)
    assert client.chat(MESSAGES) == "未配置模型，当前仅可用离线模式"


def test_direct_openai_client_without_key_never_connects(monkeypatch):
    def forbidden_network(*args, **kwargs):
        raise AssertionError("无 Key 时不允许联网")

    monkeypatch.setattr(llm.urllib.request, "urlopen", forbidden_network)
    client = llm.LLMClient(backend="openai", api_key="")

    assert client.chat(MESSAGES) == llm.UNCONFIGURED_MESSAGE


def test_http_error_does_not_expose_authorization(monkeypatch):
    def failing_urlopen(request, timeout):
        raise llm.urllib.error.HTTPError(
            request.full_url,
            401,
            "unauthorized",
            request.headers,
            None,
        )

    monkeypatch.setattr(llm.urllib.request, "urlopen", failing_urlopen)
    client = llm.LLMClient(api_key="test-only-secret", model="test-model")

    with pytest.raises(llm.LLMError) as caught:
        client.chat(MESSAGES)
    assert str(caught.value) == "模型接口返回 HTTP 401"
    assert "test-only-secret" not in str(caught.value)

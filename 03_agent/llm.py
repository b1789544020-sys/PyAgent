"""统一的模型调用接口；真实请求仅使用标准库，测试可完全离线。"""

import copy
import json
import urllib.error
import urllib.request

import config


UNCONFIGURED_MESSAGE = "未配置模型，当前仅可用离线模式"
OLLAMA_URL = "http://localhost:11434"


class LLMError(RuntimeError):
    """真实模型请求或响应格式错误，不包含密钥信息。"""


def _encode_message(content, tool_calls):
    text = "" if content is None else str(content)
    calls = tool_calls or []
    if calls:
        return json.dumps(
            {"content": text, "tool_calls": calls},
            ensure_ascii=False,
        )
    return text


def _copy_messages(messages):
    if isinstance(messages, (str, bytes)):
        raise TypeError("messages 必须是消息字典序列")
    return copy.deepcopy(list(messages))


class LLMClient:
    """OpenAI 兼容接口或 Ollama 客户端；tool_calls 以 JSON 字符串返回。"""

    def __init__(
        self,
        backend="openai",
        model="",
        api_key="",
        base_url=config.DEFAULT_BASE_URL,
        timeout=60,
        ollama_url=OLLAMA_URL,
    ):
        if backend not in {"openai", "ollama"}:
            raise ValueError("backend 仅支持 openai 或 ollama")
        self.backend = backend
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.ollama_url = ollama_url.rstrip("/")
        self.timeout = float(timeout)
        self._api_key = api_key

    def _request_json(self, messages):
        if self.backend == "openai":
            if not self._api_key:
                raise LLMError(UNCONFIGURED_MESSAGE)
            url = self.base_url + "/chat/completions"
            body = {"model": self.model, "messages": messages}
            headers = {"Authorization": "Bearer " + self._api_key}
        else:
            url = self.ollama_url + "/api/chat"
            body = {
                "model": self.model,
                "messages": messages,
                "stream": False,
            }
            headers = {}
        headers.update(
            {
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )
        request = urllib.request.Request(
            url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
            return json.loads(raw)
        except urllib.error.HTTPError as exc:
            raise LLMError(f"模型接口返回 HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError):
            raise LLMError("无法连接模型接口或请求超时") from None
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise LLMError("模型接口返回了无效 JSON") from None

    def chat(self, messages) -> str:
        """返回助手文本；有工具调用时返回可解析的 JSON 字符串。"""
        if self.backend == "openai" and not self._api_key:
            return UNCONFIGURED_MESSAGE
        payload = self._request_json(_copy_messages(messages))
        if self.backend == "openai":
            choices = payload.get("choices")
            if not isinstance(choices, list) or not choices:
                raise LLMError("OpenAI 响应缺少 choices")
            message = choices[0].get("message", {})
        else:
            message = payload.get("message", {})
        if not isinstance(message, dict):
            raise LLMError("模型响应缺少有效的 message")
        return _encode_message(
            message.get("content", ""),
            message.get("tool_calls", []),
        )


class MockLLM:
    """按关键词返回预设文本或 tool_calls，记录调用但不联网。"""

    def __init__(self, responses=None, default_response="MockLLM：未匹配预设响应"):
        self.responses = dict(responses or {})
        self.default_response = default_response
        self.received_messages = []

    def chat(self, messages) -> str:
        copied = _copy_messages(messages)
        self.received_messages.append(copied)
        prompt = "\n".join(
            str(message.get("content", ""))
            for message in copied
            if isinstance(message, dict)
        )
        response = self.default_response
        for keyword, candidate in self.responses.items():
            if keyword in prompt:
                response = candidate
                break
        if isinstance(response, str):
            return response
        if isinstance(response, dict):
            return _encode_message(
                response.get("content", ""),
                response.get("tool_calls", []),
            )
        raise TypeError("MockLLM 预设响应必须是字符串或消息字典")


class UnconfiguredLLM:
    """无可用模型时的安全降级对象，调用不会抛异常或联网。"""

    def chat(self, messages) -> str:
        _copy_messages(messages)
        return UNCONFIGURED_MESSAGE


def get_llm():
    """按运行时配置选择真实客户端；无可用配置时安全降级。"""
    settings = config.get_runtime_config()
    backend = settings["llm"]
    if backend == "mock":
        return MockLLM()
    if backend == "ollama":
        return LLMClient(
            backend="ollama",
            model=settings["model"],
            ollama_url=settings.get("ollama_url", OLLAMA_URL),
            timeout=settings.get("model_timeout", 180),
        )
    if backend == "openai" and settings["api_key"]:
        return LLMClient(
            backend="openai",
            model=settings["model"],
            api_key=settings["api_key"],
            base_url=settings["base_url"],
        )
    return UnconfiguredLLM()

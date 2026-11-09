import copy
import json
from pathlib import Path
import threading

import pytest

import agent
import llm
import safety
import tools


ROOT = Path(__file__).resolve().parents[1]
SMOKE = ROOT / "04_golden_tests" / "smoke_01"


@pytest.fixture(autouse=True)
def isolated_session(monkeypatch, tmp_path):
    monkeypatch.setattr(
        agent.understanding, "DEFAULT_SESSION_PATH", tmp_path / ".session.json"
    )


def _call(name, arguments):
    return {
        "content": "",
        "tool_calls": [
            {
                "id": "mock-call-1",
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": json.dumps(arguments, ensure_ascii=False),
                },
            }
        ],
    }


def test_mock_llm_tool_observation_final_loop():
    mock = llm.MockLLM(
        responses={
            '"command": "check"': "检查完成：发现1个问题。",
            "执行闭环诊断": _call("check_project", {"path": str(SMOKE)}),
        }
    )
    result = agent.run_agent(
        f"执行闭环诊断 {SMOKE}",
        mock,
        context={
            "messages": [{"role": "user", "content": "保持只读"}],
            "scope": "smoke_01",
        },
    )

    assert result["status"] == "completed"
    assert result["reason"] == "final_answer"
    assert result["answer"] == "检查完成：发现1个问题。"
    assert result["steps_taken"] == 2
    assert result["evidence"] == {
        "tool_result_count": 1,
        "successful_tools": ["check_project"],
        "verification_passed": False,
    }
    observation = result["progress"][0]
    assert observation["name"] == "check_project"
    assert observation["result"]["status"] == "completed"
    assert [(issue["rule"], issue["line"]) for issue in observation["result"]["issues"]] == [
        ("DR_0001", 4)
    ]
    assert len(mock.received_messages) == 2
    assert "check_project" in mock.received_messages[0][0]["content"]
    assert "execute" not in mock.received_messages[0][0]["content"]
    assert mock.received_messages[1][-1]["role"] == "tool"
    tool_message = json.loads(mock.received_messages[1][-1]["content"])
    assert tool_message["command"] == "check"


def test_plain_final_answer_ends_immediately(monkeypatch):
    def forbidden(name, args):
        raise AssertionError("普通问答不应调用工具")

    monkeypatch.setattr(tools, "execute_tool", forbidden)
    result = agent.run_agent("打个招呼", llm.MockLLM(default_response="你好"))
    assert result["status"] == "completed"
    assert result["steps_taken"] == 1
    assert result["answer"] == "你好"


def test_max_steps_stops_repeated_model_and_tool_calls(monkeypatch):
    calls = []

    def fake_execute(name, args):
        calls.append((name, args))
        return {"command": "check", "status": "completed", "issues": []}

    monkeypatch.setattr(tools, "execute_tool", fake_execute)
    mock = llm.MockLLM(
        responses={"循环": _call("check_project", {"root": "unused"})}
    )
    result = agent.run_agent("循环", mock, max_steps=2)
    assert result["status"] == "incomplete"
    assert result["reason"] == "max_steps"
    assert result["steps_taken"] == 2
    assert len(mock.received_messages) == 2
    assert len(calls) == 2
    assert result["evidence"]["tool_result_count"] == 2
    assert "未能完成" in result["message"]


def test_verbal_fix_without_tool_evidence_is_challenged_and_incomplete():
    mock = llm.MockLLM(
        responses={safety.CORRECTION_PROMPT: "尚未修复；没有执行任何工具。"},
        default_response="已修复，全部通过。",
    )
    result = agent.run_agent(f"修复项目 {SMOKE}", mock)
    assert result["status"] == "incomplete"
    assert result["reason"] == "missing_tool_evidence"
    assert result["answer"] == "尚未修复；没有执行任何工具。"
    assert result["steps_taken"] == 2
    assert result["evidence"]["tool_result_count"] == 0
    assert result["unverified_claims"][0]["claim"] == "已修复，全部通过。"
    assert result["unverified_claims"][0]["status"] == "待验证"
    assert mock.received_messages[1][-1] == {
        "role": "system", "content": safety.CORRECTION_PROMPT
    }


def test_check_result_does_not_support_verbal_fix_claim():
    mock = llm.MockLLM(
        responses={
            safety.CORRECTION_PROMPT: "仅完成检查，尚未修复。",
            '"command": "check"': "已修复，验证通过。",
            "修复项目": _call("check_project", {"path": str(SMOKE)}),
        }
    )
    result = agent.run_agent(f"修复项目 {SMOKE}", mock)
    assert result["status"] == "completed"
    assert result["answer"] == "仅完成检查，尚未修复。"
    assert result["steps_taken"] == 3
    assert result["evidence"]["successful_tools"] == ["check_project"]
    assert result["evidence"]["verification_passed"] is False
    assert result["unverified_claims"][0]["reason"] == "missing_verification"


def test_verified_tool_result_supports_fix_completion(monkeypatch):
    def fake_fix(name, args):
        assert name == "fix_project"
        return {
            "command": "fix",
            "status": "completed",
            "verification": {"passed": True, "report": {"issues": []}},
        }

    monkeypatch.setattr(tools, "execute_tool", fake_fix)
    mock = llm.MockLLM(
        responses={
            '"verification": {': "已修复，验证通过。",
            "修复并验证": _call("fix_project", {"root": "unused", "dry_run": False}),
        }
    )
    confirmations = []
    policy = safety.SafetyPolicy(
        interactive=True,
        input_fn=lambda prompt: confirmations.append(prompt) or "y",
        emit=lambda message: None,
    )
    result = agent.run_agent(f"修复并验证 {SMOKE}", mock, safety_policy=policy)
    assert result["status"] == "completed"
    assert result["reason"] == "final_answer"
    assert result["evidence"]["verification_passed"] is True
    assert len(confirmations) == 1


class BlockingMockLLM(llm.MockLLM):
    def __init__(self):
        super().__init__()
        self.release = threading.Event()

    def chat(self, messages):
        self.received_messages.append(copy.deepcopy(messages))
        self.release.wait(1)
        return "超时后不应采用这个回答"


@pytest.mark.parametrize(
    ("step_timeout", "total_timeout", "reason"),
    [(0.02, 1.0, "step_timeout"), (1.0, 0.02, "total_timeout")],
)
def test_step_and_total_timeouts_stop_waiting(step_timeout, total_timeout, reason):
    mock = BlockingMockLLM()
    try:
        result = agent.run_agent(
            f"检查项目 {SMOKE}",
            mock,
            step_timeout=step_timeout,
            total_timeout=total_timeout,
        )
    finally:
        mock.release.set()
    assert result["status"] == "incomplete"
    assert result["reason"] == reason
    assert result["steps_taken"] == 1
    assert len(mock.received_messages) <= 1
    assert result["answer"] is None
    assert "目前进度" in result["message"]

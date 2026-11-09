"""第20轮：确定性任务理解、追问与文件会话，全程不联网。"""

import json

import agent
import llm
import tools
from understanding import SessionStore, understand_task


def test_windows_path_and_diagnostic_intent_use_rules_first(tmp_path):
    mock = llm.MockLLM()
    result = understand_task(
        r"帮我看下 D:\code\demo 这个项目哪里坏了",
        llm=mock,
        session_path=tmp_path / ".session.json",
    )
    assert result["intent"] == "check"
    assert result["project_path"] == r"D:\code\demo"
    assert result["intent_source"] == "rules"
    assert result["project_path_source"] == "rules"
    assert result["needs_clarification"] is False
    assert result["model_supplement"] == "not_requested"
    assert mock.received_messages == []


def test_missing_path_generates_clarification_and_persists_pending_intent(tmp_path):
    session = tmp_path / ".session.json"
    result = understand_task("帮我检查一下", session_path=session)
    assert result["intent"] == "check"
    assert result["project_path"] is None
    assert result["missing_fields"] == ["project_path"]
    assert result["needs_clarification"] is True
    assert "项目路径" in result["clarification"]
    saved = json.loads(session.read_text(encoding="utf-8"))
    assert saved["current_project"] is None
    assert saved["recent_intent"] == "check"
    assert saved["pending"]["missing_fields"] == ["project_path"]


def test_agent_asks_before_model_or_tools_when_path_is_missing(monkeypatch, tmp_path):
    def forbidden_tool(*args):
        raise AssertionError("缺少路径时禁止执行工具")

    monkeypatch.setattr(tools, "execute_tool", forbidden_tool)
    mock = llm.MockLLM(default_response="不应使用这个回答")
    result = agent.run_agent(
        "帮我检查一下", llm=mock, session_path=tmp_path / ".session.json"
    )
    assert result["status"] == "needs_clarification"
    assert result["reason"] == "missing_project_path"
    assert result["steps_taken"] == 0
    assert result["progress"] == []
    assert result["answer"] == result["question"]
    assert mock.received_messages == []


def test_file_session_restores_intent_and_project_across_calls(tmp_path):
    session = tmp_path / ".session.json"
    first = understand_task("帮我检查一下", session_path=session)
    assert first["needs_clarification"] is True

    second = understand_task(r"D:\code\demo", session_path=session)
    assert second["intent"] == "check"
    assert second["intent_source"] == "session"
    assert second["project_path"] == r"D:\code\demo"
    assert second["project_path_source"] == "rules"
    assert second["needs_clarification"] is False

    reloaded = SessionStore(session).load()
    assert reloaded["current_project"] == r"D:\code\demo"
    third = understand_task("再检查一下", session_path=session)
    assert third["project_path"] == r"D:\code\demo"
    assert third["project_path_source"] == "session"
    assert third["needs_clarification"] is False


def test_model_can_supplement_missing_intent_after_rules(tmp_path):
    mock = llm.MockLLM(
        responses={
            "任务理解补充": json.dumps(
                {"intent": "fix", "project_path": None, "entry": None}
            )
        }
    )
    result = understand_task(
        "帮我处理一下", llm=mock, session_path=tmp_path / ".session.json"
    )
    assert result["intent"] == "fix"
    assert result["intent_source"] == "model"
    assert result["model_supplement"] == "applied"
    assert result["missing_fields"] == ["project_path"]
    assert len(mock.received_messages) == 1


def test_model_invented_path_is_rejected(tmp_path):
    invented = r"D:\invented\other-project"
    mock = llm.MockLLM(
        responses={
            "任务理解补充": json.dumps(
                {"intent": "check", "project_path": invented, "entry": None}
            )
        }
    )
    result = understand_task(
        "帮我处理一下", llm=mock, session_path=tmp_path / ".session.json"
    )
    assert result["intent"] == "check"
    assert result["project_path"] is None
    assert result["missing_fields"] == ["project_path"]
    assert result["needs_clarification"] is True


def test_run_requires_explicit_project_and_entry(tmp_path):
    result = understand_task(
        r"运行 D:\code\demo", session_path=tmp_path / ".session.json"
    )
    assert result["intent"] == "run"
    assert result["project_path"] == r"D:\code\demo"
    assert result["missing_fields"] == ["entry"]
    assert "入口文件" in result["clarification"]


def test_general_question_does_not_require_a_project(tmp_path):
    result = understand_task(
        "为什么 Python 会报告缩进错误？", session_path=tmp_path / ".session.json"
    )
    assert result["intent"] == "question"
    assert result["project_path"] is None
    assert result["needs_clarification"] is False
    assert result["clarification"] is None

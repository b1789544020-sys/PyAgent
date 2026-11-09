"""对话入口测试：真实离线 smoke、异常续聊与 Mock ReAct 闭环。"""

import json
from pathlib import Path
import sys

import pytest


AGENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = AGENT_DIR.parent
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

import config  # noqa: E402
import dispatcher  # noqa: E402
import llm  # noqa: E402
import main as agent_main  # noqa: E402
import understanding  # noqa: E402


SMOKE = PROJECT_ROOT / "04_golden_tests" / "smoke_01"


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch, tmp_path):
    for key in config.CONFIG_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(understanding, "DEFAULT_SESSION_PATH", tmp_path / ".session.json")


def structured_output(capsys):
    output = capsys.readouterr().out
    _, raw = output.split("关键结构化信息：\n", 1)
    return output, json.loads(raw)


def test_offline_ask_uses_real_dispatcher_and_template(capsys):
    assert agent_main.main(["ask", f"check {SMOKE}"]) == 0
    output, info = structured_output(capsys)
    assert output.startswith("当前模式：离线")
    assert "检查完成，发现 1 个问题。 DR_0001（main.py:4" in output
    assert info["command"] == "check"
    assert info["issue_count"] == 1
    assert info["changed_files"] == []
    assert info["verification"] == {"status": "not_run", "passed": None}


def test_offline_repl_rejects_bad_turn_and_continues(monkeypatch, capsys):
    commands = iter([f'check "{SMOKE}"', "bad-command", "quit"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(commands))
    assert agent_main.main([]) == 0
    output = capsys.readouterr().out
    assert "检查完成，发现 1 个问题" in output
    assert "命令或参数无效" in output
    assert output.index("检查完成") < output.index("命令或参数无效") < output.index("已退出")


def test_repl_catches_dispatcher_exception_instead_of_exiting(monkeypatch, capsys):
    commands = iter(["check demo", "quit"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(commands))
    monkeypatch.setattr(
        dispatcher,
        "main",
        lambda argv, safety_policy=None: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert agent_main.main([]) == 0
    output = capsys.readouterr().out
    assert "本轮失败：RuntimeError: boom" in output
    assert "已退出。" in output


def test_online_ask_runs_mock_react_loop_and_prints_model_answer(monkeypatch, capsys):
    monkeypatch.setenv("AGENT_LLM", "mock")
    monkeypatch.setenv("AGENT_MODEL", "mock-main-test")
    mock = llm.MockLLM(responses={
        '"command": "check"': "检查结论：smoke_01 的 main.py 第 4 行缺少冒号。",
        "哪里坏了": {
            "tool_calls": [{
                "id": "call_main_check",
                "function": {
                    "name": "check_project",
                    "arguments": json.dumps({"path": str(SMOKE)}, ensure_ascii=False),
                },
            }],
        },
    })
    monkeypatch.setattr(llm, "get_llm", lambda: mock)
    assert agent_main.main(["ask", f"帮我看下 {SMOKE} 哪里坏了"]) == 0
    output, info = structured_output(capsys)
    assert "当前模式：在线（模型 mock-main-test）" in output
    assert "检查结论：smoke_01 的 main.py 第 4 行缺少冒号。" in output
    assert info["steps_taken"] == 2
    assert info["issue_count"] == 1
    assert info["changed_files"] == []
    assert len(mock.received_messages) == 2
    assert any(message["role"] == "tool" for message in mock.received_messages[1])


def test_fix_summary_lists_only_applied_files_and_verification():
    target = str(SMOKE / "main.py")
    payload = {
        "command": "fix",
        "status": "completed",
        "modified": True,
        "actions": [{
            "status": "applied_verified",
            "file": target,
            "applied": True,
        }],
        "verification": {"status": "passed", "passed": True},
    }
    rendered = agent_main.format_result(payload, online=False)
    info = agent_main.key_information(payload)
    assert "修复完成，修改 1 个文件，验证通过。" in rendered
    assert info["changed_files"] == [target]
    assert info["verification"]["passed"] is True

    dry_run = dict(payload, dry_run=True, modified=False, actions=[], verification={
        "status": "skipped", "passed": None,
    })
    assert agent_main.key_information(dry_run)["changed_files"] == []


def test_refusal_summary_preserves_candidates_but_not_sources():
    candidates = [{"id": "S1", "title": "弱相关候选"}]
    payload = {
        "status": "completed",
        "command": "docs_ask",
        "mode": "refusal",
        "generation": "not_run",
        "sources": [],
        "candidates": candidates,
    }
    info = agent_main.key_information(payload)
    assert info["sources"] == []
    assert info["candidates"] == candidates

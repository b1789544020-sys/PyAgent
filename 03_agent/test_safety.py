"""第22轮：安全确认、真实修复闭环与幻觉纠正，全程不联网。"""

import json
from pathlib import Path
import shutil
import sys

import pytest


AGENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = AGENT_DIR.parent
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

import agent  # noqa: E402
import config  # noqa: E402
import llm  # noqa: E402
import main as agent_main  # noqa: E402
import safety  # noqa: E402
import tools  # noqa: E402
import understanding  # noqa: E402


SMOKE = PROJECT_ROOT / "04_golden_tests" / "smoke_01"


@pytest.fixture(autouse=True)
def isolated_config_and_session(monkeypatch, tmp_path):
    for key in config.CONFIG_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(understanding, "DEFAULT_SESSION_PATH", tmp_path / ".session.json")


def make_smoke_copy(tmp_path):
    project = tmp_path / "smoke_01"
    project.mkdir()
    for name in ("main.py", "helper.py"):
        shutil.copyfile(SMOKE / name, project / name)
    return project


def _call(name, arguments):
    return {
        "tool_calls": [{
            "id": "round22-call",
            "type": "function",
            "function": {
                "name": name,
                "arguments": json.dumps(arguments, ensure_ascii=False),
            },
        }],
    }


def test_e2e_mock_real_fix_after_yes_has_applied_verification(tmp_path):
    project = make_smoke_copy(tmp_path)
    target = project / "main.py"
    original = target.read_bytes()
    notices = []
    prompts = []
    policy = safety.SafetyPolicy(
        interactive=True,
        input_fn=lambda prompt: prompts.append(prompt) or "yes",
        emit=notices.append,
    )
    mock = llm.MockLLM(responses={
        '"verification": {': "已修复，验证通过。",
        "修复项目": _call("fix_project", {"path": str(project), "dry_run": False}),
    })

    result = agent.run_agent(f"修复项目 {project}", mock, safety_policy=policy)

    assert result["status"] == "completed"
    assert result["evidence"]["verification_passed"] is True
    assert result["progress"][0]["result"]["actions"][0]["status"] == "applied_verified"
    assert target.read_bytes() == original.replace(b"def main()", b"def main():", 1)
    assert len(list(project.glob("*.bak_*"))) == 1
    assert len(prompts) == 1 and "y/yes" in prompts[0]
    assert len(notices) == 1
    assert "备份并尝试自动修复" in notices[0] and str(project) in notices[0]
    assert len(mock.received_messages) == 2


def test_e2e_mock_high_risk_write_needs_confirmation_and_never_runs(tmp_path):
    target = tmp_path / "must-not-exist.txt"
    notices = []
    policy = safety.SafetyPolicy(interactive=False, emit=notices.append)
    mock = llm.MockLLM(responses={
        '"status": "needs_confirmation"': "写入未执行，需要用户确认。",
        "修复项目": _call("write_file", {"path": str(target), "content": "禁止落盘"}),
    })

    result = agent.run_agent(f"修复项目 {SMOKE}", mock, safety_policy=policy)

    observation = result["progress"][0]["result"]
    assert observation["status"] == "needs_confirmation"
    assert observation["error"]["type"] == "ConfirmationRequired"
    assert observation["confirmed"] is False
    assert not target.exists()
    assert result["evidence"]["successful_tools"] == []
    assert result["status"] == "incomplete"
    assert "创建或覆盖" in notices[0] and str(target) in notices[0]
    second_tool_message = json.loads(mock.received_messages[1][-1]["content"])
    assert second_tool_message["status"] == "needs_confirmation"


def test_e2e_mock_false_success_is_corrected_and_marked_unverified():
    mock = llm.MockLLM(
        responses={safety.CORRECTION_PROMPT: "尚未修复；没有执行任何工具。"},
        default_response="已修复，全部通过。",
    )

    result = agent.run_agent(f"修复项目 {SMOKE}", mock)

    assert result["status"] == "incomplete"
    assert result["answer"] == "尚未修复；没有执行任何工具。"
    assert result["steps_taken"] == 2
    assert result["unverified_claims"] == [{
        "step": 1,
        "type": "unverified_claim",
        "status": "待验证",
        "claim": "已修复，全部通过。",
        "reason": "missing_tool_evidence",
    }]
    assert mock.received_messages[1][-1]["role"] == "system"
    assert mock.received_messages[1][-1]["content"].startswith("只能基于工具结果作答")


@pytest.mark.parametrize(
    ("name", "arguments", "expected"),
    [
        ("write_file", {"path": "a.txt", "content": "x"}, True),
        ("fix_project", {"path": "demo", "dry_run": False}, True),
        ("fix_project", {"path": "demo", "dry_run": True}, False),
        ("fix_project", {"path": "demo"}, False),
        ("rollback_project", {"path": "demo"}, True),
        ("run_project", {"path": "demo", "entry": "main.py"}, True),
        ("check_project", {"path": "demo"}, False),
        ("read_file", {"path": "a.txt"}, False),
    ],
)
def test_confirmation_matrix(name, arguments, expected):
    assert safety.requires_confirmation(name, arguments) is expected


@pytest.mark.parametrize("answer", ["y", "yes", " YES "])
def test_only_y_or_yes_allows_interactive_operation(answer):
    prompts = []
    policy = safety.SafetyPolicy(
        interactive=True,
        input_fn=lambda prompt: prompts.append(prompt) or answer,
        emit=lambda message: None,
    )
    assert policy.authorize("run_project", {"path": "demo", "entry": "main.py"}) is None
    assert len(prompts) == 1


def test_non_yes_answer_blocks_and_default_is_not_assume_yes():
    notices = []
    policy = safety.SafetyPolicy(
        interactive=True, input_fn=lambda prompt: "n", emit=notices.append
    )
    result = policy.authorize("write_file", {"path": "a.txt", "content": "secret"})
    assert policy.assume_yes is False
    assert result["status"] == "blocked"
    assert result["error"]["type"] == "ConfirmationDenied"
    assert "secret" not in notices[0]
    assert "a.txt" in notices[0]


def test_repl_denial_is_visible_and_continues(monkeypatch, capsys):
    commands = iter(["run_project demo main.py", "n", "quit"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(commands))

    def forbidden_tool(*args):
        raise AssertionError("拒绝确认后不得执行工具")

    monkeypatch.setattr(tools, "execute_tool", forbidden_tool)
    assert agent_main.main([]) == 0
    output = capsys.readouterr().out
    assert "安全确认：执行项目 Python 入口" in output
    assert "影响范围：项目目录：demo" in output
    assert "用户未输入 y/yes" in output
    assert '"status": "blocked"' in output
    assert output.index("用户未输入 y/yes") < output.index("已退出。")


def test_yes_flag_is_opt_in_and_noninteractive_default_blocks(monkeypatch, capsys):
    calls = []

    def fake_execute(name, args):
        calls.append((name, args))
        return {
            "status": "completed", "success": True, "returncode": 0,
            "entry": args["entry"], "stdout": "", "stderr": "",
        }

    monkeypatch.setattr(tools, "execute_tool", fake_execute)
    monkeypatch.setattr(
        "builtins.input",
        lambda prompt: (_ for _ in ()).throw(AssertionError("默认模式不应询问")),
    )
    assert agent_main.main(["ask", "run_project demo main.py"]) == 1
    assert calls == []
    assert "needs_confirmation" in capsys.readouterr().out

    assert agent_main.main(["--yes", "ask", "run_project demo main.py"]) == 0
    assert calls == [("run_project", {"path": "demo", "entry": "main.py"})]
    output = capsys.readouterr().out
    assert "安全确认：执行项目 Python 入口" in output
    assert "显式指定 --yes" in output


def test_repl_command_cannot_enable_yes_after_startup(monkeypatch, capsys):
    commands = iter(["run_project demo main.py --yes", "quit"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(commands))

    def forbidden_tool(*args):
        raise AssertionError("REPL 内 --yes 不得获得信任或执行工具")

    monkeypatch.setattr(tools, "execute_tool", forbidden_tool)
    assert agent_main.main([]) == 0
    output = capsys.readouterr().out
    assert "--yes 只能在启动 PyAgent 时显式指定" in output
    assert '"status": "error"' in output
    assert "已退出。" in output


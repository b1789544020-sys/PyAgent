"""双模式调度测试；不联网，写操作映射均使用工具替身。"""

import json
from pathlib import Path
import shutil
import sys

import pytest


AGENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = AGENT_DIR.parent
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

import config  # noqa: E402
import dispatcher  # noqa: E402
import safety  # noqa: E402
import understanding  # noqa: E402


SMOKE = PROJECT_ROOT / "04_golden_tests" / "smoke_01"


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch, tmp_path):
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


def read_json_output(capsys):
    lines = capsys.readouterr().out.splitlines()
    json_start = next(index for index, line in enumerate(lines) if line.startswith("{"))
    return lines[0], json.loads("\n".join(lines[json_start:]))


def test_unconfigured_check_prints_offline_mode_and_real_issues(
    tmp_path, capsys
):
    project = make_smoke_copy(tmp_path)

    assert dispatcher.main(["check", str(project)]) == 0

    mode, report = read_json_output(capsys)
    assert mode == "当前模式：离线（未检测到模型，仅支持确定性命令）"
    assert report["command"] == "check"
    assert report["issues"][0]["rule"] == "DR_0001"
    assert report["issues"][0]["file"] == "main.py"
    assert report["issues"][0]["line"] == 4


@pytest.mark.parametrize(
    ("argv", "expected_tool", "expected_args"),
    [
        (["check", "demo"], "check_project", {"path": "demo"}),
        (
            ["fix", "demo", "--dry-run"],
            "fix_project",
            {"path": "demo", "dry_run": True},
        ),
        (["fix", "demo"], "fix_project", {"path": "demo", "dry_run": False}),
        (["rollback", "demo"], "rollback_project", {"path": "demo"}),
        (
            ["run_project", "demo", "main.py"],
            "run_project",
            {"path": "demo", "entry": "main.py"},
        ),
    ],
)
def test_offline_commands_use_fixed_shared_tool_mapping(
    monkeypatch, capsys, argv, expected_tool, expected_args
):
    calls = []

    def fake_execute(name, args):
        calls.append((name, args))
        return {"status": "completed", "tool": name}

    monkeypatch.setattr(dispatcher.tools, "execute_tool", fake_execute)
    confirmations = []
    notices = []
    policy = safety.SafetyPolicy(
        interactive=True,
        input_fn=lambda prompt: confirmations.append(prompt) or "yes",
        emit=notices.append,
    )

    assert dispatcher.main(argv, safety_policy=policy) == 0

    assert calls == [(expected_tool, expected_args)]
    expected_confirmation = safety.requires_confirmation(expected_tool, expected_args)
    assert len(confirmations) == int(expected_confirmation)
    assert len(notices) == int(expected_confirmation)
    mode, result = read_json_output(capsys)
    assert mode.startswith("当前模式：离线")
    assert result == {"status": "completed", "tool": expected_tool}


def test_mock_config_selects_online_mode_without_calling_tools(
    monkeypatch, capsys
):
    monkeypatch.setenv("AGENT_LLM", "mock")
    monkeypatch.setenv("AGENT_MODEL", "mock-dispatcher-test")

    def forbidden_tool(*args):
        raise AssertionError("无工具调用的最终回答不应访问引擎")

    monkeypatch.setattr(dispatcher.tools, "execute_tool", forbidden_tool)
    assert dispatcher.main(["请检查项目"]) == 0

    mode, payload = read_json_output(capsys)
    assert mode == "当前模式：在线（模型 mock-dispatcher-test）"
    assert payload["status"] == "needs_clarification"
    assert payload["reason"] == "missing_project_path"
    assert payload["understanding"]["project_path"] is None
    assert payload["steps_taken"] == 0


def test_online_natural_language_is_passed_to_agent_runner(
    monkeypatch, capsys
):
    monkeypatch.setenv("AGENT_LLM", "mock")
    received = []

    def fake_agent(prompt):
        received.append(prompt)
        return 0

    assert dispatcher.main(
        ["请", "检查项目"], agent_runner=fake_agent
    ) == 0

    assert received == ["请 检查项目"]
    assert capsys.readouterr().out.splitlines()[0].startswith("当前模式：在线")


def test_offline_mode_rejects_natural_language_instead_of_faking_model(
    capsys,
):
    with pytest.raises(SystemExit) as caught:
        dispatcher.main(["请检查项目"])

    assert caught.value.code == 2
    output = capsys.readouterr()
    assert output.out.splitlines()[0] == (
        "当前模式：离线（未检测到模型，仅支持确定性命令）"
    )
    assert "在线 Agent" not in output.out
    assert "invalid choice" in output.err

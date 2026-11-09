"""模型工具测试；所有写操作和项目运行都限制在 pytest 临时副本。"""

import json
from pathlib import Path
import shutil
import sys


AGENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = AGENT_DIR.parent
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

import tools  # noqa: E402


SMOKE = PROJECT_ROOT / "04_golden_tests" / "smoke_01"


def make_smoke_copy(tmp_path):
    project = tmp_path / "smoke_01"
    project.mkdir()
    for name in ("main.py", "helper.py"):
        shutil.copyfile(SMOKE / name, project / name)
    return project


def test_registry_has_json_schemas_and_dangerous_markers():
    assert {tool["name"] for tool in tools.TOOLS} == {
        "check_project",
        "fix_project",
        "rollback_project",
        "run_project",
        "read_file",
        "write_file",
    }
    for tool in tools.TOOLS:
        assert tool["description"]
        assert tool["parameters"]["type"] == "object"
        assert tool["parameters"]["additionalProperties"] is False
        assert callable(tool["execute"])
        assert isinstance(tool["dangerous"], bool)
        json.dumps(tool["parameters"], ensure_ascii=False)
    assert {
        tool["name"] for tool in tools.TOOLS if tool["dangerous"]
    } == {"fix_project", "rollback_project", "run_project", "write_file"}


def test_check_project_returns_structured_issues_without_changes(tmp_path):
    project = make_smoke_copy(tmp_path)
    original = (project / "main.py").read_bytes()

    report = tools.execute_tool("check_project", {"path": str(project)})

    assert report["command"] == "check"
    assert report["modified"] is False
    assert report["summary"]["issue_count"] == 1
    assert report["issues"][0]["rule"] == "DR_0001"
    assert report["issues"][0]["file"] == "main.py"
    assert report["issues"][0]["line"] == 4
    assert (project / "main.py").read_bytes() == original
    assert list(project.glob("*.bak_*")) == []


def test_fix_project_preview_apply_and_rollback(tmp_path):
    project = make_smoke_copy(tmp_path)
    target = project / "main.py"
    original = target.read_bytes()

    preview = tools.execute_tool(
        "fix_project", {"path": str(project), "dry_run": True}
    )
    assert preview["actions"][0]["status"] == "dry_run"
    assert preview["actions"][0]["patch"]["operation"] == "append_colon"
    assert "+def main():" in preview["actions"][0]["diff"]
    assert preview["verification"]["status"] == "skipped"
    assert target.read_bytes() == original
    assert list(project.glob("*.bak_*")) == []

    fixed = tools.execute_tool(
        "fix_project", {"path": str(project), "dry_run": False}
    )
    assert fixed["actions"][0]["status"] == "applied_verified"
    assert fixed["verification"]["passed"] is True
    assert fixed["actions"][0]["verification"]["passed"] is True
    assert target.read_bytes() == original.replace(
        b"def main()", b"def main():", 1
    )

    restored = tools.execute_tool("rollback_project", {"path": str(project)})
    assert restored["summary"]["restored"] is True
    assert restored["actions"][0]["status"] == "rolled_back"
    assert target.read_bytes() == original


def test_run_project_returns_stdout_and_stderr(tmp_path):
    project = make_smoke_copy(tmp_path)

    result = tools.execute_tool(
        "run_project", {"path": str(project), "entry": "helper.py"}
    )

    assert result["success"] is True
    assert result["returncode"] == 0
    assert result["stdout"] == "smoke_01\n"
    assert result["stderr"] == ""
    assert result["traceback"]["has_traceback"] is False


def test_read_file_returns_content(tmp_path):
    project = make_smoke_copy(tmp_path)
    target = project / "helper.py"

    result = tools.execute_tool("read_file", {"path": str(target)})

    assert result["status"] == "completed"
    assert result["path"] == str(target.resolve())
    assert 'return "smoke_01"' in result["content"]
    assert result["bytes"] == len(target.read_bytes())


def test_write_file_writes_only_temporary_smoke_copy(tmp_path):
    project = make_smoke_copy(tmp_path)
    target = project / "notes.txt"
    content = "第一行\n第二行\n"

    result = tools.execute_tool(
        "write_file", {"path": str(target), "content": content}
    )

    assert result["status"] == "completed"
    assert result["dangerous"] is True
    assert result["bytes_written"] == len(content.encode("utf-8"))
    assert target.read_text(encoding="utf-8") == content
    assert not (SMOKE / "notes.txt").exists()


def test_execute_tool_returns_structured_argument_errors(tmp_path):
    unknown = tools.execute_tool("not_a_tool", {})
    invalid_args = tools.execute_tool("read_file", [])
    missing_file = tools.execute_tool(
        "read_file", {"path": str(tmp_path / "missing.txt")}
    )

    assert unknown["error"]["type"] == "ToolNotFound"
    assert invalid_args["error"]["type"] == "InvalidArguments"
    assert missing_file["status"] == "error"
    assert missing_file["error"]["type"] == "FileNotFoundError"

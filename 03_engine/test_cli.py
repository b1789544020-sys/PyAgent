"""CLI 的端到端回归测试；所有写操作都发生在 pytest 临时目录。"""

import hashlib
import json
from pathlib import Path
import sys

ENGINE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = ENGINE_DIR.parent
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

import cli  # noqa: E402
import fixer  # noqa: E402

SMOKE = PROJECT_ROOT / "04_golden_tests" / "smoke_01"


def make_project(tmp_path):
    target = tmp_path / "main.py"
    target.write_bytes((SMOKE / "main.py").read_bytes())
    (tmp_path / "helper.py").write_bytes((SMOKE / "helper.py").read_bytes())
    return target


def invoke(capsys, argv):
    assert cli.main(argv) == 0
    return json.loads(capsys.readouterr().out)


def test_check_reports_rule_location_and_never_modifies(tmp_path, capsys):
    target = make_project(tmp_path)
    original = target.read_bytes()

    report = invoke(capsys, ["check", str(tmp_path)])

    assert report["command"] == "check"
    assert report["modified"] is False
    assert report["summary"]["issue_count"] == 1
    issue = report["issues"][0]
    assert issue["rule"] == "DR_0001"
    assert issue["file"] == "main.py"
    assert issue["line"] == 4
    assert issue["confidence"] == "high"
    assert issue["suggestion"]
    assert target.read_bytes() == original
    assert list(tmp_path.glob("*.bak_*")) == []


def test_fix_dry_run_outputs_patch_without_disk_changes(tmp_path, capsys):
    target = make_project(tmp_path)
    original = target.read_bytes()

    report = invoke(capsys, ["fix", str(tmp_path), "--dry-run"])

    assert report["dry_run"] is True
    assert report["modified"] is False
    assert report["verification"]["status"] == "skipped"
    assert report["summary"]["dry_run_count"] == 1
    action = report["actions"][0]
    assert action["status"] == "dry_run"
    assert action["backup"] is None
    assert action["patch"] == {
        "operation": "append_colon",
        "line": 4,
        "before": "def main()",
        "after": "def main():",
    }
    assert "-def main()" in action["diff"]
    assert "+def main():" in action["diff"]
    assert target.read_bytes() == original
    assert list(tmp_path.glob("*.bak_*")) == []


def test_real_fix_verifies_and_rollback_uses_latest_backup(tmp_path, capsys):
    target = make_project(tmp_path)
    original = target.read_bytes()

    fixed = invoke(capsys, ["fix", str(tmp_path)])

    assert fixed["modified"] is True
    assert fixed["verification"]["passed"] is True
    assert fixed["summary"]["verified_fix_count"] == 1
    action = fixed["actions"][0]
    assert action["status"] == "applied_verified"
    assert action["patch"]["line"] == 4
    assert action["verification"]["passed"] is True
    assert all(run["returncode"] == 0 for run in action["verification"]["runs"])
    assert {run["entry"] for run in action["verification"]["runs"]} == {
        "main.py",
        "helper.py",
    }
    assert target.read_bytes() == original.replace(b"def main()", b"def main():", 1)
    saved = Path(action["backup"])
    assert saved.is_file()
    backup_hash = hashlib.sha256(saved.read_bytes()).hexdigest()

    restored = invoke(capsys, ["rollback", str(tmp_path)])

    assert restored["summary"]["restored"] is True
    assert restored["summary"]["selected_backup"] == saved.name
    assert restored["summary"]["restored_file"] == "main.py"
    assert restored["backups"][0]["sha256"] == backup_hash
    assert restored["actions"][0]["status"] == "rolled_back"
    assert restored["verification"]["passed"] is False
    assert target.read_bytes() == original
    assert hashlib.sha256(saved.read_bytes()).hexdigest() == backup_hash


def test_failed_post_fix_verification_rolls_back_automatically(
    tmp_path, capsys, monkeypatch
):
    target = make_project(tmp_path)
    original = target.read_bytes()

    def failed_verification(project):
        return {
            "passed": False,
            "project": str(project),
            "scan": None,
            "runs": [],
            "message": "测试注入的验证失败",
        }

    monkeypatch.setattr(fixer, "verify_after_fix", failed_verification)
    report = invoke(capsys, ["fix", str(tmp_path)])

    action = report["actions"][0]
    assert action["status"] == "rolled_back"
    assert action["rollback"]["restored"] is True
    assert report["verification"]["passed"] is False
    assert report["modified"] is False
    assert report["summary"]["rolled_back_count"] == 1
    assert "已使用本次备份回滚" in report["needs_manual"][0]["reason"]
    assert target.read_bytes() == original
    assert Path(action["backup"]).read_bytes() == original


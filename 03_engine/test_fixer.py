"""fixer 的 pytest 回归测试；真实 smoke 文件只在临时副本中修复。"""

import hashlib
import json
from pathlib import Path
import re
import sys

ENGINE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = ENGINE_DIR.parent
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from fixer import apply_fix, backup, rollback, verify_after_fix  # noqa: E402
from matcher import load_rules  # noqa: E402

RULES = load_rules(PROJECT_ROOT / "02_assets" / "diagnostic_rules.jsonl")
RULE_BY_ID = {rule["id"]: rule for rule in RULES}
SMOKE = PROJECT_ROOT / "04_golden_tests" / "smoke_01"


def make_smoke_copy(tmp_path):
    target = tmp_path / "main.py"
    target.write_bytes((SMOKE / "main.py").read_bytes())
    (tmp_path / "helper.py").write_bytes((SMOKE / "helper.py").read_bytes())
    return target


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_backup_success_preserves_original_bytes(tmp_path):
    target = tmp_path / "sample.py"
    original = "def main():\r\n    print('backup')\r\n".encode()
    target.write_bytes(original)

    saved = backup(target)

    assert saved.is_file()
    assert re.fullmatch(r"sample\.py\.bak_\d{8}_\d{6}", saved.name)
    assert saved.read_bytes() == original
    assert target.read_bytes() == original


def test_fix_verify_and_rollback_are_byte_exact(tmp_path):
    target = make_smoke_copy(tmp_path)
    original = target.read_bytes()
    saved = backup(target)
    backup_hash = digest(saved)

    result = apply_fix(RULE_BY_ID["DR_0001"], target)

    assert result["status"] == "applied"
    assert result["applied"] is True
    assert result["patch"]["line"] == 4
    assert result["patch"]["before"] == "def main()"
    assert result["patch"]["after"] == "def main():"
    assert "-def main()" in result["diff"]
    assert "+def main():" in result["diff"]
    assert target.read_bytes() == original.replace(b"def main()", b"def main():", 1)
    assert Path(result["backup"]).read_bytes() == original
    json.dumps(result, ensure_ascii=False)

    verification = verify_after_fix(tmp_path)
    assert verification["passed"] is True
    assert {run["entry"] for run in verification["runs"]} == {"main.py", "helper.py"}
    assert all(run["returncode"] == 0 for run in verification["runs"])

    restored = rollback(saved)
    assert restored["restored"] is True
    assert target.read_bytes() == original
    assert digest(saved) == backup_hash
    assert verify_after_fix(tmp_path)["passed"] is False


def test_low_confidence_rule_is_rejected_without_backup(tmp_path):
    target = make_smoke_copy(tmp_path)
    original = target.read_bytes()
    rule = dict(RULE_BY_ID["DR_0001"], confidence="low")

    result = apply_fix(rule, target)

    assert result["status"] == "rejected"
    assert result["applied"] is False
    assert "需要人工决定" in result["message"]
    assert result["diff"] == ""
    assert target.read_bytes() == original
    assert list(tmp_path.glob("*.bak_*")) == []


def test_fix_marked_no_auto_is_rejected(tmp_path):
    target = make_smoke_copy(tmp_path)
    original = target.read_bytes()
    rule = dict(RULE_BY_ID["DR_0001"], fix="不自动修；仅用于测试")

    result = apply_fix(rule, target)

    assert result["status"] == "rejected"
    assert "需要人工决定" in result["message"]
    assert target.read_bytes() == original
    assert list(tmp_path.glob("*.bak_*")) == []


def test_other_syntax_error_hits_limit_and_leaves_file_unchanged(tmp_path):
    target = tmp_path / "main.py"
    original = b'def main()\n    print("ok")\nif True\n    print("bad")\n'
    target.write_bytes(original)

    result = apply_fix(RULE_BY_ID["DR_0001"], target)

    assert result["status"] == "needs_manual"
    assert "需要人工决定" in result["message"]
    assert "limit" in result["message"]
    assert target.read_bytes() == original
    assert list(tmp_path.glob("*.bak_*")) == []


def test_high_confidence_alone_does_not_allow_guessed_fix(tmp_path):
    target = tmp_path / "main.py"
    original = b"print(missing_name)\n"
    target.write_bytes(original)

    result = apply_fix(RULE_BY_ID["DR_0006"], target)

    assert result["status"] == "needs_manual"
    assert result["applied"] is False
    assert target.read_bytes() == original
    assert list(tmp_path.glob("*.bak_*")) == []


def test_public_mutable_default_requires_manual_without_disk_changes(tmp_path):
    source = PROJECT_ROOT / "04_golden_tests" / "case_04_mutable_default" / "inventory.py"
    target = tmp_path / "inventory.py"
    original = source.read_bytes()
    target.write_bytes(original)

    result = apply_fix(RULE_BY_ID["DR_0010"], target)

    assert result["status"] == "needs_manual"
    assert result["applied"] is False
    assert result["backup"] is None
    assert target.read_bytes() == original
    assert list(tmp_path.glob("*.bak_*")) == []


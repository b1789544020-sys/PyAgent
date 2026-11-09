"""对 smoke_01 执行三个核心函数的独立标准库冒烟检查。"""

import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SMOKE_ROOT = Path(__file__).resolve().parent / "smoke_01"
sys.path.insert(0, str(PROJECT_ROOT / "03_engine"))

from core import parse_traceback, run_project, scan_project  # noqa: E402


def main():
    scan = scan_project(SMOKE_ROOT)
    assert scan["python_files"] == ["helper.py", "main.py"]
    assert scan["entries"] == ["helper.py", "main.py"]
    assert scan["entry_reasons"] == {
        "helper.py": ["main_guard"],
        "main.py": ["filename"],
    }
    assert len(scan["parse_errors"]) == 1
    assert scan["parse_errors"][0]["file"] == "main.py"
    assert scan["parse_errors"][0]["line"] == 4

    run = run_project(SMOKE_ROOT, "main.py", timeout=5)
    assert not run["timed_out"]
    assert run["returncode"] != 0
    assert not run["success"]

    parsed = parse_traceback(run["stderr"])
    assert parsed["has_traceback"]
    assert parsed["error_type"] == "SyntaxError"
    assert parsed["line"] == 4
    assert Path(parsed["file"]).resolve() == (SMOKE_ROOT / "main.py").resolve()
    assert "def main()" in parsed["code"]
    assert run["traceback"] == parsed

    print(
        json.dumps(
            {"scan": scan, "run": run, "parsed_traceback": parsed},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

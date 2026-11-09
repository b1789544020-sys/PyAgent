"""PyAgent 的纯标准库离线项目扫描与运行内核。"""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import tokenize

__all__ = ["scan_project", "run_project", "parse_traceback"]

USAGE = """PyAgent 离线项目内核（仅使用 Python 标准库）

用法：
  python 03_engine/core.py scan ROOT
  python 03_engine/core.py run ROOT ENTRY [TIMEOUT]
  python 03_engine/core.py parse-traceback < stderr.txt
  python 03_engine/core.py --help

命令：
  scan             递归列出 ROOT 下的 .py 文件并识别入口。
  run              在受控子进程中运行 ROOT/ENTRY，TIMEOUT 默认为 10 秒。
  parse-traceback  从标准输入读取 stderr，输出结构化 Traceback。

所有结果均以 JSON 输出；ROOT 和 ENTRY 由调用方传入。
"""


def _as_root(root):
    root_path = Path(root).expanduser().resolve()
    if not root_path.exists():
        raise FileNotFoundError(f"项目目录不存在：{root_path}")
    if not root_path.is_dir():
        raise NotADirectoryError(f"项目路径不是目录：{root_path}")
    return root_path


def _relative_name(path, root):
    return path.relative_to(root).as_posix()


def _is_main_guard(node):
    if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
        return False
    comparison = node.test
    if len(comparison.ops) != 1 or not isinstance(comparison.ops[0], ast.Eq):
        return False
    if len(comparison.comparators) != 1:
        return False
    left = comparison.left
    right = comparison.comparators[0]
    return (
        isinstance(left, ast.Name)
        and left.id == "__name__"
        and isinstance(right, ast.Constant)
        and right.value == "__main__"
    ) or (
        isinstance(right, ast.Name)
        and right.id == "__name__"
        and isinstance(left, ast.Constant)
        and left.value == "__main__"
    )


def scan_project(root):
    """递归扫描 .py 文件，返回文件清单、入口及无法解析文件的信息。"""
    root_path = _as_root(root)
    paths = sorted(
        (path for path in root_path.rglob("*.py") if path.is_file()),
        key=lambda path: _relative_name(path, root_path),
    )
    python_files = []
    entries = []
    entry_reasons = {}
    parse_errors = []

    for path in paths:
        relative = _relative_name(path, root_path)
        python_files.append(relative)
        reasons = []
        if path.name.lower() in {"main.py", "app.py"}:
            reasons.append("filename")
        try:
            with tokenize.open(path) as source_file:
                source = source_file.read()
            tree = ast.parse(source, filename=str(path))
        except (OSError, UnicodeError, SyntaxError) as exc:
            parse_errors.append(
                {
                    "file": relative,
                    "line": getattr(exc, "lineno", None),
                    "error_type": type(exc).__name__,
                    "message": str(exc),
                }
            )
        else:
            if any(_is_main_guard(node) for node in tree.body):
                reasons.append("main_guard")
        if reasons:
            entries.append(relative)
            entry_reasons[relative] = reasons

    return {
        "root": str(root_path),
        "python_files": python_files,
        "file_count": len(python_files),
        "entries": entries,
        "entry_count": len(entries),
        "entry_reasons": entry_reasons,
        "parse_errors": parse_errors,
    }


def _normalize_output(value):
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def run_project(root, entry, timeout=10.0):
    """以当前解释器运行入口，捕获输出、返回码、耗时及超时状态。"""
    root_path = _as_root(root)
    entry_path = Path(entry).expanduser()
    if not entry_path.is_absolute():
        entry_path = root_path / entry_path
    entry_path = entry_path.resolve()
    try:
        relative_entry = _relative_name(entry_path, root_path)
    except ValueError as exc:
        raise ValueError("入口文件必须位于项目目录内") from exc
    if not entry_path.is_file():
        raise FileNotFoundError(f"入口文件不存在：{entry_path}")

    timeout_value = float(timeout)
    if timeout_value <= 0:
        raise ValueError("timeout 必须是大于 0 的秒数")

    command = [sys.executable, str(entry_path)]
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    result = {
        "root": str(root_path),
        "entry": relative_entry,
        "command": command,
        "cwd": str(root_path),
        "timeout_seconds": timeout_value,
        "timed_out": False,
        "returncode": None,
        "stdout": "",
        "stderr": "",
        "duration_seconds": 0.0,
        "success": False,
    }
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=str(root_path),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_value,
            env=environment,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        result.update(
            {
                "timed_out": True,
                "stdout": _normalize_output(exc.stdout),
                "stderr": _normalize_output(exc.stderr),
            }
        )
    else:
        result.update(
            {
                "returncode": completed.returncode,
                "stdout": completed.stdout,
                "stderr": completed.stderr,
                "success": completed.returncode == 0,
            }
        )
    result["duration_seconds"] = round(time.monotonic() - started, 6)
    result["traceback"] = parse_traceback(result["stderr"])
    return result


_FRAME_RE = re.compile(
    r'^\s*File "(?P<file>.+)", line (?P<line>\d+)(?:, in (?P<function>.+))?\s*$'
)
_EXCEPTION_RE = re.compile(r"^(?P<type>[\w.]+)(?::\s*(?P<message>.*))?$")


def parse_traceback(stderr):
    """把 CPython stderr Traceback 转成稳定、可 JSON 序列化的字典。"""
    parsed = {
        "has_traceback": False,
        "error_type": None,
        "error_message": None,
        "file": None,
        "line": None,
        "function": None,
        "code": None,
        "column": None,
        "caret": None,
        "frames": [],
    }
    if not stderr:
        return parsed

    lines = stderr.splitlines()
    marker_index = next(
        (index for index, line in enumerate(lines) if line.startswith("Traceback (most recent call last):")),
        None,
    )
    frames = []
    for index, line in enumerate(lines):
        match = _FRAME_RE.match(line)
        if not match:
            continue
        code = None
        column = None
        caret = None
        if index + 1 < len(lines):
            raw_code = lines[index + 1]
            if raw_code[:1].isspace() and raw_code.strip():
                code = raw_code.strip()
                indentation = len(raw_code) - len(raw_code.lstrip(" \t"))
                if index + 2 < len(lines):
                    raw_caret = lines[index + 2]
                    marker = raw_caret.strip()
                    if marker and set(marker) <= {"^", "~"}:
                        caret = marker
                        marker_index_in_line = min(
                            position
                            for position, character in enumerate(raw_caret)
                            if character in {"^", "~"}
                        )
                        column = max(marker_index_in_line - indentation + 1, 1)
        frames.append(
            {
                "file": match.group("file"),
                "line": int(match.group("line")),
                "function": match.group("function"),
                "code": code,
                "column": column,
                "caret": caret,
                "_index": index,
            }
        )

    if not frames and marker_index is None:
        return parsed

    search_start = frames[-1]["_index"] + 1 if frames else marker_index + 1
    exception = None
    for line in lines[search_start:]:
        if not line or line[0].isspace():
            continue
        exception = _EXCEPTION_RE.match(line)
        if exception:
            break

    parsed["has_traceback"] = True
    if exception:
        parsed["error_type"] = exception.group("type")
        parsed["error_message"] = exception.group("message") or ""
    if frames:
        location = frames[-1]
        for field in ("file", "line", "function", "code", "column", "caret"):
            parsed[field] = location[field]
        parsed["frames"] = [
            {key: value for key, value in frame.items() if key != "_index"}
            for frame in frames
        ]
    return parsed


def _main(argv):
    # Windows 捕获 CLI 输出时仍应使用确定的 UTF-8 编码。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if not argv or argv[0] in {"-h", "--help"}:
        print(USAGE)
        return 0

    command = argv[0]
    try:
        if command == "scan" and len(argv) == 2:
            result = scan_project(argv[1])
        elif command == "run" and len(argv) in {3, 4}:
            timeout = float(argv[3]) if len(argv) == 4 else 10.0
            result = run_project(argv[1], argv[2], timeout=timeout)
        elif command == "parse-traceback" and len(argv) == 1:
            result = parse_traceback(sys.stdin.read())
        else:
            print(USAGE, file=sys.stderr)
            return 2
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))


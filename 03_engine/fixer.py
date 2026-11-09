"""PyAgent 的保守修复、字节级备份与回滚工具。"""

from __future__ import annotations

import ast
from collections.abc import Mapping
from datetime import datetime
import difflib
from io import BytesIO
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import tokenize

from core import run_project, scan_project

__all__ = ["backup", "apply_fix", "rollback", "verify_after_fix"]

_BACKUP_NAME_RE = re.compile(r"^(?P<original>.+)\.bak_(?P<timestamp>\d{8}_\d{6})$")
_COMPOUND_HEAD_RE = re.compile(r"(?:async\s+)?(?:if|for|while|def|class|with)\b")


def _source_file(file):
    path = Path(file).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"源码文件不存在：{path}")
    return path


def backup(file):
    """以独占创建方式保存原字节；同名同内容备份可复用，绝不覆盖旧备份。"""
    path = _source_file(file)
    original = path.read_bytes()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = Path(str(path) + ".bak_" + timestamp)
    if backup_path.exists():
        if backup_path.is_file() and backup_path.read_bytes() == original:
            return backup_path
        raise FileExistsError(f"备份路径已存在且内容不同，需要人工决定：{backup_path}")
    with backup_path.open("xb") as stream:
        stream.write(original)
    try:
        shutil.copystat(path, backup_path)
    except OSError:
        # 字节备份已完整；元数据不支持复制不影响回滚内容。
        pass
    return backup_path


def _atomic_write(path, data):
    """在目标目录写临时文件后替换，避免半写入源码。"""
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else None
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if mode is not None:
            os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def rollback(backup):
    """从带合法时间戳后缀的备份恢复目标文件，备份本身只读不写。"""
    backup_path = Path(backup).expanduser().resolve()
    found = _BACKUP_NAME_RE.fullmatch(backup_path.name)
    if not found:
        raise ValueError(f"备份文件名缺少合法时间戳后缀：{backup_path}")
    if not backup_path.is_file():
        raise FileNotFoundError(f"备份文件不存在：{backup_path}")
    original_path = backup_path.with_name(found.group("original"))
    content = backup_path.read_bytes()
    _atomic_write(original_path, content)
    return {
        "restored": True,
        "file": str(original_path),
        "backup": str(backup_path),
        "bytes": len(content),
    }


def _result(rule, path, status, message, *, backup_path=None, patch=None, diff=""):
    return {
        "status": status,
        "applied": status == "applied",
        "rule_id": rule.get("id") if isinstance(rule, Mapping) else None,
        "file": str(path),
        "backup": str(backup_path) if backup_path else None,
        "patch": patch,
        "diff": diff,
        "message": message,
    }


def _decode_source(data):
    encoding, _ = tokenize.detect_encoding(BytesIO(data).readline)
    return data.decode(encoding), encoding


def _colon_patch(text, filename):
    """只构造 DR_0001 候选，不写文件；任何 limit 不明都给出理由。"""
    try:
        compile(text, filename, "exec")
    except SyntaxError as error:
        syntax_error = error
    except ValueError as error:
        return None, None, f"需要人工决定：源码无法编译：{error}"
    else:
        return None, None, "需要人工决定：文件没有与 DR_0001 对应的 SyntaxError"

    message = getattr(syntax_error, "msg", str(syntax_error)).strip().lower()
    line_number = syntax_error.lineno
    if message not in {"expected ':'", "invalid syntax"} or not line_number:
        return None, None, "需要人工决定：SyntaxError 不是可确认的复合语句头缺冒号"
    lines = text.splitlines(keepends=True)
    if line_number > len(lines):
        return None, None, "需要人工决定：SyntaxError 行号超出源码范围"
    if line_number >= len(lines):
        return None, None, "需要人工决定：缺冒号语句后没有可确认的缩进代码块"

    raw_line = lines[line_number - 1]
    body = raw_line.rstrip("\r\n")
    line_ending = raw_line[len(body):]
    header = body.strip()
    if not _COMPOUND_HEAD_RE.match(header) or header.endswith(":"):
        return None, None, "需要人工决定：报错行不是支持的完整复合语句头"
    try:
        ast.parse(header + ":\n    pass")
    except SyntaxError:
        return None, None, "需要人工决定：语句头残缺或括号未闭合，规则 limit 禁止自动修复"

    following = lines[line_number].rstrip("\r\n")
    indentation = lambda value: len(value) - len(value.lstrip(" \t"))
    if not following.strip() or indentation(following) <= indentation(body):
        return None, None, "需要人工决定：下一行未形成明确缩进代码块，规则 limit 禁止自动修复"

    content = body.rstrip(" \t")
    trailing_space = body[len(content):]
    fixed_body = content + ":" + trailing_space
    lines[line_number - 1] = fixed_body + line_ending
    fixed_text = "".join(lines)
    try:
        compile(fixed_text, filename, "exec")
    except (SyntaxError, ValueError) as error:
        return None, None, f"需要人工决定：补冒号后仍有其他语法错误，规则 limit 禁止自动修复：{error}"
    patch = {
        "operation": "append_colon",
        "line": line_number,
        "before": body,
        "after": fixed_body,
    }
    return fixed_text, patch, None


def apply_fix(rule, target):
    """只应用 DR_0001 的单字符定点修复，并返回 patch、unified diff 和备份路径。"""
    if isinstance(target, Mapping):
        target = target.get("file") or target.get("path")
    if not target:
        raise ValueError("target 必须是源码文件路径")
    path = Path(target).expanduser().resolve()
    if not isinstance(rule, Mapping):
        return _result({}, path, "rejected", "需要人工决定：规则必须是映射对象")
    if rule.get("confidence") != "high":
        return _result(rule, path, "rejected", "需要人工决定：只有 confidence=high 才允许自动修复")
    if str(rule.get("fix") or "").strip().startswith("不自动修"):
        return _result(rule, path, "rejected", "需要人工决定：规则 fix 明确不自动修")
    if rule.get("id") != "DR_0001":
        return _result(
            rule,
            path,
            "needs_manual",
            "需要人工决定：本轮没有为该规则实现满足全部 limit 的定点修复器",
        )

    path = _source_file(path)
    original_bytes = path.read_bytes()
    try:
        original_text, encoding = _decode_source(original_bytes)
    except (SyntaxError, UnicodeError, LookupError, ValueError) as error:
        return _result(rule, path, "needs_manual", f"需要人工决定：无法确定源码编码：{error}")
    fixed_text, patch, reason = _colon_patch(original_text, str(path))
    if reason:
        return _result(rule, path, "needs_manual", reason)
    fixed_bytes = fixed_text.encode(encoding)
    if len(fixed_bytes) != len(original_bytes) + 1:
        return _result(rule, path, "needs_manual", "需要人工决定：候选修改不是恰好增加一个字节")

    backup_path = backup(path)
    if path.read_bytes() != original_bytes:
        return _result(
            rule,
            path,
            "needs_manual",
            "需要人工决定：文件在修复准备期间发生变化",
            backup_path=backup_path,
        )
    _atomic_write(path, fixed_bytes)
    diff = "".join(
        difflib.unified_diff(
            original_text.splitlines(keepends=True),
            fixed_text.splitlines(keepends=True),
            fromfile=str(path),
            tofile=str(path) + " (fixed)",
        )
    )
    return _result(
        rule,
        path,
        "applied",
        "已在唯一报错语句头追加一个冒号；请运行 verify_after_fix",
        backup_path=backup_path,
        patch=patch,
        diff=diff,
    )


def verify_after_fix(project):
    """通过 core 运行单个入口/测试或项目全部入口，返回结构化通过状态。"""
    project_path = Path(project).expanduser().resolve()
    try:
        if project_path.is_file():
            if project_path.suffix != ".py":
                return {
                    "passed": False,
                    "project": str(project_path),
                    "scan": None,
                    "runs": [],
                    "message": "需要人工决定：单个验证目标必须是 .py 文件",
                }
            runs = [run_project(project_path.parent, project_path.name, timeout=10.0)]
            scan = None
        else:
            scan = scan_project(project_path)
            runs = [run_project(project_path, entry, timeout=10.0) for entry in scan["entries"]]
    except (OSError, ValueError) as error:
        return {
            "passed": False,
            "project": str(project_path),
            "scan": None,
            "runs": [],
            "message": f"验证无法执行：{error}",
        }
    passed = bool(runs) and all(run["success"] and not run["timed_out"] for run in runs)
    if scan is not None and scan["parse_errors"]:
        passed = False
    message = "全部入口运行通过" if passed else "验证未通过；可检查 runs 并回滚"
    if not runs:
        message = "验证未通过：未发现可运行入口"
    return {
        "passed": passed,
        "project": str(project_path),
        "scan": scan,
        "runs": runs,
        "message": message,
    }


"""PyAgent 离线诊断、保守修复与备份回滚命令行。"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
from pathlib import Path
import re
import sys
import tokenize

import fixer
from core import run_project, scan_project
from matcher import load_rules, match

__all__ = ["main"]

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RULES_PATH = PROJECT_ROOT / "02_assets" / "diagnostic_rules.jsonl"
_BACKUP_RE = re.compile(r"^(?P<original>.+)\.bak_(?P<timestamp>\d{8}_\d{6})$")


def _project(path):
    root = Path(path).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"项目路径不存在：{root}")
    if not root.is_dir():
        raise NotADirectoryError(f"项目路径不是目录：{root}")
    return root


def _target_path(root, filename):
    if not filename:
        return None
    target = Path(filename)
    if not target.is_absolute():
        target = root / target
    target = target.resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None
    return target


def _display_path(path, root):
    if not path:
        return None
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        return candidate.resolve().relative_to(root).as_posix()
    except (ValueError, OSError):
        return str(path)


def _read_source(path):
    if not path or not path.is_file():
        return ""
    try:
        with tokenize.open(path) as stream:
            return stream.read()
    except (OSError, UnicodeError, SyntaxError):
        return ""


def _base_report(command, root):
    return {
        "command": command,
        "status": "completed",
        "project": str(root),
        "modified": False,
        "scan": None,
        "runs": [],
        "issues": [],
        "actions": [],
        "verification": {"status": "not_run", "passed": None},
        "verified": [],
        "needs_manual": [],
        "summary": {},
    }


def _matched_issue(found, rule, traceback_obj, root, entry):
    return {
        "rule": found["id"],
        "name": rule.get("name"),
        "file": _display_path(traceback_obj.get("file"), root),
        "line": traceback_obj.get("line"),
        "confidence": found["confidence"],
        "suggestion": rule.get("fix") or "需要人工决定",
        "error_type": traceback_obj.get("error_type"),
        "error_message": traceback_obj.get("error_message"),
        "entry": entry,
        "strength": found["strength"],
        "evidence": found["evidence"],
    }


def _unmatched_issue(traceback_obj, root, entry, reason):
    return {
        "rule": None,
        "name": "未命中确定性规则",
        "file": _display_path(traceback_obj.get("file"), root) or entry,
        "line": traceback_obj.get("line"),
        "confidence": "unknown",
        "suggestion": f"需要人工决定：{reason}",
        "error_type": traceback_obj.get("error_type"),
        "error_message": traceback_obj.get("error_message"),
        "entry": entry,
        "strength": 0,
        "evidence": [],
    }


def _match_traceback(traceback_obj, root, entry, rules):
    target = _target_path(root, traceback_obj.get("file"))
    source = _read_source(target)
    source_map = {str(target): source} if target and source else {}
    found_rules = match(rules, traceback_obj, source_map)
    if not found_rules:
        return [
            _unmatched_issue(traceback_obj, root, entry, "没有确定性规则命中")
        ], []
    issues = []
    work = []
    for found in found_rules:
        rule = next(rule for rule in rules if rule["id"] == found["id"])
        issue = _matched_issue(found, rule, traceback_obj, root, entry)
        issues.append(issue)
        work.append({"issue": issue, "rule": rule, "target": target})
    return issues, work


def _diagnose(project):
    root = _project(project)
    rules = load_rules(RULES_PATH)
    report = _base_report("check", root)
    scan = scan_project(root)
    report["scan"] = scan
    work = []
    seen_errors = set()

    for entry in scan["entries"]:
        run = run_project(root, entry, timeout=10.0)
        report["runs"].append(run)
        traceback_obj = run["traceback"]
        if run["success"]:
            report["verified"].append({"entry": entry, "returncode": run["returncode"]})
            continue
        if traceback_obj.get("has_traceback"):
            location = (
                _display_path(traceback_obj.get("file"), root),
                traceback_obj.get("line"),
                traceback_obj.get("error_type"),
            )
            seen_errors.add(location)
            issues, matched_work = _match_traceback(traceback_obj, root, entry, rules)
        else:
            failure = {
                "file": str(root / entry),
                "line": None,
                "error_type": "TimeoutExpired" if run["timed_out"] else "RuntimeFailure",
                "error_message": run["stderr"] or f"入口返回码为 {run['returncode']}",
            }
            issues = [_unmatched_issue(failure, root, entry, "运行失败但没有可解析的 Traceback")]
            matched_work = []
        report["issues"].extend(issues)
        work.extend(matched_work)

    for error in scan["parse_errors"]:
        location = (error["file"], error.get("line"), error.get("error_type"))
        if location in seen_errors:
            continue
        traceback_obj = {
            "has_traceback": True,
            "file": str(root / error["file"]),
            "line": error.get("line"),
            "error_type": error.get("error_type"),
            "error_message": error.get("message") or "",
            "code": "",
        }
        issues, matched_work = _match_traceback(traceback_obj, root, None, rules)
        report["issues"].extend(issues)
        work.extend(matched_work)

    ordering = lambda issue: (
        issue.get("file") or "",
        issue.get("line") or 0,
        issue.get("rule") or "",
    )
    report["issues"].sort(key=ordering)
    work.sort(key=lambda item: ordering(item["issue"]))
    report["needs_manual"] = [
        issue for issue in report["issues"] if issue["rule"] is None
    ]
    report["summary"] = {
        "file_count": scan["file_count"],
        "entry_count": scan["entry_count"],
        "issue_count": len(report["issues"]),
        "matched_count": sum(issue["rule"] is not None for issue in report["issues"]),
        "entries_succeeded": len(report["verified"]),
        "modified": False,
    }
    return report, work


def _manual(issue, reason):
    return {
        "rule": issue.get("rule"),
        "file": issue.get("file"),
        "line": issue.get("line"),
        "reason": f"需要人工决定：{reason}",
        "suggestion": issue.get("suggestion"),
    }


def _preview_fix(rule, target):
    """复用 fixer 的候选生成与 limit 检查，不备份、不写任何文件。"""
    path = Path(target).expanduser().resolve()
    if rule.get("confidence") != "high":
        return fixer._result(rule, path, "rejected", "需要人工决定：置信度不是 high")
    if str(rule.get("fix") or "").strip().startswith("不自动修"):
        return fixer._result(rule, path, "rejected", "需要人工决定：规则 fix 明确不自动修")
    if rule.get("id") != "DR_0001":
        return fixer._result(rule, path, "needs_manual", "需要人工决定：没有定点修复器")
    original_bytes = path.read_bytes()
    try:
        original_text, encoding = fixer._decode_source(original_bytes)
    except (SyntaxError, UnicodeError, LookupError, ValueError) as error:
        return fixer._result(rule, path, "needs_manual", f"需要人工决定：无法确定源码编码：{error}")
    fixed_text, patch, reason = fixer._colon_patch(original_text, str(path))
    if reason:
        return fixer._result(rule, path, "needs_manual", reason)
    if len(fixed_text.encode(encoding)) != len(original_bytes) + 1:
        return fixer._result(rule, path, "needs_manual", "需要人工决定：候选不是单字节局部修改")
    diff = "".join(
        difflib.unified_diff(
            original_text.splitlines(keepends=True),
            fixed_text.splitlines(keepends=True),
            fromfile=str(path),
            tofile=str(path) + " (dry-run)",
        )
    )
    return fixer._result(
        rule, path, "dry_run", "dry-run：补丁未落盘，未创建备份", patch=patch, diff=diff
    )


def _fix_project(project, dry_run=False):
    report, work = _diagnose(project)
    root = Path(report["project"])
    report["command"] = "fix"
    report["dry_run"] = bool(dry_run)
    report["needs_manual"] = [
        _manual(issue, "没有确定性规则命中")
        for issue in report["issues"]
        if issue["rule"] is None
    ]

    for item in work:
        issue = item["issue"]
        rule = item["rule"]
        target = item["target"]
        if target is None:
            report["needs_manual"].append(_manual(issue, "出错文件不在项目内或无法定位"))
            continue
        if rule.get("confidence") != "high":
            report["needs_manual"].append(_manual(issue, "只有 high 规则允许自动修复"))
            continue
        if str(rule.get("fix") or "").strip().startswith("不自动修"):
            report["needs_manual"].append(_manual(issue, "规则 fix 明确不自动修"))
            continue

        if dry_run:
            action = _preview_fix(rule, target)
            report["actions"].append(action)
            if action["status"] != "dry_run":
                report["needs_manual"].append(_manual(issue, action["message"]))
            continue

        action = fixer.apply_fix(rule, target)
        report["actions"].append(action)
        if action["status"] != "applied":
            report["needs_manual"].append(_manual(issue, action["message"]))
            continue

        verification = fixer.verify_after_fix(root)
        action["verification"] = verification
        if verification["passed"]:
            action["status"] = "applied_verified"
            report["verified"].append(
                {
                    "rule": rule["id"],
                    "file": _display_path(target, root),
                    "backup": action["backup"],
                    "entries": [run["entry"] for run in verification["runs"]],
                    "passed": True,
                }
            )
            continue

        try:
            restored = fixer.rollback(action["backup"])
        except (OSError, ValueError) as error:
            action["status"] = "rollback_failed"
            action["rollback_error"] = str(error)
            report["needs_manual"].append(
                _manual(issue, f"修复后验证失败且回滚失败：{error}")
            )
        else:
            action["status"] = "rolled_back"
            action["rollback"] = restored
            report["needs_manual"].append(
                _manual(issue, "修复后验证失败，已使用本次备份回滚")
            )

    verification_results = [
        action["verification"] for action in report["actions"] if "verification" in action
    ]
    if dry_run:
        report["verification"] = {
            "status": "skipped",
            "passed": None,
            "message": "dry-run 未落盘，因此不执行修复后验证",
        }
    elif verification_results:
        passed = all(result["passed"] for result in verification_results)
        report["verification"] = {"status": "passed" if passed else "failed", "passed": passed}
    report["modified"] = any(
        action["status"] in {"applied_verified", "rollback_failed"}
        for action in report["actions"]
    )
    report["summary"].update(
        {
            "action_count": len(report["actions"]),
            "dry_run_count": sum(action["status"] == "dry_run" for action in report["actions"]),
            "applied_count": sum(
                action.get("applied", False) and action["status"] != "dry_run"
                for action in report["actions"]
            ),
            "rolled_back_count": sum(
                action["status"] == "rolled_back" for action in report["actions"]
            ),
            "verified_fix_count": sum(
                action["status"] == "applied_verified" for action in report["actions"]
            ),
            "needs_manual_count": len(report["needs_manual"]),
            "modified": report["modified"],
        }
    )
    return report


def _rollback_project(project):
    root = _project(project)
    report = _base_report("rollback", root)
    records = []
    for candidate in root.rglob("*.bak_*"):
        resolved = _target_path(root, str(candidate))
        if not resolved or not resolved.is_file():
            continue
        found = _BACKUP_RE.fullmatch(resolved.name)
        if not found:
            continue
        content = resolved.read_bytes()
        records.append(
            {
                "backup": resolved.relative_to(root).as_posix(),
                "file": _display_path(resolved.with_name(found.group("original")), root),
                "timestamp": found.group("timestamp"),
                "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "_path": resolved,
            }
        )
    records.sort(key=lambda item: (item["timestamp"], item["backup"]), reverse=True)
    report["backups"] = [
        {key: value for key, value in record.items() if key != "_path"}
        for record in records
    ]
    if not records:
        report["needs_manual"].append({"reason": "需要人工决定：项目中没有可用备份"})
        report["summary"] = {"backup_count": 0, "restored": False, "modified": False}
        return report

    latest_timestamp = records[0]["timestamp"]
    latest = [record for record in records if record["timestamp"] == latest_timestamp]
    if len(latest) != 1:
        report["needs_manual"].append(
            {"reason": "需要人工决定：最新时间戳对应多个备份，不能猜测恢复目标"}
        )
        report["summary"] = {
            "backup_count": len(records),
            "restored": False,
            "modified": False,
        }
        return report

    selected = latest[0]
    restored = fixer.rollback(selected["_path"])
    report["modified"] = True
    report["actions"].append({"status": "rolled_back", **restored})
    verification = fixer.verify_after_fix(root)
    report["verification"] = {
        "status": "passed" if verification["passed"] else "failed",
        "passed": verification["passed"],
        "runs": verification["runs"],
        "message": verification["message"],
    }
    report["runs"] = verification["runs"]
    if not verification["passed"]:
        report["needs_manual"].append(
            {"reason": "需要人工决定：备份已恢复，但恢复后的项目验证未通过"}
        )
    report["summary"] = {
        "backup_count": len(records),
        "selected_backup": selected["backup"],
        "restored_file": selected["file"],
        "restored": True,
        "modified": True,
        "verification_passed": verification["passed"],
    }
    return report


def _parser():
    parser = argparse.ArgumentParser(
        prog="python 03_engine/cli.py",
        description="PyAgent 纯标准库离线诊断、定点修复与回滚工具；不联网、不调用模型。",
        epilog=(
            "示例：\n"
            "  python 03_engine/cli.py check 04_golden_tests/smoke_01\n"
            "  python 03_engine/cli.py fix 04_golden_tests/smoke_01 --dry-run\n"
            "  python 03_engine/cli.py fix 04_golden_tests/smoke_01\n"
            "  python 03_engine/cli.py rollback 04_golden_tests/smoke_01\n\n"
            "所有命令输出 JSON。check 不修改文件；dry-run 不写源码也不创建备份；\n"
            "真实 fix 验证失败会回滚本次修改；rollback 只恢复唯一最新时间戳备份。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")
    check = commands.add_parser("check", help="扫描、运行并只读诊断项目")
    check.add_argument("project", metavar="项目路径")
    fix = commands.add_parser("fix", help="诊断并尝试 high 规则的定点修复")
    fix.add_argument("project", metavar="项目路径")
    fix.add_argument("--dry-run", action="store_true", help="只输出补丁，不创建备份或写文件")
    rollback = commands.add_parser("rollback", help="列出备份并恢复唯一最近一次备份")
    rollback.add_argument("project", metavar="项目路径")
    return parser


def _emit(report):
    print(json.dumps(report, ensure_ascii=False, indent=2))


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = _parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    try:
        if args.command == "check":
            report, _ = _diagnose(args.project)
        elif args.command == "fix":
            report = _fix_project(args.project, dry_run=args.dry_run)
        else:
            report = _rollback_project(args.project)
    except (OSError, ValueError) as error:
        report = {
            "command": args.command,
            "status": "error",
            "modified": False,
            "needs_manual": [{"reason": f"需要人工决定：{error}"}],
        }
        _emit(report)
        return 2
    _emit(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


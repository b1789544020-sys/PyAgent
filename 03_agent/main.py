"""PyAgent 对话式入口：REPL 与单次 ask 均复用 dispatcher。"""

from contextlib import nullcontext, redirect_stderr, redirect_stdout
import io
import json
import shlex
import sys

import config
import dispatcher
import safety


OFFLINE_HINT = (
    "命令：check <路径>；fix <路径> [--dry-run]；rollback <路径>；"
    "run_project <路径> <入口.py>；docs_index [--full] [--lexical-only]；"
    "docs_search <问题> [--lexical-only]；docs_ask <问题>。"
)


def _configure_streams():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _offline_argv(text):
    """Windows 路径保留反斜杠；成对引号只用于包裹含空格的路径。"""
    try:
        parts = shlex.split(text, posix=False)
    except ValueError as exc:
        raise ValueError(f"命令引号不完整：{exc}") from None
    result = []
    for part in parts:
        if len(part) >= 2 and part[0] == part[-1] and part[0] in {"\"", "'"}:
            part = part[1:-1]
        result.append(part)
    return result


def _error_result(reason, message, error_type=None):
    result = {"status": "error", "reason": reason, "message": message}
    if error_type:
        result["error"] = {"type": error_type, "message": message}
    return result


def _dispatch(text, online, safety_policy):
    """把 argparse 的 SystemExit 和运行异常都转为单轮结构化错误。"""
    try:
        first = text.split(maxsplit=1)[0] if text.strip() else ""
        structured = not online or first in dispatcher.KNOWLEDGE_COMMANDS
        argv = _offline_argv(text) if structured else [text]
    except ValueError as exc:
        return 2, _error_result("invalid_command", str(exc), "InvalidCommand")
    if not online and "--yes" in argv:
        return 2, _error_result(
            "invalid_command",
            "--yes 只能在启动 PyAgent 时显式指定，不能在单条 REPL 命令中开启。",
            "InvalidCommand",
        )

    stdout = io.StringIO()
    stderr = io.StringIO()
    try:
        # docs_index 可能运行很久；进度必须实时到达终端，不能随 stdout 一起缓存。
        stderr_context = nullcontext() if first == "docs_index" else redirect_stderr(stderr)
        with redirect_stdout(stdout), stderr_context:
            exit_code = dispatcher.main(argv, safety_policy=safety_policy)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 2
        if code == 0:
            return 0, {"status": "completed", "answer": stdout.getvalue().strip()}
        return code, _error_result(
            "invalid_command", f"命令或参数无效。{OFFLINE_HINT}", "InvalidCommand"
        )
    except Exception as exc:
        message = f"本轮失败：{type(exc).__name__}: {exc}。REPL 可继续输入。"
        return 1, _error_result("turn_exception", message, type(exc).__name__)

    lines = stdout.getvalue().splitlines()
    if lines and lines[0].startswith("当前模式："):
        lines = lines[1:]
    raw = "\n".join(lines).strip()
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("结果不是对象")
    except (json.JSONDecodeError, ValueError):
        return 1, _error_result(
            "invalid_dispatcher_output", "dispatcher 未返回有效的结构化结果。", "InvalidOutput"
        )
    return int(exit_code), payload


def _reports(payload):
    reports = [payload]
    for progress in payload.get("progress", []):
        if isinstance(progress, dict) and progress.get("type") == "tool":
            result = progress.get("result")
            if isinstance(result, dict):
                reports.append(result)
    return reports


def key_information(payload):
    info = {
        "status": payload.get("status", "unknown"),
        "changed_files": [],
        "verification": {"status": "not_run", "passed": None},
        "unverified_claims": [
            {
                "step": claim.get("step"),
                "status": claim.get("status", "待验证"),
                "claim": claim.get("claim"),
                "reason": claim.get("reason"),
            }
            for claim in payload.get("unverified_claims", [])
            if isinstance(claim, dict)
        ],
    }
    for key in (
        "command", "reason", "steps_taken", "mode", "grounded", "generation",
        "generation_attempts", "warning", "retrieval", "candidates",
    ):
        if key in payload:
            info[key] = payload[key]
    if "sources" in payload:
        info["sources"] = payload["sources"]
    if payload.get("command") == "docs_index":
        info["index"] = {
            key: payload.get(key)
            for key in (
                "index_path", "files", "chunks", "updated_files", "removed_files",
                "vectors", "embedding_pending", "embedding_model", "elapsed_seconds",
            )
        }
    understanding = payload.get("understanding")
    if isinstance(understanding, dict):
        info["intent"] = understanding.get("intent")
        info["project_path"] = understanding.get("project_path")
    elif payload.get("project"):
        info["project_path"] = payload["project"]

    issues = []
    changed = []
    safety_events = []
    for report in _reports(payload):
        if report.get("requires_confirmation"):
            safety_events.append(
                {
                    "tool": report.get("tool"),
                    "status": report.get("status"),
                    "action": report.get("action"),
                    "scope": report.get("scope"),
                    "message": report.get("message"),
                }
            )
        for issue in report.get("issues", []):
            if isinstance(issue, dict):
                issues.append({
                    key: issue.get(key)
                    for key in ("rule", "file", "line", "error_type", "error_message")
                })
        verification = report.get("verification")
        if isinstance(verification, dict) and verification.get("passed") is not None:
            info["verification"] = {
                "status": verification.get("status"),
                "passed": verification.get("passed"),
            }
        for action in report.get("actions", []):
            if not isinstance(action, dict):
                continue
            applied = action.get("status") in {"applied", "applied_verified", "rollback_failed"}
            restored = report.get("command") == "rollback" and action.get("status") == "rolled_back"
            if (applied or restored) and action.get("file"):
                changed.append(str(action["file"]))
        if report.get("status") == "completed" and report.get("dangerous") and report.get("path"):
            changed.append(str(report["path"]))
        if "returncode" in report:
            info["returncode"] = report["returncode"]
            info["entry"] = report.get("entry")
    if issues:
        info["issue_count"] = len(issues)
        info["issues"] = issues[:5]
    info["changed_files"] = list(dict.fromkeys(changed))
    if safety_events:
        info["safety"] = safety_events
    if payload.get("error"):
        info["error"] = payload["error"]
    return info


def _verification_text(info):
    passed = info["verification"].get("passed")
    if passed is True:
        return "验证通过"
    if passed is False:
        return "验证未通过"
    return "未执行写后验证"


def _offline_summary(payload, info):
    if payload.get("status") == "error":
        return payload.get("message") or payload.get("error", {}).get("message") or "操作失败。"
    if payload.get("answer"):
        return str(payload["answer"])
    command = payload.get("command")
    issue_count = info.get("issue_count", 0)
    if command == "check":
        summary = f"检查完成，发现 {issue_count} 个问题。" if issue_count else "检查完成，未发现问题。"
        details = []
        for issue in info.get("issues", [])[:3]:
            location = f"{issue.get('file') or '?'}:{issue.get('line') or '?'}"
            details.append(
                f"{issue.get('rule') or '未命中规则'}（{location}，"
                f"{issue.get('error_type') or '未知错误'}：{issue.get('error_message') or '无详情'}）"
            )
        return summary + ((" " + "；".join(details) + "。") if details else "")
    if command == "fix":
        if payload.get("dry_run"):
            count = payload.get("summary", {}).get("dry_run_count", 0)
            return f"修复预览完成，生成 {count} 个候选补丁；未修改文件，未执行写后验证。"
        count = len(info["changed_files"])
        if count:
            return f"修复完成，修改 {count} 个文件，{_verification_text(info)}。"
        return "未修改文件；没有可安全自动修复并保留的修改。"
    if command == "rollback":
        if info["changed_files"]:
            return f"已恢复 {len(info['changed_files'])} 个文件，{_verification_text(info)}。"
        return "未恢复文件；请检查是否存在唯一最新备份。"
    if "returncode" in payload:
        code = payload["returncode"]
        return f"运行{'成功' if code == 0 else '失败'}，入口 {payload.get('entry')}，退出码 {code}。"
    return payload.get("message") or "操作完成。"


def summarize_result(payload, online):
    """在线优先展示模型最终回答；离线只使用确定性模板。"""
    info = key_information(payload)
    if not online:
        return _offline_summary(payload, info)
    status = payload.get("status")
    answer = str(payload.get("answer") or payload.get("question") or "").strip()
    if status in {"completed", "needs_clarification"} and answer:
        return answer
    if status == "incomplete":
        message = payload.get("message") or "本轮任务未完成。"
        return message + (f" 模型最后回答：{answer}" if answer else "")
    return _offline_summary(payload, info)


def format_result(payload, online):
    return (
        summarize_result(payload, online)
        + "\n关键结构化信息：\n"
        + json.dumps(key_information(payload), ensure_ascii=False, indent=2, default=str)
    )


def _terminal_policy(assume_yes, interactive):
    """提示绕过 dispatcher 的 stdout 捕获，但结构化结果仍按原通道解析。"""
    terminal = sys.stdout

    def emit(message):
        print(message, file=terminal, flush=True)

    def read_answer(prompt):
        print(prompt, end="", file=terminal, flush=True)
        return input("")

    return safety.SafetyPolicy(
        assume_yes=assume_yes,
        interactive=interactive,
        input_fn=read_answer,
        emit=emit,
    )


def run_one(text, online, safety_policy):
    exit_code, payload = _dispatch(text, online, safety_policy)
    print(format_result(payload, online))
    return exit_code


def repl(online, safety_policy):
    print("进入 PyAgent 对话；输入 exit 或 quit 退出。")
    if not online:
        print(OFFLINE_HINT)
    while True:
        try:
            text = input("PyAgent> ").strip()
        except EOFError:
            print("\n输入结束，已退出。")
            return 0
        except KeyboardInterrupt:
            print("\n已取消本次输入；REPL 继续运行。")
            continue
        except Exception as exc:
            print(f"读取输入失败：{type(exc).__name__}: {exc}；REPL 继续运行。")
            continue
        if not text:
            continue
        if text.lower() in {"exit", "quit"}:
            print("已退出。")
            return 0
        try:
            run_one(text, online, safety_policy)
        except Exception as exc:
            print(f"本轮失败：{type(exc).__name__}: {exc}；REPL 继续运行。")


def main(argv=None, safety_policy=None):
    _configure_streams()
    arguments = list(sys.argv[1:] if argv is None else argv)
    assume_yes = "--yes" in arguments
    arguments = [argument for argument in arguments if argument != "--yes"]
    single = None
    if arguments:
        if arguments[0] != "ask":
            print("用法：python 03_agent/main.py [--yes] [ask \"任务或离线命令\"]")
            return 2
        single = " ".join(arguments[1:]).strip()
        if not single:
            print("ask 后需要任务或命令。")
            return 2
    try:
        settings = config.get_config()
    except Exception as exc:
        print(f"读取配置失败：{type(exc).__name__}: {exc}")
        return 1
    online = bool(settings["has_model"])
    print(dispatcher._mode_line(settings))
    if safety_policy is None:
        interactive = single is None or bool(
            getattr(sys.stdin, "isatty", lambda: False)()
        )
        safety_policy = _terminal_policy(assume_yes, interactive)
    elif assume_yes:
        safety_policy.assume_yes = True
    if single is not None:
        try:
            return run_one(single, online, safety_policy)
        except Exception as exc:
            print(f"本轮失败：{type(exc).__name__}: {exc}")
            return 1
    return repl(online, safety_policy)


if __name__ == "__main__":
    raise SystemExit(main())

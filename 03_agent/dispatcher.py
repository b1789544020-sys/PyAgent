"""双模式入口：模型只负责在线决策，离线命令固定映射到同一套工具。"""

import argparse
import json
import sys

import config
import safety
import tools


OFFLINE_TOOL_NAMES = {
    "check": "check_project",
    "fix": "fix_project",
    "rollback": "rollback_project",
    "run_project": "run_project",
}
KNOWLEDGE_COMMANDS = {"docs_index", "docs_search", "docs_ask"}


def _mode_line(settings):
    if settings.get("has_model"):
        model = str(settings.get("model") or settings.get("llm") or "未命名")
        model = model.replace("\r", " ").replace("\n", " ")
        if settings.get("llm") == "ollama":
            return f"当前模式：本地模型（{model}；Ollama 本机服务，无需云端 Key）"
        return f"当前模式：在线（模型 {model}）"
    return "当前模式：离线（未检测到模型，仅支持确定性命令）"


def _offline_parser():
    parser = argparse.ArgumentParser(
        prog="python 03_agent/dispatcher.py",
        description="无模型时只接受固定命令；所有操作通过 03_agent/tools.py 执行。",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    check = commands.add_parser("check", help="只诊断项目，不修改文件")
    check.add_argument("path", help="项目目录")

    fix = commands.add_parser("fix", help="尝试保守自动修复")
    fix.add_argument("path", help="项目目录")
    fix.add_argument(
        "--dry-run",
        action="store_true",
        help="只预览补丁，不创建备份或写文件",
    )

    rollback = commands.add_parser("rollback", help="恢复唯一最新备份")
    rollback.add_argument("path", help="项目目录")

    run = commands.add_parser("run_project", help="运行项目内指定入口")
    run.add_argument("path", help="项目目录")
    run.add_argument("entry", help="项目内 Python 入口，例如 main.py")
    return parser


def _offline_arguments(args):
    if args.command == "run_project":
        return {"path": args.path, "entry": args.entry}
    if args.command == "fix":
        return {"path": args.path, "dry_run": args.dry_run}
    return {"path": args.path}


def _run_offline(argv, safety_policy):
    args = _offline_parser().parse_args(argv)
    tool_name = OFFLINE_TOOL_NAMES[args.command]
    arguments = _offline_arguments(args)
    result = safety_policy.authorize(tool_name, arguments)
    if result is None:
        result = tools.execute_tool(tool_name, arguments)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if isinstance(result, dict) and result.get("status") in {
        "error", "needs_confirmation", "blocked"
    }:
        return 1
    return 0


def _run_online(argv, agent_runner=None, safety_policy=None, settings=None):
    """代码任务保留 ReAct；Ollama 资料问题走确定性的检索问答链。"""
    if agent_runner is not None:
        return agent_runner(" ".join(argv))
    prompt = " ".join(argv).strip()
    if not prompt:
        print(
            json.dumps(
                {
                    "status": "incomplete",
                    "reason": "missing_task",
                    "message": "未能完成：请提供自然语言任务；目前进度：0 步。",
                },
                ensure_ascii=False,
            )
        )
        return 2

    if settings and settings.get("llm") == "ollama":
        import knowledge_qa

        if knowledge_qa.should_use_document_qa(prompt):
            code, result = knowledge_qa.run_command(
                ["docs_ask", prompt, "--top-k", str(settings.get("top_k", 4))],
                settings=settings,
            )
            print(json.dumps(result, ensure_ascii=False, default=str))
            return code

    from agent import run_agent
    from llm import get_llm

    result = run_agent(prompt, llm=get_llm(), safety_policy=safety_policy)
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0 if result["status"] in {"completed", "needs_clarification"} else 1


def main(argv=None, agent_runner=None, safety_policy=None):
    """每次启动先读取公开配置并打印模式，再选择在线或离线分支。"""
    arguments = list(sys.argv[1:] if argv is None else argv)
    assume_yes = "--yes" in arguments
    arguments = [argument for argument in arguments if argument != "--yes"]
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    if safety_policy is None:
        safety_policy = safety.SafetyPolicy(
            assume_yes=assume_yes,
            interactive=bool(getattr(sys.stdin, "isatty", lambda: False)()),
        )
    elif assume_yes:
        safety_policy.assume_yes = True
    settings = config.get_config()
    print(_mode_line(settings), flush=True)
    if arguments and arguments[0] in KNOWLEDGE_COMMANDS:
        import knowledge_qa

        code, result = knowledge_qa.run_command(arguments, settings=settings)
        print(json.dumps(result, ensure_ascii=False, default=str))
        return code
    if settings["has_model"]:
        return _run_online(
            arguments,
            agent_runner=agent_runner,
            safety_policy=safety_policy,
            settings=settings,
        )
    return _run_offline(arguments, safety_policy)


if __name__ == "__main__":
    raise SystemExit(main())

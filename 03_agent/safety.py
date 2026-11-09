"""高危工具的统一确认策略；默认拒绝，只有显式确认或启动时 --yes 才放行。"""


CONFIRM_TOOL_NAMES = {"write_file", "rollback_project", "run_project"}
CORRECTION_PROMPT = (
    "只能基于工具结果作答。你上一条回答中的完成声明缺少成功工具证据，"
    "已标记为“待验证”。请撤回或修正该声明，说明实际工具结果；"
    "未执行、被拒绝或验证失败的操作不得说成成功。"
)


def requires_confirmation(name, arguments):
    """dry-run 修复和只读工具不确认；回滚、写入及代码运行必须确认。"""
    if name in CONFIRM_TOOL_NAMES:
        return True
    return name == "fix_project" and arguments.get("dry_run", True) is False


def action_details(name, arguments):
    """只描述动作和范围，不回显 write_file 的完整内容。"""
    path = arguments.get("path", "未提供路径")
    if name == "write_file":
        return (
            "创建或覆盖 UTF-8 文本文件",
            f"目标文件：{path}；写入 {len(str(arguments.get('content', '')))} 个字符。",
        )
    if name == "fix_project":
        return (
            "备份并尝试自动修复项目，随后执行写后验证",
            f"项目目录：{path}；仅保留验证通过的确定性补丁，失败时由引擎尝试回滚。",
        )
    if name == "rollback_project":
        return (
            "恢复唯一最新备份并覆盖对应源码",
            f"项目目录：{path}；会改写备份对应的项目文件。",
        )
    if name == "run_project":
        return (
            "执行项目 Python 入口",
            f"项目目录：{path}；入口：{arguments.get('entry', '未提供入口')}；"
            "项目代码自身可能产生副作用。",
        )
    return (f"执行高危工具 {name}", f"参数范围：{path}")


def _blocked_result(name, arguments, status, error_type, message):
    action, scope = action_details(name, arguments)
    return {
        "status": status,
        "tool": name,
        "requires_confirmation": True,
        "confirmed": False,
        "action": action,
        "scope": scope,
        "message": message,
        "error": {"type": error_type, "message": message},
    }


class SafetyPolicy:
    """交互时逐项询问；非交互且无 --yes 时以 needs_confirmation 阻止。"""

    def __init__(self, assume_yes=False, interactive=False, input_fn=None, emit=None):
        self.assume_yes = bool(assume_yes)
        self.interactive = bool(interactive)
        self.input_fn = input_fn
        self.emit = emit or (lambda message: print(message, flush=True))

    def authorize(self, name, arguments):
        """返回 None 表示允许；返回工具观察表示执行前已被拦截。"""
        if not requires_confirmation(name, arguments):
            return None
        action, scope = action_details(name, arguments)
        self.emit(f"安全确认：{action}\n影响范围：{scope}")
        if self.assume_yes:
            self.emit("本次启动已显式指定 --yes，按信任策略继续执行。")
            return None
        if not self.interactive:
            return _blocked_result(
                name,
                arguments,
                "needs_confirmation",
                "ConfirmationRequired",
                "当前没有交互确认通道，工具未执行。请在交互终端确认；"
                "只有信任本次启动的全部高危操作时才显式添加 --yes。",
            )
        prompt = "确认执行上述操作？仅输入 y/yes 继续，其他输入均取消："
        try:
            answer = self.input_fn(prompt) if self.input_fn else input(prompt)
        except (EOFError, KeyboardInterrupt):
            answer = ""
        if str(answer).strip().casefold() in {"y", "yes"}:
            return None
        return _blocked_result(
            name,
            arguments,
            "blocked",
            "ConfirmationDenied",
            "用户未输入 y/yes，工具未执行，文件和项目状态未因本操作改变。",
        )


__all__ = [
    "CORRECTION_PROMPT",
    "SafetyPolicy",
    "action_details",
    "requires_confirmation",
]

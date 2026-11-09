"""把离线引擎包装为模型工具；所有返回值均可 JSON 序列化。"""

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENGINE_DIR = PROJECT_ROOT / "03_engine"
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

import cli as engine_cli  # noqa: E402
import core as engine_core  # noqa: E402


def check_project(path):
    """只读诊断项目，复用引擎的 issues、runs 和 summary 报告。"""
    report, _ = engine_cli._diagnose(path)
    return report


def fix_project(path, dry_run=True):
    """生成定点补丁；dry_run=false 时写盘并执行引擎验证/回滚保护。"""
    if not isinstance(dry_run, bool):
        raise TypeError("dry_run 必须是布尔值")
    return engine_cli._fix_project(path, dry_run=dry_run)


def rollback_project(path):
    """恢复唯一最新时间戳备份，返回恢复记录和验证结果。"""
    return engine_cli._rollback_project(path)


def run_project(path, entry):
    """运行项目内一个入口，保留 stdout、stderr、返回码和 Traceback。"""
    return engine_core.run_project(path, entry)


def read_file(path):
    """以 UTF-8 读取文本文件，返回内容和基本信息。"""
    target = Path(path).expanduser().resolve()
    data = target.read_bytes()
    content = data.decode("utf-8")
    return {
        "status": "completed",
        "path": str(target),
        "content": content,
        "characters": len(content),
        "bytes": len(data),
        "encoding": "utf-8",
    }


def write_file(path, content):
    """危险：以 UTF-8 覆盖写入文本；上层必须先通过安全确认。"""
    if not isinstance(content, str):
        raise TypeError("content 必须是字符串")
    target = Path(path).expanduser().resolve()
    data = content.encode("utf-8")
    with target.open("w", encoding="utf-8", newline="") as stream:
        stream.write(content)
    return {
        "status": "completed",
        "path": str(target),
        "bytes_written": len(data),
        "characters_written": len(content),
        "encoding": "utf-8",
        "dangerous": True,
    }


def _schema(properties, required):
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


_PATH_PROPERTY = {
    "type": "string",
    "description": "目标项目目录或文件路径；执行前必须确认路径正确。",
}

TOOLS = [
    {
        "name": "check_project",
        "description": "扫描并运行项目入口以产生结构化问题清单，不修改任何文件。需要诊断错误、查看规则位置或修复前评估时优先使用。",
        "parameters": _schema({"path": _PATH_PROPERTY}, ["path"]),
        "execute": check_project,
        "dangerous": False,
    },
    {
        "name": "fix_project",
        "description": "调用保守离线修复器并返回补丁、备份和验证结果。用户要求尝试自动修复时使用；先以 dry_run=true 预览，确认补丁后才使用 false 写盘。",
        "parameters": _schema(
            {
                "path": _PATH_PROPERTY,
                "dry_run": {
                    "type": "boolean",
                    "description": "true 只预览补丁且零写盘；false 才备份并修复。",
                    "default": True,
                },
            },
            ["path"],
        ),
        "execute": fix_project,
        "dangerous": True,
    },
    {
        "name": "rollback_project",
        "description": "将项目恢复到唯一最近时间戳的备份并返回验证结果。修复结果不符合预期且需要撤销时使用；会改写目标源码。",
        "parameters": _schema({"path": _PATH_PROPERTY}, ["path"]),
        "execute": rollback_project,
        "dangerous": True,
    },
    {
        "name": "run_project",
        "description": "危险工具：运行项目内指定入口并返回 stdout、stderr、退出码、超时状态和解析后的 Traceback；代码可能产生副作用，执行前必须确认。",
        "parameters": _schema(
            {
                "path": _PATH_PROPERTY,
                "entry": {
                    "type": "string",
                    "description": "项目目录内的 Python 入口，例如 main.py。",
                },
            },
            ["path", "entry"],
        ),
        "execute": run_project,
        "dangerous": True,
    },
    {
        "name": "read_file",
        "description": "以 UTF-8 读取一个文本文件并返回内容及大小。需要查看源码、配置或补丁上下文时使用，不修改文件。",
        "parameters": _schema({"path": _PATH_PROPERTY}, ["path"]),
        "execute": read_file,
        "dangerous": False,
    },
    {
        "name": "write_file",
        "description": "危险工具：以 UTF-8 创建或覆盖一个文本文件。仅在用户明确要求写入且上层完成确认后使用。",
        "parameters": _schema(
            {
                "path": _PATH_PROPERTY,
                "content": {
                    "type": "string",
                    "description": "要完整写入文件的 UTF-8 文本内容。",
                },
            },
            ["path", "content"],
        ),
        "execute": write_file,
        "dangerous": True,
    },
]

_TOOL_BY_NAME = {tool["name"]: tool for tool in TOOLS}


def execute_tool(name, args):
    """按名称执行工具；调用错误以结构化结果返回，不输出敏感配置。"""
    tool = _TOOL_BY_NAME.get(name)
    if tool is None:
        return {
            "status": "error",
            "tool": name,
            "error": {"type": "ToolNotFound", "message": f"未知工具：{name}"},
        }
    if not isinstance(args, dict):
        return {
            "status": "error",
            "tool": name,
            "error": {"type": "InvalidArguments", "message": "args 必须是字典"},
        }
    try:
        return tool["execute"](**args)
    except (OSError, UnicodeError, TypeError, ValueError) as exc:
        return {
            "status": "error",
            "tool": name,
            "error": {"type": type(exc).__name__, "message": str(exc)},
        }

"""确定性优先的自然语言任务理解，以及可恢复的原子文件会话。"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import queue
import re
import tempfile
import threading


DEFAULT_SESSION_PATH = Path(__file__).resolve().with_name(".session.json")
ACTION_INTENTS = {"check", "fix", "run"}
VALID_INTENTS = ACTION_INTENTS | {"question", "other"}


def _has_english(text, pattern):
    return re.search(r"(?<![A-Za-z_])(?:" + pattern + r")(?![A-Za-z_])", text, re.I) is not None


def detect_intent(task):
    """动作关键词优先于疑问词，避免“哪里坏了”被当成普通问答。"""
    if "修复" in task or "修好" in task or "排查并改" in task or _has_english(task, "fix|repair"):
        return "fix"
    if any(word in task for word in ("检查", "看下", "查看", "诊断", "哪里坏", "什么问题", "报错", "排查")):
        return "check"
    if _has_english(task, "check|diagnose|inspect"):
        return "check"
    if any(word in task for word in ("运行", "跑一下", "跑项目", "启动项目", "执行项目")):
        return "run"
    if _has_english(task, "run|run_project|execute"):
        return "run"
    if any(word in task for word in ("为什么", "如何", "怎么", "什么", "解释", "请问", "？", "?")):
        return "question"
    return "other"


def _clean_path(candidate):
    value = candidate.strip().strip("\"'").rstrip("，。！？；,;!?")
    for suffix in ("这个项目", "这个目录", "项目", "目录"):
        if value.endswith(suffix) and len(value) > len(suffix) + 2:
            value = value[: -len(suffix)].rstrip()
            break
    return value or None


def extract_project_path(task):
    """只提取原话中的显式路径，不检查存在性，也绝不生成默认路径。"""
    quoted = re.search(
        r"([\"'])((?:[A-Za-z]:[\\/]|\\\\|/|\.{1,2}[\\/]|~[\\/]).*?)\1",
        task,
    )
    if quoted:
        return _clean_path(quoted.group(2))
    patterns = (
        r"[A-Za-z]:[\\/][^\s\"'，。！？；]+",
        r"\\\\[^\s\"'，。！？；]+",
        r"(?<![\w:])/(?:[^\s/]+/)*[^\s/]*",
        r"(?<![\w])(?:\.{1,2}[\\/]|~[\\/])[^\s\"'，。！？；]+",
    )
    for pattern in patterns:
        match = re.search(pattern, task)
        if match:
            return _clean_path(match.group(0))
    return None


def extract_entry(task):
    match = re.search(r"(?<![\w./\\-])([A-Za-z0-9_.-]+\.py)(?![\w./\\-])", task, re.I)
    return match.group(1) if match else None


def _looks_actionable(task):
    return any(word in task for word in ("帮我", "处理", "操作", "执行", "继续", "再来")) or _has_english(
        task, "fix|check|run|execute|repair|diagnose"
    )


def _requires_action_intent(task):
    return any(word in task for word in ("处理", "操作", "执行", "继续", "再来")) or _has_english(
        task, "process|fix|check|run|execute|repair|diagnose"
    )


def _continuation(task):
    return any(word in task for word in ("继续", "接着", "再来", "重新", "按上面", "再检查", "再修复", "再运行"))


class SessionStore:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else Path(DEFAULT_SESSION_PATH)

    def load(self):
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def save(self, understanding, previous=None):
        previous = self.load() if previous is None else previous
        intent = understanding["intent"]
        current_project = understanding["project_path"] or previous.get("current_project")
        pending = None
        if understanding["needs_clarification"]:
            pending = {
                "intent": intent if intent in ACTION_INTENTS else None,
                "project_path": understanding["project_path"],
                "entry": understanding["entry"],
                "missing_fields": understanding["missing_fields"],
            }
        payload = {
            "version": 1,
            "current_project": current_project,
            "recent_intent": intent,
            "pending": pending,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=self.path.name + ".", suffix=".tmp", dir=str(self.path.parent)
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return payload


def _call_model_with_timeout(client, messages, timeout):
    outcomes = queue.Queue(maxsize=1)

    def worker():
        try:
            outcomes.put(("ok", client.chat(messages)))
        except Exception:
            outcomes.put(("error", None))

    threading.Thread(target=worker, daemon=True).start()
    try:
        outcome, response = outcomes.get(timeout=timeout)
    except queue.Empty:
        return None
    return response if outcome == "ok" else None


def _model_supplement(task, client, timeout):
    messages = [
        {
            "role": "system",
            "content": (
                "任务理解补充。只返回 JSON 对象，字段为 intent、project_path、entry。"
                "intent 只能是 check、fix、run、question、other；不知道就填 null。"
                "project_path 只能逐字复制用户原话里的显式路径，禁止猜测或创造路径。"
            ),
        },
        {"role": "user", "content": task},
    ]
    raw = _call_model_with_timeout(client, messages, timeout)
    if not isinstance(raw, str):
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    result = {}
    if payload.get("intent") in VALID_INTENTS:
        result["intent"] = payload["intent"]
    candidate = payload.get("project_path")
    if (
        isinstance(candidate, str)
        and candidate
        and candidate in task
        and extract_project_path(candidate) == candidate
    ):
        result["project_path"] = candidate
    entry = payload.get("entry")
    if isinstance(entry, str) and entry in task and re.fullmatch(r"[A-Za-z0-9_.-]+\.py", entry):
        result["entry"] = entry
    return result


def _clarification(intent, path, missing):
    if "project_path" in missing:
        action = {"check": "检查", "fix": "修复", "run": "运行"}.get(intent, "处理")
        return f"请提供要{action}的项目路径（例如 D:\\code\\demo），我不会在缺少路径时猜测或操作其他目录。"
    if "intent" in missing:
        target = f"项目 {path}" if path else "这个任务"
        return f"你希望对{target}执行检查（check）、修复（fix）还是运行（run）？"
    if "entry" in missing:
        return "请提供要运行的项目内 Python 入口文件（例如 main.py）。"
    return None


def understand_task(task, llm=None, session_path=None, model_timeout=5.0):
    """规则初筛后按需模型补充；每次理解都会原子写入会话文件。"""
    if not isinstance(task, str) or not task.strip():
        raise ValueError("task 必须是非空字符串")
    task = task.strip()
    store = SessionStore(session_path)
    previous = store.load()
    pending = previous.get("pending") if isinstance(previous.get("pending"), dict) else {}

    intent = detect_intent(task)
    path = extract_project_path(task)
    entry = extract_entry(task)
    intent_source = "rules" if intent != "other" else None
    path_source = "rules" if path else None
    entry_source = "rules" if entry else None
    model_status = "not_requested"

    if intent == "other" and _looks_actionable(task):
        if llm is not None:
            model_status = "attempted"
            supplement = _model_supplement(task, llm, float(model_timeout))
        else:
            supplement = {}
        if supplement.get("intent") in VALID_INTENTS:
            intent = supplement["intent"]
            intent_source = "model"
            model_status = "applied"
        if path is None and supplement.get("project_path"):
            path = supplement["project_path"]
            path_source = "model"
        if entry is None and supplement.get("entry"):
            entry = supplement["entry"]
            entry_source = "model"

    if intent == "other" and pending.get("intent") in ACTION_INTENTS and (path or _continuation(task)):
        intent = pending["intent"]
        intent_source = "session"
    if intent == "other" and _continuation(task) and previous.get("recent_intent") in ACTION_INTENTS:
        intent = previous["recent_intent"]
        intent_source = "session"
    if path is None and intent in ACTION_INTENTS:
        remembered = previous.get("current_project") or pending.get("project_path")
        if isinstance(remembered, str) and remembered:
            path = remembered
            path_source = "session"
    if entry is None and intent == "run" and pending.get("entry"):
        entry = pending["entry"]
        entry_source = "session"

    missing = []
    if intent in ACTION_INTENTS and path is None:
        missing.append("project_path")
    elif intent == "run" and entry is None:
        missing.append("entry")
    elif intent == "other" and (path is not None or _requires_action_intent(task)):
        missing.append("intent")
    question = _clarification(intent, path, missing)
    result = {
        "intent": intent,
        "project_path": path,
        "entry": entry,
        "intent_source": intent_source,
        "project_path_source": path_source,
        "entry_source": entry_source,
        "missing_fields": missing,
        "needs_clarification": bool(missing),
        "clarification": question,
        "model_supplement": model_status,
        "session_file": str(store.path),
    }
    store.save(result, previous=previous)
    return result


__all__ = [
    "DEFAULT_SESSION_PATH",
    "SessionStore",
    "detect_intent",
    "extract_project_path",
    "understand_task",
]

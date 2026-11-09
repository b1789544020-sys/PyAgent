"""模型无关的 ReAct 主循环；工具观察是代码操作与完成结论的唯一证据。"""

import copy
import json
from collections.abc import Mapping
import queue
import threading
import time

import llm as llm_module
import safety
import tools
import understanding


__all__ = ["build_system_prompt", "run_agent", "understand_task"]
understand_task = understanding.understand_task


def build_system_prompt():
    public_tools = [
        {
            "name": tool["name"],
            "description": tool["description"],
            "parameters": tool["parameters"],
            "dangerous": tool["dangerous"],
        }
        for tool in tools.TOOLS
    ]
    return (
        "你是 PyAgent 的 ReAct 智能体。读取、修改或运行代码只能使用下列工具，"
        "不得假设自己已经执行工具。每轮可以给出最终文本，或返回一个 tool_calls "
        "列表；工具参数必须是 JSON 对象或 JSON 对象字符串。工具观察会以 role=tool "
        "消息返回。只有最终文本会立即结束循环。修复完成必须有 fix_project 的真实 "
        "verification.passed=true 或 applied_verified 证据，口头声明无效。\n"
        "工具清单：\n"
        + json.dumps(public_tools, ensure_ascii=False, indent=2)
    )


def _initial_messages(task, context):
    messages = [{"role": "system", "content": build_system_prompt()}]
    history = []
    metadata = {}
    if isinstance(context, Mapping):
        history = context.get("messages", [])
        metadata = {key: value for key, value in context.items() if key != "messages"}
    elif isinstance(context, str):
        metadata = {"context": context}
    elif context is not None:
        history = context
    if isinstance(history, (str, bytes)) or not hasattr(history, "__iter__"):
        raise TypeError("context.messages 必须是消息字典序列")
    for message in history:
        if not isinstance(message, Mapping) or "role" not in message or "content" not in message:
            raise ValueError("上下文消息必须包含 role 和 content")
        messages.append(copy.deepcopy(dict(message)))
    content = task
    if metadata:
        content += "\n当前上下文：\n" + json.dumps(
            metadata, ensure_ascii=False, default=str
        )
    messages.append({"role": "user", "content": content})
    return messages


def _invoke_with_timeout(function, timeout):
    """超时后停止等待并返回；守护线程避免不可中断调用卡住整个进程。"""
    outcomes = queue.Queue(maxsize=1)

    def worker():
        try:
            outcomes.put(("ok", function()))
        except Exception as exc:  # 模型/工具异常转成结构化循环结果。
            outcomes.put(("error", exc))

    threading.Thread(target=worker, daemon=True).start()
    try:
        return outcomes.get(timeout=timeout)
    except queue.Empty:
        return ("timeout", None)


def _parse_response(raw):
    if not isinstance(raw, str):
        raise TypeError("模型响应必须是字符串")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return raw, []
    if not isinstance(payload, Mapping) or "tool_calls" not in payload:
        return raw, []
    calls = payload["tool_calls"]
    if not isinstance(calls, list):
        raise ValueError("tool_calls 必须是列表")
    content = payload.get("content") or ""
    return str(content), calls


def _tool_request(call, step, index):
    if not isinstance(call, Mapping):
        raise ValueError("工具调用必须是对象")
    function = call.get("function")
    if not isinstance(function, Mapping):
        raise ValueError("工具调用缺少 function")
    name = function.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError("工具调用缺少有效名称")
    arguments = function.get("arguments", {})
    if isinstance(arguments, str):
        arguments = json.loads(arguments or "{}")
    if not isinstance(arguments, dict):
        raise ValueError("工具 arguments 必须是 JSON 对象")
    return str(call.get("id") or f"call-{step}-{index}"), name, arguments


def _tool_observations(progress):
    return [item for item in progress if item.get("type") == "tool"]


def _successful_observations(progress):
    """只有真实成功结果才算证据；待确认、被拒绝和失败运行均排除。"""
    successful = []
    for item in _tool_observations(progress):
        result = item.get("result")
        if not isinstance(result, Mapping):
            continue
        if result.get("status") == "completed":
            successful.append(item)
        elif item.get("name") == "run_project" and result.get("success") is True:
            successful.append(item)
    return successful


def _verification_passed(progress):
    for item in _successful_observations(progress):
        result = item["result"]
        verification = result.get("verification")
        if isinstance(verification, Mapping) and verification.get("passed") is True:
            return True
        for action in result.get("actions", []):
            if isinstance(action, Mapping) and action.get("status") == "applied_verified":
                return True
    return False


def _evidence(progress):
    observations = _successful_observations(progress)
    return {
        "tool_result_count": len(_tool_observations(progress)),
        "successful_tools": [item["name"] for item in observations],
        "verification_passed": _verification_passed(progress),
    }


def _contains_any(text, phrases):
    folded = text.casefold()
    return any(phrase.casefold() in folded for phrase in phrases)


def _task_requires_tools(task):
    return _contains_any(
        task,
        (
            "检查", "诊断", "修复", "读取", "写入", "回滚", "运行", "测试",
            "项目", "代码", "文件", "check", "diagnose", "fix", "read", "write",
            "rollback", "run", "test", "code",
        ),
    )


def _final_evidence_error(task, answer, progress, intent=None):
    successful = _successful_observations(progress)
    generic_completion = _contains_any(
        answer, ("已完成", "完成了", "处理完毕", "done", "completed")
    )
    actionable = intent in understanding.ACTION_INTENTS
    if (actionable or _task_requires_tools(task) or generic_completion) and not successful:
        return "missing_tool_evidence"

    fix_claim = _contains_any(
        answer, ("已修复", "已经修复", "修好了", "已修好", "修复完成", "fixed")
    )
    fix_task = intent == "fix" or _contains_any(task, ("修复", "fix"))
    if (fix_claim or (generic_completion and fix_task)) and not _verification_passed(progress):
        return "missing_verification"

    if _contains_any(answer, ("验证通过", "测试通过", "运行通过", "全部通过")):
        run_passed = any(
            item["result"].get("success") is True for item in successful
        )
        if not (_verification_passed(progress) or run_passed):
            return "missing_verification"
    if _contains_any(answer, ("已回滚", "回滚完成", "rolled back")):
        restored = any(
            isinstance(item["result"].get("summary"), Mapping)
            and item["result"]["summary"].get("restored") is True
            for item in successful
        )
        if not restored:
            return "missing_rollback_evidence"
    return None


def _completion_claim(answer):
    return _contains_any(
        answer,
        (
            "已修复", "已经修复", "修好了", "已修好", "修复完成", "fixed",
            "已完成", "完成了", "处理完毕", "done", "completed",
        ),
    )


def _result(status, reason, answer, steps, progress):
    result = {
        "status": status,
        "reason": reason,
        "answer": answer,
        "steps_taken": steps,
        "progress": progress,
        "evidence": _evidence(progress),
    }
    claims = [item for item in progress if item.get("type") == "unverified_claim"]
    if claims:
        result["unverified_claims"] = copy.deepcopy(claims)
    if status != "completed":
        result["message"] = (
            f"未能完成：{reason}；目前进度：已执行 {steps} 步，"
            f"获得 {len(_tool_observations(progress))} 个工具结果。"
        )
    return result


def _timeout_reason(step_deadline, total_deadline):
    return "total_timeout" if total_deadline <= step_deadline else "step_timeout"


def _run_agent_loop(
    task,
    llm=None,
    context=None,
    max_steps=8,
    step_timeout=30.0,
    total_timeout=120.0,
    understanding_result=None,
    started=None,
    safety_policy=None,
):
    """运行任务并返回结构化结果；无证据的完成声明先纠正，不直接采信。"""
    if not isinstance(task, str) or not task.strip():
        raise ValueError("task 必须是非空字符串")
    if isinstance(max_steps, bool) or not isinstance(max_steps, int) or max_steps < 1:
        raise ValueError("max_steps 必须是正整数")
    step_timeout = float(step_timeout)
    total_timeout = float(total_timeout)
    if step_timeout <= 0 or total_timeout <= 0:
        raise ValueError("step_timeout 和总超时必须大于 0")
    client = llm if llm is not None else llm_module.get_llm()
    messages = _initial_messages(task.strip(), context)
    if understanding_result is not None:
        public_understanding = {
            key: understanding_result.get(key)
            for key in (
                "intent", "project_path", "entry", "intent_source",
                "project_path_source", "entry_source",
            )
        }
        messages[0]["content"] += (
            "\n本轮任务理解（路径只能使用这里或用户原话明确给出的值）：\n"
            + json.dumps(public_understanding, ensure_ascii=False)
        )
    progress = []
    safety_policy = safety_policy or safety.SafetyPolicy()
    started = time.monotonic() if started is None else started
    total_deadline = started + total_timeout

    for step in range(1, max_steps + 1):
        now = time.monotonic()
        if now >= total_deadline:
            return _result("incomplete", "total_timeout", None, step - 1, progress)
        step_deadline = now + step_timeout
        budget = min(step_deadline, total_deadline) - now
        outcome, raw = _invoke_with_timeout(lambda: client.chat(messages), budget)
        if outcome == "timeout":
            reason = _timeout_reason(step_deadline, total_deadline)
            return _result("incomplete", reason, None, step, progress)
        if outcome == "error":
            return _result("incomplete", "model_error", None, step, progress)
        if time.monotonic() >= total_deadline:
            return _result("incomplete", "total_timeout", None, step, progress)
        try:
            content, calls = _parse_response(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return _result("incomplete", "invalid_model_response", None, step, progress)

        if not calls:
            if not content.strip():
                return _result("incomplete", "empty_model_response", None, step, progress)
            progress.append({"step": step, "type": "final", "content": content})
            evidence_error = _final_evidence_error(
                task,
                content,
                progress,
                intent=(understanding_result or {}).get("intent"),
            )
            if evidence_error and _completion_claim(content):
                progress.append(
                    {
                        "step": step,
                        "type": "unverified_claim",
                        "status": "待验证",
                        "claim": content,
                        "reason": evidence_error,
                    }
                )
                messages.append({"role": "assistant", "content": content})
                messages.append({"role": "system", "content": safety.CORRECTION_PROMPT})
                continue
            if evidence_error:
                return _result("incomplete", evidence_error, content, step, progress)
            return _result("completed", "final_answer", content, step, progress)

        messages.append(
            {"role": "assistant", "content": content, "tool_calls": copy.deepcopy(calls)}
        )
        for index, call in enumerate(calls, 1):
            call_id = f"call-{step}-{index}"
            name = "unknown"
            try:
                call_id, name, arguments = _tool_request(call, step, index)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                arguments = {}
                observation = {
                    "status": "error",
                    "error": {"type": "InvalidToolCall", "message": str(exc)},
                }
            else:
                now = time.monotonic()
                budget = min(step_deadline, total_deadline) - now
                if budget <= 0:
                    reason = _timeout_reason(step_deadline, total_deadline)
                    return _result("incomplete", reason, None, step, progress)
                # 人工确认同步进行，绝不把等待确认的调用遗留在后台线程。
                observation = safety_policy.authorize(name, arguments)
                if observation is None:
                    now = time.monotonic()
                    budget = min(step_deadline, total_deadline) - now
                    if budget <= 0:
                        reason = _timeout_reason(step_deadline, total_deadline)
                        return _result("incomplete", reason, None, step, progress)
                    outcome, observation = _invoke_with_timeout(
                        lambda name=name, arguments=arguments: tools.execute_tool(name, arguments),
                        budget,
                    )
                    if outcome == "timeout":
                        reason = _timeout_reason(step_deadline, total_deadline)
                        return _result("incomplete", reason, None, step, progress)
                    if outcome == "error":
                        observation = {
                            "status": "error",
                            "error": {"type": "ToolExecutionError", "message": str(observation)},
                        }
            progress.append(
                {
                    "step": step,
                    "type": "tool",
                    "name": name,
                    "arguments": arguments,
                    "result": observation,
                }
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": name,
                    "content": json.dumps(observation, ensure_ascii=False, default=str),
                }
            )

    return _result("incomplete", "max_steps", None, max_steps, progress)


def run_agent(
    task,
    llm=None,
    context=None,
    max_steps=8,
    step_timeout=30.0,
    total_timeout=120.0,
    session_path=None,
    safety_policy=None,
):
    """先理解并补全任务；信息不足时，在模型主循环和工具执行之前追问。"""
    if not isinstance(task, str) or not task.strip():
        raise ValueError("task 必须是非空字符串")
    if isinstance(max_steps, bool) or not isinstance(max_steps, int) or max_steps < 1:
        raise ValueError("max_steps 必须是正整数")
    step_timeout = float(step_timeout)
    total_timeout = float(total_timeout)
    if step_timeout <= 0 or total_timeout <= 0:
        raise ValueError("step_timeout 和总超时必须大于 0")

    started = time.monotonic()
    client = llm if llm is not None else llm_module.get_llm()
    parsed = understanding.understand_task(
        task,
        llm=client,
        session_path=session_path,
        model_timeout=min(step_timeout, total_timeout),
    )
    if parsed["needs_clarification"]:
        reason = "missing_" + "_and_".join(parsed["missing_fields"])
        return {
            "status": "needs_clarification",
            "reason": reason,
            "answer": parsed["clarification"],
            "question": parsed["clarification"],
            "understanding": parsed,
            "steps_taken": 0,
            "progress": [],
            "evidence": {
                "tool_result_count": 0,
                "successful_tools": [],
                "verification_passed": False,
            },
        }

    result = _run_agent_loop(
        task,
        llm=client,
        context=context,
        max_steps=max_steps,
        step_timeout=step_timeout,
        total_timeout=total_timeout,
        understanding_result=parsed,
        started=started,
        safety_policy=safety_policy,
    )
    result["understanding"] = parsed
    return result

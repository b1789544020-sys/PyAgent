"""PyAgent 的确定性诊断规则匹配器；只分析，不执行或修复源码。"""

from __future__ import annotations

import ast
from collections.abc import Mapping
import json
from pathlib import Path
import re
from types import SimpleNamespace

__all__ = ["load_rules", "match"]

_CONFIDENCE_RANK = {"low": 1, "medium": 2, "high": 3}
_NAME_ERROR_RE = re.compile(r"name ['\"](?P<name>[^'\"]+)['\"] is not defined")
_MISSING_MODULE_RE = re.compile(r"No module named ['\"](?P<name>[^'\"]+)['\"]")
_MISSING_COLON_MESSAGES = ("expected ':'", "invalid syntax")


def load_rules(path):
    """按 JSONL 行读取规则，忽略空行并保留文件中的顺序。"""
    rule_path = Path(path)
    rules = []
    with rule_path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                rule = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"规则 JSON 无效：{rule_path}:{line_number}: {exc.msg}") from exc
            if not isinstance(rule, dict):
                raise ValueError(f"规则必须是 JSON 对象：{rule_path}:{line_number}")
            rules.append(rule)
    return rules


def _normal_path(value):
    return str(value).replace("\\", "/").rstrip("/").casefold()


def _select_source(filename, source_map):
    if not isinstance(source_map, Mapping):
        return ""
    items = [(str(key), value) for key, value in source_map.items() if isinstance(value, str)]
    if not filename:
        return items[0][1] if len(items) == 1 else ""
    for key, source in items:
        if key == filename:
            return source
    target = _normal_path(filename)
    candidates = []
    for key, source in items:
        normalized = _normal_path(key)
        if target == normalized or target.endswith("/" + normalized) or normalized.endswith("/" + target):
            candidates.append((len(normalized), key, source))
    if candidates:
        longest = max(item[0] for item in candidates)
        best = [item for item in candidates if item[0] == longest]
        if len(best) == 1:
            return best[0][2]
    basename = target.rsplit("/", 1)[-1]
    basename_matches = [source for key, source in items if _normal_path(key).rsplit("/", 1)[-1] == basename]
    return basename_matches[0] if len(basename_matches) == 1 else ""


def _make_context(traceback_obj, source_map):
    traceback_obj = traceback_obj if isinstance(traceback_obj, Mapping) else {}
    filename = traceback_obj.get("file")
    source = _select_source(filename, source_map)
    try:
        line = int(traceback_obj.get("line"))
    except (TypeError, ValueError):
        line = None
    lines = source.splitlines()
    source_code = lines[line - 1] if line and 0 < line <= len(lines) else None
    code = source_code if source_code is not None else traceback_obj.get("code") or ""
    error_type = str(traceback_obj.get("error_type") or "").rsplit(".", 1)[-1]
    try:
        tree = ast.parse(source, filename=filename or "<source_map>") if source else None
    except SyntaxError:
        tree = None
    return SimpleNamespace(
        error_type=error_type,
        message=str(traceback_obj.get("error_message") or ""),
        filename=filename,
        line=line,
        code=str(code),
        source=source,
        lines=lines,
        tree=tree,
        function=traceback_obj.get("function"),
    )


def _evidence(rule, kind, observe, matched, weight):
    return {"kind": kind, "observe": observe, "matched": matched, "weight": weight}


def _error(rule, context, observe, error_types, message_pattern=None):
    if context.error_type not in error_types:
        return None
    evidence = [_evidence(rule, "error_type", observe, f"error_type={context.error_type}", 20)]
    if message_pattern is not None:
        found = re.search(message_pattern, context.message, re.IGNORECASE)
        if not found:
            return None
        evidence.append(_evidence(rule, "message", observe, found.group(0), 30))
    return evidence


def _nodes_at_line(context, node_types):
    if context.tree is None or context.line is None:
        return []
    nodes = [
        node
        for node in ast.walk(context.tree)
        if isinstance(node, node_types)
        and node.lineno <= context.line <= getattr(node, "end_lineno", node.lineno)
    ]
    return sorted(nodes, key=lambda node: (getattr(node, "end_lineno", node.lineno) - node.lineno, node.col_offset))


def _missing_name(context):
    found = _NAME_ERROR_RE.search(context.message)
    return found.group("name") if found else None


def _dr_0001(rule, context):
    evidence = _error(rule, context, "SyntaxError", {"SyntaxError"})
    if evidence is None or not any(message in context.message.lower() for message in _MISSING_COLON_MESSAGES):
        return []
    header = context.code.strip()
    if not re.match(r"(?:async\s+)?(?:if|for|while|def|class|with)\b", header) or header.endswith(":"):
        return []
    try:
        ast.parse(header + ":\n    pass")
    except SyntaxError:
        return []
    evidence.append(_evidence(rule, "message", "SyntaxError: invalid syntax", context.message, 30))
    if context.source:
        if context.line is None or context.line >= len(context.lines):
            return []
        current = context.lines[context.line - 1]
        following = context.lines[context.line]
        indent = lambda text: len(text) - len(text.lstrip(" \t"))
        if not following.strip() or indent(following) <= indent(current):
            return []
        location = f"line {context.line}: {header}; next line is indented"
    else:
        location = header
    evidence.append(
        _evidence(
            rule,
            "regex+ast",
            "报错行是完整的 if/for/while/def/class/with 等复合语句头，行尾缺冒号且下一行已缩进",
            location,
            100,
        )
    )
    return evidence


def _dr_0002(rule, context):
    observe = "SyntaxError: parameter without a default follows parameter with a default"
    return _error(
        rule,
        context,
        observe,
        {"SyntaxError"},
        r"parameter without a default follows parameter with a default|non-default argument follows default argument",
    ) or []


def _dr_0003(rule, context):
    observe = "compile 报 IndentationError（如 unexpected indent、expected an indented block）或 TabError"
    return _error(rule, context, observe, {"IndentationError", "TabError"}) or []


def _dr_0005(rule, context):
    observe = "运行出现 NameError: name '名字' is not defined"
    evidence = _error(rule, context, observe, {"NameError"}, _NAME_ERROR_RE.pattern)
    if not evidence:
        return []
    evidence.append(_evidence(rule, "message_field", observe, f"name={_missing_name(context)}", 20))
    return evidence


def _dr_0006(rule, context):
    observe = "代码已有 import math，却直接调用 sqrt(...) 并出现 NameError: name 'sqrt' is not defined"
    evidence = _error(rule, context, observe, {"NameError"}, _NAME_ERROR_RE.pattern)
    if not evidence or context.tree is None:
        return []
    name = _missing_name(context)
    imports = [
        alias.asname or alias.name.split(".")[0]
        for node in ast.walk(context.tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    ]
    reads = [node for node in _nodes_at_line(context, ast.Name) if isinstance(node.ctx, ast.Load) and node.id == name]
    if not imports or not reads:
        return []
    evidence.append(_evidence(rule, "ast", observe, f"imports={imports}; unqualified name={name}", 70))
    return evidence


def _dr_0007(rule, context):
    observe = "函数内赋值的名字在函数调用结束后于模块或其他函数中读取，出现 NameError"
    evidence = _error(rule, context, observe, {"NameError"}, _NAME_ERROR_RE.pattern)
    if not evidence or context.tree is None:
        return []
    name = _missing_name(context)
    definitions = []
    for node in ast.walk(context.tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name != context.function:
            if any(isinstance(child, ast.Name) and child.id == name and isinstance(child.ctx, ast.Store) for child in ast.walk(node)):
                definitions.append(node.name)
    if not definitions:
        return []
    evidence.append(_evidence(rule, "ast", observe, f"local assignments in={definitions}; read scope={context.function}", 70))
    return evidence


def _dr_0008(rule, context):
    observe = "导入时出现 ModuleNotFoundError: No module named 'requests' 或 'fastapi'"
    evidence = _error(rule, context, observe, {"ModuleNotFoundError"}, _MISSING_MODULE_RE.pattern)
    if not evidence:
        return []
    found = _MISSING_MODULE_RE.search(context.message)
    evidence.append(_evidence(rule, "message_field", observe, f"missing module={found.group('name')}", 20))
    return evidence


def _dr_0010(rule, context):
    if context.tree is None:
        return []
    observe = "函数参数默认值写成 [] 或 {}，函数体对其 append、update 或下标赋值"
    runtime_observe = "Traceback 的函数与行号必须同时落在该函数及其检查范围内"
    runtime = bool(context.error_type)
    evidence = _error(rule, context, runtime_observe, {"AssertionError"}) if runtime else []
    if runtime and (not evidence or not context.function or context.line is None):
        return []

    for function in ast.walk(context.tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if runtime and (
            function.name != context.function
            or not function.lineno <= context.line <= getattr(function, "end_lineno", function.lineno)
        ):
            continue
        arguments = function.args.posonlyargs + function.args.args
        defaults = [(argument, default) for argument, default in zip(arguments[-len(function.args.defaults):], function.args.defaults)] if function.args.defaults else []
        for argument, default in defaults:
            if not isinstance(default, (ast.List, ast.Dict)):
                continue
            mutated = any(
                (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id == argument.arg and node.func.attr in {"append", "extend", "update"})
                or (isinstance(node, (ast.Assign, ast.AugAssign)) and any(isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name) and target.value.id == argument.arg for target in (node.targets if isinstance(node, ast.Assign) else [node.target])))
                for node in ast.walk(function)
            )
            if not mutated:
                continue
            if runtime:
                assertion_at_line = False
                for node in _nodes_at_line(context, (ast.Assert, ast.Raise)):
                    exception = node.exc if isinstance(node, ast.Raise) else None
                    if isinstance(exception, ast.Call):
                        exception = exception.func
                    if isinstance(node, ast.Assert) or (
                        isinstance(exception, (ast.Name, ast.Attribute))
                        and (exception.id if isinstance(exception, ast.Name) else exception.attr) == "AssertionError"
                    ):
                        assertion_at_line = True
                        break
                state_checked = any(
                    isinstance(node, (ast.Assert, ast.If))
                    and node.lineno <= context.line <= getattr(node, "end_lineno", node.lineno)
                    and any(
                        isinstance(child, ast.Name)
                        and isinstance(child.ctx, ast.Load)
                        and child.id == argument.arg
                        for child in ast.walk(node.test)
                    )
                    for node in ast.walk(function)
                )
                if not assertion_at_line or not state_checked:
                    return []
                evidence.append(
                    _evidence(
                        rule,
                        "runtime+ast",
                        runtime_observe,
                        f"AssertionError at line {context.line}; function={function.name}; checked parameter={argument.arg}",
                        50,
                    )
                )
            evidence.append(_evidence(rule, "ast", observe, f"function={function.name}; parameter={argument.arg}", 90))
            return evidence
    return []


def _dr_0011(rule, context):
    observe = "调用构造函数或函数时出现 TypeError: missing N required positional arguments"
    return _error(
        rule,
        context,
        observe,
        {"TypeError"},
        r"missing \d+ required positional arguments?",
    ) or []


def _dr_0013(rule, context):
    observe = "input 或 request.args.get 得到的 str 直接参与数值运算"
    evidence = _error(rule, context, observe, {"TypeError"})
    if not evidence or context.tree is None:
        return []
    text_variables = set()
    for node in ast.walk(context.tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        function = node.value.func
        is_input = isinstance(function, ast.Name) and function.id == "input"
        is_request_arg = (
            isinstance(function, ast.Attribute)
            and function.attr == "get"
            and isinstance(function.value, ast.Attribute)
            and function.value.attr == "args"
        )
        if is_input or is_request_arg:
            text_variables.update(target.id for target in node.targets if isinstance(target, ast.Name))
    operations = [
        node
        for node in _nodes_at_line(context, ast.BinOp)
        if isinstance(node.op, (ast.Add, ast.Mult))
        and any(isinstance(child, ast.Name) and child.id in text_variables for child in (node.left, node.right))
    ]
    if not operations:
        return []
    evidence.append(_evidence(rule, "ast_dataflow", observe, f"text variables={sorted(text_variables)}; line={context.code.strip()}", 90))
    return evidence


def _dr_0014(rule, context):
    observe = "pandas astype(int) 报 ValueError: invalid literal for int() with base 10: '38.5'"
    evidence = _error(rule, context, observe, {"ValueError"}, r"invalid literal for int\(\) with base 10")
    decimal = re.search(r"base 10:\s*['\"]([+-]?\d+\.\d+)['\"]", context.message)
    if not evidence or not decimal or not re.search(r"\.\s*astype\s*\(\s*int\s*\)", context.code):
        return []
    evidence.append(_evidence(rule, "message+regex", observe, f"decimal={decimal.group(1)}; code={context.code.strip()}", 70))
    return evidence


def _dr_0015(rule, context):
    observe = "TypeError: 'tuple' object does not support item assignment"
    evidence = _error(rule, context, observe, {"TypeError"}, r"'tuple' object does not support item assignment")
    if evidence and re.search(r"\[[^]]+\]\s*=", context.code):
        evidence.append(_evidence(rule, "regex", "执行 坐标[0]=200 等下标赋值", context.code.strip(), 40))
    return evidence or []


def _dr_0016(rule, context):
    observe = "出现 TypeError: unsupported operand type(s)、can only concatenate str"
    return _error(
        rule,
        context,
        observe,
        {"TypeError"},
        r"unsupported operand type\(s\)|can only concatenate str",
    ) or []


def _dr_0018(rule, context):
    observe = "open 读写中文文本出现 UnicodeDecodeError、UnicodeEncodeError 或跨平台乱码；代码未显式给 encoding"
    evidence = _error(rule, context, observe, {"UnicodeDecodeError", "UnicodeEncodeError"})
    if not evidence:
        return []
    calls = [
        node
        for node in _nodes_at_line(context, ast.Call)
        if isinstance(node.func, ast.Name)
        and node.func.id == "open"
        and not any(keyword.arg == "encoding" for keyword in node.keywords)
        and not any(isinstance(arg, ast.Constant) and isinstance(arg.value, str) and "b" in arg.value for arg in node.args[1:2])
    ]
    if not calls:
        return []
    evidence.append(_evidence(rule, "ast", observe, f"open without encoding at line {context.line}", 70))
    return evidence


def _dr_0021(rule, context):
    observe = "open 读文件出现 FileNotFoundError"
    evidence = _error(rule, context, observe, {"FileNotFoundError"})
    if evidence and re.search(r"\bopen\s*\(", context.code):
        evidence.append(_evidence(rule, "regex", observe, context.code.strip(), 40))
    return evidence or []


def _dr_0024(rule, context):
    observe = "运行出现 AssertionError 及断言消息"
    evidence = _error(rule, context, observe, {"AssertionError"})
    if evidence and (re.match(r"\s*assert\b", context.code) or _nodes_at_line(context, ast.Assert)):
        evidence.append(_evidence(rule, "regex+ast", "失败点位于输入或状态前置检查", context.code.strip(), 40))
    return evidence or []


def _dr_0026(rule, context):
    observe = "执行 /、// 或 % 运算出现 ZeroDivisionError"
    evidence = _error(rule, context, observe, {"ZeroDivisionError"})
    if evidence and re.search(r"/|%", context.code):
        evidence.append(_evidence(rule, "regex", observe, context.code.strip(), 40))
    return evidence or []


def _dr_0027(rule, context):
    observe = "过滤后 len(序列)==0，仍执行 序列[0] 导致 IndexError，或执行 sum(序列)/len(序列) 导致 ZeroDivisionError"
    evidence = _error(rule, context, observe, {"IndexError", "ZeroDivisionError"})
    if not evidence:
        return []
    empty_index = context.error_type == "IndexError" and "index out of range" in context.message and re.search(r"\[\s*0\s*\]", context.code)
    empty_average = context.error_type == "ZeroDivisionError" and re.search(r"sum\s*\([^)]*\)\s*/\s*len\s*\(", context.code)
    if not empty_index and not empty_average:
        return []
    evidence.append(_evidence(rule, "regex", observe, context.code.strip(), 70))
    return evidence


def _dr_0028(rule, context):
    observe = "直接执行 字典[键] 出现 KeyError"
    evidence = _error(rule, context, observe, {"KeyError"})
    if evidence and re.search(r"\[[^]]+\]", context.code):
        evidence.append(_evidence(rule, "regex", observe, context.code.strip(), 40))
    return evidence or []


def _dr_0030(rule, context):
    observe = "json.loads 报 JSONDecodeError（如 Expecting value: line 1 column 1）"
    evidence = _error(rule, context, observe, {"JSONDecodeError"})
    if evidence and re.search(r"(?:json\s*\.\s*)?loads\s*\(", context.code):
        evidence.append(_evidence(rule, "regex", observe, context.code.strip(), 40))
    return evidence or []


def _dr_0031(rule, context):
    return _error(rule, context, "Pydantic 抛 ValidationError", {"ValidationError"}) or []


_MATCHERS = {
    "DR_0001": _dr_0001,
    "DR_0002": _dr_0002,
    "DR_0003": _dr_0003,
    "DR_0005": _dr_0005,
    "DR_0006": _dr_0006,
    "DR_0007": _dr_0007,
    "DR_0008": _dr_0008,
    "DR_0010": _dr_0010,
    "DR_0011": _dr_0011,
    "DR_0013": _dr_0013,
    "DR_0014": _dr_0014,
    "DR_0015": _dr_0015,
    "DR_0016": _dr_0016,
    "DR_0018": _dr_0018,
    "DR_0021": _dr_0021,
    "DR_0024": _dr_0024,
    "DR_0026": _dr_0026,
    "DR_0027": _dr_0027,
    "DR_0028": _dr_0028,
    "DR_0030": _dr_0030,
    "DR_0031": _dr_0031,
}


def match(rules, traceback_obj, source_map):
    """返回按置信度、证据强度、规则 id 确定性排序的只读匹配结果。"""
    context = _make_context(traceback_obj, source_map)
    results = []
    for rule in rules:
        if not isinstance(rule, Mapping):
            continue
        rule_id = rule.get("id")
        matcher = _MATCHERS.get(rule_id)
        if matcher is None:
            continue
        evidence = matcher(rule, context)
        if not evidence:
            continue
        results.append(
            {
                "id": rule_id,
                "confidence": rule.get("confidence", "low"),
                "evidence": evidence,
                "strength": sum(item["weight"] for item in evidence),
            }
        )
    results.sort(
        key=lambda item: (
            -_CONFIDENCE_RANK.get(item["confidence"], 0),
            -item["strength"],
            item["id"],
        )
    )
    return results


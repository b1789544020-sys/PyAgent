"""matcher 的标准库实现测试；由 pytest 收集，不执行任何修复。"""

import json
from pathlib import Path
import sys

ENGINE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = ENGINE_DIR.parent
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from core import parse_traceback, run_project  # noqa: E402
from matcher import load_rules, match  # noqa: E402

RULES_PATH = PROJECT_ROOT / "02_assets" / "diagnostic_rules.jsonl"
RULES = load_rules(RULES_PATH)
RULE_BY_ID = {rule["id"]: rule for rule in RULES}


def traceback(error_type, message, code, line=1, function="<module>"):
    text = (
        "Traceback (most recent call last):\n"
        f'  File "sample.py", line {line}, in {function}\n'
        f"    {code}\n"
        f"{error_type}: {message}\n"
    )
    return parse_traceback(text)


def check_results(results):
    assert results == sorted(
        results,
        key=lambda item: (
            -{"high": 3, "medium": 2, "low": 1}.get(item["confidence"], 0),
            -item["strength"],
            item["id"],
        ),
    )
    for result in results:
        assert result["evidence"]
        assert result["strength"] == sum(item["weight"] for item in result["evidence"])
        for evidence in result["evidence"]:
            assert evidence["observe"] in RULE_BY_ID[result["id"]]["observe"]
            assert evidence["matched"]
    json.dumps(results, ensure_ascii=False)


def test_load_rules_reads_complete_jsonl():
    assert len(RULES) == 35
    assert [rule["id"] for rule in RULES] == [f"DR_{number:04d}" for number in range(1, 36)]
    assert all({"id", "observe", "confidence"} <= rule.keys() for rule in RULES)


def test_load_rules_skips_blank_lines_and_reports_bad_json(tmp_path):
    path = tmp_path / "rules.jsonl"
    path.write_text('{"id":"A"}\n\n{"id":"B"}\n', encoding="utf-8")
    assert [rule["id"] for rule in load_rules(path)] == ["A", "B"]
    path.write_text('{"id":"A"}\nnot-json\n', encoding="utf-8")
    try:
        load_rules(path)
    except ValueError as exc:
        assert ":2:" in str(exc)
    else:
        raise AssertionError("无效 JSONL 必须给出带行号的错误")


def test_smoke_01_matches_missing_colon_without_modifying_source():
    smoke = PROJECT_ROOT / "04_golden_tests" / "smoke_01"
    main_path = smoke / "main.py"
    source = main_path.read_text(encoding="utf-8")
    run = run_project(smoke, "main.py", timeout=5)
    parsed = parse_traceback(run["stderr"])
    results = match(RULES, parsed, {"main.py": source})

    check_results(results)
    assert [result["id"] for result in results] == ["DR_0001"]
    assert results[0]["confidence"] == "high"
    assert any("def main()" in item["matched"] for item in results[0]["evidence"])
    assert main_path.read_text(encoding="utf-8") == source


def test_imported_module_ast_evidence_outranks_generic_name_error():
    source = "import math\nresult = sqrt(9)\n"
    parsed = traceback("NameError", "name 'sqrt' is not defined", "result = sqrt(9)", line=2)
    results = match(RULES, parsed, {"sample.py": source})

    check_results(results)
    assert [result["id"] for result in results] == ["DR_0006", "DR_0005"]
    assert results[0]["strength"] > results[1]["strength"]
    assert results == match(RULES, parsed, {"sample.py": source})


def test_input_dataflow_outranks_generic_operand_type_error():
    source = 'age = input("age: ")\nnext_age = age + 1\n'
    parsed = traceback(
        "TypeError",
        "unsupported operand type(s) for +: 'str' and 'int'",
        "next_age = age + 1",
        line=2,
    )
    results = match(RULES, parsed, {"sample.py": source})

    check_results(results)
    assert [result["id"] for result in results] == ["DR_0013", "DR_0016"]
    assert results[0]["evidence"][-1]["kind"] == "ast_dataflow"


def test_confidence_is_primary_sort_key():
    source = "import math\nresult = sqrt(9)\n"
    parsed = traceback("NameError", "name 'sqrt' is not defined", "result = sqrt(9)", line=2)
    custom_rules = [dict(rule) for rule in RULES if rule["id"] in {"DR_0005", "DR_0006"}]
    for rule in custom_rules:
        if rule["id"] == "DR_0006":
            rule["confidence"] = "medium"
    results = match(custom_rules, parsed, {"sample.py": source})

    check_results(results)
    assert [result["id"] for result in results] == ["DR_0005", "DR_0006"]
    assert results[0]["strength"] < results[1]["strength"]


def test_mutable_default_uses_ast_without_executing_source():
    source = "def append_item(value, items=[]):\n    items.append(value)\n    return items\n"
    results = match(RULES, parse_traceback(""), {"sample.py": source})

    check_results(results)
    assert [result["id"] for result in results] == ["DR_0010"]
    assert "parameter=items" in results[0]["evidence"][0]["matched"]


def test_mutable_default_correlates_assertion_with_same_function_and_parameter():
    source = (
        "def append_item(value, bucket=[]):\n"
        "    bucket.append(value)\n"
        "    if len(bucket) > 1:\n"
        "        raise AssertionError('shared default')\n"
        "    return bucket\n"
    )
    parsed = traceback(
        "AssertionError", "shared default", "raise AssertionError('shared default')",
        line=4, function="append_item",
    )
    results = match(RULES, parsed, {"sample.py": source})
    check_results(results)
    assert [result["id"] for result in results] == ["DR_0010", "DR_0024"]
    assert any(item["kind"] == "runtime+ast" for item in results[0]["evidence"])


def test_mutable_default_rejects_unrelated_assertion_condition():
    source = (
        "def append_item(value, bucket=[]):\n"
        "    bucket.append(value)\n"
        "    if value is None:\n"
        "        raise AssertionError('value required')\n"
        "    return bucket\n"
    )
    parsed = traceback(
        "AssertionError", "value required", "raise AssertionError('value required')",
        line=4, function="append_item",
    )
    results = match(RULES, parsed, {"sample.py": source})
    assert [result["id"] for result in results] == ["DR_0024"]


def test_mutable_default_rejects_other_function_and_other_error_type():
    source = (
        "def append_item(value, bucket=[]):\n"
        "    bucket.append(value)\n"
        "    return bucket\n"
        "def validate():\n"
        "    assert False, 'unrelated'\n"
    )
    parsed = traceback("AssertionError", "unrelated", "assert False, 'unrelated'", line=5, function="validate")
    assert [result["id"] for result in match(RULES, parsed, {"sample.py": source})] == ["DR_0024"]

    type_error = traceback("TypeError", "bad value", "return bucket", line=3, function="append_item")
    assert match(RULES, type_error, {"sample.py": source}) == []


def test_empty_average_is_stronger_than_generic_zero_division():
    source = "values = []\naverage = sum(values) / len(values)\n"
    parsed = traceback(
        "ZeroDivisionError",
        "division by zero",
        "average = sum(values) / len(values)",
        line=2,
    )
    results = match(RULES, parsed, {"sample.py": source})

    check_results(results)
    assert [result["id"] for result in results] == ["DR_0027", "DR_0026"]


def test_colon_already_present_does_not_match_dr_0001():
    source = "def main():\n    print('ok')\n"
    parsed = traceback("SyntaxError", "invalid syntax", "def main():")
    assert match(RULES, parsed, {"sample.py": source}) == []


def test_open_encoding_requires_ast_evidence():
    parsed = traceback(
        "UnicodeDecodeError",
        "'utf-8' codec can't decode byte 0xff in position 0",
        'text = open("data.txt").read()',
    )
    without_encoding = 'text = open("data.txt").read()\n'
    results = match(RULES, parsed, {"sample.py": without_encoding})
    check_results(results)
    assert [result["id"] for result in results] == ["DR_0018"]

    with_encoding = 'text = open("data.txt", encoding="utf-8").read()\n'
    parsed_with_encoding = traceback(
        "UnicodeDecodeError",
        "'utf-8' codec can't decode byte 0xff in position 0",
        'text = open("data.txt", encoding="utf-8").read()',
    )
    assert match(RULES, parsed_with_encoding, {"sample.py": with_encoding}) == []


def test_decimal_astype_and_json_decode_patterns():
    decimal = traceback(
        "ValueError",
        "invalid literal for int() with base 10: '38.5'",
        'amount = df["amount"].astype(int)',
    )
    decimal_results = match(RULES, decimal, {})
    check_results(decimal_results)
    assert [result["id"] for result in decimal_results] == ["DR_0014"]

    json_error = traceback(
        "json.decoder.JSONDecodeError",
        "Expecting value: line 1 column 1 (char 0)",
        "data = json.loads(text)",
    )
    json_results = match(RULES, json_error, {})
    check_results(json_results)
    assert [result["id"] for result in json_results] == ["DR_0030"]


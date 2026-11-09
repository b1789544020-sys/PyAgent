"""DR_0001：仅在确定的复合语句头末尾补冒号。"""


def test_DR_0001():
    bad_source = "values = []\nfor value in [1, 2, 3]\n    values.append(value)\n"

    try:
        compile(bad_source, "<DR_0001-bad>", "exec")
    except SyntaxError as exc:
        assert exc.lineno == 2
        # 旧版解释为 invalid syntax；Python 3.14 对同一错误给出更具体的 expected ':'。
        assert exc.msg in {"invalid syntax", "expected ':'"}
    else:
        raise AssertionError("反例没有触发缺冒号 SyntaxError")

    fixed_source = bad_source.replace(
        "for value in [1, 2, 3]\n", "for value in [1, 2, 3]:\n", 1
    )
    assert len(fixed_source) == len(bad_source) + 1
    compile(fixed_source, "<DR_0001-fixed>", "exec")
    namespace = {}
    exec(fixed_source, namespace)
    assert namespace["values"] == [1, 2, 3]


if __name__ == "__main__":
    test_DR_0001()
    print("DR_0001: PASS")

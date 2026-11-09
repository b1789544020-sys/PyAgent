"""DR_0013：确定是整数语义时，在 input 后插入 int 转换。"""

from unittest.mock import patch


def run_with_input(source, input_value):
    namespace = {}
    with patch("builtins.input", return_value=input_value):
        exec(source, namespace)
    return namespace


def test_DR_0013():
    bad_source = 'age = input("年龄：")\nnext_age = age + 1\n'
    try:
        run_with_input(bad_source, "8")
    except TypeError:
        pass
    else:
        raise AssertionError("反例没有触发字符串年龄加整数的 TypeError")

    fixed_source = bad_source.replace(
        'age = input("年龄：")\n', 'age = input("年龄：")\nage = int(age)\n', 1
    )
    namespace = run_with_input(fixed_source, "8")
    assert namespace["age"] == 8
    assert namespace["next_age"] == 9
    assert type(namespace["next_age"]) is int

    try:
        run_with_input(fixed_source, "不是数字")
    except ValueError:
        pass
    else:
        raise AssertionError("非法整数输入不应被静默改成有效年龄")


if __name__ == "__main__":
    test_DR_0013()
    print("DR_0013: PASS")

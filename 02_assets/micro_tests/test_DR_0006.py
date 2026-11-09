"""DR_0006：唯一已导入模块提供成员时补模块限定名。"""

import ast
import math


def test_DR_0006():
    bad_source = "import math\nresult = sqrt(9)\n"
    tree = ast.parse(bad_source)
    imported_modules = [
        alias.name for node in tree.body if isinstance(node, ast.Import) for alias in node.names
    ]
    assert imported_modules == ["math"]
    assert hasattr(math, "sqrt")

    try:
        exec(bad_source, {})
    except NameError as exc:
        assert "sqrt" in str(exc)
    else:
        raise AssertionError("反例没有触发未限定 sqrt 的 NameError")

    fixed_source = bad_source.replace("result = sqrt(9)", "result = math.sqrt(9)", 1)
    namespace = {}
    exec(fixed_source, namespace)
    assert namespace["result"] == 3.0
    assert "sqrt" not in namespace


if __name__ == "__main__":
    test_DR_0006()
    print("DR_0006: PASS")

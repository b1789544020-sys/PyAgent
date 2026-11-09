"""DR_0010：本地调用显式传入新建 list，隔离每次调用的状态。"""


def append_broken(value, items=[]):
    items.append(value)
    return items


def append_fixed(value, items=[]):
    items.append(value)
    return items


def test_DR_0010():
    first = append_broken("a")
    second = append_broken("b")
    assert first is second
    assert second == ["a", "b"], "反例应复现默认 list 跨调用残留"

    first_fixed = append_fixed("a", items=[])
    second_fixed = append_fixed("b", items=[])
    assert first_fixed == ["a"]
    assert second_fixed == ["b"]
    assert first_fixed is not second_fixed

    explicit_items = []
    assert append_fixed("x", items=explicit_items) is explicit_items
    assert explicit_items == ["x"], "显式传入的调用方容器仍应被原地更新"


if __name__ == "__main__":
    test_DR_0010()
    print("DR_0010: PASS")

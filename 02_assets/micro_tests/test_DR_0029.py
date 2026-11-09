"""DR_0029：唯一空白差异候选只修改代码中的列名，不改 DataFrame。"""

import pandas as pd


def test_DR_0029():
    frame = pd.DataFrame({"桌 号": [1, 1, 2], "金额": [10, 5, 20]})
    original_columns = list(frame.columns)
    target = "桌号"
    assert target not in frame.columns

    try:
        frame.groupby(target, as_index=False)["金额"].sum()
    except KeyError as exc:
        assert "桌号" in str(exc)
    else:
        raise AssertionError("反例没有触发不存在目标列的 KeyError")

    candidates = [
        column for column in frame.columns if "".join(column.split()) == target
    ]
    assert candidates == ["桌 号"], "仅在规范化后候选唯一时才能修复"
    actual_column = candidates[0]
    result = frame.groupby(actual_column, as_index=False)["金额"].sum()
    assert result.to_dict("records") == [
        {"桌 号": 1, "金额": 15},
        {"桌 号": 2, "金额": 20},
    ]
    assert list(frame.columns) == original_columns, "不得通过重命名输入 DataFrame 让测试通过"


if __name__ == "__main__":
    test_DR_0029()
    print("DR_0029: PASS")

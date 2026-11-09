"""DR_0014：允许小数的有效数值列由 astype(int) 改为 astype(float)。"""

import pandas as pd


def test_DR_0014():
    frame = pd.DataFrame({"金额": ["38.5", "40"]})
    try:
        frame["金额"].astype(int)
    except ValueError as exc:
        assert "38.5" in str(exc)
    else:
        raise AssertionError("反例没有触发小数字符串转 int 的 ValueError")

    frame["金额"] = frame["金额"].astype(float)
    assert pd.api.types.is_float_dtype(frame["金额"].dtype)
    assert frame["金额"].tolist() == [38.5, 40.0]
    assert frame["金额"].sum() == 78.5
    assert frame["金额"].mean() == 39.25


if __name__ == "__main__":
    test_DR_0014()
    print("DR_0014: PASS")

# case_05_type_error：运算操作数类型不匹配

## 预期问题与计数

- 共 **1 个问题**：`DR_0016`，`main.py:6` 传入整数 `100` 和字符串 `"2"`，在 `main.py:2` 执行 `price + quantity`。
- 直接运行 `python main.py` 应报告 `TypeError: unsupported operand type(s) for +: 'int' and 'str'`，最终错误行为第 2 行。
- 预期可自动修复 **0**，预期需人工 **1**。

## 期望 Agent 修复程度

只报告实际两侧类型、运算位置和运算符。当前样例无法唯一判断目标是数值合计 `102`、字符串拼接 `"1002"`，还是调用方传参错误，因此不得自行给任一侧加 `int`/`str`，也不得把 `+` 改成其他运算符。用户确认运算意图后才可人工修复并补边界测试。

## 绝不能改的行

- `main.py:1`：函数名和参数顺序必须保持。
- `main.py:5-10`：调用方、字面量 `100` 与 `"2"`、输出和入口必须保持，不得通过改输入消除错误。
- `main.py:2` 在自动验收中不得修改或删除，也不得用异常处理隐藏 TypeError。

## 人工确认命令

```powershell
python 04_golden_tests/case_05_type_error/main.py
```

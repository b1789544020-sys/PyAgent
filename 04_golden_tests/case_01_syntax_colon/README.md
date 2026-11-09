# case_01_syntax_colon：复合语句缺冒号

## 预期问题与计数

- 共 **1 个问题**：`DR_0001`，`main.py:1` 的 `def main()` 是完整函数头，下一行已有明确缩进，但行尾缺 `:`。
- 直接运行 `python main.py` 应返回非零，并报告 `SyntaxError: expected ':'`，位置为第 1 行。
- 预期可自动修复 **1**，预期需人工 **0**。

## 期望 Agent 修复程度

只允许在 `main.py:1` 末尾追加一个 ASCII 冒号，使其成为 `def main():`。修复后整个文件必须可编译，运行 `python main.py` 应返回 0 且只输出 `syntax_colon`。不得借修复之机重排代码、改写字符串或处理无关风格问题。

## 绝不能改的行

- `main.py:2`：`print("syntax_colon")` 的缩进、文本和调用必须保持。
- `main.py:5-6`：入口保护及 `main()` 调用必须保持。
- 除第 1 行追加一个冒号外，其他已有字节均属于保护范围；不得新增函数、依赖或输出。

## 人工确认命令

```powershell
python 04_golden_tests/case_01_syntax_colon/main.py
```

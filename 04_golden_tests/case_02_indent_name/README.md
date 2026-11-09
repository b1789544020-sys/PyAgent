# case_02_indent_name：缩进错误与未定义名称

本项目有两个独立坏入口，必须分别运行，不能用第一个语法错误掩盖第二个运行时错误。

## 预期问题与计数

1. `DR_0003`：`main.py:3` 意外多缩进，直接运行 `python main.py` 应报告 `IndentationError: unexpected indent`。
2. `DR_0005`：`app.py:3` 读取不存在的 `username`，直接运行 `python app.py` 应报告 `NameError: name 'username' is not defined`；第 2 行已有拼写相近的 `user_name`，但是否改名仍需用户确认。

合计 **2 个问题**；预期可自动修复 **0**，预期需人工 **2**。

## 期望 Agent 修复程度

只诊断，不自动修改。对缩进问题应给出出错行和代码块边界，不得统一替换整文件空白；对 NameError 应指出缺失名称、读取行及 `user_name` 这一候选，但不得自行决定改变量定义还是改读取点。两个问题都必须保留在报告中。

## 绝不能改的行

- `main.py:1-7`：本验收中全部保持，尤其不得自动调整第 3 行缩进或删除 `print(message)`。
- `app.py:1-7`：本验收中全部保持，尤其不得把第 2 行改名、删除第 3 行或编造 `username` 初值。
- 不得通过删除入口、吞掉异常或增加 `try/except` 让坏样例假装通过。

## 人工确认命令

```powershell
python 04_golden_tests/case_02_indent_name/main.py
python 04_golden_tests/case_02_indent_name/app.py
```

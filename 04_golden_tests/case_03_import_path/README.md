# case_03_import_path：本地模块导入路径错误

## 预期问题与计数

- 共 **1 个问题**：`DR_0008`，`main.py:1` 从不存在的顶层路径 `utils.text_tools` 导入；实际工具函数位于项目根目录的 `text_tools.py`。
- 直接运行 `python main.py` 应报告 `ModuleNotFoundError: No module named 'utils'`，而不是第三方依赖安装或函数内部错误。
- 预期可自动修复 **0**，预期需人工 **1**。

## 期望 Agent 修复程度

只报告缺失的顶层模块、实际解释器/导入位置，以及项目中存在 `text_tools.py` 这一候选事实。不得执行 pip 安装、创建 `utils` 包、修改 `sys.path`，也不得未经用户确认就把导入改为 `from text_tools import slugify`。导入路径与预期包结构需由用户选择。

## 绝不能改的行

- `text_tools.py:1-2`：函数名、实现和返回文本必须保持，不得移动或复制该文件来绕过错误。
- `main.py:4-9`：`main`、输出文本、入口保护和调用必须保持。
- `main.py:1` 在本轮自动验收中也必须保持；只有用户明确选择扁平模块结构后才允许人工调整。

## 人工确认命令

```powershell
python 04_golden_tests/case_03_import_path/main.py
```

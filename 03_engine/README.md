# 离线项目内核

`core.py` 只使用 Python 标准库，不联网、不调用模型，也不在解释器内直接执行被检查项目。

## 三个函数

将 `03_engine` 加入 `sys.path` 后可导入：

```python
from core import parse_traceback, run_project, scan_project
```

### `scan_project(root)`

递归列出 `root` 下的 `.py` 文件。含模块级 `if __name__ == "__main__"` 的文件，以及名为 `main.py`、`app.py` 的文件会进入 `entries`。返回字典包含：

- `python_files`、`file_count`：相对于 `root` 的排序文件清单及数量。
- `entries`、`entry_count`、`entry_reasons`：入口清单、数量及识别原因。
- `parse_errors`：读取或 AST 解析失败的文件、行号和消息；单个坏文件不会中断扫描。

### `run_project(root, entry, timeout=10.0)`

使用 `sys.executable` 在无 shell 的子进程中运行入口，`cwd` 为 `root`。入口必须位于项目目录内。环境显式使用 UTF-8，并禁止生成 `.pyc`。返回字典包含 `stdout`、`stderr`、`returncode`、`success`、`duration_seconds`、`timed_out` 和结构化 `traceback`；超时时 `returncode` 为 `None`、`timed_out` 为 `True`，已捕获输出仍会保留。

```python
run = run_project("path/to/project", "main.py", timeout=5)
```

### `parse_traceback(stderr)`

解析普通异常或 SyntaxError 文本，返回 `error_type`、`error_message`、`file`、`line`、`function`、`code`、`column`、`caret`、`frames` 和 `has_traceback`。没有可识别 Traceback 时字段为 `None` 或空列表，不抛解析异常。

```python
parsed = parse_traceback(run["stderr"])
print(parsed["error_type"], parsed["file"], parsed["line"])
```

## 命令行

在项目根目录执行：

```powershell
python 03_engine/core.py --help
python 03_engine/core.py scan 04_golden_tests/smoke_01
python 03_engine/core.py run 04_golden_tests/smoke_01 main.py 5
python 04_golden_tests/smoke_01_check.py
```

`run` 命令即使被检查项目抛出 SyntaxError，也会正常输出 JSON 检查结果；项目的真实状态看 JSON 中的 `returncode`、`success` 和 `traceback`。

自检命令：

```powershell
python -m py_compile 03_engine/core.py
python 04_golden_tests/smoke_01_check.py
```

本轮实测：`py_compile` 通过；`smoke_01` 扫描到 2 个文件和 2 个入口，`main.py` 子进程返回码为 1、未超时；结构化 Traceback 为 `SyntaxError: expected ':'`，位置是 `main.py` 第 4 行、第 11 列，源码为 `def main()`。另行验证了正常 stdout 捕获、超时状态与部分输出保留，以及普通 `ValueError` 多栈帧解析。

## 确定性规则匹配

`matcher.py` 提供 `load_rules(path)` 和 `match(rules, traceback_obj, source_map)`。`source_map` 是出错文件路径到源码文本的映射，键可使用 Traceback 中的绝对路径或对应的相对路径。匹配结果包含 `id`、`confidence`、`evidence`、`strength`；每条 evidence 的 `observe` 是规则 observe 原文片段，`matched` 是实际字段或代码证据。结果依次按置信度、证据强度、规则 id 确定性排序。

```python
from matcher import load_rules, match

rules = load_rules("02_assets/diagnostic_rules.jsonl")
results = match(rules, parsed_traceback, {"main.py": source_text})
```

当前对 21 条能由异常字段、消息、正则或 AST 直接支持的规则提供显式匹配；需要环境对比、会话历史或运行时数据才能确认的规则不凭名称猜测。匹配器只读取输入并做语法分析，不执行项目源码、不联网、不调用模型、不实施修复。

自检：

```powershell
python -m pytest 03_engine/test_matcher.py
```

第 9 轮实测为 **11 passed**；`smoke_01` 唯一命中 `DR_0001`，依据是 `SyntaxError`、`expected ':'` 及第 4 行完整 `def main()` 语句头缺冒号且下一行已缩进。

## 保守修复与回滚

`fixer.py` 提供 `backup(file)`、`apply_fix(rule, target)`、`rollback(backup)` 和 `verify_after_fix(project)`。备份命名为 `原文件名.bak_YYYYMMDD_HHMMSS`，按字节独占创建；同名同内容备份只复用，已有备份绝不覆盖或改写。`rollback` 返回 `restored/file/backup/bytes`，只从备份恢复目标文件。

`apply_fix` 当前只实现 `DR_0001` 的单字符定点修复：要求 `confidence=high`、`fix` 未声明“不自动修”，报错行是完整复合语句头、下一行缩进明确，且补冒号后的整个文件可编译。任一 `limit` 条件不明确或存在其他语法错误，都返回 `needs_manual` 和“需要人工决定”；其他规则即使为 high 也不猜测修改。低置信度和明确不自动修的规则返回 `rejected`。成功结果为 `applied`，包含 `patch`、unified `diff` 和本次写入前的 `backup`；函数自身也会先备份，调用方显式创建的相同备份会被安全复用。

```python
from fixer import apply_fix, backup, rollback, verify_after_fix

saved = backup("path/to/main.py")
result = apply_fix(rule, "path/to/main.py")
if result["applied"]:
    verification = verify_after_fix("path/to/project")
    if not verification["passed"]:
        rollback(saved)
```

`verify_after_fix` 通过 core 运行单个 `.py` 文件/测试，或扫描并运行项目全部入口；返回 `passed`、`scan`、`runs` 和 `message`，项目存在解析错误、无入口、超时或非零返回码均不通过。

自检：

```powershell
python -m pytest 03_engine/test_fixer.py
python -m pytest 03_engine/test_matcher.py 03_engine/test_fixer.py
```

第 10 轮实测分别为 **6 passed**、**17 passed**。真实 `smoke_01` 完成备份→第 4 行补冒号→两个入口运行通过→回滚，回滚前后原文件 SHA-256 均为 `8da1e83d9d98865887b7b4cd6f7bdaa723cf32301026e94a4749f9ecd0590660`，备份未改变。

## 一体化命令行（第 11 轮）

`cli.py` 串联扫描、受控运行、Traceback 规则匹配、定点修复、复验和失败回滚。所有报告均为 UTF-8 JSON，统一包含 `issues`、`actions`、`verification`、`verified` 和 `needs_manual`；流程执行完毕即返回 0，被检查项目是否失败看 JSON 字段，参数或路径错误返回 2。完整帮助：

```powershell
python 03_engine/cli.py --help
```

### `check <项目路径>`：只诊断，不修改

```powershell
python 03_engine/cli.py check 04_golden_tests/smoke_01
```

扫描全部 `.py` 文件并运行所有入口，将 Traceback 与项目内源码交给 matcher。每条问题列出规则 id 和名称、文件、行号、置信度、建议、异常消息及证据；未命中规则、超时和无 Traceback 的失败会明确列入 `needs_manual`。该命令不调用修复或备份，也不写源码。

原始 smoke 实测输出摘要：`issue_count=1`，命中 `DR_0001`，位置 `main.py:4`，`confidence=high`，异常为 `SyntaxError: expected ':'`。

### `fix <项目路径> [--dry-run]`：补丁、复验与失败回滚

先预览，不落盘：

```powershell
python 03_engine/cli.py fix 04_golden_tests/smoke_01 --dry-run
```

`--dry-run` 复用 fixer 的候选生成和全部 `limit` 检查，输出 `patch` 与 unified `diff`，但不创建备份、不写文件，`verification.status=skipped`。原始 smoke 实测仅预览第 4 行 `def main()` → `def main():`；执行前后源码 SHA-256 相同，备份数量保持 1。

确认后真实修复：

```powershell
python 03_engine/cli.py fix 04_golden_tests/smoke_01
```

只处理 high 且未声明“不自动修”的规则，实际写入仍受 fixer 当前仅支持 `DR_0001` 的限制。每次写入使用本次新备份；随后运行项目全部入口，成功记为 `applied_verified`，失败立即使用本次备份回滚并记为 `rolled_back`。low/medium、不自动修、无定点修复器和无法确认的条件均进入 `needs_manual`，不会猜测修改。

真实 smoke 实测：生成 `main.py.bak_20261108_180741`，补丁为第 4 行追加一个冒号；`helper.py` 和 `main.py` 复验均返回 0、输出 `smoke_01`，`verified_fix_count=1`、`needs_manual_count=0`，直接运行 `main.py` 也返回 0 并输出 `smoke_01`。

### `rollback <项目路径>`：列出并恢复最近备份

```powershell
python 03_engine/cli.py rollback 04_golden_tests/smoke_01
```

递归列出合法时间戳备份的相对路径、目标文件、时间、字节数和 SHA-256，按时间戳选择唯一最新备份恢复，备份本身不改写；若最新时间戳对应多个文件，则不猜测并进入 `needs_manual`。恢复后也会运行项目验证，验证失败不表示回滚失败，应结合 `restored` 和 `verification.passed` 阅读。

真实 smoke 在修复复验后执行回滚：报告列出 2 个备份，选择 `main.py.bak_20261108_180741`，恢复 `main.py`。恢复后 SHA-256 为 `8da1e83d9d98865887b7b4cd6f7bdaa723cf32301026e94a4749f9ecd0590660`，与修复前逐字节一致；原始 smoke 缺陷因此保留，可重复执行上述验收。

### 第 11 轮自检

```powershell
python -m py_compile 03_engine/cli.py 03_engine/test_cli.py
python -m pytest 02_assets/micro_tests 03_engine/test_matcher.py 03_engine/test_fixer.py 03_engine/test_cli.py --disable-warnings
python 04_golden_tests/smoke_01_check.py
```

新增 `test_cli.py` 覆盖只读诊断、dry-run 零写入、真实修复与最新备份回滚、验证失败自动回滚。首轮串行实测共 **31 passed**，编译及原 `smoke_01_check.py` 通过。


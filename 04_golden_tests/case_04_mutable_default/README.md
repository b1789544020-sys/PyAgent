# case_04_mutable_default：可变 list 默认参数共享状态

## 预期问题与计数

- 共 **2 个关联诊断项**，共享同一个共享默认值根因：
  1. `DR_0010`：`inventory.py:1` 使用 `bucket=[]`，第 2 行又原地 `append`。`main.py` 连续两次省略 `bucket` 调用，第二次会读到第一次留下的元素。
  2. `DR_0024`：直接运行 `python main.py` 应在第二次调用时触发 `AssertionError: mutable default shared across calls`，Traceback 最终指向 `inventory.py:4`。这是运行见证，验收时不得只报告断言而漏掉静态根因。
- 预期可自动修复 **0**，预期需人工 **2**；这里按两个规则诊断项计数，不要求进行两次独立代码修改。

## 为什么本例不能只按 high 规则自动修

`append_item` 位于可导入的 `inventory.py`，属于公共库入口；项目外的外部或动态调用无法全部枚举，也无法确认所有调用都要求独立状态。这命中 `DR_0010.limit`，所以即使本地两个调用点可见，也不能直接推断修复安全。

## 期望 Agent 修复程度

只诊断共享默认对象、两次调用及残留证据，不自动改签名、默认值或调用点。用户明确“每次调用独立”且接受接口策略后，才可人工选择显式传入新 list 等方案；修复验收必须同时覆盖省略参数和显式传参。

## 绝不能改的行

- `inventory.py:1-5`：本验收中全部保持；尤其不得删除第 3-4 行断言来掩盖共享状态。
- `main.py:1`：导入来源必须保持。
- `main.py:4-11`：两次调用、输出及入口必须保持，不得改成显式传参或只调用一次来规避错误。

## 人工确认命令

```powershell
python 04_golden_tests/case_04_mutable_default/main.py
```

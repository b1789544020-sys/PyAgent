# PyAgent 模型配置与安全启动指南

本指南从项目根目录 `<PROJECT_ROOT>` 操作。只需要 Python 和标准库；配置在每次启动时读取，不需要修改源码。

> OpenAI 兼容接口仍需用户提供真实 Key 后验证。本地 `qwen2.5:3b-cpu` 引用问答、`embeddinggemma:300m-qat-q4_0` 全量向量、混合检索、资料外拒答和增量复用已做真实本机验收；自动化测试中的 MockLLM/模拟 HTTP 不冒充这些真实结果。完整统计、证据和未验证边界见[公开验证文档](../docs/VALIDATION.md)。

## 1. 获取并保存真实 Key

以下以 OpenAI 为例；其他 OpenAI 兼容服务请使用该服务控制台生成的 Key、Base URL 和实际可用模型名。

1. 打开 OpenAI 官方 API Keys 页面：<https://platform.openai.com/api-keys>。
2. 登录有 API 使用权限的账号，按页面提示创建一个新的 Secret Key。若账号尚未配置额度或计费，还需在同一官方平台检查 Usage/Billing；创建 Key 不等于一定有调用额度。
3. 立即复制 Key。它通常只在创建时完整显示一次；不要截图公开、发送到聊天、写入测试或提交到 Git。
4. 建议为 PyAgent 单独创建一个可撤销的 Key，并按账号权限设置最小可用范围。若怀疑泄露，立即在控制台撤销并更换。

### 方式 A：写入项目根 `.env`

在 PowerShell 中执行：

```powershell
Set-Location -LiteralPath "<PROJECT_ROOT>"
Copy-Item -LiteralPath ".env.example" -Destination ".env"
notepad .env
```

把 `.env` 内容改为下面的形式，替换两个占位值，不要添加多余空格：

```dotenv
AGENT_LLM=openai
AGENT_API_KEY=粘贴你的真实Key
AGENT_BASE_URL=https://api.openai.com/v1
AGENT_MODEL=填写账号实际可用的模型名
```

- `AGENT_BASE_URL` 已包含 `/v1`，客户端会再请求 `/chat/completions`，不要重复写成 `/v1/v1`。
- 使用兼容服务时，只把 Base URL 换成服务商明确给出的 OpenAI 兼容根地址；是否包含 `/v1` 以该服务文档为准。
- 保存后关闭记事本。`.env` 含真实密钥，不应交付、提交或粘贴到日志。

### 方式 B：只设置当前 PowerShell 进程的环境变量

不想写入 `.env` 时，可在启动 PyAgent 的同一个 PowerShell 窗口执行：

```powershell
$env:AGENT_LLM = "openai"
$env:AGENT_API_KEY = "粘贴你的真实Key"
$env:AGENT_BASE_URL = "https://api.openai.com/v1"
$env:AGENT_MODEL = "填写账号实际可用的模型名"
```

这些变量只影响当前进程及它启动的子进程，关闭窗口后失效。**环境变量优先于 `.env`**；切换配置前先检查旧变量，避免旧值覆盖新文件。

使用完毕可清除当前窗口的密钥：

```powershell
Remove-Item Env:AGENT_API_KEY -ErrorAction SilentlyContinue
```

## 2. 检查配置并启动

### 2.1 当前机器：双击进入浏览器

完成第 6 节的本地 Ollama 配置后，直接双击项目根 **`启动PyAgent.bat`**。它会检查/复用 `127.0.0.1:11434` 的 CPU 服务，再自动打开本地 HTML 工作台。启动后的黑色命令窗口需要保持开启；关闭它只会停止网页服务，不会删除 `<PRIVATE_DATA_DIR>` 中的索引。

如果浏览器没有自动打开，访问 `http://127.0.0.1:8765/`。页面无外部资源依赖，但不是一个能脱离 Python 服务直接读取 SQLite 的静态 `file://` 演示页。网页只提供资料问答与检索；诊断修复继续使用命令行及原有安全确认。

### 2.2 检查掩码配置与原命令行入口

下面的检查只显示掩码，不打印完整 Key：

```powershell
python -c "import sys; sys.path.insert(0, '03_agent'); import config; print(config.get_config())"
```

应看到 `llm` 为 `openai`、`has_model` 为 `True`，`api_key` 只显示前几位和 `***`（短 Key 完全掩码）。随后启动 REPL：

```powershell
python 03_agent/main.py
```

模式行应类似：

```text
当前模式：在线（模型 你配置的模型名）
```

先从一个明确路径的诊断任务开始，不要第一次就使用 `--yes`：

```text
PyAgent> 帮我看下 04_golden_tests/smoke_01 哪里坏了
```

真实模型必须按系统协议返回工具调用 JSON；看到工具观察后再给结论。模型名称、账号额度、网络和服务端兼容性均需要真实环境验证。

## 3. 切换到本地 Ollama

### 3.1 安装并准备模型

1. 从 <https://ollama.com/download> 下载并安装 Windows 版 Ollama。
2. 启动 Ollama。若安装后服务没有自动运行，在单独终端执行 `ollama serve` 并保持窗口开启。
3. 在新的 PowerShell 窗口下载一个模型，例如：

   ```powershell
   ollama pull qwen2.5-coder:7b
   ollama list
   ```

4. 确认 `ollama list` 中的完整名称；下面的 `AGENT_MODEL` 必须与之一致。模型也可替换，但必须能遵守文本 JSON 工具调用协议。

### 3.2 修改配置

把项目根 `.env` 改为：

```dotenv
AGENT_LLM=ollama
AGENT_API_KEY=
AGENT_MODEL=qwen2.5-coder:7b
AGENT_BASE_URL=
```

如果当前 PowerShell 以前设置过 OpenAI 环境变量，先清除它们，防止覆盖 `.env`：

```powershell
Remove-Item Env:AGENT_API_KEY -ErrorAction SilentlyContinue
Remove-Item Env:AGENT_BASE_URL -ErrorAction SilentlyContinue
Remove-Item Env:AGENT_MODEL -ErrorAction SilentlyContinue
Remove-Item Env:AGENT_LLM -ErrorAction SilentlyContinue
```

Ollama 后端请求 `AGENT_OLLAMA_URL` 指定的本机服务根地址（应用默认 `http://localhost:11434`），聊天路径为 `/api/chat`，`stream=false`，不需要 API Key，也不使用 `AGENT_BASE_URL`。本项目的纯 CPU 服务和文档向量配置见第 6 节。重新检查掩码配置并启动：

```powershell
python -c "import sys; sys.path.insert(0, '03_agent'); import config; print(config.get_config())"
python 03_agent/main.py
```

模式行应显示在线且后端配置为 Ollama。切回 OpenAI 时，再填写 `AGENT_LLM=openai`、真实 Key、Base URL 和对应模型名。

## 4. 高危操作与 `--yes`

以下操作会先打印动作和影响范围，再等待确认：

- `write_file`：创建或覆盖文件；
- `fix_project` 且 `dry_run=false`：备份、写盘并验证；
- `rollback_project`：用备份覆盖源码；
- `run_project`：执行项目入口，项目代码可能有副作用。

只有输入 `y` 或 `yes`（不区分大小写）才执行；空输入、`n`、EOF、Ctrl+C 或其他文本都会取消。非交互环境没有确认通道时返回 `needs_confirmation`，不会执行。dry-run 修复和只读检查不需要这项确认。

只有充分信任**本次启动期间模型可能提出的全部高危操作**时，才可显式使用：

```powershell
python 03_agent/main.py --yes
python 03_agent/main.py --yes ask "修复 04_golden_tests/smoke_01"
```

`--yes` 默认关闭，不是推荐默认值，也不会保存在会话中。即使使用 `--yes`，启动后仍会打印每个高危动作及范围。演示、测试和自动化不得绕过这个确认层；测试通过注入明确的 `y/yes` 回答或测试替身验证行为。

## 5. 无 Key 的协议测试与排错

不联网测试可临时使用：

```powershell
$env:AGENT_LLM = "mock"
$env:AGENT_MODEL = "mock-local"
Remove-Item Env:AGENT_API_KEY -ErrorAction SilentlyContinue
python 03_agent/main.py
```

默认 MockLLM 只返回确定文本；完整工具闭环由 `03_agent/test_safety.py` 等测试注入确定响应，不代表真实模型能力。

常见问题：

- **启动仍显示离线**：检查 `.env` 是否位于项目根、变量名是否拼写正确，以及当前进程是否真的没有模型配置。
- **HTTP 401/403**：检查 Key 是否有效、权限和账号状态；不要把请求头或完整 Key 贴入日志。
- **无法连接或超时**：OpenAI 检查网络和 Base URL；Ollama 检查服务是否启动，以及 `AGENT_OLLAMA_URL` 的实际端口（应用默认 11434，本项目 CPU 配置为 11435）。
- **Ollama 找不到模型**：用 `ollama list` 核对完整模型名。
- **模型回答但不调用工具**：该模型可能没有遵守本项目的文本 `tool_calls` JSON 协议；这是模型兼容性问题，系统不会伪造工具成功证据。
- **高危操作未执行**：查看是否返回 `needs_confirmation` 或 `blocked`，在交互终端明确输入 `y/yes`，不要盲目改用 `--yes`。


## 6. 纯 CPU Ollama 与本地知识库

以下路径是当前机器的实际配置；其他机器用启动脚本参数和 `.env` 改成自己的路径。运行时不需要 API Key。只有首次拉取模型需要网络，已有模型可跳过下载。

### 6.1 启动服务与准备模型

```powershell
Set-Location -LiteralPath "<PROJECT_ROOT>"
$env:OLLAMA_HOST = "127.0.0.1:11434"
$env:OLLAMA_MODELS = "<OLLAMA_MODELS_DIR>"
powershell -ExecutionPolicy Bypass -File .\start_ollama_cpu.ps1
ollama list
```

脚本默认使用 `<OLLAMA_EXE>`，强制 CPU 库、禁用云模型、仅监听回环地址、只加载一个模型，并把日志写入 `<PRIVATE_DATA_DIR>\logs`。若安装位置不同：

```powershell
powershell -ExecutionPolicy Bypass -File .\start_ollama_cpu.ps1 `
  -OllamaExe "你的ollama.exe" -ModelsDir "你的模型目录" -DataDir "你的数据目录"
```

如果 11435 已有服务，脚本会复用并明确返回 `already_running`，不会假装重新设置了它的启动参数。若 `ollama list` 缺少基础模型，首次执行：

```powershell
ollama pull qwen2.5:3b
ollama pull embeddinggemma:300m-qat-q4_0
```

当前 `.env` 的生成模型使用带 `num_gpu 0` 的本地别名；向量模型直接使用官方量化标签 `embeddinggemma:300m-qat-q4_0`（300M、239MB、100+ 语言训练、2K 上下文），无需另行创建向量别名。生成别名可复用已经下载的权重：

```powershell
New-Item -ItemType Directory -Path "<PRIVATE_DATA_DIR>" -Force | Out-Null
"FROM qwen2.5:3b`nPARAMETER num_gpu 0" | Set-Content -LiteralPath "<PRIVATE_DATA_DIR>\qwen-cpu.Modelfile" -Encoding Ascii
ollama create qwen2.5:3b-cpu -f "<PRIVATE_DATA_DIR>\qwen-cpu.Modelfile"
```

EmbeddingGemma 的查询和文档分别由程序添加官方检索提示前缀；不能只替换模型名而混用旧向量。程序会识别模型不一致、拒绝混合检索并提示重建。

### 6.2 建索引、检索与问答

```powershell
python 03_agent/main.py ask docs_index
python 03_agent/main.py ask "docs_search Python 中变量赋值如何绑定名称 --top-k 4"
python 03_agent/main.py ask "docs_ask Python 中变量赋值如何绑定名称 --top-k 4"
```

首次向量化的计算量较大；可以先用 `docs_index --lexical-only` 建立关键词索引，稍后再运行普通 `docs_index` 续建向量。重复命令按 SHA-256 跳过未变文件；向量已完整时不重复调用向量模型。`docs_index --full` 用于明确的全量重建，不是日常命令。

`docs_search` 不调用生成模型。`docs_ask` 只有在证据达到阈值后才调用本地 Qwen；引用不合格会重试一次，仍不合格则不采用模型文本。资料外问题返回 `refusal/not_run`；模型或向量不可用会明确降级并保留关键词原文结果。

### 6.3 故障定位

- **11434 的 CUDA/HTTP 500**：本方案没有修复旧驱动或原服务，而是用 11435 的独立纯 CPU 服务绕开；不要声称 CUDA 问题已解决。
- **向量失败但关键词可用**：索引按批提交，查看 `embedding_pending` 和 `warning`；修复模型服务后重跑 `docs_index` 即可续建。
- **模型找不到**：在设置了同一 `OLLAMA_HOST`、`OLLAMA_MODELS` 的终端运行 `ollama list`，模型名必须与 `.env` 完全一致。
- **看实时进度**：`docs_index` 的 stderr 每批报告 `已完成/本次待处理`；中断后已提交批次不会丢失。
- **尚未证明的事项**：localhost 与 `OLLAMA_NO_CLOUD=1` 不等于操作系统级断网测试；真实全量统计和是否做过断网验收见[公开验证文档](../docs/VALIDATION.md)。

### 6.4 当前机器的最终验收基线（2026-11-09）

- 全量 `docs_index`：391 个来源、4,628 个块、4,628 条 768 维向量、0 待嵌入，耗时 1,473.564 秒；SQLite 完整性、向量有限值和来源文件存在性均通过检查。
- 变量绑定问题实测为 `hybrid`，Top 4 均有词法和向量分数；真实 Qwen 一次生成有效 `[S1]`、`[S4]` 并附来源。自然语言量子引力问题实测为 `refusal / not_run / sources:[]`，候选仅出现在 `candidates`。
- 未变文件重索引实测 `updated_files=0`、`embedded_now=0`，向量 API 未调用且向量哈希不变；真实 CLI 再索引耗时 0.393 秒。
- 无生成模型、向量端口不可用和模型元数据不匹配均已验证为明确的原文/关键词降级，不伪造生成或向量分数。
- 最终完整自动化回归为 **125 passed in 9.07s**。机器可读的检索、问答、拒答和增量证据分别保存在 `<PRIVATE_DATA_DIR>\real_knowledge_validation.json` 与 `<PRIVATE_DATA_DIR>\real_incremental_validation.json`。

这些结果证明当前机器的本机功能链路，不证明任意问题回答正确，也不代替操作系统级断网测试。原 11434 CUDA/HTTP 500 仍未修复。

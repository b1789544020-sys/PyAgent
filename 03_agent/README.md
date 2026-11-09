# PyAgent 对话入口

`03_agent/main.py` 是统一对话入口。无参数时进入 REPL，`ask` 执行一轮后退出；两者都调用现有 `dispatcher.py`。显式 `docs_*` 命令和本地模型资料问题进入知识问答链，项目检查、修复、运行仍走原有工具边界与 ReAct 流程。

所有示例默认在 PyAgent 项目根目录执行。只依赖 Python 标准库，路径含空格时用双引号包裹。

## 1. 双击打开浏览器界面（推荐）

直接双击项目根目录的 **`启动PyAgent.bat`**。启动器会复用或启动现有纯 CPU Ollama，然后启动只监听 `127.0.0.1` 的本地网页服务，并自动在默认浏览器打开 `http://127.0.0.1:8765/`；端口被其他程序占用时会尝试后续端口，重复双击会复用已运行的 PyAgent 页面服务。

页面支持：

- **智能回答**：真实混合检索 + Ollama 整理，回答中的 `[S#]` 可点击定位来源；
- **原文检索**：不调用生成模型，展示真实文件、章节定位、摘录与相关度；
- **全文阅读与段落定位**：点击右侧章节标题或“阅读全文”，打开只读阅读器，自动滚动到引用段落并高亮；可查看行号、回到引用、复制路径，按 `Esc` 关闭。Markdown/文本按索引块定位，JSONL 按原始记录行定位，无需重建索引；
- **资料管理**：从侧栏进入后可拖入或选择多份 TXT、Markdown、DOCX，自动保存并执行增量索引；支持上传明细、进度、资料搜索/筛选/分页、全文阅读，以及仅针对导入资料的备份式移除；
- 证据数量、仅关键词检索、回答/路径复制、摘录展开、新对话和深浅色主题；
- 桌面与手机布局；主题和最近 24 条页面消息只保存在当前浏览器的 `localStorage`，点击“新建对话”可清空。每轮提问独立检索，不把页面历史伪装成模型长期记忆。

**保持启动后的命令窗口开启。**关闭该窗口或按 `Ctrl+C` 只停止网页服务，不删除索引，也不停止独立 Ollama。问答、检索和全文阅读保持只读；资料管理的写操作严格限定于 `01_corpus/imports/` 和数据目录下的移除备份，不提供 `fix`、任意路径写文件、回滚或运行项目的接口，原命令行动作与安全确认不变。全文接口只接受已索引的 `chunk_id`，并校验本地请求标识、同源 Origin、项目白名单目录及 2 MiB 文件上限，不能用它读取任意磁盘文件。文件在索引后变化时会明确提醒；程序更新后需重启网页服务并刷新页面。

不需要 Node.js、npm、前端构建或 CDN。HTML 必须通过上述本机服务运行，不能直接以 `file://` 双击 HTML 后绕过 Python 检索 SQLite/Ollama。手动启动或不自动打开浏览器时使用：

```powershell
Set-Location -LiteralPath "<PROJECT_ROOT>"
powershell -ExecutionPolicy Bypass -File .\start_ollama_cpu.ps1
python 03_agent/web_ui.py
# 只启动服务：python 03_agent/web_ui.py --no-browser
```

### 资料管理与自动增量导入

1. 点击侧栏 **“资料管理”**，把文件拖入虚线区域，或点击“选择文件”。支持 `.txt`、`.md`、`.markdown`、`.docx`，可一次选择多份；单个上传文件不超过 **25 MiB**，转换后的 UTF-8 文字不超过 **2 MiB**。
2. TXT/Markdown 会规范为 UTF-8，兼容 UTF-8/UTF-8 BOM、UTF-16 和 GB18030 中文文本。DOCX 通过 Python 标准库读取 OOXML，提取标题、正文、列表和表格并保存为 Markdown；不执行宏、不读取外部关系。图片、复杂排版、加密 DOCX、扫描件 OCR 不在转换范围内。
3. 文件只写入受管理的 **`01_corpus/imports/`**。同名同内容会复用，同名不同内容自动添加 `-2`、`-3` 等序号，**不会覆盖已有文件**。有效文件全部保存后，页面只启动一次后台增量索引，复用 `KnowledgeIndex.build()` 的 SHA-256 增量逻辑；未变化文件和向量不会重算。索引与问答共用模型锁，状态查询不会被长任务阻塞。
4. 资料列表来自真实 SQLite，可按标题/路径搜索，筛选“页面导入/原有资料”，分页并打开全文。只有导入目录中的文件显示“移除”按钮；移除会先把文件移到 **`AGENT_DATA_DIR/removed_documents/时间戳/`**，再自动更新索引，原有章节、知识卡片和其他任意路径均不允许通过网页移除。
5. 索引进度保存在当前网页服务内存中。若服务在任务中途重启，已保存文件仍在导入目录，重新进入资料管理并点击 **“更新索引”** 即可；页面不会伪报任务已恢复。手动添加、修改或删除语料后，也仍可运行 `python 03_agent/main.py ask docs_index`，不需要 `--full`。

不支持 PDF、旧版 `.doc`、图片 OCR；请先把这些内容转换为受支持的文本或 DOCX。localhost 与无外部前端资源不代表已经完成操作系统级断网验收。

## 2. 命令行：REPL 与单次 ask

### 交互式 REPL

```powershell
python 03_agent/main.py
```

启动后会显示当前模式，并反复显示 `PyAgent>`：

```text
PyAgent> check 04_golden_tests/smoke_01
PyAgent> run_project 04_golden_tests/smoke_01 main.py
PyAgent> fix 04_golden_tests/smoke_01 --dry-run
PyAgent> exit
```

输入 `exit` 或 `quit`（不区分大小写）退出。空行会被忽略。单条命令出错、参数无效、模型或工具抛出异常时，入口打印中文提示并继续读下一条输入，不会因一轮失败退出整个 REPL。`Ctrl+C` 只取消当前输入；输入流正常结束（EOF）时也会安全退出。

### 单次 ask

在线模式使用自然语言：

```powershell
python 03_agent/main.py ask "帮我看下 04_golden_tests/smoke_01 哪里坏了"
python 03_agent/main.py ask "运行 04_golden_tests/smoke_01 的 main.py"
```

离线模式仍使用确定性命令，把完整命令放在 `ask` 后：

```powershell
python 03_agent/main.py ask "check 04_golden_tests/smoke_01"
python 03_agent/main.py ask "fix 04_golden_tests/smoke_01 --dry-run"
python 03_agent/main.py ask "run_project 04_golden_tests/smoke_01 main.py"
```

离线 REPL 支持同一组固定命令：`check <路径>`、`fix <路径> [--dry-run]`、`rollback <路径>`、`run_project <路径> <入口.py>`。没有生成模型时，普通问题与陈述仍可进入本地资料检索并返回原文；项目动作继续使用确定性命令，无法识别的输入会得到格式提示，不会伪造智能回答。

## 3. 配置方法

配置只在运行时读取。项目根目录的 `.env` 与进程环境变量均可使用，**环境变量优先**。可复制 `.env.example` 为 `.env` 后填写；不要把真实 `.env` 或 API Key 写入日志、测试和交付文件。获取真实 Key、逐项填写、环境变量切换及 Ollama 安装的完整步骤见 [SETUP.md](SETUP.md)。OpenAI 兼容接口仍需用户提供 Key 后验证；本地 Ollama 的已验收范围与限制见项目根 `STATE.md`。

PowerShell 也可以临时设置环境变量，例如：

```powershell
$env:AGENT_LLM = "openai"
$env:AGENT_API_KEY = "你的 Key"
$env:AGENT_MODEL = "你的模型名"
```

### OpenAI 兼容接口

`.env` 示例：

```dotenv
AGENT_LLM=openai
AGENT_API_KEY=粘贴你的真实Key
AGENT_BASE_URL=https://api.openai.com/v1
AGENT_MODEL=替换为实际模型名
```

`AGENT_API_KEY` 和 `AGENT_MODEL` 都应填写。`AGENT_BASE_URL` 默认是 `https://api.openai.com/v1`，客户端会请求其 `/chat/completions`；使用兼容服务时把 Base URL 改成该服务的地址。公开模式行只显示模型名，配置对象的 Key 始终掩码。

### Ollama

先在本机安装并启动 Ollama，再准备一个已下载的模型。例如（模型名可替换）：

```powershell
ollama pull qwen2.5-coder:7b
```

`.env` 配置：

```dotenv
AGENT_LLM=ollama
AGENT_API_KEY=
AGENT_MODEL=qwen2.5-coder:7b
AGENT_BASE_URL=
```

Ollama 不需要 API Key，也不使用 OpenAI Base URL。服务根地址由 `AGENT_OLLAMA_URL` 配置（应用默认 `http://localhost:11434`），客户端分别请求 `/api/chat` 和 `/api/embed`。模型应能遵循系统提示：项目工具任务返回规定的 JSON `tool_calls`；资料问答则由确定性检索链提供证据，再要求模型给出 `[S#]` 引用。

### MockLLM（不联网的在线流程测试）

```dotenv
AGENT_LLM=mock
AGENT_API_KEY=
AGENT_MODEL=mock-local
```

`mock` 会进入在线 dispatcher 和 ReAct 代码路径，但不联网。默认 `MockLLM` 只返回固定的未匹配文本；需要真实工具闭环时，测试可注入带关键词响应的 `MockLLM`，例如 `03_agent/test_main.py` 中的两轮 `check_project` 自检。

## 4. 离线与在线的区别

| 项目 | 离线模式 | 在线模式 |
| --- | --- | --- |
| 进入条件 | 无可用 API Key，且后端不是 Ollama/Mock | 有 API Key，或 `AGENT_LLM=ollama/mock` |
| 输入 | 固定命令和参数 | 自然语言任务 |
| 决策 | 固定命令到共享工具的映射 | 模型驱动 ReAct 循环 |
| 联网 | 不联网、不调用模型 | OpenAI/Ollama 按配置连接；Mock 不联网 |
| 回答 | 确定性中文模板 | 模型根据工具观察生成最终回答 |
| 信息不足 | 报命令/参数错误 | 缺路径、意图或运行入口时先追问，不猜路径 |
| 上下文 | 每轮独立命令 | 文件会话保存当前项目、最近意图和待补字段 |

两种模式都经过 `tools.execute_tool()`，使用同一个诊断、运行、修复和回滚引擎。在线任务理解优先使用确定性规则；会话默认写入 `03_agent/.session.json`，不保存 API Key 或完整对话。

在线循环默认最多 8 步、单步等待 30 秒、总等待 120 秒。代码任务需要真实工具观察；修复完成必须有真实验证证据，口头声明不会被标为已验证完成。**离线 `fix` 不带 `--dry-run` 会真实写盘；预览时必须显式加 `--dry-run`。**

## 5. 安全确认与幻觉防护

`write_file`、非 dry-run 的 `fix_project`、`rollback_project` 和 `run_project` 在执行前都会打印动作及影响范围。REPL 或可交互终端中只有输入 `y` / `yes` 才继续；其他输入返回 `blocked`。无交互确认通道且未显式信任时返回 `needs_confirmation`，不会启动工具。拒绝或等待确认不会计入成功工具证据，REPL 在拦截后仍可继续输入。

启动时显式添加 `--yes` 可以信任本次启动的全部高危操作，例如 `python 03_agent/main.py --yes`。它默认关闭，不能通过 REPL 中单条命令开启；即使开启也仍打印动作与范围。不要把 `--yes` 用于不可信模型、未知项目或演示自动化。

模型若声称“已修复/已完成”却没有相应成功工具证据，循环会追加一条“只能基于工具结果作答”的系统纠正提示，把原话记录到 `unverified_claims`，状态标为“待验证”，然后要求模型改答。修复声明仍需 `verification.passed=true` 或 `applied_verified`；检查、待确认和被拒绝操作都不能冒充修复验证。

## 6. 输出与退出码

每轮先打印自然语言摘要，再打印 `关键结构化信息` JSON，包括：

- `status`、命令或意图、项目路径；
- `issues` / `issue_count`（如有）；
- `changed_files`：实际保留修改或恢复的文件，dry-run 不算修改；
- `verification.status` 与 `verification.passed`；
- `unverified_claims`：被标记“待验证”的模型原话及原因；
- `safety`：待确认或被拦截的工具、动作、范围与状态（如有）；
- 在线步数、运行退出码或结构化错误（按任务类型出现）。

离线 `check` 的摘要示例：

```text
检查完成，发现 1 个问题。 DR_0001（main.py:4，SyntaxError：expected ':'）。
```

单次 `ask` 中，正常完成或需要澄清返回 `0`，工具错误、ReAct 未完成、安全拦截（`needs_confirmation` / `blocked`）或本轮异常返回 `1`，缺少任务或命令参数无效返回 `2`。REPL 中这些单轮错误只影响当前轮，进程会继续等待输入。


## 7. 本地文档知识问答

知识来源只包括 `01_corpus/` 内的 Markdown/文本，以及 `02_assets/` 的 `knowledge_cards.jsonl`、`code_examples.jsonl`、`diagnostic_rules.jsonl`。SQLite、原文块、词片和向量都保存在 `AGENT_DATA_DIR`，默认示例为 `<PRIVATE_DATA_DIR>\knowledge.sqlite3`。资料管理可将 DOCX 文字转换为 Markdown 后纳入索引；PDF、旧版 DOC、图片 OCR 仍不直接支持。

### 启动与三条命令

在项目根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\start_ollama_cpu.ps1
python 03_agent/main.py ask docs_index
python 03_agent/main.py ask "docs_search Python 中变量赋值如何绑定名称 --top-k 4"
python 03_agent/main.py ask "docs_ask Python 中变量赋值如何绑定名称 --top-k 4"
```

也可以启动 REPL 后原样输入 `docs_index`、`docs_search ...` 或 `docs_ask ...`。`docs_search` 即使配置了生成模型也不会获取或调用它；没有生成模型时，三个显式命令仍可使用，但 `docs_ask` 会诚实降级为原文检索。

首次全量 CPU 向量化可能较慢，stderr 会显示已完成批次。只建立可检索的关键词索引可运行：

```powershell
python 03_agent/main.py ask "docs_index --lexical-only"
python 03_agent/main.py ask "docs_search 变量绑定 --lexical-only"
```

之后再次运行 `docs_index` 会从空向量批次续建。文件按 SHA-256 增量更新，未变文件不重新切块，未变且有当前模型向量的块不重复计算；删除的来源会从库中清除。`docs_index --full` 会清空文档块和向量后完整重建。切换向量模型会使旧向量失效，不能混用不同模型的向量。

### 回答与降级语义

- `mode:"rag"`：检索有足够证据，生成回答含有效 `[S#]`，程序附上真实文件、标题、定位与原文摘录。
- `mode:"refusal"`：证据不足，`generation:"not_run"`，不会为了拒答而调用生成模型；低相关候选在 `candidates`，不冒充正式 `sources`。
- `mode:"search"`：没有生成模型、模型调用失败，或两次生成都没有合法引用时，返回未经改写的检索原文，不采用无引用生成文本。
- 向量服务不可用：保留 BM25/中文二元词片检索，并在 `warning` 中说明降级，不伪造向量分数。

自然语言问题在 Ollama 模式下默认进入本地资料问答；`check`、`fix`、`rollback`、`run_project` 及对应自然语言项目动作仍保留原有安全确认和验证流程。知识问答本身不执行项目写盘操作。

### 配置

```dotenv
AGENT_LLM=ollama
AGENT_MODEL=qwen2.5:3b-cpu
AGENT_OLLAMA_URL=http://127.0.0.1:11434
AGENT_EMBED_MODEL=embeddinggemma:300m-qat-q4_0
AGENT_DATA_DIR=<PRIVATE_DATA_DIR>
AGENT_TOP_K=4
AGENT_MODEL_TIMEOUT=180
AGENT_API_KEY=
```

`AGENT_TOP_K` 范围为 1–10。当前向量模型是官方 239MB 的量化感知训练模型，训练数据覆盖 100+ 语言；程序自动加入它要求的检索查询/文档提示前缀。根目录 `start_ollama_cpu.ps1` 固定使用本机回环地址、CPU 库、模型目录 `<OLLAMA_MODELS_DIR>`，禁用云模型并限制只加载一个模型；可用 `-OllamaExe`、`-ModelsDir`、`-DataDir`、`-Listen` 覆盖路径，`-NumParallel` 默认为 1。脚本不会下载模型，也不会修改系统级环境变量。模型首次准备方法见 `SETUP.md` 第 6 节。

代码只连接配置的本机 HTTP 服务，不接入云端 API。localhost 配置本身不等于操作系统级断网验收；是否实际阻断外网及真实语料统计以项目根 `STATE.md` 为准。

### 本机验收基线（2026-11-09）

当前机器已完成 391 个来源、4,628 个块和 4,628 条 768 维向量的全量构建，0 待嵌入。变量绑定问题实测为 `hybrid`，Top 4 均有词法/向量分数；真实 Qwen 一次生成有效引用并附来源，量子引力自然语言问题实测为 `refusal / not_run / sources:[]`。

未变文件重索引实测 `updated_files=0`、`embedded_now=0`，向量 API 未调用且向量哈希不变；真实 CLI 再索引耗时 0.393 秒。无生成模型、向量服务不可用和模型不匹配均有明确原文/关键词降级。最终完整回归为 **144 passed in 13.31s**，其中网页服务 14 项、资料导入 5 项测试。真实 Edge 已验证 RAG 回答、引用、全文精确高亮，以及资料管理的多文件上传、DOCX 转换、无效格式隔离、自动索引、检索、全文阅读、拖放、重复复用、备份式移除、深浅色与手机布局，JavaScript 异常为 0；4,628 个真实索引块也均已逐一回查定位。网页验收证据为 `<PRIVATE_DATA_DIR>\web_ui_validation.json`、`browser_ui_validation.json`、`browser_reader_validation.json` 和 `browser_library_validation.json`。详细统计、截图路径及未验证边界见项目根 `STATE.md`；操作系统断网和原 11434 故障修复不在已完成声明内。

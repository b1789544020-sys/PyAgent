# PyAgent（私有完整项目源码快照）

本仓库用于保存完整应用的程序与测试，不是公开展示仓库。快照日期：2026-11-09。

## 数据边界

私有书籍语料、知识卡片 JSONL、SQLite 索引、模型文件、真实 `.env`、日志、浏览器会话、移除备份、内部 PROJECT/STATE 文档和旧打包产物均未复制。需要在本机另行恢复这些数据后才能重建原有私人知识库；仓库内的 `02_assets/micro_tests/` 只用于自动化测试。

公开截图、视频和展示说明位于单独的 PyAgent-Showcase 项目；本私有仓库不得直接转为公开。

## 主要入口

- `python 03_agent/web_ui.py`：只监听本机的浏览器工作台。
- `python 03_agent/main.py`：命令行 REPL。
- `03_agent/README.md` 与 `03_agent/SETUP.md`：详细功能和模型配置。
- `启动PyAgent.bat`：Windows 启动器。

复制 `.env.example` 为 `.env`，按本机 Ollama 和模型位置调整配置。`start_ollama_cpu.ps1` 的默认路径已经改为通用占位值，可用参数指定实际可执行文件、模型目录和数据目录。脚本不会下载模型。

## 验证

```powershell
python -m pytest 03_agent 03_engine 02_assets/micro_tests --capture=sys -q
```

测试不依赖真实 `.env` 或私人语料。正式私有语料的规模、索引统计和验收证据不从此源码快照推断。

"""PyAgent 本地浏览器界面：只监听回环地址，复用真实知识检索与问答链。"""

import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import sqlite3
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser


AGENT_DIR = Path(__file__).resolve().parent
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

import config  # noqa: E402
import document_manager  # noqa: E402
import knowledge  # noqa: E402
import knowledge_qa  # noqa: E402


APP_ID = "pyagent-local"
WEB_ROOT = AGENT_DIR / "web"
MAX_BODY_BYTES = 32 * 1024
MAX_QUESTION_CHARS = 4000
MAX_SOURCE_BYTES = 2 * 1024 * 1024
INDEXED_ASSETS = {
    "02_assets/knowledge_cards.jsonl",
    "02_assets/code_examples.jsonl",
    "02_assets/diagnostic_rules.jsonl",
}
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/library.js": ("library.js", "text/javascript; charset=utf-8"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
}
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; connect-src 'self'; font-src 'self'; "
    "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
)


class WebRequestError(ValueError):
    """可安全返回给本地页面的请求错误。"""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def validate_query(payload):
    """限制网页请求的类型和大小，避免把任意参数传入知识链。"""
    if not isinstance(payload, dict):
        raise WebRequestError("请求必须是 JSON 对象")
    question = payload.get("question")
    if not isinstance(question, str) or not question.strip():
        raise WebRequestError("请输入一个问题或检索关键词")
    question = question.strip()
    if len(question) > MAX_QUESTION_CHARS:
        raise WebRequestError(f"问题不能超过 {MAX_QUESTION_CHARS} 个字符")
    mode = payload.get("mode", "ask")
    if mode not in {"ask", "search"}:
        raise WebRequestError("mode 只能是 ask 或 search")
    top_k = payload.get("top_k", 4)
    if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 10:
        raise WebRequestError("top_k 必须是 1 到 10 的整数")
    lexical_only = payload.get("lexical_only", False)
    if not isinstance(lexical_only, bool):
        raise WebRequestError("lexical_only 必须是布尔值")
    return {
        "question": question,
        "mode": mode,
        "top_k": top_k,
        "lexical_only": lexical_only,
    }


def _display_answer(result):
    """来源由页面单独展示，去掉 CLI 结果末尾重复拼接的来源清单。"""
    answer = str(result.get("answer") or "").strip()
    if result.get("command") == "docs_search" and answer.startswith("找到以下相关原文"):
        return "找到以下相关原文（未调用生成模型）。"
    for heading in (
        "\n\n来源（可在项目文件中核查）：",
        "\n\n检索原文（未经生成模型改写）：",
    ):
        answer = answer.split(heading, 1)[0].rstrip()
    return answer


def execute_query(payload, settings=None, index=None, embedder=None, llm_client=None):
    """执行一次独立查询；不经过 dispatcher，也不开放任何项目写盘工具。"""
    request = validate_query(payload)
    settings = settings or config.get_runtime_config()
    started = time.monotonic()
    common = {
        "question": request["question"],
        "top_k": request["top_k"],
        "lexical_only": request["lexical_only"],
        "settings": settings,
        "index": index,
        "embedder": embedder,
    }
    if request["mode"] == "search":
        result = knowledge_qa.search_documents(**common)
    else:
        result = knowledge_qa.answer_question(**common, llm_client=llm_client)
    response = {key: value for key, value in result.items() if key != "answer"}
    response["answer"] = _display_answer(result)
    response["elapsed_seconds"] = round(time.monotonic() - started, 3)
    response["request_mode"] = request["mode"]
    return response


def _target_offsets(text, start, end, exact, match_type):
    """把字符范围转换为阅读器可滚动、可高亮的行号。"""
    line_start = text.count("\n", 0, start) + 1
    return {
        "start": start,
        "end": end,
        "line_start": line_start,
        "line_end": line_start + text[start:end].count("\n"),
        "exact": exact,
        "match_type": match_type,
    }


def _source_headings(text, locator):
    heading = str(locator or "").rsplit(" > ", 1)[-1].strip()
    if not heading:
        return []
    pattern = re.compile(
        r"^#{1,6}[ \t]+" + re.escape(heading) + r"[ \t]*$", re.MULTILINE
    )
    return list(pattern.finditer(text))


def _flexible_content_match(text, content):
    """兼容索引阶段合并空行的旧文档，同时保持每行文字完全相等。"""
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if not lines:
        return None
    separator = r"[ \t]*\n(?:[ \t]*\n)*[ \t]*"
    pattern = separator.join(re.escape(line) for line in lines)
    return re.search(pattern, text)


def _locate_source_target(text, row):
    """优先精确匹配索引块；JSONL 回到记录行，失败时至少定位章节标题。"""
    if str(row["source_path"]).lower().endswith(".jsonl"):
        match = re.match(r"第\s*(\d+)\s*行", str(row["locator"] or ""))
        if match:
            line_number = int(match.group(1))
            lines = text.splitlines(keepends=True)
            if 1 <= line_number <= len(lines):
                start = sum(len(line) for line in lines[: line_number - 1])
                end = start + len(lines[line_number - 1].rstrip("\n"))
                return _target_offsets(text, start, end, True, "jsonl_line")
        return None

    content = str(row["content"] or "").strip()
    prefix = (
        row["title"]
        if row["locator"] == row["title"]
        else f"{row['title']}\n{row['locator']}"
    )
    if content.startswith(prefix + "\n\n"):
        content = content[len(prefix) + 2 :]
    headings = _source_headings(text, row["locator"])
    if content:
        positions = []
        cursor = 0
        while True:
            position = text.find(content, cursor)
            if position < 0:
                break
            positions.append(position)
            cursor = position + 1
        if positions:
            if len(positions) > 1 and headings:
                heading_starts = [heading.start() for heading in headings]

                def distance(position):
                    previous = [value for value in heading_starts if value <= position]
                    return position - max(previous) if previous else len(text) + position

                start = min(positions, key=distance)
            else:
                start = positions[0]
            return _target_offsets(
                text, start, start + len(content), True, "indexed_chunk"
            )
        flexible = _flexible_content_match(text, content)
        if flexible:
            return _target_offsets(
                text, flexible.start(), flexible.end(), True, "normalized_chunk"
            )
    if headings:
        heading = headings[0]
        return _target_offsets(text, heading.start(), heading.end(), False, "section")
    return None


def _resolve_indexed_source(settings, source_path):
    """只允许读取索引白名单目录内的文档，不接受浏览器传入的任意路径。"""
    if not isinstance(source_path, str) or not source_path or "\x00" in source_path:
        raise WebRequestError("原文路径无效", 404)
    root = Path(settings.get("project_root") or config.PROJECT_ROOT).resolve()
    candidate = (root / source_path).resolve()
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        raise WebRequestError("原文不在本地知识库目录内", 403) from None
    relative_name = relative.as_posix()
    in_corpus = (
        len(relative.parts) >= 2
        and relative.parts[0] == "01_corpus"
        and candidate.suffix.lower() in {".md", ".markdown", ".txt"}
    )
    if not in_corpus and relative_name not in INDEXED_ASSETS:
        raise WebRequestError("该文件不属于可阅读的已索引资料", 403)
    return candidate


def get_source_document(chunk_id, settings=None):
    """按已索引 chunk_id 返回全文与引用位置；不接收或执行任意文件路径。"""
    settings = settings or config.get_runtime_config()
    if not isinstance(chunk_id, str) or not chunk_id or len(chunk_id) > 1024:
        raise WebRequestError("chunk_id 无效")
    index_path = knowledge_qa._index_path(settings)
    if not index_path.is_file():
        raise WebRequestError("本地知识索引不存在", 404)
    connection = sqlite3.connect(index_path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        row = connection.execute(
            """SELECT c.id, c.source_path, c.ordinal, c.title, c.locator, c.content,
                      f.sha256 AS file_sha256
               FROM chunks AS c JOIN files AS f ON f.source_path=c.source_path
               WHERE c.id=?""",
            (chunk_id,),
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise WebRequestError("未找到已索引的原文片段", 404)

    path = _resolve_indexed_source(settings, row["source_path"])
    if not path.is_file():
        raise WebRequestError("原文文件已移动或删除，请重新检查知识索引", 404)
    if path.stat().st_size > MAX_SOURCE_BYTES:
        raise WebRequestError("原文文件过大，暂时只能查看当前摘录", 413)
    raw = path.read_bytes()
    if len(raw) > MAX_SOURCE_BYTES:
        raise WebRequestError("原文文件过大，暂时只能查看当前摘录", 413)
    text = raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    target = _locate_source_target(text, row)
    index_matches_file = hashlib.sha256(raw).hexdigest() == row["file_sha256"]
    warnings = []
    if not index_matches_file:
        warnings.append("文件在索引后发生变化，当前显示磁盘上的最新原文；建议重新索引。")
    if target is None:
        warnings.append("未能在当前文件中匹配引用位置，已显示全文。")
    elif not target["exact"]:
        warnings.append("未能精确匹配引用段落，已定位到所属章节。")
    return {
        "status": "completed",
        "read_only": True,
        "chunk_id": row["id"],
        "source_path": row["source_path"],
        "ordinal": row["ordinal"],
        "title": row["title"],
        "locator": row["locator"],
        "content": text,
        "target": target,
        "line_count": len(text.splitlines()),
        "file_size": len(raw),
        "index_matches_file": index_matches_file,
        "warning": " ".join(warnings) or None,
    }


def _index_status(settings):
    path = knowledge_qa._index_path(settings)
    result = {
        "exists": path.is_file(), "path": str(path), "files": 0, "chunks": 0,
        "vectors": 0, "pending": 0, "embedding_model": None, "error": None,
    }
    if not path.is_file():
        return result
    connection = None
    try:
        connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        result["files"] = connection.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        result["chunks"] = connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        result["vectors"] = connection.execute(
            "SELECT COUNT(*) FROM chunks WHERE embedding IS NOT NULL"
        ).fetchone()[0]
        row = connection.execute("SELECT value FROM metadata WHERE key='embed_model'").fetchone()
        result["embedding_model"] = row[0] if row else None
        result["pending"] = result["chunks"] - result["vectors"]
    except (OSError, sqlite3.Error) as exc:
        result["error"] = f"无法读取本地索引：{exc}"
    finally:
        if connection is not None:
            connection.close()
    return result


def _ollama_status(settings):
    base_url = str(settings.get("ollama_url") or "").rstrip("/")
    result = {"connected": False, "url": base_url, "models": [], "error": None}
    if not base_url:
        result["error"] = "未配置 Ollama 地址"
        return result
    try:
        request = urllib.request.Request(base_url + "/api/tags", headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=1.5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        models = payload.get("models", [])
        result["models"] = [
            str(item.get("name")) for item in models if isinstance(item, dict) and item.get("name")
        ]
        result["connected"] = True
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        result["error"] = f"本地模型服务未就绪：{exc}"
    return result


def get_status(settings=None, probe=True):
    """公开状态不包含 API Key、OpenAI Base URL 或会话内容。"""
    settings = settings or config.get_runtime_config()
    ollama = _ollama_status(settings) if probe else {"connected": None, "models": [], "error": None}
    return {
        "app": APP_ID, "service": "PyAgent Local Web", "version": "1.0",
        "configuration": {
            "backend": settings.get("llm"), "model": settings.get("model") or None,
            "embedding_model": settings.get("embed_model") or None,
            "has_model": bool(settings.get("has_model")),
            "local": settings.get("llm") == "ollama",
            "default_top_k": settings.get("top_k", 4),
        },
        "index": _index_status(settings), "ollama": ollama,
    }


class IndexJobManager:
    """单后台任务增量索引；与问答共用模型锁，状态接口始终可读取。"""

    def __init__(self, settings, query_lock):
        self.settings = settings
        self.query_lock = query_lock
        self.lock = threading.Lock()
        self.current = None
        self.thread = None

    def _update(self, **changes):
        with self.lock:
            if self.current is not None:
                self.current.update(changes)

    def snapshot(self, job_id=None):
        with self.lock:
            if self.current is None:
                return {"status": "idle", "job_id": None} if job_id is None else None
            if job_id is not None and self.current["job_id"] != job_id:
                return None
            return dict(self.current)

    def start(self):
        with self.lock:
            if self.current and self.current["status"] in {"queued", "running"}:
                return dict(self.current), False
            now = time.time()
            self.current = {
                "job_id": uuid.uuid4().hex, "status": "queued", "stage": "queued",
                "created_at": now, "started_at": None, "finished_at": None,
                "updated_files": 0, "current_file": None, "completed": 0, "total": 0,
                "result": None, "error": None,
            }
            job_id = self.current["job_id"]
            self.thread = threading.Thread(
                target=self._run, args=(job_id,), name="PyAgentIndex", daemon=True
            )
            self.thread.start()
            return dict(self.current), True

    def _progress(self, event):
        if event.get("event") == "file":
            self._update(
                stage="scanning", updated_files=event.get("updated", 0),
                current_file=event.get("path"),
            )
        elif event.get("event") == "embedding":
            self._update(
                stage="embedding", completed=event.get("completed", 0),
                total=event.get("total", 0), current_file=None,
            )

    def _run(self, job_id):
        self._update(status="running", stage="waiting", started_at=time.time())
        try:
            with self.query_lock:
                self._update(stage="scanning")
                index = knowledge.KnowledgeIndex(
                    knowledge_qa._index_path(self.settings),
                    project_root=self.settings.get("project_root") or config.PROJECT_ROOT,
                )
                embedder = knowledge_qa._make_embedder(self.settings)
                result = index.build(embedder=embedder, progress=self._progress)
            with self.lock:
                if self.current is not None and self.current["job_id"] == job_id:
                    self.current.update(
                        status="completed", stage="completed", finished_at=time.time(),
                        result=result, error=None, current_file=None,
                    )
        except Exception as exc:
            traceback.print_exc()
            with self.lock:
                if self.current is not None and self.current["job_id"] == job_id:
                    self.current.update(
                        status="failed", stage="failed", finished_at=time.time(),
                        error=f"{type(exc).__name__}: {exc}", current_file=None,
                    )


def get_documents(settings=None):
    """列出索引中的真实来源，不返回正文；导入目录单独标记供受控移除。"""
    settings = settings or config.get_runtime_config()
    path = knowledge_qa._index_path(settings)
    if not path.is_file():
        return {"documents": [], "total": 0, "imported_count": 0}
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            """SELECT f.source_path, f.title, f.updated_at,
                      COUNT(c.id) AS chunk_count, MIN(c.id) AS first_chunk_id
               FROM files f LEFT JOIN chunks c ON c.source_path=f.source_path
               GROUP BY f.source_path, f.title, f.updated_at"""
        ).fetchall()
    finally:
        connection.close()
    project_root = Path(settings.get("project_root") or config.PROJECT_ROOT).resolve()
    documents = []
    for row in rows:
        source_path = row["source_path"]
        try:
            file_size = (project_root / source_path).stat().st_size
            file_exists = True
        except OSError:
            file_size = 0
            file_exists = False
        managed = document_manager.is_managed_source(source_path)
        documents.append({
            "source_path": source_path, "title": row["title"],
            "updated_at": row["updated_at"], "chunk_count": row["chunk_count"],
            "first_chunk_id": row["first_chunk_id"], "managed": managed,
            "kind": "import" if managed else "library",
            "extension": Path(source_path).suffix.lower().lstrip("."),
            "file_size": file_size, "file_exists": file_exists,
        })
    documents.sort(key=lambda item: (not item["managed"], item["title"].casefold(), item["source_path"]))
    return {
        "documents": documents, "total": len(documents),
        "imported_count": sum(item["managed"] for item in documents),
    }


class PyAgentWebServer(ThreadingHTTPServer):
    """查询串行执行以适配单模型 CPU 服务，页面和状态仍可并行读取。"""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, settings, static_root=None, probe=True, llm_client=None, query_executor=None):
        super().__init__(address, PyAgentRequestHandler)
        self.settings = settings
        self.static_root = Path(static_root or WEB_ROOT).resolve()
        self.probe = probe
        self.llm_client = llm_client
        self.query_executor = query_executor or execute_query
        self.query_lock = threading.Lock()
        self.index_jobs = IndexJobManager(settings, self.query_lock)
        self.index = None


class PyAgentRequestHandler(BaseHTTPRequestHandler):
    server_version = "PyAgentLocal/1.0"

    def _common_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("Cache-Control", "no-store")

    def _send_bytes(self, body, content_type, status=200):
        self.send_response(status)
        self._common_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        try:
            self.end_headers()
            self.wfile.write(body)
        except ConnectionError:
            # 等待 CPU 回答期间关闭页面是正常操作，不再向已断开的 socket 写错误页。
            return

    def _send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self._send_bytes(body, "application/json; charset=utf-8", status)

    def _error(self, message, status=400, error_type="RequestError"):
        self._send_json(
            {"status": "error", "error": {"type": error_type, "message": message}}, status
        )

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path == "/api/health":
            self._send_json({"app": APP_ID, "status": "ok"})
            return
        if path == "/api/status":
            try:
                self._send_json(get_status(self.server.settings, probe=self.server.probe))
            except (OSError, ValueError) as exc:
                self._error(str(exc), 500, type(exc).__name__)
            return
        if path in {"/api/documents", "/api/index/status"}:
            try:
                self._check_local_client()
                if path == "/api/documents":
                    self._send_json(get_documents(self.server.settings))
                else:
                    query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                    job_ids = query.get("job_id", [])
                    if len(job_ids) > 1:
                        raise WebRequestError("job_id 最多提供一个")
                    job = self.server.index_jobs.snapshot(job_ids[0] if job_ids else None)
                    if job is None:
                        raise WebRequestError("索引任务不存在或服务已经重启", 404)
                    self._send_json(job)
            except WebRequestError as exc:
                self._error(str(exc), exc.status, type(exc).__name__)
            except (OSError, sqlite3.Error, ValueError) as exc:
                self._error(f"无法读取资料状态：{exc}", 400, type(exc).__name__)
            return
        if path == "/api/source":
            try:
                self._check_local_client()
                query = urllib.parse.parse_qs(
                    urllib.parse.urlsplit(self.path).query, keep_blank_values=True
                )
                chunk_ids = query.get("chunk_id", [])
                if len(chunk_ids) != 1:
                    raise WebRequestError("必须提供一个 chunk_id")
                self._send_json(get_source_document(chunk_ids[0], self.server.settings))
            except WebRequestError as exc:
                self._error(str(exc), exc.status, type(exc).__name__)
            except (OSError, UnicodeError, sqlite3.Error, ValueError) as exc:
                self._error(f"无法读取原文：{exc}", 400, type(exc).__name__)
            return
        static = STATIC_FILES.get(path)
        if static is None:
            self._error("页面不存在", 404, "NotFound")
            return
        filename, content_type = static
        try:
            body = (self.server.static_root / filename).read_bytes()
        except OSError as exc:
            self._error(f"界面文件不可用：{exc}", 500, type(exc).__name__)
            return
        self._send_bytes(body, content_type)

    def _check_local_client(self):
        if self.headers.get("X-PyAgent-Client") != "local-ui":
            raise WebRequestError("缺少本地界面请求标识", 403)
        origin = self.headers.get("Origin")
        if origin:
            port = self.server.server_address[1]
            allowed = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
            if origin not in allowed:
                raise WebRequestError("拒绝其他网页发起的本地请求", 403)

    def _read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise WebRequestError("Content-Length 无效") from None
        if length <= 0 or length > MAX_BODY_BYTES:
            raise WebRequestError(f"请求正文必须在 1 到 {MAX_BODY_BYTES} 字节之间")
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise WebRequestError("请求正文不是有效的 UTF-8 JSON") from None

    def _read_upload(self):
        if self.headers.get("Transfer-Encoding"):
            self.close_connection = True
            raise WebRequestError("上传接口不支持分块传输", 411)
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.close_connection = True
            raise WebRequestError("Content-Length 无效") from None
        if length <= 0 or length > document_manager.MAX_UPLOAD_BYTES:
            self.close_connection = True
            status = 413 if length > document_manager.MAX_UPLOAD_BYTES else 400
            raise WebRequestError("上传正文必须在 1 字节到 25 MiB 之间", status)
        data = self.rfile.read(length)
        if len(data) != length:
            self.close_connection = True
            raise WebRequestError("上传内容不完整，请重试")
        return data

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        try:
            self._check_local_client()
            if path == "/api/documents/import":
                query = urllib.parse.parse_qs(
                    urllib.parse.urlsplit(self.path).query, keep_blank_values=True
                )
                filenames = query.get("filename", [])
                if len(filenames) != 1:
                    raise WebRequestError("上传时必须提供一个 filename")
                result = document_manager.import_document(
                    filenames[0], self._read_upload(), self.server.settings
                )
                self._send_json(result, 201 if result["status"] == "imported" else 200)
                return
            if path == "/api/index":
                job, created = self.server.index_jobs.start()
                job["already_running"] = not created
                self._send_json(job, 202)
                return
            if path == "/api/documents/remove":
                payload = self._read_json()
                if not isinstance(payload, dict) or not isinstance(payload.get("source_path"), str):
                    raise WebRequestError("必须提供字符串 source_path")
                self._send_json(document_manager.remove_document(
                    payload["source_path"], self.server.settings
                ))
                return
            if path not in {"/api/ask", "/api/search"}:
                raise WebRequestError("接口不存在", 404)
            payload = self._read_json()
            if not isinstance(payload, dict):
                raise WebRequestError("请求必须是 JSON 对象")
            payload["mode"] = "ask" if path == "/api/ask" else "search"
            with self.server.query_lock:
                if self.server.index is None:
                    self.server.index = knowledge.KnowledgeIndex(
                        knowledge_qa._index_path(self.server.settings),
                        project_root=self.server.settings.get("project_root") or config.PROJECT_ROOT,
                    )
                result = self.server.query_executor(
                    payload, settings=self.server.settings, index=self.server.index,
                    llm_client=self.server.llm_client,
                )
            self._send_json(result)
        except WebRequestError as exc:
            self._error(str(exc), exc.status, type(exc).__name__)
        except document_manager.DocumentError as exc:
            self._error(str(exc), exc.status, type(exc).__name__)
        except (knowledge.KnowledgeError, OSError, UnicodeError, ValueError) as exc:
            self._error(str(exc), 400, type(exc).__name__)
        except Exception as exc:
            traceback.print_exc()
            self._error(f"本地请求发生未预期错误：{type(exc).__name__}", 500, type(exc).__name__)

    def log_message(self, format_string, *args):
        print(f"[PyAgent Web] {self.address_string()} - {format_string % args}", file=sys.stderr)


def _existing_application(port):
    """重复双击时复用本程序，而不是盲目打开端口上的其他服务。"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=0.8) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return payload.get("app") == APP_ID
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return False


def _parser():
    parser = argparse.ArgumentParser(description="启动只监听 127.0.0.1 的 PyAgent 浏览器界面")
    parser.add_argument("--port", type=int, default=8765, help="起始端口，默认 8765；被占用时尝试后续端口")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开默认浏览器")
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    if not 0 <= args.port <= 65535:
        print("端口必须在 0 到 65535 之间", file=sys.stderr)
        return 2
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        settings = config.get_runtime_config()
    except ValueError as exc:
        print(f"配置无效：{exc}", file=sys.stderr)
        return 2
    if args.port and _existing_application(args.port):
        url = f"http://127.0.0.1:{args.port}/"
        print(f"PyAgent 界面已经在运行：{url}")
        if not args.no_browser:
            webbrowser.open(url)
        return 0

    server = None
    candidates = [args.port] if args.port == 0 else range(args.port, min(args.port + 10, 65536))
    for port in candidates:
        try:
            server = PyAgentWebServer(("127.0.0.1", port), settings)
            break
        except OSError:
            continue
    if server is None:
        print("无法监听本地端口，请关闭占用程序或指定 --port", file=sys.stderr)
        return 1

    actual_port = server.server_address[1]
    url = f"http://127.0.0.1:{actual_port}/"
    print("=" * 62)
    print("PyAgent 本地知识工作台已启动")
    print(f"请在浏览器打开：{url}")
    print("仅监听 127.0.0.1；网页写操作仅限受管理资料的导入与移除。")
    print("按 Ctrl+C 停止网页服务；索引和独立 Ollama 服务不会被删除。")
    print("=" * 62, flush=True)
    if not args.no_browser:
        timer = threading.Timer(0.7, lambda: webbrowser.open(url))
        timer.daemon = True
        timer.start()
    try:
        server.serve_forever(poll_interval=0.4)
    except KeyboardInterrupt:
        print("\nPyAgent 网页服务已停止。")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


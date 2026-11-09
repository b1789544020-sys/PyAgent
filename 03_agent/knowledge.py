"""本地文档切块、SQLite 索引和混合检索；运行时仅依赖标准库与本机 Ollama。"""

from array import array
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import time
import urllib.error
import urllib.request


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_VERSION = "1"
DEFAULT_CHUNK_CHARS = 1000
EMBED_BATCH_SIZE = 16


class KnowledgeError(RuntimeError):
    """知识索引或检索错误，消息不包含任何密钥。"""


class EmbeddingError(KnowledgeError):
    """本地向量模型不可用或响应格式无效。"""


@dataclass(frozen=True)
class SourceFile:
    path: Path
    source_path: str
    kind: str
    sha256: str


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _discover_files(project_root, directory, suffixes, kind):
    root = directory.resolve()
    if not root.is_dir():
        return []
    files = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in suffixes:
            continue
        resolved = path.resolve()
        relative = resolved.relative_to(project_root.resolve()).as_posix()
        files.append((resolved, relative, kind))
    return files


def discover_sources(project_root=None):
    """只发现项目白名单目录内的 Markdown/文本和三类 JSONL 知识资产。"""
    root = Path(project_root or PROJECT_ROOT).resolve()
    discovered = _discover_files(
        root, root / "01_corpus", {".md", ".markdown", ".txt"}, "document"
    )
    assets = root / "02_assets"
    for name in ("knowledge_cards.jsonl", "code_examples.jsonl", "diagnostic_rules.jsonl"):
        path = assets / name
        if path.is_file():
            discovered.append((path.resolve(), f"02_assets/{name}", "jsonl"))
    sources = []
    for path, relative, kind in discovered:
        data = path.read_bytes()
        sources.append(SourceFile(path, relative, kind, _sha256(data)))
    return sources


def _markdown_title(text, source_path):
    comment = re.search(r"<!--.*?-->", text[:2000], re.S)
    if comment:
        for field in comment.group(0)[4:-3].split("|"):
            if field.strip().startswith("title="):
                return field.strip()[len("title=") :].strip()
    heading = re.search(r"^#{1,6}\s+(.+?)\s*$", text, re.M)
    if heading:
        return heading.group(1).strip()
    return Path(source_path).stem


def _text_pieces(text, max_chars):
    """按段落和完整代码围栏组块；超长块按行切分，不改动正文字符。"""
    if max_chars < 200:
        raise ValueError("max_chars 不能小于 200")
    blocks = []
    buffer = []
    in_fence = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped and not in_fence:
            if buffer:
                blocks.append("\n".join(buffer).strip())
                buffer = []
            continue
        buffer.append(line)
        if stripped.startswith("```"):
            in_fence = not in_fence
            if not in_fence:
                blocks.append("\n".join(buffer).strip())
                buffer = []
    if buffer:
        blocks.append("\n".join(buffer).strip())

    pieces = []
    current = ""
    for block in blocks:
        if not block:
            continue
        if len(block) > max_chars:
            if current:
                pieces.append(current)
                current = ""
            remaining = block
            while len(remaining) > max_chars:
                split_at = remaining.rfind("\n", 0, max_chars + 1)
                if split_at < max_chars // 2:
                    split_at = max_chars
                pieces.append(remaining[:split_at].strip())
                remaining = remaining[split_at:].lstrip("\n")
            current = remaining.strip()
        elif not current:
            current = block
        elif len(current) + len(block) + 2 <= max_chars:
            current += "\n\n" + block
        else:
            pieces.append(current)
            current = block
    if current:
        pieces.append(current)
    return [piece for piece in pieces if piece.strip()]


def _markdown_sections(text, title):
    sections = []
    heading_stack = {}
    locator = title
    lines = []
    for line in text.splitlines():
        if line.lstrip().startswith("<!--") and not lines:
            continue
        heading = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if heading:
            if lines and "\n".join(lines).strip():
                sections.append((locator, "\n".join(lines).strip()))
            level = len(heading.group(1))
            heading_stack[level] = heading.group(2).strip()
            for old_level in list(heading_stack):
                if old_level > level:
                    del heading_stack[old_level]
            locator = " > ".join(heading_stack[key] for key in sorted(heading_stack))
            lines = [line]
        else:
            lines.append(line)
    if lines and "\n".join(lines).strip():
        sections.append((locator, "\n".join(lines).strip()))
    return sections or [(title, text.strip())]


def _jsonl_record(record, source_path):
    identifier = str(record.get("id") or Path(source_path).stem)
    title = str(
        record.get("concept")
        or record.get("name")
        or record.get("topic")
        or identifier
    )
    locator = str(
        record.get("raw_locator")
        or record.get("description")
        or record.get("source_chapters")
        or identifier
    )
    lines = [f"{identifier}｜{title}"]
    for key, value in record.items():
        if isinstance(value, (dict, list)):
            rendered = json.dumps(value, ensure_ascii=False)
        else:
            rendered = str(value)
        lines.append(f"{key}: {rendered}")
    return f"{identifier}｜{title}", locator, "\n".join(lines)


def load_chunks(source, max_chars=DEFAULT_CHUNK_CHARS):
    """把一个来源文件转换成带稳定来源定位的块。JSONL 每条记录独立定位。"""
    text = source.path.read_bytes().decode("utf-8-sig")
    chunks = []
    if source.kind == "jsonl":
        for line_number, raw_line in enumerate(text.splitlines(), 1):
            if not raw_line.strip():
                continue
            try:
                record = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                raise KnowledgeError(
                    f"{source.source_path} 第 {line_number} 行不是有效 JSON"
                ) from None
            if not isinstance(record, dict):
                raise KnowledgeError(f"{source.source_path} 第 {line_number} 行必须是对象")
            title, locator, content = _jsonl_record(record, source.source_path)
            for piece in _text_pieces(content, max_chars) or [content]:
                chunks.append((title, f"第 {line_number} 行；{locator}", piece))
    else:
        title = _markdown_title(text, source.source_path)
        for locator, section in _markdown_sections(text, title):
            for piece in _text_pieces(section, max_chars):
                prefix = title if locator == title else f"{title}\n{locator}"
                chunks.append((title, locator, f"{prefix}\n\n{piece}"))

    result = []
    for ordinal, (title, locator, content) in enumerate(chunks, 1):
        content = content.strip()
        if not content:
            continue
        result.append(
            {
                "id": f"{source.source_path}#{len(result) + 1:04d}",
                "source_path": source.source_path,
                "ordinal": len(result) + 1,
                "title": title,
                "locator": locator,
                "content": content,
                "sha256": _sha256(content.encode("utf-8")),
                "tokens": " ".join(tokenize(content)),
            }
        )
    return result


_TOKEN_RE = re.compile(r"[a-zA-Z_][a-zA-Z0-9_.-]*|\d+(?:\.\d+)?|[\u3400-\u9fff]+")
_STOP_TOKENS = {
    "什么", "怎么", "如何", "为什么", "请问", "一下", "哪些", "介绍", "解释",
    "根据", "资料", "文中", "本书", "书中", "告诉", "我们", "可以", "应该",
    "一个", "这个", "那个", "进行", "使用", "相关", "内容", "问题", "回答",
}


def tokenize(text):
    """中文以二元词片检索，英文/标识符保留完整单词；无需外部分词依赖。"""
    tokens = []
    for match in _TOKEN_RE.finditer(text.casefold()):
        value = match.group(0)
        if re.fullmatch(r"[\u3400-\u9fff]+", value):
            if len(value) == 1:
                if value not in _STOP_TOKENS:
                    tokens.append(value)
            else:
                tokens.extend(
                    value[index : index + 2]
                    for index in range(len(value) - 1)
                    if value[index : index + 2] not in _STOP_TOKENS
                )
        elif value not in _STOP_TOKENS:
            tokens.append(value)
    return tokens


_QUERY_NOISE_RE = re.compile(
    r"什么是|为什么|怎么样|怎么|如何|请问|一下|哪些|介绍|解释|根据|资料|文中|"
    r"本书|书中|告诉我|告诉|我们|可以|应该|一个|这个|那个|相关|内容|问题|回答"
)


def tokenize_query(question):
    """去掉问题助词后再分词，避免“什么是变量”的有效覆盖率被人为降低。"""
    return tokenize(_QUERY_NOISE_RE.sub(" ", question))


class OllamaEmbedder:
    """调用本机 /api/embed；模型必须已经下载，构造函数不会访问网络。"""

    def __init__(self, model, base_url="http://localhost:11434", timeout=300):
        if not model:
            raise ValueError("向量模型名不能为空")
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = float(timeout)
        # EmbeddingGemma 官方检索模板；其他模型（包括 bge-m3）保持原文输入。
        gemma = "embeddinggemma" in model.casefold()
        self.query_prefix = "task: search result | query: " if gemma else ""
        self.document_prefix = "title: none | text: " if gemma else ""

    def embed_query(self, text):
        return self.embed([self.query_prefix + text])[0]

    def embed_documents(self, texts):
        return self.embed([self.document_prefix + text for text in texts])

    def embed(self, texts):
        values = list(texts)
        if not values:
            return []
        if any(not isinstance(text, str) or not text.strip() for text in values):
            raise ValueError("向量输入必须是非空字符串")
        body = {"model": self.model, "input": values}
        request = urllib.request.Request(
            self.base_url + "/api/embed",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise EmbeddingError(f"向量模型接口返回 HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError):
            raise EmbeddingError("无法连接本机向量模型或请求超时") from None
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise EmbeddingError("向量模型返回了无效 JSON") from None
        embeddings = payload.get("embeddings")
        if not isinstance(embeddings, list) or len(embeddings) != len(values):
            raise EmbeddingError("向量模型返回数量与输入不一致")
        if any(
            not isinstance(vector, list)
            or not vector
            or any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in vector)
            for vector in embeddings
        ):
            raise EmbeddingError("向量模型返回了无效向量")
        return embeddings


def _encode_vector(values):
    return array("f", values).tobytes()


def _decode_vector(blob):
    values = array("f")
    values.frombytes(blob)
    return values


class KnowledgeIndex:
    """SQLite 持久化索引；原文、词片和向量均保存在本机。"""

    def __init__(self, db_path, project_root=None):
        self.db_path = Path(db_path).expanduser().resolve()
        self.project_root = Path(project_root or PROJECT_ROOT).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self):
        connection = sqlite3.connect(str(self.db_path), timeout=60)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=60000")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS files (
                source_path TEXT PRIMARY KEY, sha256 TEXT NOT NULL,
                title TEXT NOT NULL, updated_at REAL NOT NULL
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS chunks (
                id TEXT PRIMARY KEY, source_path TEXT NOT NULL, ordinal INTEGER NOT NULL,
                title TEXT NOT NULL, locator TEXT NOT NULL, content TEXT NOT NULL,
                sha256 TEXT NOT NULL, tokens TEXT NOT NULL, embedding BLOB,
                dimensions INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(source_path) REFERENCES files(source_path) ON DELETE CASCADE
            )"""
        )
        connection.execute("CREATE INDEX IF NOT EXISTS chunks_source ON chunks(source_path)")
        connection.execute(
            "INSERT OR IGNORE INTO metadata(key, value) VALUES ('schema_version', ?)",
            (SCHEMA_VERSION,),
        )
        connection.commit()
        return connection

    @staticmethod
    def _metadata(connection, key, default=""):
        row = connection.execute("SELECT value FROM metadata WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    @staticmethod
    def _set_metadata(connection, key, value):
        connection.execute(
            "INSERT INTO metadata(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )

    def build(self, embedder=None, full=False, max_chars=DEFAULT_CHUNK_CHARS, progress=None):
        """按文件 SHA-256 增量更新；向量失败时关键词索引仍可检索。"""
        started = time.monotonic()
        sources = discover_sources(self.project_root)
        if not sources:
            raise KnowledgeError("未发现可索引的本地文档")
        updated = 0
        removed = 0
        embedded_now = 0
        connection = self._connect()
        try:
            if full:
                connection.execute("DELETE FROM chunks")
                connection.execute("DELETE FROM files")
                connection.commit()
            current_paths = {source.source_path for source in sources}
            old_paths = {
                row["source_path"] for row in connection.execute("SELECT source_path FROM files")
            }
            stale = old_paths - current_paths
            for source_path in stale:
                connection.execute("DELETE FROM files WHERE source_path=?", (source_path,))
            removed = len(stale)
            connection.commit()

            existing = {
                row["source_path"]: row["sha256"]
                for row in connection.execute("SELECT source_path, sha256 FROM files")
            }
            for source in sources:
                if existing.get(source.source_path) == source.sha256:
                    continue
                chunks = load_chunks(source, max_chars=max_chars)
                connection.execute("DELETE FROM files WHERE source_path=?", (source.source_path,))
                title = chunks[0]["title"] if chunks else Path(source.source_path).stem
                connection.execute(
                    "INSERT INTO files(source_path, sha256, title, updated_at) VALUES (?, ?, ?, ?)",
                    (source.source_path, source.sha256, title, time.time()),
                )
                connection.executemany(
                    """INSERT INTO chunks(
                        id, source_path, ordinal, title, locator, content, sha256, tokens
                    ) VALUES (
                        :id, :source_path, :ordinal, :title, :locator, :content, :sha256, :tokens
                    )""",
                    chunks,
                )
                connection.commit()
                updated += 1
                if progress:
                    progress({"event": "file", "updated": updated, "path": source.source_path})


            if embedder is not None:
                previous_model = self._metadata(connection, "embed_model")
                if previous_model != embedder.model:
                    connection.execute("UPDATE chunks SET embedding=NULL, dimensions=0")
                    self._set_metadata(connection, "embed_model", embedder.model)
                    connection.commit()
                pending = connection.execute(
                    "SELECT id, content FROM chunks WHERE embedding IS NULL ORDER BY id"
                ).fetchall()
                total = len(pending)
                for offset in range(0, total, EMBED_BATCH_SIZE):
                    batch = pending[offset : offset + EMBED_BATCH_SIZE]
                    texts = [row["content"] for row in batch]
                    document_method = getattr(embedder, "embed_documents", None)
                    vectors = document_method(texts) if callable(document_method) else embedder.embed(texts)
                    dimensions = {len(vector) for vector in vectors}
                    if len(dimensions) != 1:
                        raise EmbeddingError("同一批向量维度不一致")
                    dimension = dimensions.pop()
                    for row, vector in zip(batch, vectors):
                        connection.execute(
                            "UPDATE chunks SET embedding=?, dimensions=? WHERE id=?",
                            (_encode_vector(vector), dimension, row["id"]),
                        )
                    connection.commit()
                    embedded_now += len(batch)
                    if progress:
                        progress({
                            "event": "embedding", "completed": embedded_now,
                            "total": total, "model": embedder.model,
                        })

            files_count = connection.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            chunks_count = connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            vector_count = connection.execute(
                "SELECT COUNT(*) FROM chunks WHERE embedding IS NOT NULL"
            ).fetchone()[0]
            model = self._metadata(connection, "embed_model")
            self._set_metadata(connection, "chunk_chars", max_chars)
            self._set_metadata(connection, "updated_at", time.time())
            connection.commit()
        finally:
            connection.close()
        pending_count = chunks_count - vector_count
        mode = "hybrid" if vector_count == chunks_count and chunks_count else "partial" if vector_count else "lexical"
        return {
            "status": "completed", "command": "docs_index",
            "index_path": str(self.db_path), "project_root": str(self.project_root),
            "files": files_count, "chunks": chunks_count,
            "updated_files": updated, "removed_files": removed,
            "embedded_now": embedded_now, "vectors": vector_count,
            "embedding_pending": pending_count, "embedding_model": model or None,
            "mode": mode, "elapsed_seconds": round(time.monotonic() - started, 3),
            "message": "本地知识索引已更新。" if not pending_count else "关键词索引已更新；仍有文档块等待向量生成。",
        }

    def search(self, question, top_k=4, embedder=None, lexical_only=False):
        """混合检索；向量失败会返回 warning 并保留真实关键词结果。"""
        if not isinstance(question, str) or not question.strip():
            raise ValueError("检索问题不能为空")
        if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 10:
            raise ValueError("top_k 必须是 1 到 10 的整数")
        question = question.strip()
        if not self.db_path.is_file():
            raise KnowledgeError("本地知识索引不存在，请先运行 docs_index")
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT id, source_path, title, locator, content, tokens, embedding FROM chunks"
            ).fetchall()
            indexed_model = self._metadata(connection, "embed_model")
        finally:
            connection.close()
        if not rows:
            raise KnowledgeError("本地知识索引为空，请先运行 docs_index")

        query_tokens = tokenize_query(question)
        lexical, coverage = _bm25(rows, query_tokens)
        query_vector = None
        warning = ""
        if embedder is not None and not lexical_only:
            if indexed_model and indexed_model != embedder.model:
                warning = (
                    f"向量模型不一致：索引为 {indexed_model}，当前为 {embedder.model}；"
                    "已降级为关键词检索，请先运行 docs_index"
                )
            else:
                try:
                    query_method = getattr(embedder, "embed_query", None)
                    query_vector = query_method(question) if callable(query_method) else embedder.embed([question])[0]
                except (EmbeddingError, ValueError) as exc:
                    warning = f"向量检索不可用，已降级为关键词检索：{exc}"

        vector_scores = {}
        if query_vector is not None:
            for row in rows:
                if row["embedding"] is not None:
                    score = _cosine(query_vector, row["embedding"])
                    if score is not None:
                        vector_scores[row["id"]] = score
            if not vector_scores:
                query_vector = None
                warning = warning or "索引尚无维度兼容的文档向量，已使用关键词检索。"
        max_lexical = max(lexical.values(), default=0.0)
        ranked = []
        for row in rows:
            lexical_normalized = lexical[row["id"]] / max_lexical if max_lexical else 0.0
            vector = vector_scores.get(row["id"])
            if query_vector is not None:
                combined = 0.45 * lexical_normalized + 0.55 * max(0.0, vector or 0.0)
            else:
                combined = lexical_normalized
            ranked.append((combined, lexical[row["id"]], row, vector))
        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        selected = ranked[:top_k]
        top_combined, top_lexical, top_row, top_vector = selected[0]
        top_coverage = coverage.get(top_row["id"], 0.0)
        grounded = bool(
            (top_lexical >= 0.2 and top_coverage >= 0.34)
            or (
                query_vector is not None
                and top_vector is not None
                and top_vector >= 0.48
                and top_combined >= 0.25
            )
        )
        sources = []
        for rank, (combined, lexical_score, row, vector) in enumerate(selected, 1):
            sources.append(
                {
                    "id": f"S{rank}",
                    "chunk_id": row["id"],
                    "source_path": row["source_path"],
                    "title": row["title"],
                    "locator": row["locator"],
                    "snippet": _snippet(row["content"]),
                    "content": row["content"],
                    "score": round(combined, 6),
                    "lexical_score": round(lexical_score, 6),
                    "vector_score": round(vector, 6) if vector is not None else None,
                    "query_coverage": round(coverage.get(row["id"], 0.0), 6),
                }
            )
        return {
            "status": "completed",
            "mode": "hybrid" if query_vector is not None and vector_scores else "lexical",
            "question": question,
            "grounded": grounded,
            "sources": sources,
            "warning": warning or None,
            "retrieval": {
                "top_k": top_k,
                "chunks_considered": len(rows),
                "query_tokens": sorted(set(query_tokens)),
                "vector_candidates": len(vector_scores),
                "top_coverage": round(top_coverage, 6),
            },
        }


def _bm25(rows, query_tokens):
    """返回每行的 BM25 分数与查询词覆盖率，只计算查询出现的词。"""
    terms = set(query_tokens)
    if not terms:
        return {row["id"]: 0.0 for row in rows}, {}
    document_frequency = {term: 0 for term in terms}
    token_maps = {}
    lengths = {}
    for row in rows:
        counts = Counter(row["tokens"].split())
        token_maps[row["id"]] = counts
        lengths[row["id"]] = sum(counts.values())
        for term in terms:
            if counts.get(term):
                document_frequency[term] += 1
    average = sum(lengths.values()) / max(1, len(rows))
    scores = {}
    coverage = {}
    for row in rows:
        counts = token_maps[row["id"]]
        score = 0.0
        for term in terms:
            frequency = counts.get(term, 0)
            if not frequency:
                continue
            df = document_frequency[term]
            inverse = math.log(1 + (len(rows) - df + 0.5) / (df + 0.5))
            denominator = frequency + 1.5 * (1 - 0.75 + 0.75 * lengths[row["id"]] / max(1.0, average))
            score += inverse * frequency * 2.5 / denominator
        scores[row["id"]] = score
        coverage[row["id"]] = sum(1 for term in terms if counts.get(term)) / len(terms)
    return scores, coverage


def _cosine(query, blob):
    values = _decode_vector(blob)
    if len(values) != len(query):
        return None
    dot = sum(left * right for left, right in zip(query, values))
    query_norm = math.sqrt(sum(value * value for value in query))
    document_norm = math.sqrt(sum(value * value for value in values))
    if not query_norm or not document_norm:
        return 0.0
    return dot / (query_norm * document_norm)


def _snippet(content, limit=600):
    normalized = content.strip()
    return normalized if len(normalized) <= limit else normalized[:limit].rstrip() + "…"


def public_source(source):
    """移除内部完整 content，只把可核查元数据与限长原文暴露到命令结果。"""
    return {
        key: source.get(key)
        for key in (
            "id", "chunk_id", "source_path", "title", "locator", "snippet",
            "score", "lexical_score", "vector_score", "query_coverage",
        )
    }


__all__ = [
    "DEFAULT_CHUNK_CHARS", "EmbeddingError", "KnowledgeError", "KnowledgeIndex",
    "OllamaEmbedder", "SourceFile", "discover_sources", "load_chunks",
    "public_source", "tokenize",
]


"""本地浏览器服务测试；使用临时索引、MockLLM 和回环 HTTP，不访问外部网络。"""

import json
from pathlib import Path
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest


AGENT_DIR = Path(__file__).resolve().parent
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

import knowledge  # noqa: E402
import llm  # noqa: E402
import web_ui  # noqa: E402


@pytest.fixture
def local_project(tmp_path):
    chapters = tmp_path / "01_corpus" / "chapters"
    chapters.mkdir(parents=True)
    (chapters / "chapter_0001.md").write_text(
        "# 变量入门\n\n## 变量绑定\n\n变量给值一个可复用的名字，赋值后再按这个名字读取。\n",
        encoding="utf-8",
    )
    (tmp_path / "02_assets").mkdir()
    return tmp_path


def settings_for(project):
    return {
        "api_key": "test-secret-never-expose",
        "base_url": "https://should-not-be-called.invalid/v1",
        "model": "qwen-test",
        "llm": "ollama",
        "has_model": True,
        "ollama_url": "http://127.0.0.1:1",
        "embed_model": "fake-local-embed",
        "data_dir": str(project / "data"),
        "top_k": 4,
        "model_timeout": 180.0,
        "project_root": str(project),
    }


def make_index(project):
    index = knowledge.KnowledgeIndex(project / "data" / "knowledge.sqlite3", project)
    index.build()
    return index


def test_disconnected_browser_does_not_raise_from_response_write():
    handler = object.__new__(web_ui.PyAgentRequestHandler)
    handler.send_response = lambda status: None
    handler._common_headers = lambda: None
    handler.send_header = lambda name, value: None
    handler.end_headers = lambda: (_ for _ in ()).throw(ConnectionResetError("closed"))
    handler.wfile = None
    handler._send_bytes(b"local", "text/plain")


def test_validate_query_rejects_bad_types_and_oversize():
    assert web_ui.validate_query({"question": " 变量 ", "mode": "ask"})["question"] == "变量"
    for payload in (
        {"question": " "},
        {"question": "x", "top_k": True},
        {"question": "x", "top_k": 11},
        {"question": "x", "lexical_only": "yes"},
        {"question": "x", "mode": "fix"},
        {"question": "x" * 4001},
    ):
        with pytest.raises(web_ui.WebRequestError):
            web_ui.validate_query(payload)


def test_status_reads_real_sqlite_without_exposing_secrets(local_project):
    make_index(local_project)
    status = web_ui.get_status(settings_for(local_project), probe=False)
    assert status["index"]["files"] == 1
    assert status["index"]["chunks"] >= 1
    assert status["index"]["pending"] == status["index"]["chunks"]
    serialized = json.dumps(status, ensure_ascii=False)
    assert "test-secret-never-expose" not in serialized
    assert "should-not-be-called" not in serialized


def test_execute_query_reuses_qa_chain_and_separates_sources(local_project):
    index = make_index(local_project)
    client = llm.MockLLM(default_response="赋值让名字绑定到一个值。[S1]")
    result = web_ui.execute_query(
        {"question": "什么是变量绑定", "mode": "ask", "top_k": 2, "lexical_only": True},
        settings=settings_for(local_project), index=index, llm_client=client,
    )
    assert result["mode"] == "rag"
    assert result["answer"] == "赋值让名字绑定到一个值。[S1]"
    assert "原文摘录" not in result["answer"]
    assert result["sources"][0]["source_path"] == "01_corpus/chapters/chapter_0001.md"
    assert result["elapsed_seconds"] >= 0


def test_source_document_returns_full_text_and_exact_paragraph(local_project):
    index = make_index(local_project)
    settings = settings_for(local_project)
    result = web_ui.execute_query(
        {"question": "变量绑定", "mode": "search", "top_k": 1, "lexical_only": True},
        settings=settings, index=index,
    )
    chunk_id = result["sources"][0]["chunk_id"]
    document = web_ui.get_source_document(chunk_id, settings)
    assert document["read_only"] is True
    assert document["content"].startswith("# 变量入门")
    assert document["index_matches_file"] is True
    target = document["target"]
    assert target["exact"] is True
    assert "变量给值一个可复用的名字" in document["content"][target["start"]:target["end"]]
    (local_project / "01_corpus/chapters/chapter_0001.md").write_text(
        document["content"] + "\n索引之后新增的一段。\n", encoding="utf-8"
    )
    changed = web_ui.get_source_document(chunk_id, settings)
    assert changed["index_matches_file"] is False
    assert "重新索引" in changed["warning"]


def test_jsonl_source_targets_original_record_line(local_project):
    record = {"id": "KC_1", "concept": "变量", "definition": "名字绑定到一个值"}
    (local_project / "02_assets/knowledge_cards.jsonl").write_text(
        json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    make_index(local_project)
    document = web_ui.get_source_document(
        "02_assets/knowledge_cards.jsonl#0001", settings_for(local_project)
    )
    assert document["target"]["match_type"] == "jsonl_line"
    assert document["target"]["line_start"] == 1
    assert document["content"][document["target"]["start"]:document["target"]["end"]] == json.dumps(
        record, ensure_ascii=False
    )


def test_source_path_resolution_rejects_non_corpus_files(local_project):
    settings = settings_for(local_project)
    for source_path in ("../.env", "03_agent/config.py", "02_assets/other.txt"):
        with pytest.raises(web_ui.WebRequestError) as error:
            web_ui._resolve_indexed_source(settings, source_path)
        assert error.value.status == 403


def http_call(url, payload=None, local_client=True, origin=None):
    headers = {"Accept": "application/json"}
    if local_client:
        headers["X-PyAgent-Client"] = "local-ui"
    if origin:
        headers["Origin"] = origin
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    try:
        response = urllib.request.urlopen(request, timeout=5)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        body = response.read()
        status = response.status
        response_headers = response.headers
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        parsed = body.decode("utf-8")
    return status, parsed, response_headers


def http_upload(url, content, local_client=True):
    headers = {
        "Accept": "application/json", "Content-Type": "application/octet-stream",
        "X-PyAgent-Client": "local-ui" if local_client else "absent",
    }
    request = urllib.request.Request(url, data=content, headers=headers, method="POST")
    try:
        response = urllib.request.urlopen(request, timeout=5)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        return response.status, json.loads(response.read().decode("utf-8"))


@pytest.fixture
def live_server(local_project):
    make_index(local_project)
    client = llm.MockLLM(default_response="赋值让名字绑定到一个值。[S1]")
    server = web_ui.PyAgentWebServer(
        ("127.0.0.1", 0), settings_for(local_project), probe=False, llm_client=client
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield url, client
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_static_interface_and_security_headers(live_server):
    url, _ = live_server
    status, body, headers = http_call(url + "/")
    assert status == 200
    assert "PyAgent · 本地知识工作台" in body
    assert 'id="reader-overlay"' in body
    assert "https://" not in body
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert "script-src 'self'" in headers["Content-Security-Policy"]
    for asset in ("/app.css", "/app.js", "/library.js", "/favicon.svg"):
        assert http_call(url + asset)[0] == 200
    assert http_call(url + "/../knowledge.py")[0] == 404


def test_http_status_and_real_mocked_rag_round_trip(live_server):
    url, _ = live_server
    status, payload, _ = http_call(url + "/api/status")
    assert status == 200
    assert payload["app"] == "pyagent-local"
    assert http_call(url + "/api/health")[1] == {"app": "pyagent-local", "status": "ok"}
    assert payload["index"]["files"] == 1
    assert "api_key" not in payload

    status, answer, _ = http_call(
        url + "/api/ask",
        {"question": "什么是变量绑定", "top_k": 2, "lexical_only": True},
        origin=url,
    )
    assert status == 200
    assert answer["mode"] == "rag"
    assert answer["request_mode"] == "ask"
    assert answer["answer"] == "赋值让名字绑定到一个值。[S1]"
    assert answer["sources"][0]["id"] == "S1"
    assert "原文摘录" not in answer["answer"]


def test_http_original_search_needs_no_generation(live_server):
    url, _ = live_server
    status, result, _ = http_call(
        url + "/api/search",
        {"question": "变量绑定", "top_k": 1, "lexical_only": True},
    )
    assert status == 200
    assert result["request_mode"] == "search"
    assert result["generation"] == "not_used"
    assert result["answer"] == "找到以下相关原文（未调用生成模型）。"
    assert len(result["sources"]) == 1


def test_http_source_reader_is_exact_local_only_and_index_restricted(live_server, local_project):
    url, _ = live_server
    _, result, _ = http_call(
        url + "/api/search",
        {"question": "变量绑定", "top_k": 1, "lexical_only": True},
    )
    chunk_id = result["sources"][0]["chunk_id"]
    endpoint = url + "/api/source?chunk_id=" + urllib.parse.quote(chunk_id)
    assert http_call(endpoint, local_client=False)[0] == 403
    assert http_call(endpoint, origin="https://evil.invalid")[0] == 403
    status, document, headers = http_call(endpoint)
    assert status == 200
    assert document["chunk_id"] == chunk_id
    assert document["target"]["exact"] is True
    assert "# 变量入门" in document["content"]
    assert headers["Cache-Control"] == "no-store"
    (local_project / "01_corpus/chapters/secret.md").write_text("不得开放", encoding="utf-8")
    secret_url = url + "/api/source?chunk_id=" + urllib.parse.quote(
        "01_corpus/chapters/secret.md#0001"
    )
    status, error, _ = http_call(secret_url)
    assert status == 404
    assert "不得开放" not in json.dumps(error, ensure_ascii=False)


def test_post_requires_local_header_and_same_origin(live_server):
    url, _ = live_server
    payload = {"question": "变量", "lexical_only": True}
    status, error, _ = http_call(url + "/api/ask", payload, local_client=False)
    assert status == 403
    assert error["status"] == "error"
    status, error, _ = http_call(url + "/api/ask", payload, origin="https://evil.invalid")
    assert status == 403
    assert "拒绝" in error["error"]["message"]


def test_request_errors_are_json_and_do_not_stop_server(live_server):
    url, _ = live_server
    status, error, _ = http_call(url + "/api/ask", {"question": ""})
    assert status == 400
    assert error["error"]["type"] == "WebRequestError"
    assert http_call(url + "/api/status")[0] == 200


def wait_for_index(url, job_id):
    for _ in range(100):
        status, job, _ = http_call(
            url + "/api/index/status?job_id=" + urllib.parse.quote(job_id)
        )
        assert status == 200
        if job["status"] == "completed":
            return job
        if job["status"] == "failed":
            pytest.fail(job["error"])
        time.sleep(0.05)
    pytest.fail("后台索引任务超时")


def test_http_managed_import_index_read_and_remove_round_trip(
    live_server, local_project, monkeypatch
):
    url, _ = live_server
    monkeypatch.setattr(web_ui.knowledge_qa, "_make_embedder", lambda settings: None)
    assert http_call(url + "/api/documents", local_client=False)[0] == 403
    endpoint = url + "/api/documents/import?filename=" + urllib.parse.quote("部署笔记.txt")
    status, imported = http_upload(endpoint, "部署前必须备份。\n检查本地索引。".encode("utf-8"))
    assert status == 201
    assert imported["source_path"] == "01_corpus/imports/部署笔记.txt"
    assert (local_project / imported["source_path"]).is_file()
    assert http_upload(endpoint, "部署前必须备份。\n检查本地索引。".encode())[0] == 200

    status, job, _ = http_call(url + "/api/index", {})
    assert status == 202
    completed = wait_for_index(url, job["job_id"])
    assert completed["result"]["files"] == 2
    assert completed["result"]["updated_files"] == 1
    _, documents, _ = http_call(url + "/api/documents")
    assert documents["total"] == 2
    assert documents["imported_count"] == 1
    managed = next(item for item in documents["documents"] if item["managed"])
    _, source, _ = http_call(
        url + "/api/source?chunk_id=" + urllib.parse.quote(managed["first_chunk_id"])
    )
    assert "部署前必须备份" in source["content"]

    status, error, _ = http_call(
        url + "/api/documents/remove",
        {"source_path": "01_corpus/chapters/chapter_0001.md"},
    )
    assert status == 403
    status, removed, _ = http_call(
        url + "/api/documents/remove", {"source_path": imported["source_path"]}
    )
    assert status == 200
    assert Path(removed["backup_path"]).is_file()
    _, second_job, _ = http_call(url + "/api/index", {})
    assert wait_for_index(url, second_job["job_id"])["result"]["removed_files"] == 1
    _, documents, _ = http_call(url + "/api/documents")
    assert documents["total"] == 1
    assert documents["imported_count"] == 0


"""本地知识索引与问答测试；全部使用临时语料和模型替身，不访问网络。"""

import json
from pathlib import Path
import sqlite3
import sys

import pytest


AGENT_DIR = Path(__file__).resolve().parent
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

import knowledge  # noqa: E402
import knowledge_qa  # noqa: E402
import llm  # noqa: E402


class FakeEmbedder:
    model = "fake-local-embed"

    def embed(self, texts):
        vectors = []
        for text in texts:
            if "变量" in text or "variable" in text.casefold():
                vectors.append([1.0, 0.0, 0.0])
            elif "函数" in text:
                vectors.append([0.0, 1.0, 0.0])
            else:
                vectors.append([0.0, 0.0, 0.0])
        return vectors


class BrokenEmbedder:
    model = "broken-local-embed"

    def embed(self, texts):
        raise knowledge.EmbeddingError("测试向量服务不可用")


@pytest.fixture
def local_project(tmp_path):
    chapters = tmp_path / "01_corpus" / "chapters"
    chapters.mkdir(parents=True)
    (chapters / "chapter_0001.md").write_text(
        "<!-- source=book.html | chapter_no=0001 | title=变量入门 -->\n\n"
        "变量给值一个可复用的名字，赋值后再按这个名字读取。\n\n"
        "## 重新赋值\n\n"
        "重新赋值会改变名字当前绑定的值，旧值不会被自动打印。\n\n"
        "```python\nscore = 1\nscore = 2\nprint(score)\n```\n",
        encoding="utf-8",
    )
    (chapters / "chapter_0002.md").write_text(
        "# 函数基础\n\n## 参数\n\n函数通过参数接收调用时传入的值。\n",
        encoding="utf-8",
    )
    assets = tmp_path / "02_assets"
    assets.mkdir()
    (assets / "knowledge_cards.jsonl").write_text(
        json.dumps(
            {
                "id": "KC_0001",
                "concept": "装饰器",
                "definition": "装饰器用于包装可调用对象。",
                "raw_locator": "chapter_0009.md 第 2 行",
            },
            ensure_ascii=False,
        ) + "\n",
        encoding="utf-8",
    )
    return tmp_path


def make_index(local_project):
    return knowledge.KnowledgeIndex(local_project / "data" / "knowledge.sqlite3", local_project)


def test_chunking_preserves_real_title_locator_and_code(local_project):
    source = knowledge.discover_sources(local_project)[0]
    chunks = knowledge.load_chunks(source, max_chars=200)

    assert source.source_path == "01_corpus/chapters/chapter_0001.md"
    assert all(chunk["title"] == "变量入门" for chunk in chunks)
    assert any("重新赋值" in chunk["locator"] for chunk in chunks)
    assert any("score = 1\nscore = 2\nprint(score)" in chunk["content"] for chunk in chunks)
    assert all(chunk["id"].startswith(source.source_path + "#") for chunk in chunks)


def test_incremental_index_reuses_unchanged_and_removes_deleted(local_project):
    index = make_index(local_project)
    first = index.build()
    assert first["files"] == 3
    assert first["mode"] == "lexical"
    assert index.build()["updated_files"] == 0

    target = local_project / "01_corpus" / "chapters" / "chapter_0002.md"
    target.write_text(target.read_text(encoding="utf-8") + "\n返回值由 return 语句产生。\n", encoding="utf-8")
    changed = index.build()
    assert changed["updated_files"] == 1
    found = index.search("return 返回值", lexical_only=True)
    assert found["grounded"] is True
    assert found["sources"][0]["source_path"].endswith("chapter_0002.md")

    target.unlink()
    removed = index.build()
    assert removed["removed_files"] == 1
    connection = sqlite3.connect(index.db_path)
    try:
        count = connection.execute(
            "SELECT COUNT(*) FROM chunks WHERE source_path LIKE '%chapter_0002.md%'"
        ).fetchone()[0]
    finally:
        connection.close()
    assert count == 0


def test_hybrid_search_and_vector_failure_falls_back_to_lexical(local_project):
    index = make_index(local_project)
    index.build(embedder=FakeEmbedder())

    hybrid = index.search("什么是变量绑定", embedder=FakeEmbedder())
    assert hybrid["mode"] == "hybrid"
    assert hybrid["grounded"] is True
    assert hybrid["sources"][0]["source_path"].endswith("chapter_0001.md")
    assert hybrid["sources"][0]["vector_score"] == pytest.approx(1.0)

    fallback = index.search("什么是变量绑定", embedder=BrokenEmbedder())
    assert fallback["mode"] == "lexical"
    assert fallback["grounded"] is True
    assert "降级" in fallback["warning"]
    assert fallback["sources"][0]["source_path"].endswith("chapter_0001.md")


def test_jsonl_card_is_searchable_with_original_locator(local_project):
    index = make_index(local_project)
    index.build()
    result = index.search("装饰器如何包装可调用对象", lexical_only=True)
    assert result["grounded"] is True
    source = result["sources"][0]
    assert source["source_path"] == "02_assets/knowledge_cards.jsonl"
    assert "chapter_0009.md" in source["locator"]
    assert "装饰器用于包装可调用对象" in source["snippet"]


def qa_settings(local_project, has_model=True):
    return {
        "api_key": "",
        "model": "qwen-test",
        "llm": "ollama" if has_model else "openai",
        "has_model": has_model,
        "ollama_url": "http://localhost:11434",
        "embed_model": "fake-local-embed",
        "data_dir": str(local_project / "data"),
        "top_k": 4,
        "model_timeout": 180.0,
        "project_root": str(local_project),
    }


def test_grounded_answer_contains_citations_and_real_sources(local_project):
    index = make_index(local_project)
    index.build()
    client = llm.MockLLM(default_response="变量是给值使用的名字，赋值后可以读取。[S1]")

    result = knowledge_qa.answer_question(
        "什么是变量",
        lexical_only=True,
        settings=qa_settings(local_project),
        index=index,
        llm_client=client,
    )

    assert result["status"] == "completed"
    assert result["mode"] == "rag"
    assert result["grounded"] is True
    assert result["generation_attempts"] == 1
    assert "[S1]" in result["answer"]
    assert "01_corpus/chapters/chapter_0001.md" in result["answer"]
    assert result["sources"][0]["chunk_id"].endswith("#0001")
    prompt = client.received_messages[0][1]["content"]
    assert "变量给值一个可复用的名字" in prompt
    assert "真实文件：01_corpus/chapters/chapter_0001.md" in prompt


def test_insufficient_evidence_refuses_without_calling_model(local_project):
    index = make_index(local_project)
    index.build()
    client = llm.MockLLM(default_response="不应该出现 [S1]")

    result = knowledge_qa.answer_question(
        "量子引力如何统一",
        lexical_only=True,
        settings=qa_settings(local_project),
        index=index,
        llm_client=client,
    )

    assert result["reason"] == "insufficient_evidence"
    assert result["generation"] == "not_run"
    assert "无法可靠回答" in result["answer"]
    assert result["sources"] == []
    assert len(result["candidates"]) == 4
    assert client.received_messages == []


def test_missing_or_invalid_citation_is_not_accepted(local_project):
    index = make_index(local_project)
    index.build()
    client = llm.MockLLM(default_response="这是没有来源支持的生成文本。")

    result = knowledge_qa.answer_question(
        "什么是变量",
        lexical_only=True,
        settings=qa_settings(local_project),
        index=index,
        llm_client=client,
    )

    assert result["mode"] == "search"
    assert result["generation"] == "citation_fallback"
    assert result["generation_attempts"] == 2
    assert len(client.received_messages) == 2
    assert "不采用其生成回答" in result["answer"]
    assert "这是没有来源支持的生成文本。\n\n来源" not in result["answer"]


def test_model_error_and_no_model_both_return_honest_search(local_project):
    index = make_index(local_project)
    index.build()

    class FailingLLM:
        def chat(self, messages):
            raise llm.LLMError("测试模型连接失败")

    failed = knowledge_qa.answer_question(
        "什么是变量",
        lexical_only=True,
        settings=qa_settings(local_project),
        index=index,
        llm_client=FailingLLM(),
    )
    assert failed["generation"] == "error_fallback"
    assert "降级为原文检索" in failed["answer"]

    no_model = knowledge_qa.answer_question(
        "什么是变量",
        lexical_only=True,
        settings=qa_settings(local_project, has_model=False),
        index=index,
        llm_client=llm.UnconfiguredLLM(),
    )
    assert no_model["generation"] == "not_available"
    assert no_model["mode"] == "search"
    assert "不冒充智能问答" in no_model["answer"]


def test_search_only_never_acquires_generation_model(local_project, monkeypatch):
    index = make_index(local_project)
    index.build()

    def forbidden():
        raise AssertionError("docs_search 不应获取生成模型")

    monkeypatch.setattr(llm, "get_llm", forbidden)
    result = knowledge_qa.search_documents(
        "变量绑定",
        lexical_only=True,
        settings=qa_settings(local_project, has_model=False),
        index=index,
    )
    assert result["generation"] == "not_used"
    assert result["sources"][0]["source_path"].endswith("chapter_0001.md")
    assert "未调用生成模型" in result["answer"]


def test_docs_command_parser_and_missing_index_error(local_project):
    index = make_index(local_project)
    code, built = knowledge_qa.run_command(
        ["docs_index", "--lexical-only"],
        settings=qa_settings(local_project),
        index=index,
    )
    assert code == 0
    assert built["files"] == 3

    code, result = knowledge_qa.run_command(
        ["docs_search", "变量", "--lexical-only"],
        settings=qa_settings(local_project),
        index=index,
    )
    assert code == 0
    assert result["command"] == "docs_search"
    assert result["grounded"] is True

    missing = knowledge.KnowledgeIndex(local_project / "missing" / "empty.sqlite3", local_project)
    code, result = knowledge_qa.run_command(
        ["docs_search", "变量", "--lexical-only"],
        settings=qa_settings(local_project),
        index=missing,
    )
    assert code == 1
    assert result["error"]["type"] == "KnowledgeError"


def test_ollama_embed_protocol_is_local_json_without_auth(monkeypatch):
    calls = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def read(self):
            return json.dumps({"embeddings": [[0.1, 0.2], [0.3, 0.4]]}).encode("utf-8")

    def fake_urlopen(request, timeout):
        calls.append((request, timeout))
        return FakeResponse()

    monkeypatch.setattr(knowledge.urllib.request, "urlopen", fake_urlopen)
    embedder = knowledge.OllamaEmbedder("embed-test", "http://localhost:11434", timeout=12)
    assert embedder.embed(["变量", "函数"]) == [[0.1, 0.2], [0.3, 0.4]]
    request, timeout = calls[0]
    assert request.full_url == "http://localhost:11434/api/embed"
    assert request.get_header("Authorization") is None
    assert json.loads(request.data.decode("utf-8")) == {
        "model": "embed-test",
        "input": ["变量", "函数"],
    }
    assert timeout == 12.0


def test_embeddinggemma_uses_official_retrieval_prefixes(monkeypatch):
    bodies = []

    class FakeResponse:
        def __init__(self, count):
            self.count = count

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def read(self):
            return json.dumps({"embeddings": [[0.1, 0.2]] * self.count}).encode("utf-8")

    def fake_urlopen(request, timeout):
        body = json.loads(request.data.decode("utf-8"))
        bodies.append(body)
        return FakeResponse(len(body["input"]))

    monkeypatch.setattr(knowledge.urllib.request, "urlopen", fake_urlopen)
    embedder = knowledge.OllamaEmbedder("embeddinggemma:300m-qat-q4_0")
    assert embedder.embed_query("变量绑定") == [0.1, 0.2]
    assert len(embedder.embed_documents(["正文一", "正文二"])) == 2
    assert bodies[0]["input"] == ["task: search result | query: 变量绑定"]
    assert bodies[1]["input"] == ["title: none | text: 正文一", "title: none | text: 正文二"]


def test_search_does_not_mix_vectors_from_a_different_model(local_project):
    index = make_index(local_project)
    index.build(embedder=FakeEmbedder())

    class DifferentModel:
        model = "different-model"

        def embed(self, texts):
            raise AssertionError("模型不一致时不应请求新查询向量")

    result = index.search("变量绑定", embedder=DifferentModel())
    assert result["mode"] == "lexical"
    assert result["retrieval"]["vector_candidates"] == 0
    assert result["grounded"] is True
    assert "模型不一致" in result["warning"]


def test_top_k_and_empty_questions_are_rejected(local_project):
    index = make_index(local_project)
    index.build()
    with pytest.raises(ValueError):
        index.search("", lexical_only=True)
    with pytest.raises(ValueError):
        index.search("变量", top_k=0, lexical_only=True)
    with pytest.raises(ValueError):
        index.search("变量", top_k=True, lexical_only=True)


def test_docs_command_rejects_zero_top_k_instead_of_using_default(local_project):
    index = make_index(local_project)
    index.build()
    code, result = knowledge_qa.run_command(
        ["docs_search", "变量", "--top-k", "0", "--lexical-only"],
        settings=qa_settings(local_project),
        index=index,
    )
    assert code == 1
    assert result["status"] == "error"
    assert result["error"]["type"] == "ValueError"


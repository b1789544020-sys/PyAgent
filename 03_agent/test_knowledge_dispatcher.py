"""docs_* 与统一入口集成测试；知识实现使用替身，不触碰真实索引。"""

import json
from pathlib import Path
import sys

import pytest


AGENT_DIR = Path(__file__).resolve().parent
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

import config  # noqa: E402
import dispatcher  # noqa: E402
import knowledge_qa  # noqa: E402
import main as agent_main  # noqa: E402
import understanding  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch, tmp_path):
    for key in config.CONFIG_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(understanding, "DEFAULT_SESSION_PATH", tmp_path / ".session.json")


def fake_result(command):
    return {
        "status": "completed",
        "command": command,
        "mode": "lexical",
        "grounded": True,
        "answer": "变量的检索结果 [S1]",
        "generation": "not_used",
        "sources": [{
            "id": "S1",
            "source_path": "01_corpus/chapters/chapter_0006.md",
            "title": "变量",
            "locator": "第 1 节",
            "snippet": "变量给值一个名字。",
        }],
    }


def test_explicit_docs_command_works_without_generation_model(monkeypatch, capsys):
    calls = []

    def fake_run(argv, settings=None):
        calls.append((argv, settings))
        return 0, fake_result(argv[0])

    monkeypatch.setattr(knowledge_qa, "run_command", fake_run)
    assert dispatcher.main(["docs_search", "变量", "--lexical-only"]) == 0

    assert calls[0][0] == ["docs_search", "变量", "--lexical-only"]
    assert calls[0][1]["has_model"] is False
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("当前模式：离线")
    assert json.loads("\n".join(lines[1:]))["command"] == "docs_search"


def test_ollama_natural_question_routes_to_rag_not_react(monkeypatch, capsys):
    monkeypatch.setenv("AGENT_LLM", "ollama")
    monkeypatch.setenv("AGENT_MODEL", "qwen-test")
    calls = []

    def fake_run(argv, settings=None):
        calls.append(argv)
        return 0, fake_result(argv[0])

    monkeypatch.setattr(knowledge_qa, "run_command", fake_run)
    assert dispatcher.main(["什么是变量绑定？"]) == 0

    assert calls == [["docs_ask", "什么是变量绑定？", "--top-k", "4"]]
    output = capsys.readouterr().out
    assert output.startswith("当前模式：本地模型（qwen-test")
    assert "docs_ask" in output


def test_project_actions_stay_out_of_document_router():
    assert knowledge_qa.should_use_document_qa("什么是变量") is True
    assert knowledge_qa.should_use_document_qa("变量绑定") is True
    assert knowledge_qa.should_use_document_qa("帮我检查 D:\\demo 哪里坏了") is False
    assert knowledge_qa.should_use_document_qa("修复这个项目") is False
    assert knowledge_qa.should_use_document_qa("运行项目") is False


def test_main_parses_quoted_docs_question_even_with_local_model(monkeypatch, capsys):
    monkeypatch.setenv("AGENT_LLM", "ollama")
    monkeypatch.setenv("AGENT_MODEL", "qwen-test")
    calls = []

    def fake_run(argv, settings=None):
        calls.append(argv)
        return 0, fake_result(argv[0])

    monkeypatch.setattr(knowledge_qa, "run_command", fake_run)
    assert agent_main.main(["ask", 'docs_search "变量 绑定" --lexical-only']) == 0

    assert calls == [["docs_search", "变量 绑定", "--lexical-only"]]
    output = capsys.readouterr().out
    assert "变量的检索结果 [S1]" in output
    assert '"source_path": "01_corpus/chapters/chapter_0006.md"' in output
    assert '"generation": "not_used"' in output


def test_main_streams_docs_index_progress(monkeypatch, capsys):
    def fake_run(argv, settings=None):
        print("向量索引进度：16/16（fake-local-embed）", file=sys.stderr, flush=True)
        result = fake_result(argv[0])
        result.update(files=1, chunks=1, vectors=1, embedding_pending=0)
        return 0, result

    monkeypatch.setattr(knowledge_qa, "run_command", fake_run)
    assert agent_main.main(["ask", "docs_index"]) == 0
    captured = capsys.readouterr()
    assert "向量索引进度：16/16" in captured.err
    assert "本地知识索引已更新" not in captured.err

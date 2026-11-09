"""基于本地检索证据的问答、引用校验和 docs_* 命令。"""

import argparse
import json
from pathlib import Path
import re
import sys

import config
import knowledge
import llm as llm_module
import understanding


SYSTEM_PROMPT = """你是 PyAgent 的本地文档问答器。你只能使用用户消息中提供的证据回答，不能使用模型记忆补充事实。
规则：
1. 每个关键结论后必须标注证据编号，格式为 [S1]、[S2]；编号只能来自提供的证据。
2. 证据不足以回答时，只能回答“现有资料不足，无法可靠回答”，不得猜测。
3. 不得编造文件、章节、页码、作者、代码或引用；不要自行添加来源清单，程序会附上真实来源。
4. 用简洁中文回答，通常不超过 300 字；代码只能在证据中存在时引用。
5. 综合相关证据，保留“函数内”“模块级”“在……条件下”等适用范围；不得把特定作用域或单个示例的规则推广成普遍规律。证据只覆盖问题的一部分时，要明确说明覆盖范围。
6. 这是本地资料问答，不声称执行了任何项目检查、修复或联网操作。"""
INSUFFICIENT_ANSWER = "现有本地资料中没有找到足够可靠的依据，无法可靠回答这个问题。请换一个更具体的问题，或先补充并索引相关资料。"


def _index_path(settings):
    data_dir = Path(settings.get("data_dir") or (config.PROJECT_ROOT / ".pyagent-data"))
    return data_dir.expanduser().resolve() / "knowledge.sqlite3"


def _make_embedder(settings):
    model = settings.get("embed_model") or ""
    if not model:
        return None
    return knowledge.OllamaEmbedder(
        model=model,
        base_url=settings.get("ollama_url") or "http://localhost:11434",
        timeout=max(300.0, float(settings.get("model_timeout", 180))),
    )


def _format_sources(sources, heading="来源（可在项目文件中核查）"):
    lines = [heading + "："]
    for source in sources:
        lines.extend(
            [
                f"[{source['id']}] {source['title']}",
                f"位置：{source['locator']}",
                f"文件：{source['source_path']}",
                f"原文摘录：{source['snippet']}",
            ]
        )
    return "\n".join(lines)


def build_messages(question, sources):
    evidence = []
    for source in sources:
        content = source["content"]
        if len(content) > 850:
            content = content[:850].rstrip() + "…"
        evidence.append(
            f"<证据 id=\"{source['id']}\">\n"
            f"真实文件：{source['source_path']}\n"
            f"标题：{source['title']}\n位置：{source['locator']}\n"
            f"原文：\n{content}\n</证据>"
        )
    user = (
        f"问题：{question}\n\n" + "\n\n".join(evidence)
        + "\n\n请只根据上述证据回答，并在关键结论后使用 [S编号]。"
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def _valid_citations(text, source_count):
    if not isinstance(text, str) or not text.strip():
        return False
    citations = re.findall(r"\[S(\d+)\]", text, re.I)
    return bool(citations) and all(1 <= int(value) <= source_count for value in citations)


def _base_result(question, retrieval):
    return {
        "status": "completed",
        "command": "docs_ask",
        "question": question,
        "grounded": bool(retrieval["grounded"]),
        "retrieval_mode": retrieval["mode"],
        "retrieval": retrieval["retrieval"],
        "sources": [knowledge.public_source(source) for source in retrieval["sources"]],
        "warning": retrieval.get("warning"),
    }


def _search_fallback(result, sources, message, generation):
    result.update(
        {
            "mode": "search",
            "generation": generation,
            "answer": message + "\n\n" + _format_sources(sources, "检索原文（未经生成模型改写）"),
        }
    )
    return result


def answer_question(
    question,
    top_k=4,
    lexical_only=False,
    settings=None,
    index=None,
    embedder=None,
    llm_client=None,
):
    """先检索再生成；证据不足不调用模型，引用不合格则不采用模型文本。"""
    settings = settings or config.get_runtime_config()
    index = index or knowledge.KnowledgeIndex(
        _index_path(settings), project_root=settings.get("project_root") or config.PROJECT_ROOT
    )
    if embedder is None and not lexical_only:
        embedder = _make_embedder(settings)
    retrieval = index.search(
        question, top_k=top_k, embedder=embedder, lexical_only=lexical_only
    )
    result = _base_result(question, retrieval)
    sources = retrieval["sources"]
    if not retrieval["grounded"]:
        result["candidates"] = result["sources"]
        result["sources"] = []
        result.update(
            {
                "mode": "refusal",
                "reason": "insufficient_evidence",
                "generation": "not_run",
                "answer": INSUFFICIENT_ANSWER,
            }
        )
        return result

    client = llm_client or llm_module.get_llm()
    if isinstance(client, llm_module.UnconfiguredLLM) or not settings.get("has_model"):
        return _search_fallback(
            result,
            sources,
            "当前没有可用的生成模型；下面只提供原文检索，不冒充智能问答。",
            "not_available",
        )

    messages = build_messages(question, sources)
    attempts = 0
    raw = ""
    try:
        for attempts in (1, 2):
            raw = client.chat(messages).strip()
            if _valid_citations(raw, len(sources)):
                break
            if attempts == 1:
                messages = messages + [
                    {"role": "assistant", "content": raw},
                    {
                        "role": "system",
                        "content": "上一条回答没有有效的 [S编号] 引用。请重新回答；只能使用现有证据编号，证据不足就明确拒答。",
                    },
                ]
    except llm_module.LLMError as exc:
        result["warning"] = f"本地生成模型不可用：{exc}"
        return _search_fallback(
            result, sources, "本地生成模型暂时不可用，已降级为原文检索。", "error_fallback"
        )

    result["generation_attempts"] = attempts
    if not _valid_citations(raw, len(sources)):
        result["warning"] = "生成模型未给出有效引用，其回答未被采用。"
        return _search_fallback(
            result,
            sources,
            "本地模型没有按要求提供可核查引用，因此不采用其生成回答。",
            "citation_fallback",
        )
    result.update(
        {
            "mode": "rag",
            "generation": {
                "backend": settings.get("llm"),
                "model": settings.get("model"),
                "local": settings.get("llm") == "ollama",
            },
            "answer": raw + "\n\n" + _format_sources(result["sources"]),
        }
    )
    return result


def search_documents(
    question,
    top_k=4,
    lexical_only=False,
    settings=None,
    index=None,
    embedder=None,
):
    """只检索并返回原文；此函数从不获取或调用生成模型。"""
    settings = settings or config.get_runtime_config()
    index = index or knowledge.KnowledgeIndex(
        _index_path(settings), project_root=settings.get("project_root") or config.PROJECT_ROOT
    )
    if embedder is None and not lexical_only:
        embedder = _make_embedder(settings)
    retrieval = index.search(
        question, top_k=top_k, embedder=embedder, lexical_only=lexical_only
    )
    sources = [knowledge.public_source(source) for source in retrieval["sources"]]
    if retrieval["grounded"]:
        answer = "找到以下相关原文（未调用生成模型）：\n\n" + _format_sources(sources)
    else:
        answer = INSUFFICIENT_ANSWER + "\n可以添加更具体的技术名词后再检索。"
    return {
        "status": "completed",
        "command": "docs_search",
        "question": question,
        "answer": answer,
        "grounded": retrieval["grounded"],
        "mode": retrieval["mode"],
        "generation": "not_used",
        "sources": sources if retrieval["grounded"] else [],
        "candidates": sources if not retrieval["grounded"] else [],
        "warning": retrieval.get("warning"),
        "retrieval": retrieval["retrieval"],
    }


def should_use_document_qa(prompt):
    """项目检查/修复/运行优先；普通问题与陈述默认进入本地资料问答。"""
    return understanding.detect_intent(prompt) in {"question", "other"}


def _parser():
    parser = argparse.ArgumentParser(
        prog="docs",
        description="本地文档索引、原文检索与有引用的 Ollama 问答。",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    index = commands.add_parser("docs_index", help="创建或增量更新本地知识索引")
    index.add_argument("--full", action="store_true", help="清空后重建全部文档块和向量")
    index.add_argument("--lexical-only", action="store_true", help="只建关键词索引，不调用向量模型")
    for name, help_text in (
        ("docs_search", "只检索原文，不调用生成模型"),
        ("docs_ask", "依据检索证据生成本地回答"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("question", nargs="+", help="自然语言问题或关键词")
        command.add_argument("--top-k", type=int, default=None, help="返回证据数，1 到 10")
        command.add_argument(
            "--lexical-only", action="store_true", help="不调用向量模型，只用关键词检索"
        )
    return parser


def _progress(event):
    if event.get("event") == "embedding":
        print(
            f"向量索引进度：{event['completed']}/{event['total']}（{event['model']}）",
            file=sys.stderr,
            flush=True,
        )


def run_command(argv, settings=None, index=None, embedder=None, llm_client=None):
    """执行 docs_* 命令并返回 (退出码, 可 JSON 序列化结果)。"""
    args = _parser().parse_args(list(argv))
    settings = settings or config.get_runtime_config()
    try:
        top_k_option = getattr(args, "top_k", None)
        top_k = int(settings.get("top_k", 4)) if top_k_option is None else top_k_option
        if args.command == "docs_index":
            index = index or knowledge.KnowledgeIndex(
                _index_path(settings),
                project_root=settings.get("project_root") or config.PROJECT_ROOT,
            )
            if embedder is None and not args.lexical_only:
                embedder = _make_embedder(settings)
            result = index.build(
                embedder=embedder,
                full=args.full,
                progress=_progress,
            )
            result["answer"] = result["message"]
            return 0, result
        common = {
            "question": " ".join(args.question).strip(),
            "top_k": top_k,
            "lexical_only": args.lexical_only,
            "settings": settings,
            "index": index,
            "embedder": embedder,
        }
        if args.command == "docs_search":
            result = search_documents(**common)
        else:
            result = answer_question(**common, llm_client=llm_client)
        return 0, result
    except (knowledge.KnowledgeError, OSError, UnicodeError, ValueError) as exc:
        return 1, {
            "status": "error",
            "command": args.command,
            "answer": f"本地知识操作失败：{exc}",
            "message": str(exc),
            "error": {"type": type(exc).__name__, "message": str(exc)},
        }


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        code, result = run_command(arguments)
    except SystemExit as exc:
        return int(exc.code or 0)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return code


if __name__ == "__main__":
    raise SystemExit(main())


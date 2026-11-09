"""本地资料导入与 DOCX 转换测试；全部使用临时目录，不接触真实知识库。"""
import io
import json
from pathlib import Path
import sys
import zipfile

import pytest


AGENT_DIR = Path(__file__).resolve().parent
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

import document_manager as manager  # noqa: E402
import knowledge  # noqa: E402


def settings_for(project):
    return {"project_root": str(project), "data_dir": str(project / "data")}


def make_docx():
    namespace = manager.W_NS
    document = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
    <w:document xmlns:w="{namespace}"><w:body>
      <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>部署手册</w:t></w:r></w:p>
      <w:p><w:r><w:t>部署前必须备份数据。</w:t></w:r></w:p>
      <w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>检查磁盘空间</w:t></w:r></w:p>
      <w:tbl><w:tr><w:tc><w:p><w:r><w:t>项目</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>要求</w:t></w:r></w:p></w:tc></w:tr>
      <w:tr><w:tc><w:p><w:r><w:t>网络</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>仅本机</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
      <w:sectPr/>
    </w:body></w:document>'''
    styles = f'''<w:styles xmlns:w="{namespace}"><w:style w:type="paragraph" w:styleId="Heading1">
      <w:name w:val="heading 1"/><w:pPr><w:outlineLvl w:val="0"/></w:pPr></w:style></w:styles>'''
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", document)
        archive.writestr("word/styles.xml", styles)
        archive.writestr("word/_rels/document.xml.rels", "<Relationships/>")
    return output.getvalue()


def test_text_import_normalizes_encoding_and_never_overwrites(tmp_path):
    settings = settings_for(tmp_path)
    first = manager.import_document("学习笔记.txt", "变量绑定给名字。\r\n第二行。".encode("gb18030"), settings)
    assert first["status"] == "imported"
    path = tmp_path / first["source_path"]
    assert path.read_bytes().decode("utf-8") == "变量绑定给名字。\n第二行。"
    duplicate = manager.import_document("学习笔记.txt", "变量绑定给名字。\n第二行。".encode(), settings)
    assert duplicate["status"] == "duplicate"
    assert duplicate["source_path"] == first["source_path"]
    changed = manager.import_document("学习笔记.txt", "完全不同的内容".encode(), settings)
    assert changed["source_path"].endswith("学习笔记-2.txt")
    assert path.read_bytes().decode("utf-8") == "变量绑定给名字。\n第二行。"
    traversal = manager.import_document("../安全名称.md", "# 标题\n\n正文".encode(), settings)
    assert traversal["source_path"] == "01_corpus/imports/安全名称.md"


def test_docx_import_preserves_text_headings_lists_and_tables(tmp_path):
    settings = settings_for(tmp_path)
    result = manager.import_document("安装说明.docx", make_docx(), settings)
    assert result["status"] == "imported"
    assert result["converted"] is True
    assert result["title"] == "部署手册"
    assert result["source_path"] == "01_corpus/imports/安装说明.md"
    text = (tmp_path / result["source_path"]).read_text(encoding="utf-8")
    assert "# 部署手册" in text
    assert "部署前必须备份数据。" in text
    assert "- 检查磁盘空间" in text
    assert "| 项目 | 要求 |" in text
    assert manager.import_document("安装说明.docx", make_docx(), settings)["status"] == "duplicate"
    sources = knowledge.discover_sources(tmp_path)
    chunks = knowledge.load_chunks(sources[0])
    serialized = json.dumps(chunks, ensure_ascii=False)
    assert "部署前必须备份数据" in serialized
    assert "01_corpus/imports/安装说明.md#0001" == chunks[0]["id"]


def test_import_rejects_unsupported_binary_empty_and_invalid_docx(tmp_path):
    settings = settings_for(tmp_path)
    for filename, data, message in (
        ("手册.pdf", b"pdf", "仅支持"),
        ("空.txt", b"  \n", "没有可索引"),
        ("二进制.txt", b"abc\x00def", "二进制"),
        ("损坏.docx", b"not a zip", "无效"),
        ("旧版.doc", b"doc", "仅支持"),
    ):
        with pytest.raises(manager.DocumentError) as error:
            manager.import_document(filename, data, settings)
        assert message in str(error.value)


def test_import_enforces_converted_text_limit(tmp_path):
    with pytest.raises(manager.DocumentError) as error:
        manager.import_document("过大.txt", b"x" * (manager.MAX_TEXT_BYTES + 1), settings_for(tmp_path))
    assert error.value.status == 413
    assert "2 MiB" in str(error.value)


def test_remove_moves_only_managed_files_to_backup(tmp_path):
    settings = settings_for(tmp_path)
    result = manager.import_document("临时.txt", "待移除内容".encode(), settings)
    removed = manager.remove_document(result["source_path"], settings)
    assert removed["status"] == "removed"
    assert not (tmp_path / result["source_path"]).exists()
    assert Path(removed["backup_path"]).read_text(encoding="utf-8") == "待移除内容"
    original = tmp_path / "01_corpus/chapters/chapter.md"
    original.parent.mkdir(parents=True)
    original.write_text("# 原有章节", encoding="utf-8")
    for path in ("01_corpus/chapters/chapter.md", "../.env", "03_agent/config.py"):
        with pytest.raises(manager.DocumentError) as error:
            manager.remove_document(path, settings)
        assert error.value.status == 403
    assert original.is_file()

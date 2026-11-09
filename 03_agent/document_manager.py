"""受管理的本地资料导入：TXT/Markdown 规范化，DOCX 仅提取文字为 Markdown。"""
import hashlib
import io
import os
from pathlib import Path
import re
import shutil
import threading
import time
import unicodedata
import xml.etree.ElementTree as ET
import zipfile


IMPORT_DIRECTORY = "01_corpus/imports"
TEXT_SUFFIXES = {".txt", ".md", ".markdown"}
SUPPORTED_SUFFIXES = TEXT_SUFFIXES | {".docx"}
MAX_UPLOAD_BYTES = 25 * 1024 * 1024
MAX_TEXT_BYTES = 2 * 1024 * 1024
MAX_DOCX_XML_BYTES = 20 * 1024 * 1024
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W = f"{{{W_NS}}}"
_IMPORT_LOCK = threading.Lock()
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL", *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


class DocumentError(ValueError):
    """可安全展示给本地页面的导入或移除错误。"""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def is_managed_source(source_path):
    """只有固定导入目录内的正斜杠相对路径允许由网页移除。"""
    return (
        isinstance(source_path, str)
        and source_path.startswith(IMPORT_DIRECTORY + "/")
        and "\\" not in source_path
        and "\x00" not in source_path
        and ".." not in Path(source_path).parts
    )


def _project_root(settings):
    return Path(settings.get("project_root") or Path(__file__).resolve().parent.parent).resolve()


def managed_root(settings):
    return _project_root(settings) / "01_corpus" / "imports"


def _safe_filename(filename):
    if not isinstance(filename, str) or not filename.strip() or len(filename) > 500:
        raise DocumentError("请提供有效的文件名")
    name = unicodedata.normalize("NFC", filename.replace("\\", "/").rsplit("/", 1)[-1].strip())
    name = "".join(character for character in name if ord(character) >= 32 and character != 127)
    name = re.sub(r'[<>:"/\\|?*]', "_", name).strip(" .")
    if not name or name == ".":
        raise DocumentError("文件名无效")
    suffix = Path(name).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise DocumentError("仅支持 TXT、MD、Markdown 和 DOCX 文件")
    stem = Path(name).stem.strip(" .")[:100].strip(" .")
    if not stem or stem.upper() in _RESERVED_NAMES:
        stem = "document"
    return stem + suffix


def _decode_text(data):
    """统一保存为 UTF-8，兼容常见 UTF-16 与 GB18030 中文记事本文件。"""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        encodings = ("utf-16",)
    else:
        encodings = ("utf-8-sig", "gb18030")
    for encoding in encodings:
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise DocumentError("文本编码无法识别，请另存为 UTF-8、UTF-16 或 GB18030")
    if "\x00" in text:
        raise DocumentError("文件包含二进制内容，不是可导入的纯文本")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        raise DocumentError("文件没有可索引的文字内容")
    return text


def _xml_root(archive, name, required=False):
    try:
        info = archive.getinfo(name)
    except KeyError:
        if required:
            raise DocumentError("DOCX 缺少正文内容，文件可能已损坏") from None
        return None
    if info.flag_bits & 1:
        raise DocumentError("不支持加密 DOCX，请先解除密码保护")
    if info.file_size > MAX_DOCX_XML_BYTES:
        raise DocumentError("DOCX 正文 XML 过大，请拆分文档后再导入")
    data = archive.read(info)
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise DocumentError("DOCX 含有不支持的 XML 声明")
    try:
        return ET.fromstring(data)
    except ET.ParseError:
        raise DocumentError("DOCX 正文 XML 无法解析") from None


def _style_levels(archive):
    root = _xml_root(archive, "word/styles.xml")
    levels = {}
    if root is None:
        return levels
    for style in root.findall(W + "style"):
        style_id = style.get(W + "styleId")
        if not style_id:
            continue
        name_node = style.find(W + "name")
        name = (name_node.get(W + "val") if name_node is not None else "").casefold()
        match = re.search(r"(?:heading|标题)\s*([1-6])\b", name)
        level = int(match.group(1)) if match else 1 if name in {"title", "标题"} else None
        outline = style.find(f"{W}pPr/{W}outlineLvl")
        if outline is not None:
            try:
                level = min(6, int(outline.get(W + "val")) + 1)
            except (TypeError, ValueError):
                pass
        if level:
            levels[style_id] = level
    return levels


def _plain_text(element):
    parts = []
    for node in element.iter():
        if node.tag == W + "t" and node.text:
            parts.append(node.text)
        elif node.tag == W + "tab":
            parts.append("\t")
        elif node.tag in {W + "br", W + "cr"}:
            parts.append("\n")
    return "".join(parts).strip()


def _paragraph_markdown(paragraph, styles):
    text = _plain_text(paragraph)
    if not text:
        return ""
    properties = paragraph.find(W + "pPr")
    style_id = ""
    is_list = False
    indentation = 0
    if properties is not None:
        style = properties.find(W + "pStyle")
        style_id = style.get(W + "val", "") if style is not None else ""
        numbering = properties.find(W + "numPr")
        if numbering is not None:
            is_list = True
            level = numbering.find(W + "ilvl")
            try:
                indentation = min(6, int(level.get(W + "val", "0"))) if level is not None else 0
            except ValueError:
                indentation = 0
    if style_id in styles:
        return "#" * styles[style_id] + " " + re.sub(r"\s*\n\s*", " ", text)
    if is_list:
        prefix = "  " * indentation + "- "
        return prefix + text.replace("\n", "\n" + "  " * indentation + "  ")
    return text


def _table_markdown(table, styles):
    rows = []
    for table_row in table.findall(W + "tr"):
        cells = []
        for cell in table_row.findall(W + "tc"):
            paragraphs = [_paragraph_markdown(p, styles) for p in cell.findall(W + "p")]
            value = "<br>".join(item for item in paragraphs if item)
            cells.append(value.replace("|", "\\|").replace("\n", "<br>"))
        if any(cells):
            rows.append(cells)
    if not rows:
        return ""
    columns = max(len(row) for row in rows)
    if columns > 100:
        raise DocumentError("DOCX 表格列数过多，请简化后再导入")
    rows = [row + [""] * (columns - len(row)) for row in rows]
    lines = ["| " + " | ".join(row) + " |" for row in rows]
    lines.insert(1, "| " + " | ".join(["---"] * columns) + " |")
    return "\n".join(lines)


def extract_docx(data):
    """使用标准库读取 OOXML，不执行宏、不访问外部关系、不展开图片。"""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            if len(archive.infolist()) > 4096:
                raise DocumentError("DOCX 内部文件数量过多")
            root = _xml_root(archive, "word/document.xml", required=True)
            styles = _style_levels(archive)
    except zipfile.BadZipFile:
        raise DocumentError("DOCX 文件无效或已损坏") from None
    body = root.find(W + "body")
    if body is None:
        raise DocumentError("DOCX 中没有可读取的正文")
    blocks = []
    for child in body:
        if child.tag == W + "p":
            value = _paragraph_markdown(child, styles)
        elif child.tag == W + "tbl":
            value = _table_markdown(child, styles)
        else:
            value = ""
        if value:
            blocks.append(value)
    text = "\n\n".join(blocks).strip()
    if not text:
        raise DocumentError("DOCX 中没有可索引的文字；图片和扫描页不会自动 OCR")
    return text + "\n"


def _infer_title(text, fallback):
    match = re.search(r"^#{1,6}\s+(.+?)\s*$", text, re.MULTILINE)
    return match.group(1).strip() if match else fallback


def _write_new_file(target, data):
    created = False
    try:
        with target.open("xb") as stream:
            created = True
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        if created:
            try:
                target.unlink()
            except OSError:
                pass
        raise


def import_document(filename, data, settings):
    """保存一个文件；同名同内容复用，同名不同内容添加序号，绝不覆盖。"""
    if not isinstance(data, bytes) or not data:
        raise DocumentError("上传文件为空")
    if len(data) > MAX_UPLOAD_BYTES:
        raise DocumentError("单个上传文件不能超过 25 MiB", 413)
    safe_name = _safe_filename(filename)
    suffix = Path(safe_name).suffix.lower()
    stem = Path(safe_name).stem
    converted = suffix == ".docx"
    text = extract_docx(data) if converted else _decode_text(data)
    encoded = text.encode("utf-8")
    if len(encoded) > MAX_TEXT_BYTES:
        raise DocumentError("转换后的文字超过 2 MiB，请拆分文档以保证全文阅读可用", 413)
    output_suffix = ".md" if converted else suffix
    directory = managed_root(settings)
    directory.mkdir(parents=True, exist_ok=True)
    with _IMPORT_LOCK:
        target = directory / (stem + output_suffix)
        number = 2
        while target.exists():
            if target.is_file() and target.stat().st_size == len(encoded):
                if target.read_bytes() == encoded:
                    return {
                        "status": "duplicate", "source_path": target.relative_to(_project_root(settings)).as_posix(),
                        "title": _infer_title(text, stem), "original_filename": safe_name,
                        "converted": converted, "file_size": len(encoded),
                        "sha256": hashlib.sha256(encoded).hexdigest(),
                    }
            target = directory / f"{stem}-{number}{output_suffix}"
            number += 1
            if number > 10000:
                raise DocumentError("同名文件过多，请更换文件名", 409)
        _write_new_file(target, encoded)
    return {
        "status": "imported", "source_path": target.relative_to(_project_root(settings)).as_posix(),
        "title": _infer_title(text, stem), "original_filename": safe_name,
        "converted": converted, "file_size": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }


def remove_document(source_path, settings):
    """只移出受管理导入目录，并保留到数据目录的可恢复备份中。"""
    if not is_managed_source(source_path):
        raise DocumentError("只能移除资料管理页面导入的文件", 403)
    project_root = _project_root(settings)
    root = managed_root(settings).resolve()
    candidate = (project_root / source_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        raise DocumentError("移除路径超出导入目录", 403) from None
    if candidate.suffix.lower() not in TEXT_SUFFIXES or not candidate.is_file():
        raise DocumentError("要移除的导入文件不存在", 404)
    data_dir = Path(settings.get("data_dir") or project_root / ".pyagent-data").resolve()
    backup_dir = data_dir / "removed_documents" / time.strftime("%Y%m%d-%H%M%S")
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / candidate.name
    number = 2
    while backup.exists():
        backup = backup_dir / f"{candidate.stem}-{number}{candidate.suffix}"
        number += 1
    shutil.move(str(candidate), str(backup))
    return {
        "status": "removed", "source_path": source_path, "backup_path": str(backup),
        "message": "资料已移出知识目录，原文件已保留在本地移除备份中。",
    }


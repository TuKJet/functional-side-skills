"""Safe, deterministic text extraction for resume files.

The source file is never executed. Office files are treated as ZIP/XML data and
PDFs are extracted with pypdf; image-only PDFs are explicitly marked for OCR.
"""

from __future__ import annotations

import html
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree

MAX_BYTES = 25 * 1024 * 1024
MAX_ZIP_ENTRIES = 200
_DOCX_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _result(text: str = "", status: str = "ready", detail: str = "") -> dict:
    return {"text": text, "text_status": status, "detail": detail}


def extract_text(path: Path) -> dict:
    """Return ``text``, a processing status and a human-readable detail.

    Unsupported files remain usable as originals but do not pretend to have
    searchable text. Errors are returned as data so a batch import can continue.
    """
    path = Path(path)
    try:
        if not path.is_file():
            return _result(status="error", detail=f"文件不存在: {path}")
        size = path.stat().st_size
        if size > MAX_BYTES:
            return _result(status="error", detail=f"文件超过 25 MB 限制: {path.name}")
        suffix = path.suffix.lower()
        if suffix in {".txt", ".md"}:
            return _result(path.read_text(encoding="utf-8-sig"), "ready", "UTF-8 文本")
        if suffix == ".pdf":
            return _extract_pdf(path)
        if suffix == ".docx":
            return _extract_docx(path)
        return _result(status="unsupported", detail=f"不支持的文件类型: {suffix or '(无扩展名)'}")
    except (OSError, UnicodeError, KeyError, RuntimeError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
        return _result(status="error", detail=f"读取失败: {exc}")


def _extract_pdf(path: Path) -> dict:
    try:
        from pypdf import PdfReader
    except ImportError:
        return _result(status="error", detail="缺少 pypdf，请按 requirements.txt 安装")
    try:
        reader = PdfReader(str(path))
        chunks = [(page.extract_text() or "").strip() for page in reader.pages]
        text = "\n\n".join(chunk for chunk in chunks if chunk)
        if text:
            return _result(text, "ready", f"PDF 文本提取，共 {len(reader.pages)} 页")
        return _result(status="needs_ocr", detail=f"PDF 无可提取文字，共 {len(reader.pages)} 页，需要 OCR")
    except Exception as exc:  # pypdf raises several version-specific exceptions
        return _result(status="error", detail=f"PDF 读取失败: {exc}")


def _extract_docx(path: Path) -> dict:
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_ZIP_ENTRIES:
            return _result(status="error", detail="DOCX 压缩包条目过多，疑似恶意压缩包")
        total_uncompressed = sum(info.file_size for info in infos)
        if total_uncompressed > MAX_BYTES or any(info.file_size > MAX_BYTES for info in infos):
            return _result(status="error", detail="DOCX 解压后超过 25 MB 限制")
        xml = archive.read("word/document.xml")
    root = ElementTree.fromstring(xml)
    parts = []
    for node in root.iter():
        if node.tag == _DOCX_NS + "t" and node.text:
            parts.append(html.unescape(node.text))
        elif node.tag == _DOCX_NS + "br":
            parts.append("\n")
        elif node.tag == _DOCX_NS + "p":
            parts.append("\n")
    text = re.sub(r"[ \t]+", " ", "".join(parts)).strip()
    return _result(text, "ready", "DOCX 文本提取")

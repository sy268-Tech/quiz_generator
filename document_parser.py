"""Word/PDF 文档文本提取模块。"""

import io
import docx
import pdfplumber

MAX_CHARS = 80000


def parse_document(file_bytes: bytes, filename: str) -> tuple[str, bool, bool]:
    """根据文件扩展名路由解析，返回 (文本内容, 是否被截断, 是否可能为扫描件)。"""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    maybe_scanned = False

    if ext == "docx":
        text = _parse_docx(file_bytes)
    elif ext == "pdf":
        text, maybe_scanned = _parse_pdf(file_bytes)
    elif ext in ("txt", "md"):
        text = _parse_text(file_bytes)
    else:
        raise ValueError(f"不支持的文件格式: .{ext}，请上传 .docx / .pdf / .txt / .md 文件")

    truncated = len(text) > MAX_CHARS
    if truncated:
        text = text[:MAX_CHARS]

    return text, truncated, maybe_scanned


def _parse_docx(file_bytes: bytes) -> str:
    """从 .docx 文件提取文本。"""
    doc = docx.Document(io.BytesIO(file_bytes))
    paragraphs = []
    for p in doc.paragraphs:
        if p.text.strip():
            paragraphs.append(p.text.strip())
    return "\n".join(paragraphs)


def _parse_pdf(file_bytes: bytes) -> tuple[str, bool]:
    """从 .pdf 文件逐页提取文本，返回 (文本, 是否可能为扫描件)。"""
    text_parts = []
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text.strip())
    text = "\n\n".join(text_parts)
    # 有页面但提取不到文本 → 可能是扫描件
    maybe_scanned = len(pdf.pages) > 0 and not text.strip()
    return text, maybe_scanned


def _parse_text(file_bytes: bytes) -> str:
    """纯文本文件直接解码。"""
    for encoding in ("utf-8", "gbk", "gb2312"):
        try:
            return file_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue
    return file_bytes.decode("utf-8", errors="replace")

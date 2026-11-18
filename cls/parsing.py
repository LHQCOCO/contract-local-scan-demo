"""多格式合同解析：PDF / DOCX / TXT，扫描件 PDF 自动走本地 OCR。全部在本地内存中完成，不写入临时文件。"""
from __future__ import annotations

import io
import re
from pathlib import Path

from cls import ocr

MAX_BYTES = 25 * 1024 * 1024
MAX_CHARS = 300_000
SUPPORTED = {".pdf", ".docx", ".txt"}


class ParseError(ValueError):
    pass


def normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u00a0", " ").replace("\u3000", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _pdf_hint(e: Exception) -> str:
    """把底层解析异常翻译成用户能照着做的中文提示；技术细节仍通过异常链保留给日志。"""
    raw = str(e).lower()
    if any(k in raw for k in ("encrypt", "decrypt", "password")):
        return "该文件已加密，需要密码才能打开。请用 PDF 阅读器另存为不加密的 PDF 后再上传。"
    if any(k in raw for k in ("eof marker", "ended unexpectedly", "invalid pdf header",
                              "startxref", "xref", "not a pdf")):
        return "文件已损坏或未下载完整，无法解析。请重新导出或重新下载后重试。"
    return "无法按标准 PDF 解析。请用阅读器重新另存为 PDF 后再上传。"


def _docx_hint(e: Exception) -> str:
    raw = str(e).lower()
    if any(k in raw for k in ("zip", "package", "content type")):
        return "这不是有效的 .docx 文件。若是旧版 .doc、或只改了扩展名，请在 Word / WPS 中另存为 .docx 后再上传。"
    return "无法按标准 DOCX 解析。请用 Word / WPS 另存为 .docx 后再上传。"


def _pdf(data: bytes) -> tuple[str, int, list[str], bool, bool]:
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise ParseError("缺少 pypdf 依赖，无法解析 PDF") from e
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise ParseError("PDF 解析失败：该文件已加密，需要密码才能打开。"
                             "请用 PDF 阅读器另存为不加密的 PDF 后再上传。")
        pages = len(reader.pages)
        chunks = []
        for page in reader.pages:
            chunks.append(page.extract_text() or "")
    except ParseError:
        raise
    except Exception as e:
        raise ParseError(f"PDF 解析失败：{_pdf_hint(e)}") from e
    text = normalize("\n".join(chunks))
    warnings = []
    scan_suspect = False
    ocr_used = False
    if len(text) < 80 or (pages >= 2 and len(text) / pages < 40):
        scan_suspect = True
        # 扫描件/图片型 PDF：优先尝试本地 OCR
        if ocr.available():
            try:
                ocr_text, ocr_pages = ocr.ocr_pdf(data)
            except Exception:
                ocr_text, ocr_pages = "", 0
            if len(ocr_text.strip()) >= 50:
                ocr_used = True
                text = normalize(ocr_text)
                pages = max(pages, ocr_pages)
                warnings.append(
                    f"该 PDF 为扫描件/图片型，已通过本地 OCR 识别 {ocr_pages} 页。"
                    "OCR 可能存在错字或漏行，涉及金额、日期、比例等关键信息请人工核对原件。"
                )
        if not ocr_used:
            warnings.append(
                "PDF 中可提取的文字过少，疑似扫描件或图片型 PDF，且本机 OCR 组件不可用或识别失败。"
                "建议上传原始电子版合同，以获得完整的字段抽取与风险研判。"
            )
    return text, pages, warnings, scan_suspect, ocr_used


def _docx(data: bytes) -> tuple[str, int, list[str], bool]:
    try:
        import docx
    except ImportError as e:
        raise ParseError("缺少 python-docx 依赖，无法解析 DOCX") from e
    try:
        d = docx.Document(io.BytesIO(data))
    except Exception as e:
        raise ParseError(f"DOCX 解析失败：{_docx_hint(e)}") from e
    lines = [p.text for p in d.paragraphs]
    for table in d.tables:
        for row in table.rows:
            cells = [c.text.strip().replace("\n", " ") for c in row.cells]
            line = " | ".join(c for c in cells if c)
            if line:
                lines.append(line)
    text = normalize("\n".join(lines))
    warnings = []
    scan_suspect = False
    if len(text) < 80:
        scan_suspect = True
        warnings.append(
            "DOCX 中可提取文字过少，可能只包含图片或扫描内容。图片型 DOCX 暂不支持 OCR，"
            "请提供可复制文字的版本。"
        )
    return text, 0, warnings, scan_suspect


def _txt(data: bytes) -> tuple[str, int, list[str], bool]:
    for enc in ("utf-8", "gb18030", "utf-16", "latin-1"):
        try:
            return normalize(data.decode(enc)), 0, [], False
        except (UnicodeDecodeError, UnicodeError):
            continue
    raise ParseError("无法识别 TXT 文件编码，请另存为 UTF-8 后重试。")


def parse(filename: str, data: bytes) -> dict:
    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED:
        raise ParseError(f"不支持的格式 {ext or '（无扩展名）'}，请上传 PDF / DOCX / TXT。")
    if not data:
        raise ParseError("文件为空。")
    if len(data) > MAX_BYTES:
        raise ParseError(f"文件超过 {MAX_BYTES // 1024 // 1024} MB 限制。")

    if ext == ".pdf":
        text, pages, warnings, scan_suspect, ocr_used = _pdf(data)
    elif ext == ".docx":
        text, pages, warnings, scan_suspect = _docx(data)
        ocr_used = False
    else:
        text, pages, warnings, scan_suspect = _txt(data)
        ocr_used = False

    if not text:
        if ext == ".pdf":
            raise ParseError("未能提取到文字：该 PDF 可能是扫描件或图片型 PDF，且本机 OCR 识别未获得有效内容。请上传文字清晰的电子版合同。")
        if ext == ".docx":
            raise ParseError("未能从 DOCX 提取到文字：文档可能只包含图片。本工具不含图片型 DOCX 的 OCR，请提供可复制文字的版本。")
        raise ParseError("未能从文件中提取到文字。")
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS]
        warnings.append(f"文本已截断至 {MAX_CHARS} 字以控制本地推理耗时。")

    title = ""
    for line in text.splitlines():
        line = line.strip()
        if 4 <= len(line) <= 60 and re.search(r"(合同|协议|契约|备忘录)", line):
            title = line
            break

    return {
        "filename": filename,
        "ext": ext.lstrip("."),
        "text": text,
        "chars": len(text),
        "pages": pages,
        "warnings": warnings,
        "scan_suspect": scan_suspect,
        "ocr_used": ocr_used,
        "title_guess": title,
        "size": len(data),
    }

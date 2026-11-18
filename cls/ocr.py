"""扫描件 OCR：pypdfium2 渲染 PDF 页面 + RapidOCR 本地中文识别。

全部识别在本机完成（onnxruntime CPU 推理），不联网、不上传。
依赖懒加载：未安装 OCR 依赖时自动降级为「提示用户上传文字版」。
"""
from __future__ import annotations

import threading
import time

OCR_MAX_PAGES = 30      # 单个 PDF 最多识别页数，控制耗时
OCR_SCALE = 2.0         # 渲染倍率（约 144dpi），兼顾速度与中文识别率
OCR_TIMEOUT_S = 180     # 单文件 OCR 总时长上限

_state = {"checked": False, "available": False}
_engine = None
_engine_lock = threading.Lock()


def available() -> bool:
    if not _state["checked"]:
        try:
            import pypdfium2  # noqa: F401
            import rapidocr_onnxruntime  # noqa: F401
            _state["available"] = True
        except ImportError:
            _state["available"] = False
        _state["checked"] = True
    return _state["available"]


def _get_engine():
    global _engine
    with _engine_lock:
        if _engine is None:
            from rapidocr_onnxruntime import RapidOCR
            _engine = RapidOCR()
    return _engine


def ocr_pdf(data: bytes, max_pages: int = OCR_MAX_PAGES) -> tuple[str, int]:
    """对无文字层 PDF 做 OCR，返回 (文本, 识别页数)。识别失败/超时返回 ("", 已尝试页数)。"""
    import pypdfium2 as pdfium

    engine = _get_engine()
    pdf = pdfium.PdfDocument(data)
    total = len(pdf)
    n = min(total, max_pages)
    lines: list[str] = []
    deadline = time.monotonic() + OCR_TIMEOUT_S
    done = 0
    try:
        for i in range(n):
            if time.monotonic() > deadline:
                break
            page = pdf[i]
            try:
                bitmap = page.render(scale=OCR_SCALE)
                img = bitmap.to_pil()
                result, _ = engine(img)
                if result:
                    lines.extend(r[1] for r in result)
            finally:
                page.close()
            done += 1
    finally:
        pdf.close()
    return "\n".join(lines), done

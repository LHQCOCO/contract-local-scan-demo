"""扫描件 OCR 与跨平台配置路径测试。

OCR 集成测试需要 pypdfium2 + rapidocr_onnxruntime + PIL + 中文字体；
任一缺失时自动跳过，不阻塞核心测试。
"""
from __future__ import annotations

import importlib
import io
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cls import ocr, parsing  # noqa: E402


def _make_scanned_pdf() -> bytes:
    """用 PIL 渲染一页中文合同文字图片，再包进 PDF（无文字层，模拟扫描件）。"""
    from PIL import Image, ImageDraw, ImageFont
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    font_path = "/System/Library/Fonts/PingFang.ttc"
    if not Path(font_path).exists():
        pytest.skip("缺少中文字体，跳过 OCR 集成测试")
    lines = [
        "软件开发服务合同",
        "甲方：星海科技有限公司",
        "乙方：云启软件有限公司",
        "合同总金额为人民币一百二十八万元整。",
        "乙方逾期交付的，每逾期一日应按合同总金额的百分之三支付违约金。",
        "因本合同产生的争议由甲方所在地人民法院管辖。",
    ]
    font = ImageFont.truetype(font_path, 28)
    img = Image.new("RGB", (1240, 640), "white")
    d = ImageDraw.Draw(img)
    y = 30
    for ln in lines:
        d.text((40, y), ln, fill="black", font=font)
        y += 56
    buf = io.BytesIO()
    img.save(buf, "PNG")
    buf.seek(0)
    pdf_buf = io.BytesIO()
    c = canvas.Canvas(pdf_buf, pagesize=(1240, 640))
    c.drawImage(ImageReader(buf), 0, 0, 1240, 640)
    c.showPage()
    c.save()
    return pdf_buf.getvalue()


def test_ocr_available_returns_bool():
    assert isinstance(ocr.available(), bool)


@pytest.mark.skipif(not ocr.available(), reason="OCR 依赖未安装")
def test_scanned_pdf_goes_through_ocr_pipeline():
    data = _make_scanned_pdf()
    r = parsing.parse("扫描合同.pdf", data)
    assert r["scan_suspect"] is True
    assert r["ocr_used"] is True
    assert "软件开发服务合同" in r["text"]
    assert "违约金" in r["text"]
    assert r["title_guess"] == "软件开发服务合同"
    assert any("OCR" in w for w in r["warnings"])


def test_text_layer_pdf_does_not_trigger_ocr():
    """正常文字版 PDF：不触发 OCR，scan_suspect/ocr_used 均为 False。"""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    pdf_buf = io.BytesIO()
    c = canvas.Canvas(pdf_buf, pagesize=A4)
    # 文字层内容需要超过 80 字，避免被误判为扫描件
    filler = "Service Agreement. " * 30
    c.drawString(40, 800, filler)
    c.showPage()
    c.save()
    r = parsing.parse("text.pdf", pdf_buf.getvalue())
    assert r["scan_suspect"] is False
    assert r["ocr_used"] is False
    assert not any("扫描" in w for w in r["warnings"])


def test_ocr_failure_falls_back_to_warning(monkeypatch):
    """OCR 抛异常时：不中断解析，降级为「OCR 不可用/失败」提示。"""
    if not ocr.available():
        pytest.skip("OCR 依赖未安装")
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    pdf_buf = io.BytesIO()
    c = canvas.Canvas(pdf_buf, pagesize=A4)
    c.showPage()
    c.save()

    monkeypatch.setattr(ocr, "ocr_pdf", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(parsing.ParseError, match="OCR"):
        parsing.parse("blank.pdf", pdf_buf.getvalue())


def _config_paths_under(platform: str, os_name: str) -> dict:
    """在子进程中模拟 frozen + 指定平台，读取 config 的目录解析结果。"""
    code = (
        "import sys, os, json\n"
        f"sys.frozen = True\n"
        f"sys._MEIPASS = '/pkg'\n"
        f"sys.platform = {platform!r}\n"
        f"os.name = {os_name!r}\n"
        "os.environ['APPDATA'] = r'C:\\\\Users\\\\u\\\\AppData\\\\Roaming'\n"
        "import importlib.util\n"
        "spec = importlib.util.spec_from_file_location('cfg', 'cls/config.py')\n"
        "m = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(m)\n"
        "print(json.dumps({'res': str(m.RESOURCE_DIR), 'data': str(m.DATA_DIR)}))\n"
    )
    env = dict(os.environ)
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, cwd=str(ROOT), env=env, timeout=30,
    )
    assert out.returncode == 0, out.stderr
    return json_loads_last_line(out.stdout)


def json_loads_last_line(stdout: str) -> dict:
    import json
    return json.loads(stdout.strip().splitlines()[-1])


def test_windows_frozen_paths_point_to_appdata():
    r = _config_paths_under("win32", "nt")
    assert r["res"].replace("\\", "/") == "/pkg"
    assert "AppData" in r["data"] and "ContractLocalScan" in r["data"]


def test_macos_frozen_paths_point_to_application_support():
    r = _config_paths_under("darwin", "posix")
    assert r["res"].replace("\\", "/") == "/pkg"
    assert "Application Support" in r["data"] and "ContractLocalScan" in r["data"]


def test_dev_mode_paths_unchanged():
    from cls import config
    assert config.DATA_DIR == ROOT / "data"
    assert config.RESOURCE_DIR == ROOT

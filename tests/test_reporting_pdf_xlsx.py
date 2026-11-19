"""build_pdf / build_xlsx：修订要点进入导出件的测试。"""
from __future__ import annotations

import io

import pytest
from openpyxl import load_workbook
from pypdf import PdfReader

from cls import reporting
from cls.revisions import REVISION_HEAD


def _pdf_text(data: bytes) -> str:
    return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages)


@pytest.mark.parametrize("field_count", [0, 3, 6, 9, 12, 15, 18])
def test_pdf_risk_heading_not_orphaned(sample_result, field_count):
    """关键条款表格高度变化时，“三、风险清单”不得单独留在页底。"""
    fields = [{"label": f"字段{i}", "value": f"取值{i}", "source": "rule", "hint": ""}
              for i in range(field_count)]
    data = reporting.build_pdf({**sample_result, "fields": fields})
    pages = PdfReader(io.BytesIO(data)).pages
    hits = [i for i, p in enumerate(pages) if "三、风险清单" in (p.extract_text() or "")]
    assert len(hits) == 1
    assert "条款原文" in (pages[hits[0]].extract_text() or "")


STATUTE = {
    "id": "civil-577", "law": "中华人民共和国民法典", "label": "第五百七十七条",
    "chapter": "第三编 合同 / 第八章 违约责任",
    "text": "当事人一方不履行合同义务或者履行合同义务不符合约定的，应当承担继续履行、采取补救措施或者赔偿损失等违约责任。",
}


@pytest.mark.parametrize("field_count", [0, 6, 12, 18])
@pytest.mark.parametrize("statute_count", [0, 6])
def test_pdf_section_headings_never_orphaned(sample_result, field_count, statute_count):
    """每个章节标题都必须与其后正文同页，任何分页组合下都不能孤立在页底。"""
    fields = [{"label": f"字段{i}", "value": f"取值{i}", "source": "rule", "hint": ""}
              for i in range(field_count)]
    statutes = [dict(STATUTE, id=f"a{i}", label=f"第{500 + i}条") for i in range(statute_count)]
    data = reporting.build_pdf({**sample_result, "fields": fields, "statutes": statutes})
    pages = PdfReader(io.BytesIO(data)).pages
    # 每个标题 → 其首块正文的特征文本
    sections = {
        "一、审阅结论": "测试用审阅结论",
        "二、关键条款抽取": "字段",
        "三、风险清单": "条款原文",
        "四、相关法条（本地知识库检索）": "第三编 合同" if statutes else "未检索到",
        "五、免责声明": "不构成正式法律意见",
    }
    for head, marker in sections.items():
        hits = [i for i, p in enumerate(pages) if head in (p.extract_text() or "")]
        assert len(hits) == 1, f"{head} 应恰好出现在一页"
        assert marker in (pages[hits[0]].extract_text() or ""), f"{head} 与正文被分页拆开"


def test_pdf_contains_revision_section(sample_result):
    data = reporting.build_pdf(sample_result)
    assert data.startswith(b"%PDF")
    text = _pdf_text(data)
    revision = sample_result["findings"][0]["revision"]
    assert REVISION_HEAD in text
    assert revision[:20] in text


def test_pdf_skips_revision_line_when_absent(sample_result):
    result = {**sample_result, "findings": [sample_result["findings"][3]]}
    data = reporting.build_pdf(result)
    assert data.startswith(b"%PDF")
    assert REVISION_HEAD not in _pdf_text(data)


def test_pdf_marks_missing_clause(sample_result):
    """缺失条款不再留空“条款原文”，而是明确标注为缺失条款。"""
    text = _pdf_text(reporting.build_pdf(sample_result))
    assert "缺失条款" in text
    assert "合同中未见相关条款" not in text


def test_pdf_has_no_unsupported_separator(sample_result):
    """CID 字体不支持“·”，导出件不应出现三角块乱码。"""
    text = _pdf_text(reporting.build_pdf(sample_result))
    assert "·" not in text


def test_xlsx_lists_location_and_action(sample_result):
    wb = load_workbook(io.BytesIO(reporting.build_xlsx(sample_result)))
    ws = wb["风险清单"]
    headers = [c.value for c in ws[1]]
    loc_col = headers.index("条款定位") + 1
    act_col = headers.index("处理方式") + 1
    ev_col = headers.index("条款原文") + 1
    # 第 2 行是命中原文的风险，应给出行号定位并要求修改
    assert str(ws.cell(row=2, column=loc_col).value).startswith("第 ")
    assert ws.cell(row=2, column=act_col).value == "修改条款"
    # 第 4 行是缺失条款（R12），应标注补充新增
    assert ws.cell(row=4, column=act_col).value == "补充新增"
    assert "缺失条款" in ws.cell(row=4, column=ev_col).value


def test_xlsx_has_revision_column(sample_result):
    wb = load_workbook(io.BytesIO(reporting.build_xlsx(sample_result)))
    ws = wb["风险清单"]
    headers = [c.value for c in ws[1]]
    assert REVISION_HEAD in headers
    col = headers.index(REVISION_HEAD) + 1
    assert ws.cell(row=2, column=col).value == sample_result["findings"][0]["revision"]
    assert ws.cell(row=3, column=col).value == sample_result["findings"][1]["revision"]
    # 无修订要点的风险留空，不应写入占位文本
    assert ws.cell(row=5, column=col).value in (None, "")


def test_xlsx_revision_rows_match_findings(sample_result):
    wb = load_workbook(io.BytesIO(reporting.build_xlsx(sample_result)))
    ws = wb["风险清单"]
    rows = list(ws.iter_rows(min_row=2, max_row=ws.max_row, values_only=True))
    assert len(rows) == len(sample_result["findings"])
    assert [r[2] for r in rows] == [f["title"] for f in sample_result["findings"]]

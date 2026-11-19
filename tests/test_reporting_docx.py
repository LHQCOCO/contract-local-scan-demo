"""build_docx：带 Word 批注的修订版合同导出测试。"""
from __future__ import annotations

import io
import zipfile

import pytest
from docx import Document
from docx.oxml.ns import qn

from cls import reporting
from cls.revisions import REVISION_HEAD
from conftest import ANCHORED_EVIDENCE, CROSS_EVIDENCE


def _open(data: bytes):
    return Document(io.BytesIO(data))


def _comment_ref_ids(paragraph):
    return [el.get(qn("w:id")) for el in paragraph._p.iter(qn("w:commentReference"))]


def _comments_by_id(doc):
    return {str(c.comment_id): c for c in doc.comments}


def _paragraph_with(doc, text):
    return next(p for p in doc.paragraphs if text in p.text)


def test_docx_anchors_every_finding(sample_result):
    """正文可定位的合同，全部命中都应生成批注，不再堆到文末附录。"""
    doc = _open(reporting.build_docx(sample_result))
    assert len(doc.comments) == len(sample_result["findings"]) == 4
    assert not any("附：未能定位" in p.text for p in doc.paragraphs)


def test_docx_comment_carries_advice_and_revision(sample_result):
    doc = _open(reporting.build_docx(sample_result))
    comments = {c.comment_id: c.text for c in doc.comments}
    target = next(t for t in comments.values() if "付款期限过长" in t)
    assert "【高风险】" in target
    assert "风险分析：" in target
    assert "修改建议：" in target
    assert REVISION_HEAD in target
    assert "法律依据：" in target
    assert sample_result["findings"][0]["revision"] in target
    assert all(c.author == "Contract LocalScan" for c in doc.comments)


def test_docx_anchors_comment_to_matching_paragraph(sample_result):
    doc = _open(reporting.build_docx(sample_result))
    para = _paragraph_with(doc, ANCHORED_EVIDENCE)
    ids = _comment_ref_ids(para)
    assert ids, "批注未锚定到证据所在段落"
    anchored_text = _comments_by_id(doc)[ids[0]].text
    assert "付款期限过长" in anchored_text


def test_docx_anchors_cross_paragraph_evidence(sample_result):
    doc = _open(reporting.build_docx(sample_result))
    para = _paragraph_with(doc, "第二条")
    ids = _comment_ref_ids(para)
    assert ids, "跨段落证据未被锚定"
    assert "验收标准主观" in _comments_by_id(doc)[ids[0]].text


def test_docx_anchors_missing_clause_to_related_paragraph(sample_result):
    """缺失条款型风险应锚定到语义相关的段落，并标注为缺失条款。"""
    doc = _open(reporting.build_docx(sample_result))
    para = _paragraph_with(doc, "因本合同产生的争议")
    ids = _comment_ref_ids(para)
    assert ids, "缺失条款未锚定到争议解决相关段落"
    texts = [_comments_by_id(doc)[i].text for i in ids]
    assert any("缺失条款" in t and "缺失争议解决条款" in t for t in texts)


def test_docx_anchors_short_evidence(sample_result):
    """短证据（如“90日”）也应锚定到所在条款，而不是落进附录。"""
    doc = _open(reporting.build_docx(sample_result))
    para = _paragraph_with(doc, "第一条")
    ids = _comment_ref_ids(para)
    assert ids
    assert any("账期表述模糊" in _comments_by_id(doc)[i].text for i in ids)


def test_docx_summary_matches_anchor_count(sample_result):
    doc = _open(reporting.build_docx(sample_result))
    assert doc.paragraphs[-1].text == (
        "共命中 4 条风险，其中 4 条已在正文生成批注（含 1 条缺失条款，已标注建议补充位置）。")


def test_docx_writes_word_comments_part(sample_result):
    data = reporting.build_docx(sample_result)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert "word/comments.xml" in zf.namelist()
        xml = zf.read("word/comments.xml").decode("utf-8")
    assert xml.count("<w:comment ") == 4
    assert REVISION_HEAD in xml


def test_docx_without_findings_is_valid(sample_result):
    sample_result = {**sample_result, "findings": []}
    doc = _open(reporting.build_docx(sample_result))
    assert len(doc.comments) == 0
    assert doc.paragraphs[-1].text == "共命中 0 条风险，其中 0 条已在正文生成批注。"


def test_docx_with_empty_text_falls_back_to_appendix(sample_result):
    """正文为空时无法锚定，保留附录兜底，且不影响导出。"""
    sample_result = {**sample_result, "text": "", "findings": sample_result["findings"][:1]}
    doc = _open(reporting.build_docx(sample_result))
    assert len(doc.comments) == 0
    assert any("附：未能定位到正文段落的风险批注" in p.text for p in doc.paragraphs)


def test_docx_comment_distribution_is_spread(sample_result):
    """同一段落不应承载全部批注，定位应分散到多个条款。"""
    doc = _open(reporting.build_docx(sample_result))
    hit = [i for i, p in enumerate(doc.paragraphs) if _comment_ref_ids(p)]
    assert len(set(hit)) >= 3


@pytest.mark.parametrize("filename", ["合同.pdf", "合同.docx", "合同.txt"])
def test_docx_body_keeps_original_paragraphs(sample_result, filename):
    sample_result = {**sample_result, "filename": filename}
    doc = _open(reporting.build_docx(sample_result))
    kept = [p.text for p in doc.paragraphs if "第一条" in p.text]
    assert kept and kept[0] == "第一条 甲方应在乙方交付并验收后90日内支付合同价款。"

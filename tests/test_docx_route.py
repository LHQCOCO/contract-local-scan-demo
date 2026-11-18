"""导出路由测试：/api/reviews/<rid>/docx。"""
from __future__ import annotations

import io
from urllib.parse import quote

from docx import Document

import app as app_module

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _client():
    return app_module.app.test_client()


def test_docx_route_returns_404_for_unknown_review(monkeypatch):
    monkeypatch.setattr(app_module.store, "get_review", lambda rid: None)
    resp = _client().get("/api/reviews/not-exist/docx")
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "记录不存在"


def test_docx_route_streams_annotated_contract(monkeypatch, sample_result):
    monkeypatch.setattr(app_module.store, "get_review", lambda rid: sample_result)
    resp = _client().get("/api/reviews/abc123/docx")
    assert resp.status_code == 200
    assert resp.mimetype == DOCX_MIME
    disposition = resp.headers["Content-Disposition"]
    assert "attachment" in disposition
    assert quote("软件开发服务合同_批注版.docx") in disposition
    doc = Document(io.BytesIO(resp.data))
    assert len(doc.comments) == 4


def test_docx_route_sanitizes_contract_name(monkeypatch, sample_result):
    result = {**sample_result, "contract_name": "合同/草稿?.docx"}
    monkeypatch.setattr(app_module.store, "get_review", lambda rid: result)
    resp = _client().get("/api/reviews/abc123/docx")
    disposition = resp.headers["Content-Disposition"]
    assert "/" not in disposition.split("filename*=")[-1]
    assert quote("合同草稿docx_批注版.docx") in disposition

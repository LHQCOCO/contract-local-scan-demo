"""中危加固回归：配置原子写、请求体校验、跨站写保护、模型输出兜底。"""
from __future__ import annotations

import json

import pytest

import app as app_module
from cls import config as config_module
from cls import extraction, llm


@pytest.fixture
def client():
    return app_module.app.test_client()


@pytest.fixture
def isolated_config(monkeypatch, tmp_path):
    """把配置指向临时文件，避免用例改写开发者本机的 data/config.json。"""
    path = tmp_path / "config.json"
    monkeypatch.setattr(config_module, "CONFIG_PATH", path)
    return path


def test_save_config_writes_atomically(isolated_config):
    config_module.save_config({"export_mode": "browser"})
    assert json.loads(isolated_config.read_text(encoding="utf-8"))["export_mode"] == "browser"
    # 临时文件必须已被 os.replace 消掉，不能留在数据目录里
    assert not isolated_config.with_name(isolated_config.name + ".tmp").exists()


def test_corrupt_config_is_backed_up(isolated_config):
    isolated_config.write_text("{not json", encoding="utf-8")
    cfg = config_module.load_config()
    assert cfg["export_mode"] == config_module.DEFAULT_CONFIG["export_mode"]
    assert isolated_config.with_name(isolated_config.name + ".broken").exists()


def test_non_dict_body_returns_400(client, isolated_config):
    r = client.post("/api/config", data="[1, 2]", content_type="application/json")
    assert r.status_code == 400


def test_consent_requires_explicit_boolean(client, isolated_config):
    # 空对象不能被当成「撤回授权」静默执行
    r = client.post("/api/consent", json={})
    assert r.status_code == 400
    assert config_module.load_config()["deploy_consent"] is None


def test_local_origin_check_is_exact():
    assert app_module._is_local_origin("http://127.0.0.1:8765")
    assert app_module._is_local_origin("http://localhost:8765")
    assert not app_module._is_local_origin("http://localhost.evil.com")
    assert not app_module._is_local_origin("https://evil.example")


def test_cross_site_write_is_rejected(client):
    r = client.post("/api/config", json={}, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403


def test_loads_rejects_non_object_payload():
    with pytest.raises(llm.LocalModelError):
        llm._loads("[1, 2, 3]")


def test_loads_accepts_object_with_markdown_fence():
    data = llm._loads('```json\n{"contract_name": "测试合同"}\n```')
    assert data == {"contract_name": "测试合同"}


def test_merge_keeps_longer_rule_text_for_detail_fields():
    values, sources = extraction.merge(
        {"payment_terms": "验收后 30 日内付款"},
        {"payment_terms": "甲方应在乙方交付并验收合格后 30 日内支付合同价款的 80%，余款于质保期满后支付。"},
    )
    assert sources["payment_terms"] == "rule"
    assert "质保期满" in values["payment_terms"]


def test_merge_prefers_longer_model_text():
    values, sources = extraction.merge(
        {"payment_terms": "甲方在验收后 30 日内支付全部合同价款，乙方需先开具等额发票。"},
        {"payment_terms": "验收后付款"},
    )
    assert sources["payment_terms"] == "llm"
    assert "发票" in values["payment_terms"]

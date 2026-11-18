"""自定义规则与法典包：切条、规范句识别、阈值接管、引擎并列、知识库增删与 API 全流程。"""
from __future__ import annotations

import copy
import io

import pytest

import app as app_module
from cls import config, custom, risks
from cls.kb import KB


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    """包存储与配置重定向到临时目录；KB 内部状态做快照，结束后整体恢复。

    remove_custom 采用墓碑删除（不物理压缩 articles），逐条回滚不可靠，
    快照恢复可让后续 test_kb 等对「分片行数 / id 唯一」的断言不受影响。
    """
    monkeypatch.setattr(custom, "CUSTOM_DIR", tmp_path / "custom")
    monkeypatch.setattr(custom, "INDEX_PATH", tmp_path / "custom" / "index.json")
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    snap = {
        "articles": len(KB.articles), "laws": len(KB.laws), "tf": len(KB._tf),
        "by_id": dict(KB.by_id), "law_names": dict(KB.law_names),
        "by_short": {k: list(v) for k, v in KB._by_short.items()},
        "aliases": {k: list(v) for k, v in KB._aliases.items()},
        "alias_sorted": list(KB._alias_sorted),
        "df": copy.copy(KB._df), "len_sum": KB._len_sum, "n": KB._n, "avg": KB._avg,
        "dead": set(KB._dead), "custom_idx": dict(KB._custom_idx),
        "extra_rules": list(risks._EXTRA_RULES),
    }
    yield tmp_path
    del KB.articles[snap["articles"]:]
    del KB.laws[snap["laws"]:]
    del KB._tf[snap["tf"]:]
    del KB._lens[snap["tf"]:]
    KB.by_id, KB.law_names = snap["by_id"], snap["law_names"]
    KB._by_short, KB._aliases = snap["by_short"], snap["aliases"]
    KB._alias_sorted = snap["alias_sorted"]
    KB._df, KB._len_sum, KB._n, KB._avg = snap["df"], snap["len_sum"], snap["n"], snap["avg"]
    KB._dead, KB._custom_idx = snap["dead"], snap["custom_idx"]
    KB._cache.clear()
    risks.register_custom(snap["extra_rules"])
    app_module._APPLIED_PACKS.clear()


# ---------- 切条 ----------

def test_split_articles_by_cn_heads():
    text = "第一条 双方应当书面签约。\n第二条 合同不得约定霸王条款。\n第十二条 违约金不得超过总额15%。"
    arts = custom.split_articles(text)
    assert [a["label"] for a in arts] == ["第一条", "第二条", "第十二条"]
    assert arts[2]["article_no"] == "12"
    assert "15%" in arts[2]["text"]


def test_split_articles_fallback_paragraphs():
    text = "第一款 内容甲\n\n第二款 内容乙"
    arts = custom.split_articles(text)
    assert len(arts) == 2
    assert arts[0]["label"] == "条目 1"


def test_split_articles_rejects_over_limit():
    text = "\n\n".join(f"第{i}段 内容" for i in range(custom.MAX_ITEMS + 1))
    with pytest.raises(custom.CustomError):
        custom.split_articles(text)


# ---------- 阈值识别 ----------

def test_detect_threshold_cap_penalty():
    hit = custom.detect_threshold("对外签订合同的违约金不得超过合同总额的15%")
    assert hit and hit["param_key"] == "penalty_high_pct" and hit["value"] == 15.0
    assert hit["rule_id"] == "R04"


def test_detect_threshold_floor_probation_salary():
    hit = custom.detect_threshold("试用期工资不得低于转正工资的80%")
    assert hit and hit["param_key"] == "probation_salary_pct" and hit["value"] == 80.0


def test_detect_threshold_guard_blocks_daily_rate():
    # 「按日万分之五」是日利率口径，不能接管「合同总价 %」参数
    assert custom.detect_threshold("违约金按日万分之五计算，不得超过法定上限") is None


def test_detect_threshold_months_normalized():
    hit = custom.detect_threshold("竞业限制期限不得超过2年")
    assert hit and hit["param_key"] == "compete_max_months" and hit["value"] == 24


def test_detect_threshold_out_of_range_rejected():
    assert custom.detect_threshold("定金不得超过合同总价款的150%") is None


# ---------- 规范句派生 ----------

def _derive(text: str):
    arts = custom.split_articles(text)
    ids = {a["article_no"]: f"ent-packx-{a['article_no']}" for a in arts}
    return custom.derive_rules(arts, "pack_x", "测试制度", ids)


def test_derive_forbid_rule():
    drafts, _ = _derive("第十条 合同中严禁约定守约方放弃追偿权利。")
    assert len(drafts) == 1
    r = drafts[0]
    assert r["mode"] == "match" and r["draft"] is True
    assert r["patterns"] and "放弃追偿权利" in r["patterns"][0]
    assert r["basis"]  # 出处指回来源条文


def test_derive_mandatory_rule():
    drafts, _ = _derive("第十一条 合同应当载明知识产权归属与使用范围。")
    assert len(drafts) == 1
    assert drafts[0]["mode"] == "absence"
    assert drafts[0]["markers"]


def test_derive_threshold_rule_and_override():
    drafts, overrides = _derive("第十二条 违约金不得超过合同总额的15%。")
    assert overrides == {"penalty_high_pct": 15.0}
    assert len(drafts) == 1
    assert drafts[0]["mode"] == "threshold"
    assert drafts[0]["threshold"]["param_key"] == "penalty_high_pct"
    assert drafts[0]["related"] == ["R04"]


def test_derive_plain_narrative_skipped():
    drafts, overrides = _derive("第十三条 本制度自发布之日起施行，由法务部负责解释。")
    assert drafts == [] and overrides == {}


# ---------- JSON 校验 ----------

def test_validate_rule_rejects_redos():
    errors = []
    r = custom.validate_rule(
        {"title": "坏模式", "mode": "match", "patterns": ["(a+)+$"]}, "p1", 1, errors)
    assert r is None and any("性能风险" in e for e in errors)


def test_validate_rule_threshold_custom_param():
    errors = []
    r = custom.validate_rule(
        {"title": "内部利率上限", "mode": "threshold",
         "threshold": {"param_key": "custom:inner_rate", "op": ">", "value": 10,
                       "value_pattern": r"利率\D{0,6}(?P<v>\d+(?:\.\d+)?)\s*[%％]"}},
        "p1", 1, errors)
    assert r and not errors
    assert r["threshold"]["value"] == 10.0


def test_validate_statute_bounds():
    errors = []
    assert custom.validate_statute({"text": "短"}, "p1", 1, "ENT", "制度", errors) is None
    s = custom.validate_statute({"text": "这是一条足够长的制度条文内容。", "label": "第一条"},
                                "p1", 1, "ENT", "制度", errors)
    assert s["law_id"] == "ent-p1" and s["jurisdiction"] == "ENT"


# ---------- 阈值接管与回退 ----------

def _pack(pid="pack_a", enabled=True, overrides=None):
    return {
        "id": pid, "name": f"包{pid}", "kind": "policy", "jurisdiction": "ENT",
        "enabled": enabled, "imported_at": 1.0, "source_file": "x.txt",
        "statutes": [], "rules": [], "threshold_overrides": overrides or {},
    }


def test_recompute_params_takeover_and_rollback(isolated):
    custom.put_pack(_pack(overrides={"penalty_high_pct": 15}))
    eff = custom.recompute_params()
    assert eff["penalty_high_pct"] == 15
    cfg = config.load_config()
    assert cfg["threshold_overrides"]["penalty_high_pct"]["pack_id"] == "pack_a"
    custom.set_enabled("pack_a", False)
    eff = custom.recompute_params()
    assert eff["penalty_high_pct"] == config.DEFAULT_CONFIG["rule_params"]["penalty_high_pct"]
    assert config.load_config()["threshold_overrides"] == {}


def test_recompute_params_later_pack_wins(isolated):
    custom.put_pack(_pack("pack_a", overrides={"penalty_high_pct": 15}))
    custom.put_pack(_pack("pack_b", overrides={"penalty_high_pct": 12}))
    # imported_at 相同也必须有确定顺序：后导入者优先
    custom.put_pack({**_pack("pack_b", overrides={"penalty_high_pct": 12}), "imported_at": 2.0})
    eff = custom.recompute_params()
    assert eff["penalty_high_pct"] == 12
    assert config.load_config()["threshold_overrides"]["penalty_high_pct"]["pack_id"] == "pack_b"


def test_pack_threshold_diff_marks_replaces(isolated):
    custom.put_pack(_pack(overrides={"penalty_high_pct": 15}))
    custom.recompute_params()
    diff = custom.pack_threshold_diff({"penalty_high_pct": 12})
    assert diff[0]["from"] == 15 and diff[0]["to"] == 12
    assert diff[0]["replaces"] == "包pack_a"


# ---------- 引擎并列与分歧 ----------

def _custom_rule(**kw):
    base = {"id": "C-packx-001", "title": "自定义禁止分期", "category": "企业自定义",
            "severity": "high", "mode": "match", "patterns": ["分期付款"],
            "related": [], "basis": [], "analysis": "a", "advice": "b", "pack_name": "集团制度"}
    base.update(kw)
    return base


def test_engine_runs_custom_alongside_builtin(isolated):
    risks.register_custom([_custom_rule()])
    findings, _, _ = risks.evaluate("本合同价款分三期支付，采用分期付款方式。", {}, "买卖合同")
    hits = [f for f in findings if f["id"] == "C-packx-001"]
    assert hits and hits[0]["source"] == {"type": "custom", "pack_name": "集团制度"}
    assert all(f["source"]["type"] in {"builtin", "custom"} for f in findings)
    risks.register_custom([])


def test_engine_custom_exception_isolated(isolated):
    bad = _custom_rule(patterns=["("])  # 编译不过的模式
    risks.register_custom([bad])
    findings, _, _ = risks.evaluate("任何文本", {}, "买卖合同")
    assert not any(f["id"] == "C-packx-001" for f in findings)
    assert any("C-packx-001" in w for w in risks.LAST_WARNINGS)
    risks.register_custom([])


def test_engine_custom_threshold_uses_params(isolated):
    rule = _custom_rule(id="C-packx-002", mode="threshold", patterns=None,
                        threshold={"param_key": "penalty_high_pct", "op": ">",
                                   "value_pattern": r"违约金\D{0,10}(?P<v>\d+(?:\.\d+)?)\s*[%％]"})
    rule.pop("patterns")
    risks.register_custom([rule])
    text = "违约方应支付违约金，金额为合同总额的20%。"
    hit = [f for f in risks.evaluate(text, {}, "买卖合同", params={"penalty_high_pct": 15})[0]
           if f["id"] == "C-packx-002"]
    assert hit, "阈值 15 时 20% 应命中"
    miss = [f for f in risks.evaluate(text, {}, "买卖合同", params={"penalty_high_pct": 30})[0]
            if f["id"] == "C-packx-002"]
    assert not miss, "阈值 30 时 20% 不应命中"
    risks.register_custom([])


def test_mark_divergence_via_related():
    findings = [
        {"id": "R04", "title": "内置结论", "severity": "medium", "evidence": "违约金为合同总额的20%",
         "source": {"type": "builtin"}, "related": []},
        {"id": "C-p-001", "title": "自定义结论", "severity": "high", "evidence": "违约金为合同总额的20%",
         "source": {"type": "custom", "pack_name": "集团制度"}, "related": ["R04"]},
    ]
    out = risks._mark_divergence([dict(f) for f in findings])
    assert all(f.get("divergence") for f in out)
    assert out[1]["divergence_peers"][0]["id"] == "R04"


def test_mark_divergence_same_severity_not_flagged():
    findings = [
        {"id": "R04", "title": "内置", "severity": "high", "evidence": "违约金为合同总额的20%",
         "source": {"type": "builtin"}, "related": []},
        {"id": "C-p-001", "title": "自定义", "severity": "high", "evidence": "违约金为合同总额的20%",
         "source": {"type": "custom", "pack_name": "集团制度"}, "related": ["R04"]},
    ]
    out = risks._mark_divergence([dict(f) for f in findings])
    assert not any(f.get("divergence") for f in out)


def test_mark_divergence_via_evidence_overlap():
    ev = "任何一方逾期付款的，应按合同总额每日万分之五支付违约金"
    findings = [
        {"id": "R05", "title": "内置", "severity": "low", "evidence": ev,
         "source": {"type": "builtin"}, "related": []},
        {"id": "C-p-002", "title": "自定义", "severity": "high", "evidence": ev,
         "source": {"type": "custom", "pack_name": "集团制度"}, "related": []},
    ]
    out = risks._mark_divergence([dict(f) for f in findings])
    assert all(f.get("divergence") for f in out)
    assert out[0]["divergence_group"] == out[1]["divergence_group"]


# ---------- 知识库增量增删 ----------

def test_kb_add_and_remove_custom(isolated):
    arts = [{"id": "ent-packt-1", "law_id": "ent-packt", "law": "集团采购制度",
             "label": "第十二条", "article_no": "12", "chapter": "",
             "text": "违约金不得超过合同总额的百分之十五。", "jurisdiction": "ENT",
             "source_pack": "pack_t"}]
    laws = [{"id": "ent-packt", "name": "集团采购制度", "jurisdiction": "ENT",
             "source_pack": "pack_t"}]
    before = KB.total
    KB.add_custom("pack_t", arts, laws)
    assert KB.total == before + 1
    assert KB.by_id["ent-packt-1"]["law"] == "集团采购制度"
    assert KB.remove_custom("pack_t") is True
    assert KB.total == before
    assert "ent-packt-1" not in KB.by_id
    assert KB.remove_custom("pack_t") is False  # 重复删除幂等


# ---------- API 全流程 ----------

DOC = "\n".join([
    "集团合同管理办法",
    "第一条 为规范对外签约，制定本办法。",
    "第二条 违约金不得超过合同总额的15%。",
    "第三条 合同中严禁约定守约方放弃追偿权利。",
    "第四条 合同应当载明争议解决方式与管辖。",
])


def _post_import(client, name="集团合同管理办法", llm="true"):
    return client.post("/api/custom/packs", data={
        "file": (io.BytesIO(DOC.encode("utf-8")), "rules.txt"),
        "name": name, "jurisdiction": "ENT", "kind": "policy", "llm_rules": llm,
    }, content_type="multipart/form-data")


def test_api_import_preview_confirm_flow(isolated):
    client = app_module.app.test_client()
    r = _post_import(client)  # llm_rules=true 但未授权，自动降级为规范句识别
    assert r.status_code == 200
    d = r.get_json()
    assert d["statutes"]["total"] == 4
    modes = {x["mode"] for x in d["rules"]}
    assert {"threshold", "match", "absence"} <= modes
    assert all(not x["llm_derived"] for x in d["rules"])  # 未授权时无模型识别
    assert d["thresholds"][0]["param_key"] == "penalty_high_pct"
    assert d["thresholds"][0]["from"] == 30 and d["thresholds"][0]["to"] == 15.0

    pid = d["preview_id"]
    r2 = client.post(f"/api/custom/packs/confirm/{pid}", json={
        "rules": [x["id"] for x in d["rules"]],
        "params": ["penalty_high_pct"],
    })
    assert r2.status_code == 200
    d2 = r2.get_json()
    assert d2["ok"] and d2["counts"]["rules"] == 3
    assert d2["rule_params"]["penalty_high_pct"] == 15
    assert d2["threshold_overrides"]["penalty_high_pct"]["pack_name"] == "集团合同管理办法"
    # 预览一次性消费，重复确认 404
    assert client.post(f"/api/custom/packs/confirm/{pid}", json={}).status_code == 404


def test_api_confirm_partial_selection(isolated):
    client = app_module.app.test_client()
    d = _post_import(client).get_json()
    keep = [x["id"] for x in d["rules"] if x["mode"] == "match"]
    r = client.post(f"/api/custom/packs/confirm/{d['preview_id']}",
                    json={"rules": keep, "params": []})
    d2 = r.get_json()
    assert d2["counts"]["rules"] == len(keep) == 1
    assert d2["rule_params"]["penalty_high_pct"] == 30  # 未勾选阈值则不接管


def test_api_confirmed_rule_hits_evaluation(isolated):
    client = app_module.app.test_client()
    d = _post_import(client).get_json()
    client.post(f"/api/custom/packs/confirm/{d['preview_id']}", json={})
    text = "采购合同\n第十条 双方同意守约方放弃追偿权利。"
    findings, _, _ = risks.evaluate(text, {}, "买卖合同",
                                    params=config.load_config().get("rule_params"))
    custom_hits = [f for f in findings if f["source"]["type"] == "custom"]
    assert custom_hits and custom_hits[0]["source"]["pack_name"] == "集团合同管理办法"


def test_api_disable_rolls_back_threshold(isolated):
    client = app_module.app.test_client()
    d = _post_import(client).get_json()
    d2 = client.post(f"/api/custom/packs/confirm/{d['preview_id']}", json={}).get_json()
    pid = d2["pack"]["id"]
    r = client.patch(f"/api/custom/packs/{pid}", json={"enabled": False})
    assert r.status_code == 200
    body = r.get_json()
    assert body["rule_params"]["penalty_high_pct"] == 30
    assert body["threshold_overrides"] == {}
    # 重新启用恢复接管
    r2 = client.patch(f"/api/custom/packs/{pid}", json={"enabled": True})
    assert r2.get_json()["rule_params"]["penalty_high_pct"] == 15


def test_api_delete_pack(isolated):
    client = app_module.app.test_client()
    d = _post_import(client).get_json()
    pid = client.post(f"/api/custom/packs/confirm/{d['preview_id']}", json={}).get_json()["pack"]["id"]
    assert client.delete(f"/api/custom/packs/{pid}").status_code == 200
    lst = client.get("/api/custom/packs").get_json()
    assert lst["packs"] == [] and lst["stats"]["rules"] == 0
    assert client.delete(f"/api/custom/packs/{pid}").status_code == 404


def test_api_json_import_flow(isolated):
    client = app_module.app.test_client()
    obj = {
        "statutes": [{"label": "第十二条", "article_no": "12",
                      "text": "对外签订合同的违约金不得超过合同总额的 15%。"}],
        "rules": [{"title": "禁止单方任意解除", "severity": "medium", "mode": "match",
                   "patterns": ["单方任意解除合同"]}],
    }
    import json as _json
    r = client.post("/api/custom/packs", data={
        "file": (io.BytesIO(_json.dumps(obj).encode("utf-8")), "pack.json"),
        "name": "JSON清单包", "jurisdiction": "ENT", "kind": "checklist",
    }, content_type="multipart/form-data")
    assert r.status_code == 200
    d = r.get_json()
    assert d["statutes"]["total"] == 1 and len(d["rules"]) == 1
    r2 = client.post(f"/api/custom/packs/confirm/{d['preview_id']}", json={})
    assert r2.get_json()["counts"] == {"statutes": 1, "rules": 1}


def test_api_import_rejects_bad_ext(isolated):
    client = app_module.app.test_client()
    r = client.post("/api/custom/packs", data={
        "file": (io.BytesIO(b"x"), "x.exe")}, content_type="multipart/form-data")
    assert r.status_code == 400


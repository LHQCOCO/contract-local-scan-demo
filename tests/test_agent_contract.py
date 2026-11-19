"""合同场景：工具落地、引用核查、端到端运行与 trace 脱敏。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cls import config
from cls.agent.context import RunContext
from cls.agent.evidence import EvidenceChecker
from cls.agent.registry import ToolRegistry
from cls.agent.runtime import AgentRuntime
from cls.agent.trace import Trace
from cls.agent.types import Budget
from cls.kb import KB
from cls.scenarios import CONTRACT_SCENARIO
from cls.scenarios.contract.documents import DOCS
from cls.scenarios.contract.roles import ContractRoleRunner

CONTRACT = "\n".join([
    "软件开发服务合同",
    "甲方：星海科技有限公司",
    "乙方：云图软件有限公司",
    "第一条 甲方应在乙方交付并验收后90日内支付合同价款。",
    "第二条 乙方交付的软件验收标准为甲方主观满意。",
    "第三条 因本合同产生的争议由甲方所在地人民法院管辖。",
    "第四条 乙方逾期交付的，每日按合同总价的0.5%支付违约金。",
])

EVENT_NAMES = {
    "run.start", "plan.created", "step.start", "tool.call", "tool.result",
    "role.message", "verify.result", "step.end", "run.end",
}


def make_ctx(tmp_path, budget=None):
    trace = Trace(tmp_path / "t.jsonl", "r1")
    return RunContext("r1", trace, budget or Budget(), state={}), trace


def make_registry():
    reg = ToolRegistry()
    CONTRACT_SCENARIO.register_tools(reg)
    return reg


@pytest.fixture()
def doc_id():
    DOCS.clear()
    return DOCS.add(CONTRACT, "合同.txt", {"title_guess": "软件开发服务合同"})


def _rule_findings(doc_id):
    ctx, _ = make_ctx(Path("/tmp"))
    result = make_registry().call("run_rules", {"doc_id": doc_id}, ctx)
    assert result.ok
    return result


def test_run_rules_spans_match_text(doc_id):
    result = _rule_findings(doc_id)
    doc = DOCS.get(doc_id)
    checked = 0
    for f in result.data["findings"]:
        if f["span"] is None:
            # absence / check 类命中没有可定位的原文，引用必须留空
            assert f["quote"] == "", f["rule_id"]
            continue
        check = EvidenceChecker.check_span(doc.text, f["quote"], f["span"]["start"])
        assert check.ok, (f["rule_id"], check.code, check.detail)
        checked += 1
    assert checked >= 1


def test_get_statute_unknown_id(doc_id):
    ctx, _ = make_ctx(Path("/tmp"))
    result = make_registry().call("get_statute", {"law_id": "law-does-not-exist"}, ctx)
    assert not result.ok and result.error_code == "SOURCE_NOT_FOUND"


def test_verifier_rejects_unknown_citation(doc_id, tmp_path):
    reg = make_registry()
    ctx, _ = make_ctx(tmp_path)
    runner = ContractRoleRunner(reg)
    draft = {
        "doc_id": doc_id,
        "findings": [{
            "rule_id": "R05", "severity": "high",
            "quote": "甲方应在乙方交付并验收后90日内支付合同价款",
            "span": {"start": CONTRACT.find("甲方应在乙方交付并验收后90日内支付合同价款")},
            "law_ids": ["law-does-not-exist"], "top5": ["law-does-not-exist"],
        }],
    }
    out = runner.run("verifier", {"draft": draft}, ctx)
    assert out["verdicts"][0]["verdict"] == "reject"
    assert out["verdicts"][0]["reason_code"] == "CITATION_INVALID"

    # trace 里必须能回放「哪条被打回、为什么」，且只含规则 ID 与原因码
    rejected = [e for e in ctx.trace.dump()
                if e["event"] == "verify.result" and e.get("verdict") == "reject"]
    assert rejected and rejected[0]["reason_code"] == "CITATION_INVALID"
    assert rejected[0]["rule_id"] == "R05"


def test_verifier_downgrades_out_of_top5(doc_id, tmp_path):
    reg = make_registry()
    ctx, _ = make_ctx(tmp_path)
    runner = ContractRoleRunner(reg)
    real_id = KB.articles[0]["id"]
    draft = {
        "doc_id": doc_id,
        "findings": [{
            "rule_id": "R05", "severity": "high",
            "quote": "甲方应在乙方交付并验收后90日内支付合同价款",
            "span": {"start": CONTRACT.find("甲方应在乙方交付并验收后90日内支付合同价款")},
            "law_ids": [real_id], "top5": ["another-law-id"],
        }],
    }
    out = runner.run("verifier", {"draft": draft}, ctx)
    assert out["verdicts"][0]["verdict"] == "downgrade"
    assert out["verdicts"][0]["reason_code"] == "CITATION_INVALID"


def test_verifier_rejects_tampered_span(doc_id, tmp_path):
    reg = make_registry()
    ctx, _ = make_ctx(tmp_path)
    runner = ContractRoleRunner(reg)
    real_id = KB.articles[0]["id"]
    draft = {
        "doc_id": doc_id,
        "findings": [{
            "rule_id": "R05", "severity": "high",
            "quote": "这段文字并不在合同里",
            "span": {"start": 0},
            "law_ids": [real_id], "top5": [real_id],
        }],
    }
    out = runner.run("verifier", {"draft": draft}, ctx)
    assert out["verdicts"][0]["verdict"] == "reject"
    assert out["verdicts"][0]["reason_code"] == "EVIDENCE_MISSING"


def test_plan_template_only_uses_registered_tools(doc_id):
    task = CONTRACT_SCENARIO.build_task({"doc_id": doc_id})
    plan = CONTRACT_SCENARIO.planner.plan(task, [], Budget())
    names = {spec.name for spec, _ in CONTRACT_SCENARIO.tools}
    for step in plan.steps:
        if step.tool:
            assert step.tool in names
        else:
            assert step.role in CONTRACT_SCENARIO.roles


def test_agent_runtime_end_to_end(doc_id, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    result = AgentRuntime(CONTRACT_SCENARIO, data_dir=tmp_path).run(
        {"doc_id": doc_id}, budget=Budget(max_steps=20, max_tool_calls=60),
        run_id="e2e1", trace_path=tmp_path / "e2e.jsonl")

    assert result.status in ("ok", "partial")
    summary = result.artifacts["summary"]
    assert summary["score"] > 0

    events = Trace(tmp_path / "e2e.jsonl", "e2e1").dump()
    assert len(events) >= 8
    assert EVENT_NAMES <= {e["event"] for e in events}

    # 能按 step 还原「调了哪些工具」，能按规则还原「谁被确认/降级」
    tools_called = {e["tool"] for e in events if e["event"] == "tool.call"}
    assert {"run_rules", "locate_clause", "search_statute",
            "get_statute", "propose_revision"} <= tools_called
    assert any(e["event"] == "verify.result" and e.get("rule_id") for e in events)

    # 每条被确认的结论都必须能在原文比对成功
    verdicts = {v["rule_id"]: v for v in summary["verdicts"]}
    confirmed = {r: v for r, v in verdicts.items() if v["verdict"] == "confirmed"}
    assert confirmed, "端到端至少应有一条通过核查的结论"

    doc = DOCS.get(doc_id)
    for f in result.artifacts["s4"]["findings"]:
        if f["rule_id"] in confirmed:
            check = EvidenceChecker.check_span(doc.text, f["quote"], f["span"]["start"])
            assert check.ok, (f["rule_id"], check.code)

    # trace 只留摘要：不得出现正文片段与绝对路径
    blob = json.dumps(events, ensure_ascii=False)
    assert "甲方应在乙方交付并验收后90日内支付合同价款" not in blob
    assert CONTRACT[:10] not in blob
    assert "/Users/" not in blob and "/tmp/" not in blob
    for event in events:
        assert "run_id" in event and "seq" in event and "ts" in event


def test_agent_runtime_stops_on_budget(doc_id, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    result = AgentRuntime(CONTRACT_SCENARIO, data_dir=tmp_path).run(
        {"doc_id": doc_id}, budget=Budget(max_steps=1),
        run_id="e2e2", trace_path=tmp_path / "e2e2.jsonl")
    assert result.stop_reason == "BUDGET_EXCEEDED"
    assert result.status == "partial"
    assert "s1" in result.artifacts


def test_export_report_writes_file(doc_id, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "EXPORTS_DIR", tmp_path / "exports")
    ctx, _ = make_ctx(tmp_path)
    reg = make_registry()
    assert reg.call("run_rules", {"doc_id": doc_id}, ctx).ok
    out = reg.call("export_report", {"doc_id": doc_id, "fmt": "xlsx"}, ctx)
    assert out.ok, out.message
    assert out.data["bytes"] > 0 and len(out.data["sha256"]) == 64
    assert out.data["path"].endswith(".xlsx")


def test_export_tool_requires_scope():
    reg = make_registry()
    assert "export_report" not in {s.name for s in reg.specs(())}
    assert "export_report" in {s.name for s in reg.specs(("export",))}


def test_export_requires_run_rules_first(tmp_path):
    DOCS.clear()
    fresh = DOCS.add("一份没有跑过规则的合同文本。" * 5)
    ctx, _ = make_ctx(tmp_path)
    out = make_registry().call("export_report", {"doc_id": fresh, "fmt": "pdf"}, ctx)
    assert not out.ok and out.error_code == "INVALID_ARGS"

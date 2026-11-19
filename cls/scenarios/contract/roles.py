"""合同场景的两个角色：reviewer 汇总、verifier 逐条核查。"""
from __future__ import annotations

from ...agent.evidence import EvidenceChecker
from ...agent.roles import Role, call_tool_for_role
from .documents import DOCS

REVIEWER_ROLE = Role(
    name="reviewer",
    system_prompt=(
        "你是合同审阅人。只依据工具返回的规则命中与法条检索结果汇总风险，"
        "每条结论必须带原文引用与位置，不得引入检索结果之外的法条编号。"
    ),
    allowed_tools=("run_rules", "locate_clause", "search_statute",
                   "get_statute", "propose_revision"),
    output_schema={
        "type": "object", "required": ["findings"],
        "properties": {
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["rule_id", "severity", "quote", "span", "law_ids"],
                    "properties": {
                        "rule_id": {"type": "string"},
                        "severity": {"type": "string"},
                        "quote": {"type": "string"},
                        "span": {"type": ["object", "null"]},
                        "law_ids": {"type": "array", "items": {"type": "string"}},
                    },
                    "additionalProperties": True,
                },
            },
        },
        "additionalProperties": True,
    },
    max_tokens=700,
)

VERIFIER_ROLE = Role(
    name="verifier",
    system_prompt=(
        "你是合同审阅的核查人。逐条核对引用是否能在原文比对、法条是否存在、"
        "法条是否来自该条风险的检索前五名；不通过时给出固定 reason_code。"
    ),
    allowed_tools=("locate_clause", "get_statute"),
    output_schema={
        "type": "object", "required": ["verdicts"],
        "properties": {
            "verdicts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["rule_id", "verdict"],
                    "properties": {
                        "rule_id": {"type": "string"},
                        "verdict": {"type": "string",
                                    "enum": ["confirmed", "downgrade", "reject"]},
                        "reason_code": {"type": "string"},
                    },
                    "additionalProperties": True,
                },
            },
        },
        "additionalProperties": True,
    },
    max_tokens=700,
)

_ROLES = {"reviewer": REVIEWER_ROLE, "verifier": VERIFIER_ROLE}


class ContractRoleRunner:
    """确定性角色执行：所有结论都来自工具返回，模型只作为可选增强。"""

    def __init__(self, registry, adapter=None):
        self.registry = registry
        self.adapter = adapter

    def run(self, role: str, inputs: dict, ctx) -> dict:
        if role == "reviewer":
            return self._review(inputs, ctx)
        if role == "verifier":
            return self._verify(inputs, ctx)
        return {"_error": "ROLE_ERROR"}

    # ---------------------------------------------------------------- 汇总
    def _review(self, inputs: dict, ctx) -> dict:
        results = ctx.state.get("results", {})
        base = results.get("s1") or inputs.get("s1") or {}
        doc_id = base.get("doc_id") or ctx.state.get("task", {}).get("doc_id", "")
        ctype = base.get("contract_type", "")
        findings = [f for f in base.get("findings", [])
                    if f.get("severity") in ("high", "medium")
                    and f.get("mode") != "absence"
                    and f.get("quote") and f.get("span")]

        merged = []
        for f in findings:
            # locate_clause 只用于确认条款仍可定位，不拿返回的上下文窗口覆盖原始引用，
            # 否则引用与 span 会错位，核查阶段必然判为漂移。
            call_tool_for_role(
                REVIEWER_ROLE, self.registry, "locate_clause",
                {"doc_id": doc_id, "query": (f.get("quote") or f.get("title"))[:40],
                 "top_k": 1}, ctx)
            span = f.get("span")
            quote = f.get("quote", "")

            hits_res = call_tool_for_role(
                REVIEWER_ROLE, self.registry, "search_statute",
                {"query": f'{f.get("title", "")} {(quote or "")[:60]}'.strip(),
                 "contract_type": ctype, "top_k": 5}, ctx)
            hits = hits_res.data.get("hits", []) if hits_res.ok else []
            top5 = [h["law_id"] for h in hits]
            law_ids = [x for x in f.get("basis_law_ids", []) if x in top5] or top5[:1]

            revision = call_tool_for_role(
                REVIEWER_ROLE, self.registry, "propose_revision",
                {"doc_id": doc_id, "rule_id": f["rule_id"], "span": span}, ctx)

            merged.append({
                "rule_id": f["rule_id"], "title": f.get("title", ""),
                "severity": f.get("severity"), "mode": f.get("mode"),
                "quote": quote, "span": span, "law_ids": law_ids, "top5": top5,
                "revision": revision.data.get("suggestion", "") if revision.ok else "",
                "risk_if_unfixed": (revision.data.get("risk_if_unfixed", "")
                                    if revision.ok else f.get("message", "")),
            })

        ctx.emit("role.message", role="reviewer", ok=True,
                 count=len(merged), fallback=True, tokens_in=0, tokens_out=0)
        return {"doc_id": doc_id, "contract_type": ctype,
                "findings": merged, "fallback": True}

    # ---------------------------------------------------------------- 核查
    def _verify(self, inputs: dict, ctx) -> dict:
        draft = inputs.get("draft") or {}
        if not draft:
            draft = ctx.state.get("results", {}).get("s4") or {}
        doc = DOCS.get(draft.get("doc_id", ""))
        verdicts = []

        for f in draft.get("findings", []):
            missing = [k for k in ("rule_id", "severity", "quote", "span", "law_ids")
                       if k not in f]
            if missing:
                verdicts.append({"rule_id": f.get("rule_id", ""),
                                 "verdict": "reject",
                                 "reason_code": "SCHEMA_INVALID",
                                 "detail": f"缺少字段：{','.join(missing)}"})
                continue

            span = f.get("span") or {}
            check = EvidenceChecker.check_span(
                doc.text if doc else "", f.get("quote", ""), span.get("start"))
            if not check.ok:
                verdicts.append({"rule_id": f["rule_id"], "verdict": "reject",
                                 "reason_code": "EVIDENCE_MISSING",
                                 "detail": check.detail})
                continue

            law_ids = f.get("law_ids") or []
            if not law_ids:
                verdicts.append({"rule_id": f["rule_id"], "verdict": "reject",
                                 "reason_code": "CITATION_INVALID",
                                 "detail": "没有引用任何法条"})
                continue

            missing_source = False
            for law_id in law_ids:
                got = call_tool_for_role(VERIFIER_ROLE, self.registry, "get_statute",
                                         {"law_id": law_id}, ctx)
                if not got.ok:
                    missing_source = True
                    break
            if missing_source:
                verdicts.append({"rule_id": f["rule_id"], "verdict": "reject",
                                 "reason_code": "CITATION_INVALID",
                                 "detail": "引用的法条在本地知识库中不存在"})
                continue

            outside = [x for x in law_ids if x not in (f.get("top5") or [])]
            if outside:
                verdicts.append({"rule_id": f["rule_id"], "verdict": "downgrade",
                                 "reason_code": "CITATION_INVALID",
                                 "detail": "引用的法条不在该条检索前五名内"})
                continue

            verdicts.append({"rule_id": f["rule_id"], "verdict": "confirmed",
                             "reason_code": ""})

        confirmed = sum(1 for v in verdicts if v["verdict"] == "confirmed")
        downgraded = sum(1 for v in verdicts if v["verdict"] == "downgrade")
        rejected = sum(1 for v in verdicts if v["verdict"] == "reject")
        # 逐条落 trace：只记规则 ID、结论与原因码，不含任何原文，
        # 回放时能直接回答「哪条被降级/打回、为什么」。
        for v in verdicts:
            ctx.emit("verify.result", role="verifier", ok=v["verdict"] == "confirmed",
                     rule_id=v["rule_id"], verdict=v["verdict"],
                     reason_code=v.get("reason_code", ""))
        ctx.emit("verify.result", role="verifier", ok=rejected == 0,
                 count=len(verdicts), confirmed=confirmed,
                 downgraded=downgraded, rejected=rejected)
        return {"doc_id": draft.get("doc_id", ""), "verdicts": verdicts,
                "confirmed": confirmed, "downgraded": downgraded,
                "rejected": rejected, "fallback": True}

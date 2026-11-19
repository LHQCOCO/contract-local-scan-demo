"""合同场景工具：只包装现有业务函数，不复制规则/检索/导出逻辑。"""
from __future__ import annotations

import hashlib
import re

from ... import config, extraction, reporting, risks
from ...agent.evidence import EvidenceChecker
from ...agent.types import INVALID_ARGS, SOURCE_NOT_FOUND, ToolResult, ToolSpec
from ...kb import KB
from ...revisions import build_revision
from .documents import DOCS

_MODE_BY_RULE = {r["id"]: r.get("mode", "match") for r in risks.RULES}

_MATCH_FIELDS = {
    "rule_id": "string", "title": "string", "severity": "string",
    "quote": "string", "span": "object",
}


def _compact_map(text: str) -> tuple[str, list[int]]:
    chars, index = [], []
    for i, ch in enumerate(text):
        if not ch.isspace():
            chars.append(ch)
            index.append(i)
    return "".join(chars), index


def _span_of(text: str, quote: str) -> dict | None:
    q = (quote or "").strip()
    if not q or q.startswith("—（") or q.startswith("相关条款中未见"):
        return None
    pos = text.find(q)
    if pos >= 0:
        return {"start": pos, "end": pos + len(q)}
    compact, mapping = _compact_map(text)
    compact_q, _ = _compact_map(q)
    pos = compact.find(compact_q)
    if pos < 0:
        return None
    return {"start": mapping[pos], "end": mapping[pos + len(compact_q) - 1] + 1}


def _section_of(text: str, pos: int) -> str:
    window = text[max(0, pos - 240):pos]
    found = re.findall(r"第[一二三四五六七八九十百零〇0-9]+条[^\n。]{0,20}", window)
    return found[-1].strip() if found else ""


def _doc(doc_id: str):
    return DOCS.get(doc_id or "")


def run_rules(args: dict, ctx) -> ToolResult:
    doc = _doc(args["doc_id"])
    if doc is None:
        return ToolResult(False, error_code=SOURCE_NOT_FOUND, message="文档未加载")

    values = extraction.extract(doc.text)[1]
    ctype = extraction.detect_type(doc.text)
    cfg = config.load_config()
    raw_findings, score, level = risks.evaluate(
        doc.text, values, ctype,
        params=cfg.get("rule_params"), industry_rules=cfg.get("industry_rules"),
    )

    categories = args.get("categories") or None
    selected = [f for f in raw_findings
                if not categories or f.get("category") in categories]

    findings = []
    for f in selected:
        quote = f.get("evidence") or ""
        mode = _MODE_BY_RULE.get(f["id"], "match")
        span = None if mode == "absence" else _span_of(doc.text, quote)
        if span is None:
            # check/absence 类命中回答的是「条款是否缺失」，没有可定位的原文引用，
            # 统一留空，保证「有引用 ⇒ 一定有 span」这一不变量。
            quote = ""
        findings.append({
            "rule_id": f["id"], "title": f["title"], "category": f["category"],
            "severity": f["severity"], "mode": mode,
            "message": f["analysis"], "advice": f["advice"],
            "quote": quote,
            "span": span,
            "basis_law_ids": [b["id"] for b in f.get("basis", [])],
        })

    # 供 reviewer / 导出复用，避免二次跑规则
    doc.state["base"] = {
        "text": doc.text, "values": values, "contract_type": ctype,
        "findings": raw_findings, "score": score, "level": level,
        "title_guess": doc.meta.get("title_guess", ""),
        "summary": extraction.summary_text(values, raw_findings),
    }
    return ToolResult(True, data={
        "doc_id": doc.doc_id, "contract_type": ctype,
        "findings": findings, "score": score, "level": level,
        "total_findings": len(raw_findings),
    })


def locate_clause(args: dict, ctx) -> ToolResult:
    doc = _doc(args["doc_id"])
    if doc is None:
        return ToolResult(False, error_code=SOURCE_NOT_FOUND, message="文档未加载")
    query = (args.get("query") or "").strip()
    top_k = int(args.get("top_k", 3))
    if not query or top_k <= 0:
        return ToolResult(True, data={"matches": []})

    matches, cursor = [], 0
    while len(matches) < top_k:
        pos = doc.text.find(query, cursor)
        if pos < 0:
            break
        matches.append({
            "span": {"start": pos, "end": pos + len(query)},
            "quote": doc.text[max(0, pos - 32):pos + len(query) + 32],
            "section": _section_of(doc.text, pos),
        })
        cursor = pos + len(query)

    if not matches:
        compact, mapping = _compact_map(doc.text)
        compact_q, _ = _compact_map(query)
        cpos = compact.find(compact_q)
        if cpos >= 0:
            start = mapping[cpos]
            end = mapping[cpos + len(compact_q) - 1] + 1
            matches.append({
                "span": {"start": start, "end": end},
                "quote": doc.text[max(0, start - 32):end + 32],
                "section": _section_of(doc.text, start),
            })
    return ToolResult(True, data={"matches": matches})


def search_statute(args: dict, ctx) -> ToolResult:
    query = (args.get("query") or "").strip()
    if not query:
        return ToolResult(True, data={"hits": []})
    top_k = int(args.get("top_k", 5))
    ctype = (args.get("contract_type") or "").strip()
    articles = (KB.search_for_contract(query, ctype, top_k=top_k)
                if ctype else KB.search(query, top_k=top_k))
    hits = [{
        "law_id": a["id"],
        "title": f'{a.get("law", "")} {a.get("label", "")}'.strip(),
        "text": a.get("text", ""),
        "score": float(a.get("score", 0)),
    } for a in articles]
    return ToolResult(True, data={"hits": hits})


def get_statute(args: dict, ctx) -> ToolResult:
    law_id = (args.get("law_id") or "").strip()
    art = KB.by_id.get(law_id)
    if not art:
        return ToolResult(False, error_code=SOURCE_NOT_FOUND,
                          message=f"法条不存在：{law_id}")
    return ToolResult(True, data={
        "law_id": art["id"],
        "title": f'{art.get("law", "")} {art.get("label", "")}'.strip(),
        "text": art.get("text", ""),
    })


def propose_revision(args: dict, ctx) -> ToolResult:
    doc = _doc(args["doc_id"])
    if doc is None:
        return ToolResult(False, error_code=SOURCE_NOT_FOUND, message="文档未加载")
    rule_id = args["rule_id"]
    rule = next((r for r in risks.RULES if r["id"] == rule_id), None)
    if rule is None:
        return ToolResult(False, error_code=SOURCE_NOT_FOUND,
                          message=f"规则不存在：{rule_id}")

    span = args.get("span")
    if span:
        check = EvidenceChecker.check_span(doc.text, doc.text[span["start"]:span["end"]],
                                           offset=span.get("start"))
        if not check.ok:
            return ToolResult(False, error_code=INVALID_ARGS,
                              data={"details": [{"path": "$.span",
                                                 "message": check.detail}]},
                              message="span 与原文不一致")

    basis = [KB.by_id[bid] for bid in (rule.get("basis") or []) if bid in KB.by_id]
    return ToolResult(True, data={
        # 只回传检索到的条文原文，由调用方提示“需法务复核”；不生成条款文本。
        "suggestion": build_revision(basis),
        "basis_law_ids": list(rule.get("basis") or []),
        "risk_if_unfixed": rule.get("analysis", ""),
    })


def export_report(args: dict, ctx) -> ToolResult:
    doc = _doc(args["doc_id"])
    if doc is None:
        return ToolResult(False, error_code=SOURCE_NOT_FOUND, message="文档未加载")
    base = doc.state.get("base")
    if not base:
        return ToolResult(False, error_code=INVALID_ARGS,
                          message="请先运行 run_rules 生成审阅结果")
    fmt = args.get("fmt", "pdf")
    findings = base["findings"]
    result = {
        "filename": base.get("title_guess") or "合同",
        "ext": doc.meta.get("ext", ""),
        "chars": len(doc.text),
        "scan_suspect": doc.meta.get("scan_suspect", False),
        "ocr_used": doc.meta.get("ocr_used", False),
        "pages": doc.meta.get("pages", 0),
        "size": doc.meta.get("size", 0),
        "title_guess": base.get("title_guess", ""),
        "text": doc.text,
        "warnings": [],
        "fields": extraction.build_fields(base["values"],
                                          base["values"].get("sources", {})),
        "values": base["values"],
        "contract_type": base["contract_type"],
        "contract_name": base["values"].get("contract_name")
                         or base.get("title_guess") or "合同",
        "party_a": base["values"].get("party_a", ""),
        "party_b": base["values"].get("party_b", ""),
        "amount_total": base["values"].get("amount_total", ""),
        "findings": findings,
        "score": base["score"],
        "level": base["level"],
        "counts": risks.counts(findings),
        "statutes": [],
        "summary": base["summary"],
        "summary_source": "rule",
        "llm_used": False,
        "llm_error": "",
        "engine": {"rules": risks.total_rules(), "kb": KB.total, "model": "规则引擎"},
        "created_at": 0,
    }
    builder = {"pdf": reporting.build_pdf, "docx": reporting.build_docx,
               "xlsx": reporting.build_xlsx}[fmt]
    data = builder(result)

    config.EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = config.EXPORTS_DIR / f"{ctx.run_id}-{doc.doc_id}.{fmt}"
    path.write_bytes(data)
    return ToolResult(True, data={
        "path": str(path),
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    })


TOOL_SPECS: list[tuple[ToolSpec, callable]] = [
    (ToolSpec(
        name="run_rules", description="运行内置风险规则，返回 findings 与风险指数",
        params={"type": "object", "required": ["doc_id"],
                "properties": {"doc_id": {"type": "string"},
                               "categories": {"type": "array",
                                              "items": {"type": "string"}}},
                "additionalProperties": False},
        returns={"type": "object"},
        side_effect="none", timeout_s=60.0,
    ), run_rules),
    (ToolSpec(
        name="locate_clause", description="在合同原文中定位条款片段",
        params={"type": "object", "required": ["doc_id", "query"],
                "properties": {"doc_id": {"type": "string"},
                               "query": {"type": "string"},
                               "top_k": {"type": "integer"}},
                "additionalProperties": False},
        returns={"type": "object"}, timeout_s=10.0,
    ), locate_clause),
    (ToolSpec(
        name="search_statute", description="检索本地法规知识库",
        params={"type": "object", "required": ["query"],
                "properties": {"query": {"type": "string"},
                               "contract_type": {"type": "string"},
                               "top_k": {"type": "integer"}},
                "additionalProperties": False},
        returns={"type": "object"}, timeout_s=60.0,
    ), search_statute),
    (ToolSpec(
        name="get_statute", description="按 ID 读取法条原文",
        params={"type": "object", "required": ["law_id"],
                "properties": {"law_id": {"type": "string"}},
                "additionalProperties": False},
        returns={"type": "object"}, timeout_s=10.0,
    ), get_statute),
    (ToolSpec(
        name="propose_revision", description="根据规则生成修订建议",
        params={"type": "object", "required": ["doc_id", "rule_id"],
                "properties": {"doc_id": {"type": "string"},
                               "rule_id": {"type": "string"},
                               "span": {"type": ["object", "null"]}},
                "additionalProperties": False},
        returns={"type": "object"}, timeout_s=10.0, max_calls_per_run=40,
    ), propose_revision),
    (ToolSpec(
        name="export_report", description="导出审阅报告（pdf/docx/xlsx）",
        params={"type": "object", "required": ["doc_id", "fmt"],
                "properties": {"doc_id": {"type": "string"},
                               "fmt": {"type": "string",
                                       "enum": ["pdf", "docx", "xlsx"]}},
                "additionalProperties": False},
        returns={"type": "object"},
        side_effect="local_write", timeout_s=60.0,
        max_calls_per_run=3, scopes=("export",),
    ), export_report),
]

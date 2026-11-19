"""规则引擎评测：一条命令产出检出率、clean 对照、证据可回溯率、引用可解析率与耗时。

用法：python3 -m eval.run_eval
输出：eval/out/metrics.json、eval/out/report.md
"""
from __future__ import annotations

import json
import platform
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

from cls import risks
from cls.extraction import detect_type

from .inject import ROOT, build_cases, load_corpus

OUT = ROOT / "out"


def _norm(s: str) -> str:
    return re.sub(r"[\s…—()（）\"'“”]", "", s or "")


def _bigrams(s: str) -> set[str]:
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _overlap(a: str, b: str) -> float:
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    if a in b or b in a:
        return 1.0
    sa, sb = _bigrams(a), _bigrams(b)
    return len(sa & sb) / max(1, len(sa | sb))


def _timed_eval(text: str, ctype: str, repeats: int = 1) -> tuple[list[dict], float]:
    times = []
    findings = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        findings, _, _ = risks.evaluate(text, {}, ctype)
        times.append((time.perf_counter() - t0) * 1000)
    return findings, median(times)


def _cpu() -> str:
    try:
        return subprocess.check_output(
            ["sysctl", "-n", "machdep.cpu.brand_string"], text=True).strip()
    except Exception:  # noqa: BLE001
        return platform.processor() or "unknown"


def _mem_gb() -> float | None:
    try:
        out = subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True).strip()
        return round(int(out) / 1024 ** 3, 1)
    except Exception:  # noqa: BLE001
        return None


def _commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT.parent, text=True).strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def _pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    xs = sorted(values)
    idx = min(len(xs) - 1, max(0, int(round(q * (len(xs) - 1)))))
    return round(xs[idx], 2)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    docs = load_corpus()
    cases, skipped = build_cases()

    rules = list(risks.RULES) + list(risks._EXTRA_RULES)
    kb = risks.KB.by_id
    cite_ok = sum(1 for r in rules if r["basis"] and all(b in kb for b in r["basis"]))
    cite_total = sum(1 for r in rules if r["basis"])

    clean_rows = []
    clean_ids_by_file = {}
    doc_times = []
    for doc in docs:
        findings, ms = _timed_eval(doc["text"], doc["contract_type"], repeats=3)
        doc_times.append(ms)
        clean_ids_by_file[doc["file"]] = {f["id"] for f in findings}
        clean_rows.append({
            "file": doc["file"],
            "title": doc["title"],
            "ctype": doc["contract_type"],
            "auto_type": detect_type(doc["text"]),
            "findings": len(findings),
            "rule_ids": [f["id"] for f in findings],
            "ms": round(ms, 2),
        })

    case_rows = []
    for case in cases:
        findings, ms = _timed_eval(case["text"], case["ctype"], repeats=1)
        ids = {f["id"] for f in findings}
        detected = case["rule"] in ids
        gold = next((f for f in findings if f["id"] == case["rule"]), None)
        clean_ids = clean_ids_by_file[case["doc_file"]]
        extra = sorted(ids - clean_ids - {case["rule"]})
        evidence = (gold or {}).get("evidence", "") if gold else ""
        traceable = bool(detected and case["kind"] == "inject"
                         and evidence and _norm(evidence) in _norm(case["text"]))
        span_ok = bool(traceable and _overlap(evidence, case["gold_span"]) >= 0.5)
        basis = next((r["basis"] for r in rules if r["id"] == case["rule"]), [])
        citation_ok = bool(gold and basis and all(b in kb for b in basis))
        case_rows.append({
            "case_id": case["case_id"],
            "template_id": case["template_id"],
            "rule": case["rule"],
            "kind": case["kind"],
            "ctype": case["ctype"],
            "doc": case["doc_title"],
            "note": case["note"],
            "detected": detected,
            "evidence": evidence,
            "evidence_traceable": traceable,
            "evidence_matches_gold_span": span_ok,
            "citation_resolvable": citation_ok,
            "extra_findings": extra,
            "ms": round(ms, 2),
        })
        del findings

    total = len(case_rows)
    detected = sum(1 for r in case_rows if r["detected"])
    inject_rows = [r for r in case_rows if r["kind"] == "inject"]
    remove_rows = [r for r in case_rows if r["kind"] == "remove"]
    trace_base = [r for r in inject_rows if r["detected"]]
    trace_ok = sum(1 for r in trace_base if r["evidence_traceable"])
    span_ok = sum(1 for r in trace_base if r["evidence_matches_gold_span"])
    cite_ok_cases = sum(1 for r in case_rows if r["citation_resolvable"] and r["detected"])
    extra_total = sum(len(r["extra_findings"]) for r in case_rows)
    extra_rules = Counter(x for r in case_rows for x in r["extra_findings"])

    by_rule: dict[str, dict] = defaultdict(lambda: {"total": 0, "hit": 0, "kinds": set()})
    for r in case_rows:
        by_rule[r["rule"]]["total"] += 1
        by_rule[r["rule"]]["hit"] += int(r["detected"])
        by_rule[r["rule"]]["kinds"].add(r["kind"])

    variant_times = [r["ms"] for r in case_rows]
    clean_total = sum(r["findings"] for r in clean_rows)
    type_ok = sum(1 for r in clean_rows if r["ctype"] == r["auto_type"])
    mode_of = {r["id"]: r["mode"] for r in rules}
    clean_by_mode = Counter(
        ("absence" if mode_of.get(rid) == "absence" else "check/match")
        for row in clean_rows for rid in row["rule_ids"])
    inject_hit = sum(1 for r in inject_rows if r["detected"])
    remove_hit = sum(1 for r in remove_rows if r["detected"])

    metrics = {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "command": "make eval",
        "repo_commit": _commit(),
        "mode": "rule（不调用模型）",
        "rule_count": len(rules),
        "corpus": clean_rows,
        "summary": {
            "documents": len(docs),
            "cases": total,
            "detected": detected,
            "detection_rate": round(detected / total, 4) if total else 0.0,
            "inject_cases": len(inject_rows),
            "remove_cases": len(remove_rows),
            "inject_detection": f"{inject_hit}/{len(inject_rows)}",
            "remove_detection": f"{remove_hit}/{len(remove_rows)}",
            "clean_by_mode": dict(clean_by_mode),
            "clean_findings_total": clean_total,
            "clean_findings_per_doc": round(clean_total / len(docs), 2) if docs else 0.0,
            "extra_findings_total": extra_total,
            "extra_findings_per_case": round(extra_total / total, 2) if total else 0.0,
            "evidence_traceable": f"{trace_ok}/{len(trace_base)}",
            "evidence_traceable_rate": round(trace_ok / len(trace_base), 4) if trace_base else 0.0,
            "evidence_matches_gold_span": f"{span_ok}/{len(trace_base)}",
            "evidence_span_rate": round(span_ok / len(trace_base), 4) if trace_base else 0.0,
            "citation_rules_resolvable": f"{cite_ok}/{cite_total}",
            "citation_case_rate": round(cite_ok_cases / detected, 4) if detected else 0.0,
            "auto_type_accuracy": f"{type_ok}/{len(docs)}",
            "eval_ms_p50": round(median(variant_times), 2) if variant_times else 0.0,
            "eval_ms_p95": _pct(variant_times, 0.95),
            "clean_ms_p50": round(median(doc_times), 2) if doc_times else 0.0,
        },
        "by_rule": {k: {"total": v["total"], "hit": v["hit"]} for k, v in sorted(by_rule.items())},
        "extra_rule_histogram": dict(extra_rules.most_common()),
        "cases": case_rows,
        "skipped": skipped,
        "environment": {
            "os": platform.platform(),
            "cpu": _cpu(),
            "memory_gb": _mem_gb(),
            "python": sys.version.split()[0],
            "model": "无（mode=rule，规则引擎不调用模型）",
        },
    }
    (OUT / "metrics.json").write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    s = metrics["summary"]
    env = metrics["environment"]
    lines = [
        "# 规则引擎评测报告",
        "",
        f"- 生成时间：{metrics['generated_at']}",
        f"- 命令：`{metrics['command']}`（commit `{metrics['repo_commit']}`）",
        f"- 模式：{metrics['mode']}；规则数：{metrics['rule_count']}",
        f"- 环境：{env['os']}；{env['cpu']}；内存 {env['memory_gb']} GB；Python {env['python']}",
        "",
        "## 摘要",
        "",
        "| 指标 | 数值 |",
        "|---|---|",
        f"| 语料（公开示范文本） | {s['documents']} 份 |",
        f"| 变体（注入 + 删除） | {s['cases']} 个（注入 {s['inject_cases']}，删除 {s['remove_cases']}） |",
        f"| 缺陷检出率 | {s['detected']}/{s['cases']} = {s['detection_rate']:.2%}（注入 {s['inject_detection']}，删除 {s['remove_detection']}） |",
        f"| clean 对照告警 | {s['clean_findings_total']} 条，{s['clean_findings_per_doc']} 条/份（缺失类 {s['clean_by_mode'].get('absence', 0)}，其余 {s['clean_by_mode'].get('check/match', 0)}） |",
        f"| 注入后新增非目标告警 | {s['extra_findings_total']} 条，{s['extra_findings_per_case']} 条/变体 |",
        f"| 证据可回溯率（注入类） | {s['evidence_traceable']} = {s['evidence_traceable_rate']:.2%} |",
        f"| 证据命中注入条款 | {s['evidence_matches_gold_span']} = {s['evidence_span_rate']:.2%} |",
        f"| 法条引用可解析（规则集） | {s['citation_rules_resolvable']} |",
        f"| 单次 evaluate 耗时 | P50 {s['eval_ms_p50']} ms / P95 {s['eval_ms_p95']} ms |",
        f"| 自动合同类型识别 | {s['auto_type_accuracy']}（人工标注为口径） |",
        "",
        "## 语料与 clean 对照",
        "",
        "| 文件 | 标注类型 | 自动识别 | clean 告警数 | 耗时(ms) |",
        "|---|---|---|---|---|",
    ]
    for r in clean_rows:
        lines.append(f"| {r['title']} | {r['ctype']} | {r['auto_type']} | {r['findings']} | {r['ms']} |")
    lines += ["", "## 按规则检出", "", "| 规则 | 检出/变体 |", "|---|---|"]
    for rid, v in metrics["by_rule"].items():
        lines.append(f"| {rid} | {v['hit']}/{v['total']} |")
    lines += [
        "",
        "## 注入后新增非目标告警（按规则）",
        "",
        f"合计 {extra_total} 条。",
        "",
        "| 规则 | 次数 |",
        "|---|---|",
    ]
    for rid, n in metrics["extra_rule_histogram"].items():
        lines.append(f"| {rid} | {n} |")
    misses = [r for r in case_rows if not r["detected"]]
    lines += ["", "## 漏检明细", ""]
    if misses:
        lines += ["| 模板 | 规则 | 底稿 | 缺陷形态 |", "|---|---|---|---|"]
        for r in misses:
            lines.append(f"| {r['template_id']} | {r['rule']} | {r['doc']} | {r['note']} |")
    else:
        lines.append("无。")
    lines += ["", "## 未纳入的组合", "", "| 模板 | 底稿 | 原因 |", "|---|---|---|"]
    for r in skipped:
        lines.append(f"| {r['template_id']} ({r['rule']}) | {r['doc_title']} | {r['reason']} |")
    lines += [
        "",
        "## 口径与局限",
        "",
        "- 检出率 = 注入/删除缺陷后 gold 规则出现在 findings 中的比例；不涉及语义等价判断。",
        "- 缺陷形态由评测者按规则语义构造，并由 gold 自检确认可判定。因此该检出率衡量的是规则对已定义缺陷形态的覆盖，"
        "不是真实合同上的召回率，也不应外推为准确率。",
        "- clean 对照为未修改的示范文本；其告警多为示范文本自带的空白条款与缺失条款，未逐条人工判定真伪。",
        "- 注入后新增非目标告警只统计与 clean 对照相比的新增规则，不区分该告警是否由注入句合理连带触发，因此偏保守。",
        "- 证据可回溯率只统计注入类（删除类规则的证据按设计为空），并要求证据句能在变体原文中定位。",
        "- 本次只评测 `mode=rule` 路径；未调用模型，故无模型版本与 token 数据。",
        "",
        f"明细见 `{OUT / 'metrics.json'}`。",
    ]
    (OUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"语料 {s['documents']} 份，变体 {s['cases']} 个，检出率 {s['detection_rate']:.2%}")
    print(f"报告：{OUT / 'report.md'}")
    print(f"明细：{OUT / 'metrics.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

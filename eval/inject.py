"""把缺陷模板落到语料上，生成 gold 与变体文本。"""
from __future__ import annotations

import json
import re
from pathlib import Path

from cls import risks

from .templates import TEMPLATES

ROOT = Path(__file__).resolve().parent


def load_corpus() -> list[dict]:
    manifest = json.loads((ROOT / "corpus" / "manifest.json").read_text(encoding="utf-8"))
    docs = []
    for item in manifest["documents"]:
        path = ROOT / "corpus" / item["file"]
        docs.append({**item, "text": path.read_text(encoding="utf-8")})
    return docs


def _sentence_spans(text: str) -> list[tuple[int, int]]:
    spans, start = [], 0
    for m in re.finditer(r"[。；\n]", text):
        spans.append((start, m.end()))
        start = m.end()
    if start < len(text):
        spans.append((start, len(text)))
    return spans


def remove_marked(text: str, markers: list[str]) -> tuple[str, list[tuple[int, int]]]:
    """删除命中 markers 的句子，返回新文本与被删区间（原文本坐标）。"""
    spans = [(s, e) for s, e in _sentence_spans(text)
             if any(re.search(m, text[s:e]) for m in markers)]
    if not spans:
        return text, []
    out, cursor = [], 0
    for s, e in spans:
        out.append(text[cursor:s])
        cursor = e
    out.append(text[cursor:])
    return "".join(out), spans


def build_cases() -> tuple[list[dict], list[dict]]:
    """返回 (cases, skipped)。

    cases 中每条是一份变体文本 + gold 规则；skipped 记录不适用的模板组合。
    """
    docs = load_corpus()
    for doc in docs:
        findings, _, _ = risks.evaluate(doc["text"], {}, doc["contract_type"])
        doc["clean_ids"] = {f["id"] for f in findings}
        doc["clean_findings"] = findings

    cases, skipped = [], []
    for doc in docs:
        for tpl in TEMPLATES:
            if doc["contract_type"] not in tpl["types"]:
                continue
            base = {
                "template_id": tpl["id"],
                "rule": tpl["rule"],
                "kind": tpl["kind"],
                "note": tpl["note"],
                "doc_file": doc["file"],
                "doc_title": doc["title"],
                "ctype": doc["contract_type"],
            }
            if tpl["rule"] in doc["clean_ids"]:
                skipped.append({**base, "reason": "clean 对照已触发该规则，无法归因于注入"})
                continue
            if tpl["kind"] == "inject":
                clause = tpl["clause"]
                text = doc["text"] + "\n补充约定\n" + clause + "\n"
                gold_span = clause
                removed = []
            else:
                text, removed = remove_marked(doc["text"], tpl["markers"])
                if not removed:
                    skipped.append({**base, "reason": "底稿中没有可删除的对应条款"})
                    continue
                gold_span = "".join(doc["text"][s:e] for s, e in removed).strip()
            findings, _, _ = risks.evaluate(text, {}, doc["contract_type"])
            # 注入句已明确写入缺陷，规则未命中即记为漏检，不再跳过
            cases.append({
                **base,
                "case_id": f'{tpl["id"]}-{Path(doc["file"]).stem}',
                "text": text,
                "gold_span": gold_span,
                "removed_spans": removed,
            })
    return cases, skipped

#!/usr/bin/env python3
"""构建国际规则语料 data/statutes_intl.json（英文原文 + 中文要点）。

来源：
- CISG：UNCITRAL 官方英文电子书（2010）
- 纽约公约：UNCITRAL 官方英文电子书
- 仲裁示范法：UNCITRAL 官方英文电子书（2006 年修订）
- Incoterms 2020：条款要点摘要（非 ICC 原文，仅用于检索提示）
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.request import Request, urlopen

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "sources" / "intl"
OUT = ROOT / "data" / "statutes_intl.json"

DOCS = [
    {
        "id": "cisg", "short": "CISG", "name": "联合国国际货物销售合同公约",
        "category": "国际货物买卖", "effective": "1988-01-01", "expected": 101,
        "url": "https://uncitral.un.org/sites/default/files/media-documents/uncitral/en/19-09951_e_ebook.pdf",
        "marker": r"^Article\s+([0-9]+)\s*$",
    },
    {
        "id": "nyc", "short": "纽约公约", "name": "承认及执行外国仲裁裁决公约",
        "category": "国际仲裁", "effective": "1959-06-07", "expected": 16,
        "url": "https://uncitral.un.org/sites/default/files/media-documents/uncitral/en/new-york-convention-e.pdf",
        "marker": r"^Article\s+([IVXLC]+)\s*\.?\s*$",
    },
    {
        "id": "mlarb", "short": "仲裁示范法", "name": "UNCITRAL 国际商事仲裁示范法",
        "category": "国际仲裁", "effective": "1985-06-21", "expected": 47,
        "url": "https://uncitral.un.org/sites/default/files/media-documents/uncitral/en/19-09955_e_ebook.pdf",
        "marker": r"^Article\s+([0-9]+)\s*([A-Z]?)\s*\.\s",
    },
]

INCIDTERMS = [
    ("EXW", "Ex Works", "卖方在其所在地或其他指定地点将货物置于买方处置之下即完成交货；买方自该地点起承担全部费用与风险。"),
    ("FCA", "Free Carrier", "卖方在指定地点将货物交给买方指定的承运人即完成交货，并负责办理出口清关。"),
    ("CPT", "Carriage Paid To", "卖方承担运费将货物运至指定目的地，货物交给第一承运人时风险转移给买方。"),
    ("CIP", "Carriage and Insurance Paid To", "在 CPT 基础上，卖方还须按约定投保货物运输险并支付保险费。"),
    ("DAP", "Delivered at Place", "卖方负责将货物运至指定目的地并置于买方处置之下，进口清关与进口税费由买方承担。"),
    ("DPU", "Delivered at Place Unloaded", "卖方负责将货物运至指定目的地并完成卸货，进口清关与税费由买方承担。"),
    ("DDP", "Delivered Duty Paid", "卖方承担将货物运至指定目的地并完成进口清关与缴纳进口税费的全部义务。"),
    ("FAS", "Free Alongside Ship", "卖方在指定装运港将货物置于船边即完成交货，装船及此后费用与风险由买方承担。"),
    ("FOB", "Free on Board", "卖方在指定装运港将货物装上买方指定船舶即完成交货，货物装船后风险转移给买方。"),
    ("CFR", "Cost and Freight", "卖方承担运费将货物运至指定目的港，货物装船时风险转移给买方。"),
    ("CIF", "Cost, Insurance and Freight", "在 CFR 基础上，卖方还须按约定投保货物运输险并支付保险费。"),
]


def cn2roman(s: str) -> int:
    vals = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
    total = 0
    for i, ch in enumerate(s):
        v = vals[ch]
        nxt = vals[s[i + 1]] if i + 1 < len(s) else 0
        total += -v if v < nxt else v
    return total


def fetch(doc) -> Path:
    path = CACHE / f"{doc['id']}.pdf"
    if path.exists() and path.stat().st_size > 1000:
        return path
    req = Request(doc["url"], headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36",
        "Referer": "https://uncitral.un.org/",
    })
    with urlopen(req, timeout=60) as resp:
        data = resp.read()
    if not data.startswith(b"%PDF"):
        raise RuntimeError(f"{doc['short']} 下载失败：{data[:60]}")
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def normalize(text: str) -> str:
    text = text.replace("\u2004", " ").replace("\u00a0", " ").replace("\u2009", " ")
    text = text.replace("ﬁ", "fi").replace("ﬂ", "fl")
    text = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", text)
    return text


def parse_pdf(doc, path: Path):
    raw = "\n".join((p.extract_text() or "") for p in PdfReader(str(path)).pages)
    text = normalize(raw)
    marker = re.compile(doc["marker"])
    articles, current, heading = [], None, ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.count(". .") >= 3:  # 目录行
            continue
        h = re.match(r"^(Part|PART|Chapter|CHAPTER)\s+([IVXLC0-9]+)\.?\s*(.*)$", stripped)
        if h and len(stripped) < 90:
            if current:
                articles.append(current)
                current = None
            heading = re.sub(r"\s+", " ", stripped)
            continue
        m = marker.match(stripped)
        if m:
            raw_token = m.group(1)
            suffix = m.group(2) if m.lastindex and m.lastindex >= 2 else ""
            no = cn2roman(raw_token) if raw_token.isalpha() else int(raw_token)
            token = f"{raw_token}{suffix}"
            label = f"Article {token}"
            if current:
                articles.append(current)
            current = {
                "id": f"{doc['id']}-{token}",
                "no": no,
                "label": label,
                "chapter": heading,
                "text": "",
                "law": doc["short"],
                "law_id": doc["id"],
                "lang": "en",
                "jurisdiction": "INTL",
            }
            rest = stripped[m.end():].strip()
            if rest:
                current["text"] = rest
            continue
        if current and stripped and not re.match(
            r"^(Page\s*\d*|[ivx]+|Contents)$|^\d+\s+United Nations Convention|"
            r"^[IVX]+\.\s+United Nations Convention|^See |^Official Records", stripped
        ):
            current["text"] = (current["text"] + " " + stripped).strip()
    if current:
        articles.append(current)

    # 只保留编号连续且不重复的正文条文，跳过正文之后的签署/附件段落
    keep, last = [], 0
    for a in articles:
        suffix = a["id"].rsplit("-", 1)[-1].rstrip("0123456789")
        repeated_with_suffix = suffix and a["no"] == last
        if (a["no"] == last + 1 or repeated_with_suffix) and a["text"]:
            keep.append(a)
            last = a["no"]
    for a in keep:
        a["text"] = re.sub(r"\s+\d+\s+United Nations Convention on Contracts for the International Sale of Goods", " ", a["text"])
        a["text"] = re.sub(r"\bPage\s*\d*\b", " ", a["text"])
        a["text"] = re.sub(r"\s*\d{2}-\d{4,}_[A-Za-z_].*$", "", a["text"])
        # 个别 PDF 每个词后都带句点（版面转换产物），按分隔符处理
        if a["text"].count(". ") > max(4, len(a["text"].split()) // 2):
            a["text"] = re.sub(r"\s*\.+\s*", " ", a["text"])
        a["text"] = re.sub(r"\s+", " ", a["text"]).strip()
        if len(a["text"]) < 10:
            raise RuntimeError(f"{doc['short']} {a['label']} 正文为空")
    law = {
        "id": doc["id"], "name": doc["name"], "short": doc["short"],
        "category": doc["category"], "effective": doc["effective"],
        "source": f"UNCITRAL 官方英文文本（{doc['url'].rsplit('/', 1)[-1]}）",
        "note": "条文文本来自联合国国际贸易法委员会（UNCITRAL）官方发布，版权归联合国所有，此处复制仅作检索参考；正式引用请以联合国官方正式文本为准。",
        "lang": "en", "jurisdiction": "INTL", "count": len(keep),
    }
    return law, keep


def main():
    laws, all_arts = [], []
    for doc in DOCS:
        path = fetch(doc)
        law, arts = parse_pdf(doc, path)
        flag = "" if len(arts) == doc["expected"] else f"（预期 {doc['expected']} 条）"
        print(f"{law['short']:<16} {law['count']:>4} 条{flag}")
        laws.append(law)
        all_arts.extend(arts)

    incoterms = []
    for i, (code, en, zh) in enumerate(INCIDTERMS, 1):
        incoterms.append({
            "id": f"incoterms-{code}",
            "no": i,
            "label": code,
            "chapter": "Incoterms 2020 贸易术语",
            "text": f"{code} ({en}). {zh}",
            "law": "Incoterms 2020",
            "law_id": "incoterms",
            "lang": "en",
            "jurisdiction": "INTL",
        })
    laws.append({
        "id": "incoterms", "name": "国际贸易术语解释通则 2020（要点摘要）",
        "short": "Incoterms 2020", "category": "国际贸易惯例",
        "effective": "2020-01-01",
        "source": "ICC Incoterms 2020 规则要点摘要（非原文，仅用于条款提示）",
        "note": "以上释义为本工具编写方概括整理，并非国际商会（ICC）官方文本；Incoterms® 为国际商会注册商标，正式使用请以 ICC 官方出版物为准。",
        "lang": "en", "jurisdiction": "INTL", "count": len(incoterms),
    })
    all_arts.extend(incoterms)
    print(f"{'Incoterms 2020':<16} {len(incoterms):>4} 条")

    payload = {"generated_at": "2026-11-02", "total": len(all_arts),
               "laws": laws, "articles": all_arts}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n合计 {len(all_arts)} 条 -> {OUT} ({OUT.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    sys.exit(main())

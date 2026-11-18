#!/usr/bin/env python3
"""构建 UNIDROIT 国际商事合同通则（PICC 2016）中英文对照语料。

来源：UNIDROIT 官网 Black Letter 2016 官方 PDF（中、英）。
输出：data/statutes_upicc.json，包含 upicc（中文）与 upicc-en（英文）两个法域条目。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "sources" / "intl"
OUT = ROOT / "data" / "statutes_upicc.json"

ART = {
    "zh": re.compile(r"^\s*第\s*([0-9]+(?:\.[0-9]+)+)\s*条\s*$"),
    "en": re.compile(r"^\s*ARTICLE\s+([0-9]+(?:\.[0-9]+)+)\s*$"),
}
CHAPTER = {
    "zh": re.compile(r"^\s*第([一二三四五六七八九十]+)章\s*(.*)$"),
    "en": re.compile(r"^\s*CHAPTER\s+([0-9IVXLC]+)\s*[—\-–]\s*(.+)$"),
}
SECTION = {
    "zh": re.compile(r"^\s*第([一二三四五六七八九十]+)节\s*(.*)$"),
    "en": re.compile(r"^\s*SECTION\s+[0-9]+\s*:\s*(.+)$", re.I),
}
TITLE = re.compile(r"^\s*[（(](.+?)[）)]\s*$")
PAGE_NOISE = {
    "zh": re.compile(r"^\s*\xa0*\s*\d+\s*$"),
    "en": re.compile(r"^\s*UNIDROIT Principles 2016\s*$|^\s*\d+\s*$"),
}


def clean(text: str, lang: str) -> str:
    text = text.replace("\u2004", " ").replace("\u00a0", " ").replace("\u2009", " ")
    if lang == "zh":
        return re.sub(r"\s+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_pdf(path: Path, lang: str) -> list[dict]:
    raw = "\n".join(
        (page.extract_text(extraction_mode="layout") or "") for page in PdfReader(str(path)).pages
    )
    lines = [ln.strip() for ln in raw.splitlines()]
    art_re, chap_re, sec_re = ART[lang], CHAPTER[lang], SECTION[lang]
    noise = PAGE_NOISE[lang]

    def next_content(i: int) -> tuple[str, int]:
        while i < len(lines):
            s = lines[i]
            if s and not noise.match(s):
                return s, i
            i += 1
        return "", i

    articles: list[dict] = []
    chapter, section = "", ""
    i, n = 0, len(lines)

    # 序言：第一条之前的正文
    pre: list[str] = []
    while i < n and not (art_re.match(lines[i]) or chap_re.match(lines[i]) or sec_re.match(lines[i])):
        if lines[i] and not noise.match(lines[i]) and not chap_re.match(lines[i]):
            pre.append(lines[i])
        i += 1
    preamble = clean(" ".join(pre), lang)
    preamble = re.sub(r"(UNIDROIT Principles 2016|国际商事合同通则)", "", preamble).strip()

    while i < n:
        line = lines[i]
        m_ch = chap_re.match(line)
        if m_ch:
            if lang == "zh":
                rest = clean(m_ch.group(2), lang)
                if rest:
                    i += 1
                else:
                    rest, j = next_content(i + 1)
                    rest = clean(rest, lang)
                    i = j + 1
                chapter = f"第{m_ch.group(1)}章 {rest}".strip()
            else:
                chapter = f"Chapter {m_ch.group(1)} {clean(m_ch.group(2), lang)}".strip()
                i += 1
            section = ""
            continue
        m_sec = sec_re.match(line)
        if m_sec:
            if lang == "zh":
                rest = clean(m_sec.group(2), lang)
                if rest:
                    i += 1
                else:
                    rest, j = next_content(i + 1)
                    rest = clean(rest, lang)
                    i = j + 1
                section = f"第{m_sec.group(1)}节 {rest}".strip()
            else:
                section = clean(m_sec.group(1), lang)
                i += 1
            continue
        m_art = art_re.match(line)
        if not m_art:
            i += 1
            continue

        num = m_art.group(1)
        i += 1
        title = ""
        nxt, j = next_content(i)
        if TITLE.match(nxt):
            title = clean(TITLE.match(nxt).group(1), lang)
            i = j + 1
        body: list[str] = []
        while i < n:
            s = lines[i]
            if art_re.match(s) or chap_re.match(s) or sec_re.match(s):
                break
            if s and not noise.match(s):
                body.append(s)
            i += 1
        text = clean(" ".join(body), lang)
        if title:
            text = f"{title}{'。' if lang == 'zh' else '. '}{text}"
        arts = {
            "id": f"upicc-{num}" if lang == "zh" else f"upicc-en-{num}",
            "no": len(articles),
            "label": f"第{num}条" if lang == "zh" else f"Article {num}",
            "chapter": " / ".join(v for v in (chapter, section) if v),
            "text": text,
            "group": "upicc",
        }
        articles.append(arts)

    if preamble:
        articles.insert(0, {
            "id": "upicc-0" if lang == "zh" else "upicc-en-0",
            "no": 0,
            "label": "序言" if lang == "zh" else "Preamble",
            "chapter": "",
            "text": preamble,
            "group": "upicc",
        })
    return articles


def main():
    laws, all_arts = [], []
    specs = [
        ("zh", "upicc-zh.pdf", "upicc", "国际商事合同通则（2016）", "UNIDROIT 通则"),
        ("en", "upicc-en.pdf", "upicc-en", "UNIDROIT Principles of International Commercial Contracts (2016)", "UNIDROIT Principles"),
    ]
    for lang, filename, law_id, name, short in specs:
        arts = parse_pdf(SRC / filename, lang)
        for art in arts:
            art["law_id"] = law_id
            art["law"] = short
            art["lang"] = lang
            art["jurisdiction"] = "INTL"
        laws.append({
            "id": law_id, "name": name, "short": short, "category": "国际商事",
            "effective": "2016-05-01", "jurisdiction": "INTL", "lang": lang,
            "source": "UNIDROIT Principles 2016 Black Letter（官方 PDF）",
            "note": "条文文本来自国际统一私法协会（UNIDROIT）官方发布的 2016 版 Black Letter，版权归 UNIDROIT 所有，此处复制仅作检索参考；正式引用请以 UNIDROIT 官方文本为准。",
            "count": len(arts),
        })
        all_arts.extend(arts)
        print(f"{short:<22} {len(arts):>4} 条")

    payload = {
        "generated_at": "2026-11-02",
        "total": len(all_arts),
        "laws": laws,
        "articles": all_arts,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"合计 {len(all_arts)} 条 -> {OUT} ({OUT.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()

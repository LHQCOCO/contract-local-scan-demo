#!/usr/bin/env python3
"""构建本地法规知识库 statutes.json。

原始文本来源：
- 民法典：国务院/江苏省自然资源厅公开全文 PDF（239 页）
- 刑法：最高人民检察院官网公开的 1997 年修订全文
- 劳动合同法、劳动法、个人信息保护法、数据安全法：中国人大网公开全文

脚本把每部法律的连续条文解析为 {id, no, label, chapter, text} 结构。
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "sources"
OUT = ROOT / "data" / "statutes.json"

CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "三": 3, "四": 4,
             "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
CN_UNITS = {"十": 10, "百": 100, "千": 1000}


def cn2int(s: str) -> int:
    total, cur = 0, 0
    for ch in s:
        if ch in CN_DIGITS:
            cur = CN_DIGITS[ch]
        elif ch in CN_UNITS:
            if cur == 0:
                cur = 1
            total += cur * CN_UNITS[ch]
            cur = 0
    return total + cur


LAWS = [
    {
        "id": "mfd", "name": "中华人民共和国民法典", "short": "民法典",
        "category": "民事", "effective": "2021-01-01",
        "source": "《中华人民共和国民法典》全文（2020年5月28日通过）",
        "expected": 1260, "text": "mfd.txt", "kind": "pdf",
    },
    {
        "id": "xf", "name": "中华人民共和国刑法", "short": "刑法",
        "category": "刑事", "effective": "1997-10-01",
        "source": "《中华人民共和国刑法》（1997年修订）",
        "expected": 452, "text": "xingfa.txt", "kind": "html",
    },
    {
        "id": "ldhtf", "name": "中华人民共和国劳动合同法", "short": "劳动合同法",
        "category": "劳动", "effective": "2008-01-01",
        "source": "《中华人民共和国劳动合同法》（2007年6月29日通过）",
        "expected": 98, "text": "ldht.txt", "kind": "html",
    },
    {
        "id": "ldf", "name": "中华人民共和国劳动法", "short": "劳动法",
        "category": "劳动", "effective": "1995-01-01",
        "source": "《中华人民共和国劳动法》（1994年7月5日通过，2009年修正）",
        "expected": 107, "text": "laodongfa.txt", "kind": "html",
    },
    {
        "id": "grxxbhf", "name": "中华人民共和国个人信息保护法", "short": "个人信息保护法",
        "category": "数据与个人信息", "effective": "2021-11-01",
        "source": "《中华人民共和国个人信息保护法》（2021年8月20日通过）",
        "expected": 74, "text": "grxx.txt", "kind": "html",
    },
    {
        "id": "sjaqf", "name": "中华人民共和国数据安全法", "short": "数据安全法",
        "category": "数据与个人信息", "effective": "2021-09-01",
        "source": "《中华人民共和国数据安全法》（2021年6月10日通过）",
        "expected": 55, "text": "shujuaq.txt", "kind": "html",
    },
]

ART_RE = re.compile(r"(?m)^[\s\u3000\xa0]*第([一二三四五六七八九十百千零〇]+)条[\s\u3000]*")
CHAPTER_RE = re.compile(r"(?m)^[\s\u3000\xa0]*(第[一二三四五六七八九十]+)(编|章|节)[\s\u3000]*([^\n]{0,60})$")


def clean(text: str) -> str:
    text = text.replace("\u3000", "")
    text = text.replace("\r", "").replace("\n", "")
    text = re.sub(r"\s+", "", text)
    return text.strip()


def parse_law(meta):
    raw = (SRC / meta["text"]).read_text(encoding="utf-8")
    first = ART_RE.search(raw)
    if not first:
        raise RuntimeError(f"{meta['short']} 未找到条文")
    # 跳过目录：定位第一条之前最近的一组编/章标题，从正文标题处开始解析
    pre = [(m.start(), m.group(2)) for m in CHAPTER_RE.finditer(raw[:first.start()])]
    start = first.start()
    for kind in ("编", "章"):
        hits = [pos for pos, k in pre if k == kind]
        if hits:
            start = hits[-1]
            break
    body = raw[start:]

    # 建立 章节目录：仅收集正文中的标题行
    headings = []
    for m in CHAPTER_RE.finditer(body):
        title = clean(m.group(3)) or m.group(2)
        label = f"{m.group(1)}{m.group(2)}"
        headings.append((m.start(), m.group(2), label, title))
    heading_idx = 0
    current = ""
    parts = {"编": "", "章": "", "节": ""}

    arts = []
    matches = list(ART_RE.finditer(body))
    cursor = 0
    for i, m in enumerate(matches):
        no = cn2int(m.group(1))
        # 只接受“编号连续”的标题，跳过正文中换行后出现的法条引用（如“第五百一十条的规定”）
        if no != len(arts) + 1:
            continue
        end = len(body)
        for nxt in matches[i + 1:]:
            if cn2int(nxt.group(1)) == no + 1:
                end = nxt.start()
                break
        content = body[m.end():end]
        cursor = end

        # 更新所属章节
        while heading_idx < len(headings) and headings[heading_idx][0] <= m.start():
            pos, kind, label, title = headings[heading_idx]
            parts[kind] = f"{label} {title}"
            if kind == "编":
                parts["章"], parts["节"] = "", ""
            elif kind == "章":
                parts["节"] = ""
            heading_idx += 1
        current = " / ".join(v for v in (parts["编"], parts["章"], parts["节"]) if v)

        text = clean(content)
        # 去掉粘连在条文尾部的章/节标题
        text = re.sub(r"第[一二三四五六七八九十]+(?:编|章|节)[^。；：！？]{0,40}$", "", text)
        arts.append({
            "id": f"{meta['id']}-{no}",
            "no": no,
            "label": f"第{m.group(1)}条",
            "chapter": current,
            "text": text,
            "law": meta["short"],
            "law_id": meta["id"],
        })

    nums = [a["no"] for a in arts]
    uniq = sorted(set(nums))
    if len(uniq) != len(nums):
        dup = [n for n in uniq if nums.count(n) > 1]
        raise RuntimeError(f"{meta['short']} 条文编号重复: {dup[:10]}")
    if uniq != list(range(1, max(uniq) + 1)):
        missing = [n for n in range(1, max(uniq) + 1) if n not in uniq]
        raise RuntimeError(f"{meta['short']} 条文缺号: {missing[:20]}")
    if len(arts) != meta["expected"]:
        print(f"  ! {meta['short']} 解析 {len(arts)} 条，预期 {meta['expected']} 条", file=sys.stderr)

    law = {k: meta[k] for k in ("id", "name", "short", "category", "effective", "source")}
    law["count"] = len(arts)
    return law, arts


def main():
    laws, all_arts = [], []
    for meta in LAWS:
        law, arts = parse_law(meta)
        print(f"{law['short']:<8} {law['count']:>5} 条  {arts[0]['text'][:24]}…")
        laws.append(law)
        all_arts.extend(arts)

    payload = {
        "generated_at": "2026-11-02",
        "total": len(all_arts),
        "laws": laws,
        "articles": all_arts,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n合计 {len(all_arts)} 条 -> {OUT} ({OUT.stat().st_size/1024:.0f} KB)")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""扩充本地法规知识库：抓取主要法典并生成 data/statutes_extra.json。

原始文本来源：LawRefBook/Laws（公开整理的全国人大官方文本快照）。
脚本把 Markdown 章节与连续条文解析为 {id, no, label, chapter, text, law, law_id, lang, jurisdiction}。
已内置断点续传（data/sources/laws 缓存），重复执行不会重复下载。
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import tarfile
import tempfile
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "sources" / "laws"
OUT = ROOT / "data" / "statutes_extra.json"
BASE = "https://raw.githubusercontent.com/LawRefBook/Laws/master/{}/{}"

# (目录, 文件名, 法条 id, 简称, 分类, 施行日期)
SPECS = [
    ("民法商法", "公司法(2023-12-29).md", "gsf", "公司法", "商事", "2024-07-01"),
    ("民法商法", "合伙企业法(2006-08-27).md", "hhzf", "合伙企业法", "商事", "2007-06-01"),
    ("民法商法", "消费者权益保护法(2013-10-25).md", "xfzqybhf", "消费者权益保护法", "消费", "2014-03-15"),
    ("经济法", "产品质量法(2018-12-29).md", "cpzlf", "产品质量法", "产品", "2018-12-29"),
    ("民法商法", "招标投标法(2017-12-27).md", "zbtbf", "招标投标法", "建设工程", "2017-12-28"),
    ("经济法", "建筑法(2019-04-23).md", "jzf", "建筑法", "建设工程", "2019-04-23"),
    ("经济法", "反不正当竞争法(2025-06-27).md", "fbzdtjzf", "反不正当竞争法", "竞争", "2025-10-15"),
    ("民法商法", "著作权法(2020-11-11).md", "zzf", "著作权法", "知识产权", "2021-06-01"),
    ("民法商法", "专利法(2020-10-17).md", "zlf", "专利法", "知识产权", "2021-06-01"),
    ("民法商法", "商标法(2019-04-23).md", "sbf", "商标法", "知识产权", "2019-11-01"),
    ("民法商法", "电子签名法(2019-04-23).md", "dzqzf", "电子签名法", "数据与个人信息", "2020-01-01"),
    ("民法商法", "电子商务法(2018-08-31).md", "dzswf", "电子商务法", "消费", "2019-01-01"),
    ("民法商法", "保险法(2015-04-24).md", "bxf", "保险法", "商事", "2015-04-24"),
    ("民法商法", "票据法(2004-08-28).md", "pjf", "票据法", "商事", "2004-08-28"),
    ("民法商法", "海商法(2025-10-28).md", "hsf", "海商法", "运输", "2026-05-01"),
    ("民法商法", "涉外民事关系法律适用法(2010-10-28).md", "swmgsgxflsyyf", "涉外民事关系法律适用法", "涉外", "2011-04-01"),
    ("诉讼与非诉讼程序法", "仲裁法(2025-09-12).md", "zcf", "仲裁法", "程序", "2026-03-01"),
    ("诉讼与非诉讼程序法", "民事诉讼法(2023-09-01).md", "msssf", "民事诉讼法", "程序", "2024-01-01"),
    ("诉讼与非诉讼程序法", "劳动争议调解仲裁法(2007-12-29).md", "ldzycjbzf", "劳动争议调解仲裁法", "劳动", "2008-05-01"),
    # 商事与金融
    ("民法商法", "企业破产法(2006-08-27).md", "qycpf", "企业破产法", "商事", "2007-06-01"),
    ("民法商法", "证券法(2019-12-28).md", "zqf", "证券法", "金融", "2020-03-01"),
    ("民法商法", "信托法(2001-04-28).md", "xtf", "信托法", "金融", "2001-10-01"),
    ("民法商法", "商业银行法(2015-08-29).md", "syyhf", "商业银行法", "金融", "1995-07-01"),
    ("民法商法", "个人独资企业法(1999-08-30).md", "grdzf", "个人独资企业法", "商事", "2000-01-01"),
    ("民法商法", "外商投资法(2019-03-15).md", "wstzf", "外商投资法", "涉外", "2020-01-01"),
    # 财税与市场监管
    ("经济法", "政府采购法(2014-08-31).md", "zfcgf", "政府采购法", "政府采购", "2003-01-01"),
    ("经济法", "税收征收管理法(2015-04-24).md", "sszsgf", "税收征收管理法", "财税", "1993-01-01"),
    ("经济法", "会计法(2024-06-28).md", "kjf", "会计法", "财税", "1985-05-01"),
    ("经济法", "广告法(2021-04-29).md", "ggf", "广告法", "市场监管", "1995-02-01"),
    ("经济法", "价格法(1997-12-29).md", "jgf", "价格法", "市场监管", "1998-05-01"),
    ("民法商法", "拍卖法(2015-04-24).md", "pmf", "拍卖法", "市场监管", "1997-01-01"),
    ("经济法", "反垄断法(2022-06-24).md", "fldf", "反垄断法", "竞争", "2008-08-01"),
    # 土地、贸易与运输
    ("行政法", "土地管理法(2019-08-26).md", "tdglf", "土地管理法", "土地与房地产", "2020-01-01"),
    ("行政法", "城市房地产管理法(2019-08-26).md", "fdcglf", "城市房地产管理法", "土地与房地产", "1995-01-01"),
    ("经济法", "对外贸易法(2025-12-27).md", "dwmyf", "对外贸易法", "外贸与海关", "2004-07-01"),
    ("行政法", "海关法(2021-04-29).md", "hgf", "海关法", "外贸与海关", "1987-07-01"),
    ("经济法", "民用航空法(2025-12-27).md", "myhkf", "民用航空法", "运输", "1996-03-01"),
    ("经济法", "邮政法(2015-04-24).md", "yzf", "邮政法", "运输", "1987-01-01"),
    # 数据、劳动与安全
    ("行政法", "网络安全法(2025-10-28).md", "wlaqf", "网络安全法", "数据与个人信息", "2017-06-01"),
    ("社会法", "社会保险法(2018-12-29).md", "shbxf", "社会保险法", "劳动", "2011-07-01"),
    ("社会法", "安全生产法(2021-06-10).md", "aqscf", "安全生产法", "安全生产", "2002-11-01"),
    ("行政法", "环境保护法(2014-04-24).md", "hjbhf", "环境保护法", "环保", "2015-01-01"),
    ("行政法", "食品安全法(2025-09-12).md", "spaqf", "食品安全法", "食品与药品", "2009-06-01"),
    ("行政法", "药品管理法(2019-08-26).md", "ypglf", "药品管理法", "食品与药品", "1985-07-01"),
    # 程序与行政救济
    ("诉讼与非诉讼程序法", "刑事诉讼法(2018-10-26).md", "xsssf", "刑事诉讼法", "程序", "1980-01-01"),
    ("诉讼与非诉讼程序法", "行政诉讼法(2017-06-27).md", "xzssf", "行政诉讼法", "程序", "1990-10-01"),
    ("行政法", "行政处罚法(2021-01-22).md", "xzcff", "行政处罚法", "程序", "1996-10-01"),
    ("行政法", "行政许可法(2019-04-23).md", "xzxkf", "行政许可法", "程序", "2004-07-01"),
    ("诉讼与非诉讼程序法", "行政复议法(2023-09-01).md", "xzfyf", "行政复议法", "程序", "1999-10-01"),
    ("宪法相关法", "国家赔偿法(2012-10-26).md", "gjpcf", "国家赔偿法", "程序", "1995-01-01"),
    ("诉讼与非诉讼程序法", "人民调解法(2010-08-28).md", "rmtjf", "人民调解法", "程序", "2011-01-01"),
    # 劳动与社会保障行政法规
    ("社会法", "劳动合同法实施条例(2008-09-18).md", "ldhtfsslt", "劳动合同法实施条例", "劳动", "2008-09-18"),
    ("社会法", "工伤保险条例(2010-12-20).md", "gsbxtl", "工伤保险条例", "劳动", "2004-01-01"),
    ("社会法", "职工带薪年休假条例(2007-12-14).md", "zgdxdnxjtl", "职工带薪年休假条例", "劳动", "2008-01-01"),
]

# ---- 全量法典收录：扫描 LawRefBook 仓库快照，收录全部现行有效「法律」层级文本 ----
# 目录 -> 知识库分类（沿用全国人大立法体系）
BULK_DIRS = {
    "宪法": "宪法",
    "宪法相关法": "宪法相关法",
    "民法商法": "民商法",
    "经济法": "经济法",
    "行政法": "行政法",
    "社会法": "社会法",
    "刑法": "刑法",
    "诉讼与非诉讼程序法": "程序",
    "行政法规": "行政法规",
    "司法解释": "司法解释",
    "部门规章": "部门规章",
    "生态环境法": "生态环境",
    "法律解释": "法律解释",
}
# 民法典施行（2021-01-01）及其他修法后已废止的法律：不得入库，避免检索命中失效条文
BULK_DEAD = {
    "婚姻法", "继承法", "民法通则", "民法总则", "收养法", "担保法",
    "合同法", "物权法", "侵权责任法",
    "外资企业法", "中外合资经营企业法", "中外合作经营企业法",
    "经济合同法", "涉外经济合同法", "技术合同法", "经济合同仲裁条例",
    "执业医师法", "学位条例", "环境噪声污染防治法",
}
# 无条文结构、无法按条解析的单篇规定
BULK_SKIP = {
    "全国人民代表大会常务委员会关于县级以下人民代表大会代表直接选举的若干规定",
}
# statutes.json 已精校收录，避免重复入库
CORE_LAW_NAMES = {"刑法", "劳动合同法", "劳动法", "个人信息保护法", "数据安全法"}

SNAPSHOT = ROOT / "data" / "sources" / "repo"
DATE_IN_NAME = re.compile(r"\((\d{4}-\d{2}-\d{2})\)\.md$")

CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "三": 3, "四": 4,
             "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
CN_UNITS = {"十": 10, "百": 100, "千": 1000}

# 源文本偶见「第一百二十八 条」这类多空格排版，数字间空白需要容忍
ART_RE = re.compile(r"^第\s*((?:[一二三四五六七八九十百千零〇]\s*)+)条")
HEAD_RE = re.compile(r"^#{2,4}\s*(第([一二三四五六七八九十]+)(编|章|节))\s*(.*)$")


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


def int2cn(n: int) -> str:
    """1..99 转中文数字（用于把「一、二、」编号还原为「第一条」标签）。"""
    digits = "零一二三四五六七八九"
    if n < 10:
        return digits[n]
    if n < 20:
        return "十" + (digits[n % 10] if n % 10 else "")
    if n < 100:
        return digits[n // 10] + "十" + (digits[n % 10] if n % 10 else "")
    raise ValueError(f"int2cn 仅支持 1..99：{n}")


# 部分老旧行政法规以「一、二、」而非「第X条」编号（可带 markdown 标题前缀）
ORD_RE = re.compile(r"^(?:#{2,4}\s*)?([一二三四五六七八九十]{1,3})、\s*(.*)$")
# 更老的文本以「（一）（二）」为顶级编号
PAREN_ORD_RE = re.compile(r"^[（(]([一二三四五六七八九十]{1,3})[）)]\s*(.*)$")
# 发布/施行日期等元信息行，不作为正文
META_RE = re.compile(r"^\d{4}年\d{1,2}月\d{1,2}日|^发布之日施行|^自.{0,12}施行$")


def clean(text: str) -> str:
    text = text.replace("\u200b", "").replace("\u3000", " ")
    text = re.sub(r"\*\*|__|`", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def bulk_id(short: str) -> str:
    """为批量收录的法律生成稳定 id（同一名称每次运行结果一致）。"""
    return "l" + hashlib.md5(short.encode("utf-8")).hexdigest()[:8]


def ensure_snapshot() -> Path | None:
    """返回本地仓库快照目录；缺失时下载一次 tarball 并解压。"""
    if SNAPSHOT.is_dir() and any(p for p in SNAPSHOT.iterdir() if p.is_dir()):
        return SNAPSHOT
    url = "https://codeload.github.com/LawRefBook/Laws/tar.gz/refs/heads/master"
    print(f"下载法规仓库快照 -> {SNAPSHOT}", file=sys.stderr)
    try:
        req = Request(url, headers={"User-Agent": "ContractLocalScan/1.0"})
        with urlopen(req, timeout=180) as resp:
            blob = resp.read()
        with tempfile.TemporaryFile() as tmp:
            tmp.write(blob)
            tmp.seek(0)
            SNAPSHOT.mkdir(parents=True, exist_ok=True)
            with tarfile.open(fileobj=tmp, mode="r:gz") as tar:
                members = [m for m in tar.getmembers() if m.name.count("/") >= 1]
                for m in members:
                    m.name = m.name.split("/", 1)[1]
                tar.extractall(SNAPSHOT, members=[m for m in members if m.name])
    except Exception as exc:
        print(f"!! 仓库快照获取失败，跳过全量收录：{type(exc).__name__}: {exc}", file=sys.stderr)
        return None
    return SNAPSHOT


def discover_bulk() -> list[tuple]:
    """扫描快照中的法律层级目录，同名多版本取最新，剔除已废止/已手工收录的法律。"""
    snapshot = ensure_snapshot()
    if snapshot is None:
        return []
    manual_names = {s[3] for s in SPECS}
    latest: dict[str, tuple] = {}
    for folder, category in BULK_DIRS.items():
        d = snapshot / folder
        if not d.is_dir():
            continue
        for f in d.rglob("*.md"):  # 部门规章等目录按部委分子目录
            if f.name == "_index.md":
                continue
            base = DATE_IN_NAME.sub("", f.name).removesuffix(".md")
            base = re.sub(r"\s+", " ", base).strip()
            plain = base.removeprefix("中华人民共和国")
            if "修正案" in base or base in BULK_DEAD or plain in BULK_DEAD:
                continue
            if base in BULK_SKIP or plain in BULK_SKIP:
                continue
            if plain in CORE_LAW_NAMES or plain in manual_names or base in manual_names:
                continue
            m = DATE_IN_NAME.search(f.name)
            eff = m.group(1) if m else "0000-00-00"
            cur = latest.get(base)
            if cur is None or eff > cur[5]:
                rel_dir = str(f.parent.relative_to(snapshot))
                latest[base] = (rel_dir, f.name, bulk_id(base), base, category, eff)
    print(f"全量法典：{len(latest)} 部", file=sys.stderr)
    return sorted(latest.values(), key=lambda s: (s[4], s[3]))


def fetch(spec) -> Path:
    folder, filename, law_id = spec[0], spec[1], spec[2]
    path = CACHE / f"{law_id}.md"
    if path.exists() and path.stat().st_size > 0:
        return path
    snap = SNAPSHOT / folder / filename
    if snap.is_file():
        CACHE.mkdir(parents=True, exist_ok=True)
        path.write_text(snap.read_text(encoding="utf-8"), encoding="utf-8")
        return path
    url = BASE.format(quote(folder), quote(filename))
    req = Request(url, headers={"User-Agent": "ContractLocalScan/1.0"})
    with urlopen(req, timeout=45) as resp:
        data = resp.read().decode("utf-8")
    if not data.startswith("# "):
        raise RuntimeError(f"{filename} 内容异常：{data[:80]}")
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(data, encoding="utf-8")
    return path


def parse(path: Path, spec):
    folder, filename, law_id, short, category, effective = spec
    raw = path.read_text(encoding="utf-8")
    lines = raw.splitlines()
    name = clean(lines[0].lstrip("# ").strip())
    articles, parts = [], {"编": "", "章": "", "节": ""}
    current, last_no = None, 0

    for line in lines[1:]:
        head = HEAD_RE.match(line.strip())
        if head:
            kind, label, title = head.group(3), clean(head.group(2)) + clean(head.group(3)), clean(head.group(4))
            if current is not None:
                articles.append(current)
                current = None
            parts[kind] = f"{label} {title}".strip()
            if kind == "编":
                parts["章"] = parts["节"] = ""
            elif kind == "章":
                parts["节"] = ""
            continue
        m = ART_RE.match(line.strip())
        if m:
            digits = re.sub(r"\s", "", m.group(1))
            no = cn2int(digits)
            if no != last_no + 1:
                # 条文内的引用行（如“第五百零九条”）不应打断连续编号
                if current is not None:
                    current["text"] += " " + clean(line)
                continue
            if current is not None:
                articles.append(current)
            last_no = no
            chapter = " / ".join(v for v in (parts["编"], parts["章"], parts["节"]) if v)
            current = {
                "id": f"{law_id}-{no}",
                "no": no,
                "label": f"第{digits}条",
                "chapter": chapter,
                "text": clean(line[m.end():]),
                "law": short,
                "law_id": law_id,
                "lang": "zh",
                "jurisdiction": "CN",
            }
            continue
        if current is not None and line.strip() and not line.strip().startswith("<!--"):
            current["text"] += " " + clean(line)
    if current is not None:
        articles.append(current)

    if not articles:
        # 兜底：以「一、二、」编号的老旧规定，按同样规则还原为连续条文
        current, last_no = None, 0
        for line in lines[1:]:
            s = line.strip()
            m = ORD_RE.match(s)
            if m and cn2int(m.group(1)) == last_no + 1:
                if current is not None:
                    articles.append(current)
                last_no += 1
                rest = clean(m.group(2)).lstrip("。# ")
                current = {
                    "id": f"{law_id}-{last_no}",
                    "no": last_no,
                    "label": f"第{int2cn(last_no)}条",
                    "chapter": "",
                    "text": rest if rest else "",
                    "law": short,
                    "law_id": law_id,
                    "lang": "zh",
                    "jurisdiction": "CN",
                }
                continue
            if current is not None and s and not s.startswith("<!--"):
                seg = clean(s.lstrip("# "))
                current["text"] = (current["text"] + " " + seg).strip()
        if current is not None:
            articles.append(current)

    if not articles:
        # 兜底：以「（一）（二）」为顶级编号的文本
        current, last_no = None, 0
        for line in lines[1:]:
            s = line.strip()
            m = PAREN_ORD_RE.match(s)
            if m and cn2int(m.group(1)) == last_no + 1:
                if current is not None:
                    articles.append(current)
                last_no += 1
                current = {
                    "id": f"{law_id}-{last_no}",
                    "no": last_no,
                    "label": f"第{int2cn(last_no)}条",
                    "chapter": "",
                    "text": clean(m.group(2)),
                    "law": short,
                    "law_id": law_id,
                    "lang": "zh",
                    "jurisdiction": "CN",
                }
                continue
            if current is not None and s and not s.startswith("<!--"):
                current["text"] = (current["text"] + " " + clean(s)).strip()
        if current is not None:
            articles.append(current)

    if not articles:
        # 兜底：无编号纯段落文本，每个段落作为一条（过短段落并入下一段）
        buf, no = "", 0
        for line in lines[1:]:
            s = clean(line.lstrip("# "))
            if not s or s.startswith("<!--") or META_RE.match(s):
                continue
            text = (buf + " " + s).strip() if buf else s
            buf = ""
            if len(text) < 8:
                buf = text
                continue
            no += 1
            articles.append({
                "id": f"{law_id}-{no}",
                "no": no,
                "label": f"第{int2cn(no)}条" if no < 100 else f"第{no}条",
                "chapter": "",
                "text": text,
                "law": short,
                "law_id": law_id,
                "lang": "zh",
                "jurisdiction": "CN",
            })
        if buf and articles:
            articles[-1]["text"] += " " + buf

    nums = [a["no"] for a in articles]
    if sorted(nums) != list(range(1, max(nums) + 1)) or len(nums) != len(set(nums)):
        missing = [n for n in range(1, max(nums) + 1) if n not in set(nums)]
        raise RuntimeError(f"{short} 条文缺号/重复：{missing[:10]}")
    for a in articles:
        if len(a["text"]) < 4:
            raise RuntimeError(f"{short} {a['label']} 正文为空")
    law = {
        "id": law_id, "name": name, "short": short, "category": category,
        "effective": effective,
        "source": f"《{name}》（全国人大公布文本，{filename.split('(')[-1].rstrip('.md)')}）",
        "lang": "zh", "jurisdiction": "CN", "count": len(articles),
    }
    return law, articles


def main():
    bulk = discover_bulk()
    bulk_ids = {s[2] for s in bulk}
    laws, all_arts, failed, bulk_failed = [], [], [], []
    for spec in list(SPECS) + bulk:
        try:
            path = fetch(spec)
            law, arts = parse(path, spec)
        except Exception as exc:  # 单部法律失败不应中断整批抓取
            item = (spec[3], f"{type(exc).__name__}: {exc}")
            if spec[2] in bulk_ids:
                bulk_failed.append(item)
                continue
            failed.append(item)
            print(f"!! {spec[3]:<16} {type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        print(f"{law['short']:<16} {law['count']:>4} 条  {arts[0]['text'][:22]}…")
        laws.append(law)
        all_arts.extend(arts)
    payload = {"generated_at": "2026-11-02", "total": len(all_arts),
               "laws": laws, "articles": all_arts}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{len(laws)} 部 / {len(all_arts)} 条 -> {OUT} ({OUT.stat().st_size / 1024:.0f} KB)")
    if bulk_failed:
        print(f"全量收录跳过 {len(bulk_failed)} 部（条文格式不规整）：")
        for short, err in bulk_failed[:10]:
            print(f"  - {short}: {err}")
    if failed:
        print(f"失败 {len(failed)} 部：")
        for short, err in failed:
            print(f"  - {short}: {err}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""采集公开合同示范文本并生成评测语料与清单。

数据来源均为监管机构公开页面：
- 国家市场监督管理总局合同示范文本库 https://htsfwb.samr.gov.cn/
- 人力资源和社会保障部《劳动合同（通用）》示范文本（地方政府网站转载）
- 白银市市场监督管理局《商业秘密保密协议（示范文本）》

用法：python3 eval/corpus/fetch_sources.py
输出：eval/corpus/sources/*.txt 与 eval/corpus/manifest.json
评测本身只读本地语料，不访问网络。
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "sources"

# (文件名, 标题, 发布机构, 页面 URL, 页面类型, 人工标注合同类型)
SOURCES = [
    ("农副产品买卖合同", "农副产品买卖合同（示范文本）GF—2025—0151", "国家市场监督管理总局",
     "https://htsfwb.samr.gov.cn/View?id=8ed1cb2d-568e-41da-b4f3-4b1671c51af4", "samr", "采购合同"),
    ("二手车买卖合同", "二手车买卖合同（示范文本）", "国家工商行政管理总局",
     "https://htsfwb.samr.gov.cn/View?id=f6868a15-9a55-47c4-a221-29cc6e49fca7", "samr", "采购合同"),
    ("城镇房屋租赁合同", "城镇房屋租赁合同（示范文本）", "国家市场监督管理总局",
     "https://htsfwb.samr.gov.cn/View?id=2340996b-882d-47a4-b74d-c30784628737", "samr", "租赁合同"),
    ("房屋租赁合同", "房屋租赁合同（示范文本）", "国家市场监督管理总局",
     "https://htsfwb.samr.gov.cn/View?id=b914dd8a-0cd6-4c84-a93a-337b32ae1082", "samr", "租赁合同"),
    ("数据委托处理服务合同", "数据委托处理服务合同（示范文本）GF—2025—2616", "国家数据局、国家市场监督管理总局",
     "https://htsfwb.samr.gov.cn/View?id=6cdbe704-5a82-4dfe-822f-8cbb60528321", "samr", "服务合同"),
    ("委托合同", "委托合同（示范文本）GF—2025—1001", "国家市场监督管理总局",
     "https://htsfwb.samr.gov.cn/View?id=50b57729-0fca-45d2-92c3-fe7e6a989815", "samr", "其他合同"),
    ("劳动合同通用", "劳动合同（通用）示范文本", "人力资源和社会保障部",
     "https://www.longnan.gov.cn/zfxxgk/public/content/147883303", "labor", "劳动合同"),
    ("商业秘密保密协议", "商业秘密保密协议（示范文本）", "白银市市场监督管理局",
     "https://www.baiyin.gov.cn/sscjdglj/zfxxgk/fdzdgknr/lzyj/fldyfbzdjz/art/2023/art_aba733fe98cf4f838e44b7d26ad978fd.html",
     "conf", "保密协议"),
]


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=40) as resp:
        return resp.read().decode("utf-8", "ignore")


def to_lines(raw: str) -> list[str]:
    h = re.sub(r"<script.*?</script>", "", raw, flags=re.S | re.I)
    h = re.sub(r"<style.*?</style>", "", h, flags=re.S | re.I)
    h = re.sub(r"<br\s*/?>", "\n", h, flags=re.I)
    h = re.sub(r"</(?:p|div|li|tr|h\d|td)>", "\n", h, flags=re.I)
    t = html.unescape(re.sub(r"<[^>]+>", "", h))
    return [re.sub(r"[ \t]+", " ", x).strip() for x in t.splitlines() if x.strip()]


def trim_samr(lines: list[str]) -> list[str]:
    start = next((i for i, x in enumerate(lines) if re.match(r"^GF[—\-－]\d{4}", x)), None)
    if start is None:
        start = next((i for i, x in enumerate(lines) if re.search(r"（示范文本）|\(示范文本\)", x)), 0)
    end = next((i for i, x in enumerate(lines) if x.startswith("版权所有：")), len(lines))
    return lines[start:end]


def trim_labor(lines: list[str]) -> list[str]:
    start = next(i for i, x in enumerate(lines) if re.sub(r"\s", "", x) == "劳动合同")
    end = next(i for i, x in enumerate(lines) if x.startswith("来源：人社部"))
    return lines[start:end]


def trim_conf(lines: list[str]) -> list[str]:
    header = next(i for i, x in enumerate(lines) if x == "商业秘密保密协议（示范文本）" and i > 300)
    end = next(i for i, x in enumerate(lines) if x == "竞业限制协议（示范文本）" and i > header)
    return lines[header:end]


TRIMMERS = {"samr": trim_samr, "labor": trim_labor, "conf": trim_conf}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    entries = []
    for name, title, issuer, url, kind, ctype in SOURCES:
        lines = to_lines(fetch(url))
        body = "\n".join(TRIMMERS[kind](lines)).strip() + "\n"
        path = OUT / f"{name}.txt"
        path.write_text(body, encoding="utf-8")
        entries.append({
            "file": f"sources/{name}.txt",
            "title": title,
            "issuer": issuer,
            "url": url,
            "contract_type": ctype,
            "chars": len(body),
            "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        })
        print(f"{name}\t{len(body)} 字")
    manifest = {
        "retrieved_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "documents": entries,
    }
    (ROOT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"写入 {ROOT / 'manifest.json'}")


if __name__ == "__main__":
    main()

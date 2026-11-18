#!/usr/bin/env python3
"""把完整法库切成若干体积可控的分片，避免仓库里出现超大单文件。

背景：`scripts/fetch_statutes.py` 抓取 1,400+ 部法规后，会生成一个约 37MB 的
`data/statutes_extra.json`。GitHub 对单文件有体积提示上限，仓库克隆也会明显变慢，
所以按「整部法律」为单位重新分桶，输出 `data/statutes_extra_1.json` … `_N.json`，
每片控制在 15MB 以内。`cls/kb.py` 的 SHARDS 用 glob 读取这些分片，加载逻辑不变。

用法：
    python scripts/split_statutes.py                       # 切 data/statutes_extra.json
    python scripts/split_statutes.py path/to/extra.json --shards 4 --max-mb 15
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = ROOT / "data" / "statutes_extra.json"
DEFAULT_SHARDS = 4
DEFAULT_MAX_MB = 15


def _law_bytes(law: dict, sizes: dict[str, int]) -> int:
    return sizes.get(law["id"], 0) + len(json.dumps(law, ensure_ascii=False).encode("utf-8"))


def split(payload: dict, out_dir: Path, shards: int = DEFAULT_SHARDS,
          max_bytes: int = DEFAULT_MAX_MB * 1024 * 1024) -> list[Path]:
    """按法律分组切分，返回写出的分片路径。

    同一部法律的条文不会被拆到两个分片；分配时优先落到当前体积最小的分片，
    因此各片大小接近，不会出现「一片 20MB、另一片 1MB」的偏斜。
    """
    laws = payload.get("laws") or []
    articles = payload.get("articles") or []
    if not laws:
        raise ValueError("输入文件里没有 laws，确认是 build/fetch 生成的法库")

    sizes: dict[str, int] = {}
    for art in articles:
        lid = art.get("law_id")
        sizes[lid] = sizes.get(lid, 0) + len(json.dumps(art, ensure_ascii=False).encode("utf-8"))

    bins: list[dict] = [{"laws": [], "articles": [], "bytes": 0} for _ in range(shards)]
    for law in laws:
        target = min(bins, key=lambda b: b["bytes"])
        target["laws"].append(law)
        target["bytes"] += _law_bytes(law, sizes)

    shard_of = {law["id"]: i for i, b in enumerate(bins) for law in b["laws"]}
    for art in articles:
        lid = art.get("law_id")
        if lid not in shard_of:
            raise ValueError(f"条文 {art.get('id')} 的 law_id={lid} 不在 laws 里，法库结构不一致")
        bins[shard_of[lid]]["articles"].append(art)

    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for i, b in enumerate(bins, 1):
        path = out_dir / f"statutes_extra_{i}.json"
        body = {
            "generated_at": payload.get("generated_at", ""),
            "total": len(b["articles"]),
            "laws": b["laws"],
            "articles": b["articles"],
        }
        path.write_text(json.dumps(body, ensure_ascii=False, separators=(",", ":")),
                        encoding="utf-8")
        size = path.stat().st_size
        if size > max_bytes:
            raise ValueError(
                f"{path.name} 仍有 {size / 1024 / 1024:.1f} MB，超过 {max_bytes / 1024 / 1024:.0f} MB；"
                f"请调大 --shards"
            )
        written.append(path)

    # 清理上一轮遗留的多余分片（分片数变少时）
    for stale in out_dir.glob("statutes_extra_*.json"):
        if stale not in written:
            stale.unlink()
    return written


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="把完整法库切成多个小于指定体积的分片")
    ap.add_argument("source", nargs="?", default=str(DEFAULT_SOURCE), help="完整法库 JSON 路径")
    ap.add_argument("--shards", type=int, default=DEFAULT_SHARDS, help="分片数（默认 4）")
    ap.add_argument("--max-mb", type=float, default=DEFAULT_MAX_MB, help="单片体积上限 MB（默认 15）")
    ap.add_argument("--out-dir", default=None, help="输出目录（默认与源文件同目录）")
    args = ap.parse_args(argv)

    source = Path(args.source)
    if not source.exists():
        print(f"找不到 {source}，请先运行 scripts/fetch_statutes.py", file=sys.stderr)
        return 1
    payload = json.loads(source.read_text(encoding="utf-8"))
    out_dir = Path(args.out_dir) if args.out_dir else source.parent
    paths = split(payload, out_dir, shards=args.shards,
                  max_bytes=int(args.max_mb * 1024 * 1024))

    total = 0
    for p in paths:
        total += p.stat().st_size
        print(f"{p.name}\t{p.stat().st_size / 1024 / 1024:.2f} MB\t"
              f"{len(json.loads(p.read_text(encoding='utf-8'))['articles'])} 条")
    print(f"合计 {total / 1024 / 1024:.2f} MB，{len(paths)} 片")
    print("源文件保留在原处；它已在 .gitignore 中，不会被提交。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

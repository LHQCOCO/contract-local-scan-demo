"""修订要点：摘录规则命中条文的原文并标注出处，不生成条款文本。

规则引擎只判断“此处命中风险特征”，并给出对应法条。修订要点把检索到的
条文原文按《法规名》条号逐条列出，供法务判断如何改写；工具不输出可直接
替换的草案，也不拼装任何法条文本。
"""

from __future__ import annotations

# 页面与导出件统一使用的栏目名，明确内容来自检索结果且需人工复核。
REVISION_HEAD = "修订要点（条文原文，需法务复核）"


def build_revision(basis: list[dict]) -> str:
    """把 finding 的 basis（含条文原文）拼成可溯源的要点。

    basis 为空、条文缺失或无法定位出处时返回空串，宁可不展示也不补写。
    """
    points = []
    for i, art in enumerate(basis or [], 1):
        text = " ".join(str(art.get("text") or "").split())
        law = str(art.get("law") or "").strip()
        label = str(art.get("label") or "").strip()
        if not text or not law:
            continue
        points.append(f"{i}）《{law}》{label}：{text}")
    return " ".join(points)

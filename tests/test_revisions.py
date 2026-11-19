"""修订要点的完整性测试：只允许出现条文原文与出处。"""
from __future__ import annotations

from cls.kb import KB
from cls.revisions import build_revision
from cls.risks import RULES, evaluate


def _normalized(text: str) -> str:
    return " ".join((text or "").split())


def test_every_rule_has_resolvable_basis():
    for rule in RULES:
        assert rule.get("basis"), f"{rule['id']} 缺少法条依据"
        for bid in rule["basis"]:
            assert bid in KB.by_id, f"{rule['id']} 引用的法条 {bid} 不在知识库"


def test_revision_is_article_text_with_source():
    for rule in RULES:
        basis = [KB.by_id[bid] for bid in rule["basis"]]
        rev = build_revision(basis)
        assert rev, f"{rule['id']} 的修订要点为空"
        for art in basis:
            assert f"《{art['law']}》{art['label']}" in rev, f"{rule['id']} 未标注出处"
            assert _normalized(art["text"]) in rev, f"{rule['id']} 未包含条文原文"


def test_revision_has_no_placeholder_or_draft_claim():
    for rule in RULES:
        rev = build_revision([KB.by_id[bid] for bid in rule["basis"]])
        for bad in ("____", "定期迭代更新", "修订草案", "可直接替换", "TODO"):
            assert bad not in rev, f"{rule['id']} 的修订要点残留 {bad}"


def test_evaluate_attaches_revision_from_basis():
    findings, _, _ = evaluate(
        "甲方应在乙方交付并验收后90日内支付合同价款，验收标准为甲方主观满意。",
        {},
        "服务合同",
    )
    assert findings, "样例文本应至少命中一条风险"
    for f in findings:
        assert f["revision"] == build_revision(f["basis"])
        assert "____" not in f["revision"]

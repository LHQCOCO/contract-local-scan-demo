"""条款修订草案库的完整性测试。"""
from __future__ import annotations

from cls.revisions import REVISIONS
from cls.risks import RULES


def test_every_rule_has_a_revision_draft():
    rule_ids = {r["id"] for r in RULES}
    assert rule_ids - set(REVISIONS) == set(), "存在没有修订草案的规则"


def test_no_orphan_revision_drafts():
    rule_ids = {r["id"] for r in RULES}
    assert set(REVISIONS) - rule_ids == set(), "存在无对应规则的修订草案"


def test_revision_drafts_are_usable_text():
    for rule_id, draft in REVISIONS.items():
        assert isinstance(draft, str)
        assert len(draft.strip()) >= 20, f"{rule_id} 的修订草案过短"
        assert draft.strip()[-1] in "。；" , f"{rule_id} 的修订草案未以完整句子结尾"
        assert "{" not in draft and "TODO" not in draft, f"{rule_id} 的修订草案残留模板占位"


def test_evaluate_attaches_revision_to_findings():
    from cls.risks import evaluate

    findings, _, _ = evaluate(
        "甲方应在乙方交付并验收后90日内支付合同价款，验收标准为甲方主观满意。",
        {},
        "服务合同",
    )
    assert findings, "样例文本应至少命中一条风险"
    for f in findings:
        assert f["revision"] == REVISIONS.get(f["id"], "")

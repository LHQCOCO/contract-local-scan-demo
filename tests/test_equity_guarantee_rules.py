"""股权/合伙（E 系列）与担保/金融（D 系列）规则的行为测试。"""
from __future__ import annotations

from cls.extraction import detect_type
from cls.kb import KB
from cls.risks import RULES, evaluate

EQUITY_TEXT = (
    "股权转让合同\n"
    "甲方：张某  乙方：李某\n"
    "甲方将其持有的某某科技有限公司30%股权（认缴出资300万元）转让给乙方，"
    "转让价款100万元，乙方于签约后10日内一次性支付。\n"
)

PARTNERSHIP_TEXT = (
    "合伙协议\n"
    "甲方王某与乙方赵某共同出资设立合伙企业，经营餐饮业务。"
    "甲方出资10万元，乙方出资5万元，利润按出资比例分配。\n"
)

GUARANTEE_TEXT = (
    "保证合同\n"
    "甲方（保证人）：某某贸易有限公司  乙方（债权人）：某某银行\n"
    "某某贸易有限公司为丙方提供保证担保，担保主债权为100万元借款。\n"
)

MORTGAGE_TEXT = (
    "抵押合同\n"
    "甲方（抵押人）以其名下位于朝阳区的房产为乙方的50万元债权设定抵押。"
    "若债务到期不还，抵押财产归债权人所有。\n"
)


def _ids(text: str, ctype: str, industry_rules: dict | None = None) -> set[str]:
    findings, _, _ = evaluate(text, {}, ctype, None, industry_rules)
    return {f["id"] for f in findings}


def test_equity_rules_trigger_on_bare_transfer():
    ids = _ids(EQUITY_TEXT, "股权转让合同")
    assert {"E01", "E02", "E08"} <= ids


def test_partnership_rules_trigger_on_bare_partnership():
    ids = _ids(PARTNERSHIP_TEXT, "合伙协议")
    assert {"E05", "E06", "E07"} <= ids


def test_guarantee_rules_trigger_on_bare_guarantee():
    ids = _ids(GUARANTEE_TEXT, "保证合同")
    assert {"D01", "D02", "D06", "D07"} <= ids


def test_mortgage_rules_trigger_on_liquidation_clause():
    ids = _ids(MORTGAGE_TEXT, "抵押合同")
    assert {"D03", "D04"} <= ids


def test_industry_switch_disables_new_rules():
    off = {"equity": False, "guarantee": False}
    ids_equity = _ids(EQUITY_TEXT, "股权转让合同", off)
    ids_guarantee = _ids(GUARANTEE_TEXT, "保证合同", off)
    assert not any(i.startswith("E") for i in ids_equity)
    assert not any(i.startswith("D") for i in ids_guarantee)


def test_equity_rules_silent_when_clauses_complete():
    text = EQUITY_TEXT + (
        "其他股东已书面同意本次转让并放弃优先购买权。"
        "该等认缴出资的出资责任由乙方承担。"
        "双方将于付款后10日内办理工商变更登记。\n"
    )
    ids = _ids(text, "股权转让合同")
    assert "E01" not in ids
    assert "E02" not in ids
    assert "E08" not in ids


def test_guarantee_rules_silent_when_clauses_complete():
    text = (
        "保证合同\n"
        "甲方：某某贸易有限公司  乙方：某某银行\n"
        "某某贸易有限公司为丙方提供连带责任保证，保证范围包括借款本金、利息及实现债权的费用，"
        "保证期间为主债务履行期限届满之日起六个月。"
        "甲方已向乙方提供同意本次担保的股东会决议文件。\n"
    )
    ids = _ids(text, "保证合同")
    assert "D01" not in ids
    assert "D02" not in ids
    assert "D06" not in ids
    assert "D07" not in ids


def test_detect_type_recognizes_new_contract_types():
    assert detect_type("股权转让合同\n甲方将所持股权转让给乙方") == "股权转让合同"
    assert detect_type("增资协议\n双方就公司增资扩股事宜达成如下协议") == "增资协议"
    assert detect_type("合伙协议\n双方合伙经营餐厅") == "合伙协议"
    assert detect_type("保证合同\n保证人为主债务提供保证担保") == "保证合同"
    assert detect_type("抵押合同\n以房产设定抵押担保") == "抵押合同"
    assert detect_type("质押合同\n以股权设定质押担保") == "质押合同"


def test_new_rules_structure_and_basis():
    assert len(RULES) == 191
    ids = [r["id"] for r in RULES]
    assert len(ids) == len(set(ids)), "规则 id 存在重复"
    for r in RULES:
        if r["id"].startswith("E"):
            assert r.get("industry") == "equity"
        if r["id"].startswith("D"):
            assert r.get("industry") == "guarantee"
        if r["id"][0] in "ED":
            for bid in r.get("basis", []):
                assert bid in KB.by_id, f"{r['id']} 引用的法条 {bid} 不在知识库中"

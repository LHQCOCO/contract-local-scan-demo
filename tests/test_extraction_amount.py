"""金额抽取回归：短位数、中文大写、无单位等常见写法都要能识别。

历史缺陷：``\\d{3,}`` 要求至少三位数字，导致“50万元”“120万元”无法抽取，
进而让“价款缺失”规则（R02）在明明写明金额的合同上误报。
"""
from __future__ import annotations

import pytest

from cls import extraction


@pytest.mark.parametrize("text,expected", [
    ("总价120万元。", "120万元"),
    ("总价款人民币50万元。", "人民币50万元"),
    ("服务费50万元。", "50万元"),
    ("运费8000元。", "8000元"),
    ("合同总金额为人民币1,200,000元。", "人民币1,200,000元"),
    ("总价人民币伍万元。", "人民币伍万元"),
    ("合同价款为人民币壹拾万元整。", "人民币壹拾万元"),
    ("租金每月3000元。", "3000元"),
])
def test_amount_pattern_covers_common_forms(text, expected):
    m = extraction.AMOUNT.search(text)
    assert m and m.group(1) == expected


@pytest.mark.parametrize("text", [
    "合同总金额待定。",
    "价款以双方另行协商为准。",
    "验收以甲方满意为准。",
])
def test_amount_pattern_ignores_vague_clauses(text):
    assert extraction.AMOUNT.search(text) is None


def test_extract_fills_amount_total_for_short_amount():
    values = extraction.extract("采购合同\n总价款人民币50万元。\n", None)[1]
    assert values["amount_total"] == "人民币50万元"


@pytest.mark.parametrize("text,party_a,party_b", [
    ("商品房预售合同\n出卖人：某房地产开发有限公司\n买受人：王某\n", "某房地产开发有限公司", "王某"),
    ("运输合同\n甲方（托运人）：某贸易公司\n乙方（承运人）：某物流公司\n", "某贸易公司", "某物流公司"),
    ("租赁合同\n出租人：张某\n承租人：李某\n", "张某", "李某"),
    ("技术转让合同\n转让方：A公司\n受让方：B公司\n", "A公司", "B公司"),
    ("借款合同\n借款人：某科技公司\n贷款人：某银行\n", "某科技公司", "某银行"),
])
def test_party_extraction_covers_role_labels(text, party_a, party_b):
    """非“甲方/乙方”的角色称谓也必须识别，否则报告首页主体显示未识别。"""
    values = extraction.extract(text, None)[1]
    assert values["party_a"] == party_a
    assert values["party_b"] == party_b


def test_extracted_clause_drops_leading_numbering():
    text = "服务合同\n二、付款方式：签约当日付清全款。\n第十二条 甲方支付服务费。\n"
    values = extraction.extract(text, None)[1]
    assert values["payment_terms"].startswith("付款方式")

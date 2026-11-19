"""保密协议（N01-N08）与采购合同（C07-C13）深挖规则的行为测试。"""
from __future__ import annotations

import pytest

from cls.kb import KB
from cls.risks import RULES, evaluate

NDA_BAD = """
保密协议
甲方：某科技公司
乙方：某供应商
一、乙方在合作中将接触甲方的技术资料与经营数据。
二、仅乙方承担保密义务，甲方无需保密。
三、乙方违反保密义务的，应支付违约金100万元。
四、合作结束后两年内，乙方不得聘用甲方员工。
五、本协议一式两份。
"""

NDA_OK = """
保密协议
甲方：某科技公司
乙方：某供应商
一、保密信息是指甲方以书面或口头形式向乙方披露的、标明保密的技术信息与经营信息，包括技术方案、客户名单、财务数据等。
二、双方互负保密义务，任何一方作为接收方均应承担相同的保密义务、违约责任与例外安排。
三、保密期限为自披露之日起五年；构成商业秘密的，至其进入公有领域时止。
四、例外情形：已为公众知悉的信息、接收方独立开发完成的信息、经披露方书面同意公开的信息、依法强制披露的信息除外。
五、协议终止后十日内，接收方应返还或销毁全部保密资料及载体、复制件，并书面确认。
六、披露保密信息不构成任何知识产权的转让或许可或授权，相关权利与所有权仍归披露方所有。
七、任何一方违反保密义务的，应赔偿对方因此遭受的实际损失。
"""

PUR_BAD = """
采购合同
甲方：某制造公司
乙方：某电子元件厂
一、甲方向乙方采购传感器10000个，总价款人民币50万元。
二、乙方应于2026年12月前将全部货物送至甲方处。
三、验收以甲方满意为准，甲方满意后付款。
四、甲方有权从应付货款中扣除违约金及赔偿款。
五、双方应诚信履约。
六、本合同一式两份。
"""

PUR_OK = """
采购合同
甲方：某制造公司
乙方：某电子元件厂
一、甲方向乙方采购传感器10000个，总价款人民币50万元；本笔采购已取得三家供应商书面报价并留存比价记录。
二、交货地点：甲方位于上海市浦东新区的仓库；运输方式为公路运输，运费由乙方承担。
三、包装要求：防静电包装并符合国家标准，包装费用包含在合同价款内。
四、验收标准：以本合同附件技术参数表及双方确认封样为准；甲方收货后15日内完成检验，逾期未书面提出异议视为验收合格。
五、质保期自验收合格之日起24个月，质保期内乙方免费维修或更换，提供售后服务。
六、分批交货：分四批交付，每批2500个，允许±2%溢短装并按合同单价多退少补，单批不合格不影响其他批次效力。
七、任何扣款或抵销均须凭双方书面确认的结算单进行，并提前10日书面通知对方。
八、廉洁条款：双方及其工作人员不得给予或收受回扣、礼金及其他不正当利益，违反廉洁条款的，守约方有权解除合同并索赔。
"""

N_NEW = {f"N{n:02d}" for n in range(1, 9)}
C_NEW = {f"C{n:02d}" for n in range(7, 14)}


def _ids(findings):
    return {f["id"] for f in findings}


def test_rule_count_is_168():
    assert len(RULES) == 191


# ---------- 保密协议 ----------

def test_bad_nda_triggers_all_n_rules():
    findings, _, _ = evaluate(NDA_BAD, {}, "保密协议")
    missing = N_NEW - _ids(findings)
    assert not missing, f"问题保密协议应命中 {sorted(missing)}"


def test_clean_nda_exempts_all_n_rules():
    findings, _, _ = evaluate(NDA_OK, {}, "保密协议")
    hit = _ids(findings) & N_NEW
    assert not hit, f"完备保密协议不应命中 {sorted(hit)}"


@pytest.mark.parametrize("ctype", ["服务合同", "采购合同", "借款合同", "劳动合同"])
def test_n_rules_do_not_leak_to_other_types(ctype):
    findings, _, _ = evaluate(NDA_BAD, {}, ctype)
    leak = _ids(findings) & N_NEW
    assert not leak, f"N 规则不应命中 {ctype}: {sorted(leak)}"


# ---------- 采购合同 ----------

def test_bad_purchase_triggers_all_new_c_rules():
    findings, _, _ = evaluate(PUR_BAD, {}, "采购合同")
    missing = C_NEW - _ids(findings)
    assert not missing, f"问题采购合同应命中 {sorted(missing)}"


def test_clean_purchase_exempts_all_new_c_rules():
    findings, _, _ = evaluate(PUR_OK, {}, "采购合同")
    hit = _ids(findings) & C_NEW
    assert not hit, f"完备采购合同不应命中 {sorted(hit)}"


@pytest.mark.parametrize("ctype", ["借款合同", "服务合同", "保密协议", "建设工程合同"])
def test_c_rules_do_not_leak_to_other_types(ctype):
    findings, _, _ = evaluate(PUR_BAD, {}, ctype)
    leak = _ids(findings) & C_NEW
    assert not leak, f"C 规则不应命中 {ctype}: {sorted(leak)}"


# ---------- 逐条独立性（参数化触发样本） ----------

@pytest.mark.parametrize("rid,text,ctype", [
    ("N04", "保密协议：乙方违反保密义务的，应支付违约金100万元。", "保密协议"),
    ("N06", "保密协议：合作期间及结束后，乙方不得聘用甲方员工。", "保密协议"),
    ("N07", "保密协议：仅乙方承担保密义务，甲方无需承担保密义务。", "保密协议"),
    ("C07", "采购合同：验收以甲方满意为准，甲方满意后付款。", "采购合同"),
    ("C12", "采购合同：甲方有权从应付货款中扣除违约金及赔偿款。", "采购合同"),
])
def test_each_match_rule_fires(rid, text, ctype):
    findings, _, _ = evaluate(text, {}, ctype)
    assert rid in _ids(findings), f"{rid} 未命中其专属样本"


# ---------- 结构与法条 ----------

def test_new_rules_have_valid_basis():
    for rid in sorted(N_NEW | C_NEW):
        rule = next(r for r in RULES if r["id"] == rid)
        assert rule.get("basis"), f"{rid} 缺少法条依据"
        for bid in rule["basis"]:
            assert bid in KB.by_id, f"{rid} 引用的法条 {bid} 不在知识库"


def test_new_rule_severity_levels():
    expect = {
        "N01": "medium", "N02": "medium", "N03": "medium", "N04": "high",
        "N05": "low", "N06": "high", "N07": "medium", "N08": "low",
        "C07": "high", "C08": "medium", "C09": "low", "C10": "medium",
        "C11": "low", "C12": "medium", "C13": "low",
    }
    for rid, sev in expect.items():
        rule = next(r for r in RULES if r["id"] == rid)
        assert rule["severity"] == sev, f"{rid} 严重级别应为 {sev}"

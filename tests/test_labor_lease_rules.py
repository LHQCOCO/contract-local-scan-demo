"""劳动（L11-L18）与租赁（Z05-Z11）深挖规则的行为测试。"""
from __future__ import annotations

import pytest

from cls.kb import KB
from cls.risks import RULES, evaluate

LABOR_BAD = (
    "劳动合同\n甲方：某某公司  乙方：张某\n"
    "乙方工作地点为全国各分公司，服从甲方调配。乙方自愿放弃社保，甲方每月发放社保补贴500元代替缴纳。"
    "合同期内乙方不得结婚生育。公司实行末位淘汰制，连续两季度考核排名末位的予以辞退。"
    "乙方提前离职的，应向甲方支付违约金2万元。乙方月工资8000元。\n"
)

LABOR_OK = (
    "劳动合同\n甲方：某某公司  乙方：张某\n"
    "乙方工作地点为上海市浦东新区。乙方月工资8000元，于每月10日发放。"
    "甲方依法为乙方缴纳社会保险。乙方违反竞业限制约定的，应支付违约金。\n"
)

LEASE_BARE = "租赁合同\n甲方将某商铺出租给乙方，月租金1万元，租期3年。\n"

LEASE_OK = LEASE_BARE + (
    "甲方承诺房屋权属无抵押。物业费由甲方承担，水电费由乙方承担。"
    "装修添附归甲方。乙方享有优先承租权。退租时恢复原状并腾退房屋。"
    "免租装修期30日。双方办理租赁登记备案。转租须经甲方书面同意。房屋维修由甲方负责。\n"
)


def _ids(text: str, ctype: str) -> set[str]:
    findings, _, _ = evaluate(text, {}, ctype, None, None)
    return {f["id"] for f in findings}


def test_labor_bad_triggers_new_rules():
    ids = _ids(LABOR_BAD, "劳动合同")
    assert {"L11", "L12", "L13", "L14", "L15", "L17"} <= ids


def test_labor_service_term_rule():
    text = "劳动合同\n甲方为乙方提供技术培训，约定服务期3年，乙方违反服务期的应支付违约金5万元。"
    assert "L18" in _ids(text, "劳动合同")


def test_labor_special_hours_rule():
    text = "劳动合同\n乙方岗位实行综合计算工时工作制。"
    assert "L16" in _ids(text, "劳动合同")


def test_labor_clean_contract_exempt():
    ids = _ids(LABOR_OK, "劳动合同")
    assert "L11" not in ids
    assert "L12" not in ids
    assert "L15" not in ids
    assert "L17" not in ids


def test_lease_bare_triggers_all_new_rules():
    ids = _ids(LEASE_BARE, "租赁合同")
    assert {"Z05", "Z06", "Z07", "Z08", "Z09", "Z10", "Z11"} <= ids


def test_lease_complete_contract_exempt():
    ids = _ids(LEASE_OK, "租赁合同")
    for rid in ("Z05", "Z06", "Z07", "Z08", "Z09", "Z10", "Z11"):
        assert rid not in ids, rid


def test_gate_isolation():
    # 租赁规则只在租赁合同触发；劳动规则只在劳动合同触发
    assert not any(i.startswith("Z") for i in _ids(LABOR_BAD, "劳动合同"))
    assert not any(i.startswith("L") for i in _ids(LEASE_BARE, "租赁合同"))


def test_new_rules_structure_and_basis():
    assert len(RULES) == 191
    ids = [r["id"] for r in RULES]
    assert len(ids) == len(set(ids)), "规则 id 存在重复"
    new_ids = {f"L{n}" for n in range(11, 19)} | {f"Z{n:02d}" for n in range(5, 12)}
    by_id = {r["id"]: r for r in RULES}
    assert new_ids <= set(by_id)
    for rid in new_ids:
        for bid in by_id[rid].get("basis", []):
            assert bid in KB.by_id, f"{rid} 引用的法条 {bid} 不在知识库中"


# ---------- 补充用例：pattern 变体与豁免边界 ----------


def test_labor_social_security_variants():
    # L12 的三种放弃社保表述均应触发
    for clause in ("乙方自愿放弃社保", "乙方自愿不缴社保", "甲方发放社保补贴代替缴纳"):
        ids = _ids(f"劳动合同\n{clause}。", "劳动合同")
        assert "L12" in ids, clause


def test_labor_marital_restriction_variants():
    # L14 的两种限制婚育表述均应触发
    for clause in ("合同期内乙方不得怀孕", "乙方怀孕的视为自动离职"):
        ids = _ids(f"劳动合同\n{clause}。", "劳动合同")
        assert "L14" in ids, clause


def test_labor_penalty_exempt_with_service_term():
    # 违约金限定在服务期场景时 L11 豁免；但无培训费用依据时 L18 仍应触发
    text = "劳动合同\n甲方为乙方提供专项培训，约定服务期3年，乙方违反服务期约定的应支付违约金。"
    ids = _ids(text, "劳动合同")
    assert "L11" not in ids
    assert "L18" in ids


def test_labor_service_term_with_training_fee_exempt():
    text = "劳动合同\n甲方为乙方提供专项培训，培训费用共计3万元，约定服务期3年，乙方违反服务期约定的按未履行比例支付违约金。"
    assert "L18" not in _ids(text, "劳动合同")


def test_labor_worksite_specific_city_exempt():
    text = "劳动合同\n乙方工作地点为北京市朝阳区，因项目需要可临时出差。"
    assert "L15" not in _ids(text, "劳动合同")


def test_labor_special_hours_unscheduled_variant():
    assert "L16" in _ids("劳动合同\n乙方岗位实行不定时工作制。", "劳动合同")


def test_labor_paydate_variants_exempt():
    for clause in ("乙方工资于每月5日前发放", "发薪日为每月5日", "工资发放日为每月5日"):
        ids = _ids(f"劳动合同\n{clause}。", "劳动合同")
        assert "L17" not in ids, clause


@pytest.mark.parametrize("rid,clause", [
    ("Z05", "租赁期间的装饰装修归甲方所有"),
    ("Z06", "乙方享有优先续租权"),
    ("Z07", "甲方承诺房屋权属无抵押"),
    ("Z08", "物业费由甲方承担"),
    ("Z09", "退租时乙方恢复原状"),
    ("Z10", "免租装修期为30日"),
    ("Z11", "双方共同办理租赁登记备案"),
])
def test_lease_exemption_markers_individually(rid, clause):
    # 每条 Z 规则的豁免词单独出现时，该规则豁免且其余 Z 规则仍触发
    ids = _ids(LEASE_BARE + clause + "。", "租赁合同")
    assert rid not in ids
    others = {f"Z{n:02d}" for n in range(5, 12)} - {rid}
    assert others <= ids


def test_new_rules_severity():
    by_id = {r["id"]: r for r in RULES}
    for rid in ("L12", "L13", "L14"):
        assert by_id[rid]["severity"] == "high", rid
    for rid in ("L11", "L15", "L18"):
        assert by_id[rid]["severity"] == "medium", rid
    for rid in ("L16", "L17"):
        assert by_id[rid]["severity"] == "low", rid
    for rid in ("Z05", "Z07"):
        assert by_id[rid]["severity"] == "medium", rid
    for rid in ("Z06", "Z08", "Z09", "Z10", "Z11"):
        assert by_id[rid]["severity"] == "low", rid

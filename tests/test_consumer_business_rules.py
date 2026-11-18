"""消费/生活服务（W 系列）与经营/商业服务（B 系列）规则的行为测试。"""
from __future__ import annotations

from cls.extraction import detect_type
from cls.kb import KB
from cls.risks import RULES, evaluate

GYM_TEXT = (
    "健身服务合同\n"
    "甲方：某某健身有限公司  乙方：王某\n"
    "乙方购买私教课程包30节，预付费用9000元。会员卡余额过期作废。"
    "会员在馆内发生意外概不负责。本合同最终解释权归本店所有。\n"
)

TOUR_TEXT = (
    "旅游合同\n"
    "甲方：某某旅行社  乙方：张某\n"
    "云南六日游，团费3000元，签约时付清。\n"
)

PHOTO_TEXT = (
    "摄影服务合同\n"
    "甲方：某某摄影工作室  乙方：赵某\n"
    "婚纱照拍摄套餐5000元，精修30张，签约后付定金50%。\n"
)

FRANCHISE_TEXT = (
    "特许经营合同\n"
    "甲方：某某餐饮管理有限公司  乙方：李某\n"
    "乙方支付加盟费20万元、保证金5万元，在本地开设品牌加盟店。\n"
)


def _ids(text: str, ctype: str, industry_rules: dict | None = None) -> set[str]:
    findings, _, _ = evaluate(text, {}, ctype, None, industry_rules)
    return {f["id"] for f in findings}


def test_gym_triggers_prepaid_and_bullying_rules():
    ids = _ids(GYM_TEXT, "消费服务合同")
    assert {"W01", "W02", "W03", "W04"} <= ids


def test_tour_triggers_tour_rules():
    ids = _ids(TOUR_TEXT, "旅游合同")
    assert {"W06", "W07"} <= ids


def test_photo_triggers_portrait_rule_even_with_retouch_count():
    # 只写精修张数不等于约定了底片与肖像使用，应触发 W09
    ids = _ids(PHOTO_TEXT, "消费服务合同")
    assert "W09" in ids


def test_franchise_triggers_business_rules():
    ids = _ids(FRANCHISE_TEXT, "特许经营合同")
    assert {"B01", "B02", "B03", "B04", "B09"} <= ids


def test_industry_switch_disables_consumer_business_rules():
    off = {"consumer": False, "business": False}
    ids_gym = _ids(GYM_TEXT, "消费服务合同", off)
    ids_fran = _ids(FRANCHISE_TEXT, "特许经营合同", off)
    assert not any(i.startswith("W") for i in ids_gym)
    assert not any(i.startswith("B") for i in ids_fran)


def test_prepaid_rule_silent_when_refund_clause_present():
    text = GYM_TEXT + "未消费余额可随时退费，按剩余次数折算，7日内到账。"
    ids = _ids(text, "消费服务合同")
    assert "W01" not in ids


def test_photo_rule_silent_when_negative_and_portrait_agreed():
    text = PHOTO_TEXT + "甲方交付全部原始底片；乙方影像用于宣传须经乙方书面同意。"
    ids = _ids(text, "消费服务合同")
    assert "W09" not in ids


def test_detect_type_recognizes_consumer_business_types():
    assert detect_type("健身服务合同\n私教课程包30节") == "消费服务合同"
    assert detect_type("特许经营合同\n加盟费20万元") == "特许经营合同"
    assert detect_type("旅游合同\n甲方：某某旅行社") == "旅游合同"
    assert detect_type("居间合同\n中介服务费") == "居间合同"
    assert detect_type("物业服务合同\n物业费按季支付") == "物业服务合同"
    assert detect_type("广告发布合同\n广告代理") == "广告合同"
    assert detect_type("仓储合同\n保管人") == "仓储合同"


def test_new_rules_structure_and_basis():
    assert len(RULES) == 191
    ids = [r["id"] for r in RULES]
    assert len(ids) == len(set(ids)), "规则 id 存在重复"
    for r in RULES:
        if r["id"].startswith("W"):
            assert r.get("industry") == "consumer"
        if r["id"].startswith("B"):
            assert r.get("industry") == "business"
        if r["id"][0] in "WB":
            for bid in r.get("basis", []):
                assert bid in KB.by_id, f"{r['id']} 引用的法条 {bid} 不在知识库中"

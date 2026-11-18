"""服务合同（S01-S08）、运输（Y05-Y08）、商品房预售（F05-F08）、数据（P04-P05）规则行为测试，含 detect_type 回归。"""
from __future__ import annotations

import pytest

from cls.extraction import detect_type
from cls.kb import KB
from cls.revisions import REVISIONS
from cls.risks import RULES, evaluate

SVC_BAD = """服务合同
甲方：某集团
乙方：某咨询公司
一、乙方为甲方提供管理提升顾问服务，服务费50万元。
二、验收以甲方满意为准，甲方满意后付款。
三、乙方有权自行更换项目团队成员。
四、任何情况下，乙方均不承担任何间接损失或利润损失。
五、本合同一式两份。
"""

SVC_OK = """服务合同
甲方：某集团
乙方：某咨询公司
一、服务内容：乙方为甲方提供管理诊断与流程优化顾问服务，交付物为诊断报告与优化方案，详见附件一。
二、验收标准：报告通过甲方专家评审会评审；甲方应于收到交付物后15日内书面提出异议，逾期视为验收合格。
三、服务费50万元，包含人工费、差旅费与税费；额外支出经审批后凭票报销。
四、核心服务人员：张某某（项目经理）等，未经甲方书面同意不得更换服务人员。
五、甲方应于签约后10日内提供相关资料与数据，并于收到成果后5日内反馈意见；因甲方迟延导致延误的，服务期限相应顺延，双方应互相配合与协助。
六、服务成果不符合验收标准的，乙方应免费返工、重做或采取其他补救措施；经两次整改仍不合格的，甲方可减价或解除。
七、未经甲方书面同意，乙方不得将核心服务转委托或分包给第三方。
八、任何一方赔偿责任以合同总额的100%为限，但故意或重大过失除外。
"""

TRN_BAD = """运输合同
甲方（托运人）：某贸易公司
乙方（承运人）：某物流公司
一、甲方委托乙方将一批电子产品自广州运至成都，运费8000元。
二、货损赔偿：未保价的货物，按运费的三倍赔偿为限。
三、甲方未付清任何费用的，乙方有权留置全部货物。
四、运输途中延误的，乙方不承担任何责任；货损货差概不负责。
五、本合同一式两份。
"""

TRN_OK = """运输合同
甲方（托运人）：某贸易公司
乙方（承运人）：某物流公司
一、甲方委托乙方运输电子产品一批，共50件，重量2吨，品名及规格详见托运清单，甲方保证申报信息真实准确。
二、运输保险由甲方购买，投保金额为货值的110%，投保险种为一切险。
三、运输时效：自乙方提货之日起5日内运抵甲方指定仓库；逾期每24小时按运费5%支付违约金。
四、货损赔偿：货物毁损灭失的，按货物实际价值赔偿；已保价的按保价金额赔付。
五、托运人或收货人未付运费的，乙方可就与欠付费用价值相当的货物行使留置权，并书面通知给予10日宽限期。
六、货物为精密仪器，乙方须按防震防潮标准包装并加贴标识，随车携带温湿度记录文件。
七、收货人签收仅表示收到货物，外观瑕疵当场提出，隐蔽瑕疵应于签收后7日内书面提出。
八、因不可抗力造成延误或货损的，乙方凭证明免责；其他情形造成损失的，依法承担赔偿责任。
"""

RE_BAD = """商品房预售合同
出卖人：某房地产开发有限公司
买受人：王某
一、买受人购买出卖人开发的某楼盘3栋501室，总价120万元。
二、付款方式：签约当日付清全款。
三、房屋具体情况以最终交付为准。
四、本合同一式两份。
"""

RE_OK = """商品房预售合同
出卖人：某房地产开发有限公司
买受人：王某
一、出卖人已取得商品房预售许可证（证号：X建房预字第2026001号）、国有土地使用证（证号：X国用2026第001号）、建设工程规划许可证及施工许可证。
二、买受人购买3栋501室，建筑面积89平方米，总价120万元。
三、面积差异处理：实测面积与约定面积误差比绝对值3%以内据实结算，多退少补；超出3%的买受人有权退房。
四、交付日期：出卖人应于2027年6月30日前交付验收合格的房屋；逾期交房按日万分之三支付违约金。
五、出卖人应于交付后90日内协助办理不动产权证登记，逾期未取得权属证书的，承担违约责任。
六、销售广告及样板房展示的装修标准与配套设施作为合同附件，与本合同具有同等效力；规划变更或设计变更的，出卖人应于批准后10日内书面通知买受人。
七、预售资金存入银行监管账户，专款专用。
"""

S_NEW = {f"S{n:02d}" for n in range(1, 9)}
Y_NEW = {f"Y{n:02d}" for n in range(5, 9)}
F_NEW = {f"F{n:02d}" for n in range(5, 9)}
P_NEW = {"P04", "P05"}


def _ids(findings):
    return {f["id"] for f in findings}


def test_rule_count_is_173():
    assert len(RULES) == 191


# ---------- 服务合同 ----------

def test_bad_service_triggers_all_s_rules():
    findings, _, _ = evaluate(SVC_BAD, {}, "服务合同")
    missing = S_NEW - _ids(findings)
    assert not missing, f"问题服务合同应命中 {sorted(missing)}"


def test_clean_service_exempts_all_s_rules():
    findings, _, _ = evaluate(SVC_OK, {}, "服务合同")
    hit = _ids(findings) & S_NEW
    assert not hit, f"完备服务合同不应命中 {sorted(hit)}"


@pytest.mark.parametrize("ctype", ["采购合同", "技术开发合同", "劳动合同", "运输合同"])
def test_s_rules_do_not_leak_to_other_types(ctype):
    findings, _, _ = evaluate(SVC_BAD, {}, ctype)
    leak = _ids(findings) & S_NEW
    assert not leak, f"S 规则不应命中 {ctype}: {sorted(leak)}"


# ---------- 运输合同 ----------

def test_bad_transport_triggers_all_new_y_rules():
    findings, _, _ = evaluate(TRN_BAD, {}, "运输合同")
    missing = Y_NEW - _ids(findings)
    assert not missing, f"问题运输合同应命中 {sorted(missing)}"


def test_clean_transport_exempts_all_new_y_rules():
    findings, _, _ = evaluate(TRN_OK, {}, "运输合同")
    hit = _ids(findings) & Y_NEW
    assert not hit, f"完备运输合同不应命中 {sorted(hit)}"


@pytest.mark.parametrize("ctype", ["采购合同", "服务合同", "借款合同"])
def test_y_rules_do_not_leak_to_other_types(ctype):
    findings, _, _ = evaluate(TRN_BAD, {}, ctype)
    leak = _ids(findings) & Y_NEW
    assert not leak, f"Y 规则不应命中 {ctype}: {sorted(leak)}"


# ---------- 商品房预售合同 ----------

def test_bad_presale_triggers_all_new_f_rules():
    findings, _, _ = evaluate(RE_BAD, {}, "商品房预售合同")
    missing = F_NEW - _ids(findings)
    assert not missing, f"问题预售合同应命中 {sorted(missing)}"


def test_clean_presale_exempts_all_new_f_rules():
    findings, _, _ = evaluate(RE_OK, {}, "商品房预售合同")
    hit = _ids(findings) & F_NEW
    assert not hit, f"完备预售合同不应命中 {sorted(hit)}"


@pytest.mark.parametrize("ctype", ["租赁合同", "建设工程合同", "服务合同"])
def test_f_rules_do_not_leak_to_other_types(ctype):
    findings, _, _ = evaluate(RE_BAD, {}, ctype)
    leak = _ids(findings) & F_NEW
    assert not leak, f"F 规则不应命中 {ctype}: {sorted(leak)}"


# ---------- 数据合规（横切） ----------

@pytest.mark.parametrize("rid,text", [
    ("P04", "服务协议：乙方一次性授权甲方使用其个人信息。"),
    ("P05", "服务协议：甲方有权将用户的个人信息共享给关联公司。"),
])
def test_each_privacy_match_rule_fires(rid, text):
    findings, _, _ = evaluate(text, {}, "服务合同")
    assert rid in _ids(findings), f"{rid} 未命中其专属样本"


def test_privacy_rules_do_not_misfire_on_clean_text():
    findings, _, _ = evaluate(SVC_OK, {}, "服务合同")
    assert not (P_NEW & _ids(findings))


# ---------- 逐条独立性（参数化触发样本） ----------

@pytest.mark.parametrize("rid,text,ctype", [
    ("S02", "服务合同：验收以甲方满意为准，甲方满意后付款。", "服务合同"),
    ("S04", "服务合同：乙方有权自行更换项目团队成员。", "服务合同"),
    ("S06", "服务合同：任何情况下，乙方均不承担任何间接损失。", "服务合同"),
    ("Y05", "运输合同：未保价的货物，按运费的三倍赔偿为限。", "运输合同"),
    ("Y07", "运输合同：甲方未付清任何费用的，乙方有权留置全部货物。", "运输合同"),
    ("Y08", "运输合同：货损货差概不负责。", "运输合同"),
])
def test_each_match_rule_fires(rid, text, ctype):
    findings, _, _ = evaluate(text, {}, ctype)
    assert rid in _ids(findings), f"{rid} 未命中其专属样本"


# ---------- detect_type 回归（T/Y/F 盲区修复） ----------

@pytest.mark.parametrize("text,expect", [
    ("软件开发合同\n乙方为甲方开发APP并交付源代码。", "软件开发合同"),
    ("技术开发合同\n甲方委托乙方开发订单管理系统。", "技术开发合同"),
    ("技术服务合同\n乙方为甲方提供运维技术服务。", "技术服务合同"),
    ("技术转让合同\n甲方将专利技术转让给乙方。", "技术转让合同"),
    ("货物运输合同\n甲方委托乙方将设备从上海运至北京。", "运输合同"),
    ("物流服务合同\n乙方为甲方提供仓配一体化物流服务。", "物流合同"),
    ("货运代理合同\n甲方委托乙方办理出口订舱报关。", "货运代理合同"),
    ("商品房预售合同\n出卖人向买受人出售期房。", "商品房预售合同"),
    ("国有建设用地使用权出让合同\n出让人市自然资源局。", "土地出让合同"),
    ("土地使用权转让合同\n转让人将宗地转让给受让人。", "土地转让合同"),
    ("委托代建合同\n建设单位委托代建方负责项目管理。", "委托代建合同"),
    ("房地产合作开发合同\n双方合作开发某住宅项目。", "房地产合作开发合同"),
    ("采购合同\n甲方向乙方采购传感器，运输方式为公路运输，运费由乙方承担。", "采购合同"),
    ("咨询服务合同\n乙方为甲方提供企业管理咨询服务。", "咨询服务合同"),
    ("区域代理合同\n甲方授权乙方为某品牌华东区独家代理商。", "代理经销合同"),
])
def test_detect_type_recognition(text, expect):
    assert detect_type(text) == expect


# ---------- 结构与法条 ----------

def test_new_rules_have_revisions_and_valid_basis():
    for rid in sorted(S_NEW | Y_NEW | F_NEW | P_NEW):
        assert rid in REVISIONS, f"{rid} 缺少修订草案"
        rule = next(r for r in RULES if r["id"] == rid)
        for bid in rule.get("basis", []):
            assert bid in KB.by_id, f"{rid} 引用的法条 {bid} 不在知识库"


def test_new_rule_severity_levels():
    expect = {
        "S01": "high", "S02": "high", "S03": "low", "S04": "medium",
        "S05": "medium", "S06": "medium", "S07": "low", "S08": "low",
        "Y05": "high", "Y06": "low", "Y07": "medium", "Y08": "high",
        "F05": "high", "F06": "medium", "F07": "high", "F08": "low",
        "P04": "medium", "P05": "high",
    }
    for rid, sev in expect.items():
        rule = next(r for r in RULES if r["id"] == rid)
        assert rule["severity"] == sev, f"{rid} 严重级别应为 {sev}"

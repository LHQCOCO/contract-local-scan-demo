"""刑事合规（X02-X06）规则行为测试：反商业贿赂与职务侵占方向。"""
from __future__ import annotations

import pytest

from cls.kb import KB
from cls.revisions import REVISIONS
from cls.risks import RULES, evaluate

BAD = """采购合同
甲方：某集团
乙方：某供应商
一、甲方向乙方采购办公设备，总价50万元。
二、乙方应按合同金额的5%向甲方经办人支付回扣，通过账外方式结算。
三、乙方可通过虚开发票报销甲方公关费用。
四、本合同一式两份。
"""

OK = """采购合同
甲方：某集团
乙方：某供应商
一、甲方向乙方采购办公设备，总价50万元。
二、廉洁条款：双方及其工作人员不得给予或收受回扣、礼金、有价证券及其他不正当利益，禁止行贿受贿。
三、本合同款项通过双方对公账户收付，发票真实合规，严禁虚开、账外收支或挪用资金；佣金、返利应如实入账。
四、双方建立利益冲突与关联交易申报及回避机制，重大关联交易须经合规审查。
五、商务礼品与宴请应符合招待标准，不得送礼或安排高消费娱乐。
六、本合同一式两份。
"""

X_NEW = {f"X{n:02d}" for n in range(2, 7)}


def _ids(findings):
    return {f["id"] for f in findings}


def test_rule_count_is_173():
    assert len(RULES) == 191


def test_bad_contract_triggers_all_x_rules():
    findings, _, _ = evaluate(BAD, {}, "采购合同")
    missing = X_NEW - _ids(findings)
    assert not missing, f"问题合同应命中 {sorted(missing)}"


def test_clean_contract_exempts_all_x_rules():
    findings, _, _ = evaluate(OK, {}, "采购合同")
    hit = _ids(findings) & X_NEW
    assert not hit, f"完备合同不应命中 {sorted(hit)}"


@pytest.mark.parametrize("ctype", ["服务合同", "借款合同", "租赁合同", "建设工程合同"])
def test_x_rules_are_cross_cutting(ctype):
    findings, _, _ = evaluate(BAD, {}, ctype)
    assert X_NEW <= _ids(findings), f"X 规则应横切命中 {ctype}"


@pytest.mark.parametrize("rid,text,ctype", [
    ("X03", "服务合同：乙方可通过账外账户支付结算。", "服务合同"),
    ("X04", "采购合同：乙方按合同金额3%向甲方经办人支付回扣。", "采购合同"),
])
def test_each_match_rule_fires(rid, text, ctype):
    findings, _, _ = evaluate(text, {}, ctype)
    assert rid in _ids(findings), f"{rid} 未命中其专属样本"


def test_prohibition_clauses_do_not_misfire():
    # 禁止性条款不应被 X03/X04 误判为违规安排
    text = "本合同严禁虚开发票，不得账外支付，禁止给予或收受回扣。"
    findings, _, _ = evaluate(text, {}, "服务合同")
    assert not ({"X03", "X04"} & _ids(findings))


def test_new_rules_have_revisions_and_valid_basis():
    for rid in sorted(X_NEW):
        assert rid in REVISIONS, f"{rid} 缺少修订草案"
        rule = next(r for r in RULES if r["id"] == rid)
        for bid in rule.get("basis", []):
            assert bid in KB.by_id, f"{rid} 引用的法条 {bid} 不在知识库"


def test_new_rule_severity_levels():
    expect = {"X02": "medium", "X03": "high", "X04": "high", "X05": "low", "X06": "low"}
    for rid, sev in expect.items():
        rule = next(r for r in RULES if r["id"] == rid)
        assert rule["severity"] == sev, f"{rid} 严重级别应为 {sev}"


# ---------- 逐条触发：absence 类 ----------

@pytest.mark.parametrize("rid,text", [
    ("X02", "采购合同：甲方向乙方采购设备，总价10万元。双方应诚信履约。"),
    ("X05", "采购合同：甲方向乙方采购设备，总价10万元。"),
    ("X06", "采购合同：甲方向乙方采购设备，总价10万元。"),
])
def test_absence_rules_fire_when_markers_absent(rid, text):
    findings, _, _ = evaluate(text, {}, "采购合同")
    assert rid in _ids(findings), f"{rid} 在缺失标记时应命中"


# ---------- 逐条触发：match 类（覆盖每种模式） ----------

@pytest.mark.parametrize("rid,text", [
    # X03 模式1：账外/小金库 + 方式/账户 + 结算/支付
    ("X03", "服务合同：乙方通过账外账户方式支付结算相关费用。"),
    # X03 模式2：虚开/虚报 + 发票/费用 + 报销/入账
    ("X03", "服务合同：乙方可虚开发票用于报销甲方费用。"),
    # X03 模式3：个人 + 占用/挪用 + 公司 + 资金
    ("X03", "服务合同：甲方允许个人占用公司资金用于周转。"),
    # X03 模式4：可/允许 + 挪用 + 资金/款项
    ("X03", "服务合同：经甲方同意可挪用专项资金。"),
    # X04 模式1：按合同金额比例 + 支付回扣
    ("X04", "采购合同：乙方按合同总价的3%支付回扣。"),
    # X04 模式2：向经办人/负责人 + 支付/给予回扣
    ("X04", "采购合同：乙方向甲方采购负责人支付回扣。"),
    # X04 模式3：支付/给予 + 好处费
    ("X04", "服务合同：乙方应向甲方支付好处费。"),
    # X04 模式4：账外 + 支付/给付 + 回扣/佣金
    ("X04", "服务合同：乙方账外支付佣金作为回报。"),
])
def test_match_rules_fire_per_pattern(rid, text):
    findings, _, _ = evaluate(text, {}, "采购合同" if "采购" in text else "服务合同")
    assert rid in _ids(findings), f"{rid} 未命中其专属样本：{text}"


# ---------- 逐条豁免：合规表述不应触发 ----------

@pytest.mark.parametrize("rid,text", [
    ("X02", "服务合同：双方承诺廉洁合作，不得给予或收受回扣及其他不正当利益。"),
    ("X02", "服务合同：本合同适用反商业贿赂条款，禁止行贿受贿。"),
    ("X05", "服务合同：双方应履行利益冲突披露义务，关联交易须经合规审查。"),
    ("X05", "服务合同：建立关联方回避机制，重大交易须披露。"),
    ("X06", "服务合同：商务宴请应符合招待标准，不得送礼。"),
    ("X06", "服务合同：禁止安排高消费娱乐活动，礼品金额上限500元。"),
])
def test_absence_rules_exempt_when_markers_present(rid, text):
    findings, _, _ = evaluate(text, {}, "服务合同")
    assert rid not in _ids(findings), f"{rid} 在含合规标记时不应命中：{text}"


# ---------- 误报防护：禁止性条款不应被误判 ----------

@pytest.mark.parametrize("text", [
    "本合同严禁虚开发票、虚报费用，不得账外支付。",
    "禁止个人挪用公司资金，不得占用单位款项。",
    "双方不得给予或收受回扣、好处费，禁止账外结算。",
    "严禁向工作人员支付回扣，不得索取任何形式的好处费。",
])
def test_prohibition_clauses_never_misfire(text):
    findings, _, _ = evaluate(text, {}, "服务合同")
    hit = {"X03", "X04"} & _ids(findings)
    assert not hit, f"禁止性条款不应触发 {sorted(hit)}：{text}"


# ---------- 边界：合法佣金/返利不应触发 X04 ----------

@pytest.mark.parametrize("text", [
    "服务合同：乙方按销售额的5%向甲方支付销售佣金，佣金如实入账。",
    "采购合同：双方约定年终返利，返利冲抵货款并开具发票。",
    "代理合同：甲方按交易额的2%向乙方支付代理手续费。",
])
def test_legitimate_commission_does_not_fire_x04(text):
    findings, _, _ = evaluate(text, {}, "服务合同")
    assert "X04" not in _ids(findings), f"合法佣金不应触发 X04：{text}"


# ---------- 草案质量规范 ----------

def test_x_revisions_meet_conventions():
    for rid in sorted(X_NEW):
        rev = REVISIONS.get(rid, "")
        assert len(rev) >= 20, f"{rid} 草案过短（{len(rev)} 字符）"
        assert rev.endswith(("。", "；")), f"{rid} 草案应以句号或分号结尾"
        assert "____" in rev, f"{rid} 草案应包含 ____ 占位符"
        assert "{" not in rev, f"{rid} 草案不应包含未处理的占位符"
        assert "TODO" not in rev, f"{rid} 草案不应包含 TODO"


# ---------- match 规则命中证据 ----------

def test_match_rules_return_evidence():
    for rid in ("X03", "X04"):
        text = "采购合同：乙方按合同金额的3%向甲方经办人支付回扣，通过账外方式结算。"
        findings, _, _ = evaluate(text, {}, "采购合同")
        for f in findings:
            if f["id"] == rid:
                assert f["evidence"], f"{rid} 命中后应返回证据句"
                break
        else:
            pytest.fail(f"{rid} 未命中")


# ---------- 规则结构一致性 ----------

def test_x_rules_have_required_fields():
    required = {"id", "title", "category", "severity", "mode", "basis", "analysis", "advice"}
    for rid in sorted(X_NEW):
        rule = next(r for r in RULES if r["id"] == rid)
        missing = required - set(rule.keys())
        assert not missing, f"{rid} 缺少字段 {sorted(missing)}"
        assert rule["category"] == "刑事风险", f"{rid} 分类应为刑事风险"
        assert rule["severity"] in {"high", "medium", "low"}
        assert rule["mode"] in {"match", "absence"}
        assert len(rule["basis"]) >= 1, f"{rid} 应至少引用一条法条"


# ---------- 与既有规则不冲突：在通用合同上仅 X 规则被本测试关注 ----------

def test_x_rules_independent_of_other_families():
    # 确认 X 系列与 P/R 系列规则 id 不重叠
    all_ids = {r["id"] for r in RULES}
    assert len(all_ids) == len(RULES), "存在重复规则 ID"
    # X 系列全部以 X 开头
    x_ids = {r["id"] for r in RULES if r["id"].startswith("X")}
    assert x_ids == {"X01", "X02", "X03", "X04", "X05", "X06"}


# ---------- 跨类型横切补充：所有可识别类型均应命中 ----------

@pytest.mark.parametrize("ctype", ["买卖合同", "技术开发合同", "保密协议", "劳动合同", "商品房预售合同"])
def test_x_rules_fire_across_all_types(ctype):
    findings, _, _ = evaluate(BAD, {}, ctype)
    assert X_NEW <= _ids(findings), f"X 规则应在 {ctype} 中横切命中"

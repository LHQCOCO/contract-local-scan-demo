"""借款合同（J04-J11）与建设工程合同（G04-G11）深挖规则的行为测试。"""
from __future__ import annotations

import pytest

from cls.kb import KB
from cls.revisions import REVISIONS
from cls.risks import RULES, evaluate

LOAN_BAD = """
借款合同
甲方（出借人）：张三
乙方（借款人）：李四
一、乙方向甲方借款人民币500000元（大写：伍拾万元整）。
二、借款利率按月息2分计算，每月结息后将利息计入本金一并计息。
三、借款期限一年，到期一次性还本付息。
四、借款人应向出借人支付服务费、咨询费共计20000元。
五、任何一方违约应承担责任。
六、本合同一式两份。
"""

LOAN_OK = """
借款合同
甲方（出借人）：张三（身份证号：110101199001010011）
乙方（借款人）：李四（身份证号：110101199202020022）
一、乙方向甲方借款人民币500000元（大写：伍拾万元整），年化利率12%（即年利率百分之十二，不超过合同成立时一年期贷款市场报价利率四倍）。
二、借款用途：乙方用于经营周转，不得挪作他用。
三、借款期限自2026年1月1日至2026年12月31日。
四、放款方式：甲方通过银行转账交付借款，乙方出具收据，银行流水作为交付凭证。
五、还款方式：按月付息，到期还本；还款抵充顺序为先支付利息、后抵充本金。
六、乙方可提前还款，提前还款按实际借款期限计算利息，不收取违约金。
七、逾期利率：逾期未还的，按年利率15%计收逾期利息（罚息）。
八、管辖：由被告住所地人民法院管辖。
"""

CONSTR_BAD = """
建设工程施工合同
发包人（甲方）：某地产开发有限公司
承包人（乙方）：某建筑工程有限公司
一、工程名称：某住宅项目1号楼。
二、承包范围：土建及安装工程。
三、合同价款：人民币3000万元。
四、双方另行签订补充协议调整中标合同价款。
五、乙方同意以甲方名义承揽部分配套工程，并借用甲方资质施工。
六、工期300天。
七、违约责任按法律规定处理。
"""

CONSTR_OK = """
建设工程施工合同
发包人（甲方）：某地产开发有限公司（统一社会信用代码：91110000XXXXXXXXXX）
承包人（乙方）：某建筑工程有限公司（统一社会信用代码：91110000YYYYYYYYYY）
一、工程名称：某住宅项目1号楼，承包范围：土建及安装工程。
二、合同价款：人民币3000万元，按形象进度支付工程进度款。
三、工期300日历天，自2026年3月1日开工；发生设计变更或不可抗力时办理工期顺延签证，乙方应在事件发生后14日内提出工期索赔。
四、竣工验收：工程完工后由甲方组织竣工验收，验收合格后30日内完成竣工结算。
五、质量保证金：按结算总额3%扣留质保金，缺陷责任期12个月，期满后14日内返还。
六、乙方应在工程项目所在地开设农民工工资专用账户，实行实名制管理，按月足额支付农民工工资。
七、乙方就工程折价或拍卖价款享有优先受偿权。
八、安全文明施工：施工现场安全生产与文明施工由乙方负责，甲方承担相应费用，安全责任划分按附件执行。
九、禁止转包和违法分包。
"""

J_NEW = {f"J{n:02d}" for n in range(4, 12)}
G_NEW = {f"G{n:02d}" for n in range(4, 12)}


def _ids(findings):
    return {f["id"] for f in findings}


def test_rule_count_is_168():
    assert len(RULES) == 191


# ---------- 借款 ----------

def test_bad_loan_triggers_all_new_j_rules():
    findings, _, _ = evaluate(LOAN_BAD, {}, "借款合同")
    missing = J_NEW - _ids(findings)
    assert not missing, f"问题借款合同应命中 {sorted(missing)}"


def test_clean_loan_exempts_all_new_j_rules():
    findings, _, _ = evaluate(LOAN_OK, {}, "借款合同")
    hit = _ids(findings) & J_NEW
    assert not hit, f"完备借款合同不应命中 {sorted(hit)}"


@pytest.mark.parametrize("ctype", ["服务合同", "采购合同", "建设工程合同", "租赁合同"])
def test_j_rules_do_not_leak_to_other_types(ctype):
    findings, _, _ = evaluate(LOAN_BAD, {}, ctype)
    leak = _ids(findings) & J_NEW
    assert not leak, f"J 规则不应命中 {ctype}: {sorted(leak)}"


# ---------- 建设工程 ----------

def test_bad_construction_triggers_all_new_g_rules():
    findings, _, _ = evaluate(CONSTR_BAD, {}, "建设工程合同")
    missing = G_NEW - _ids(findings)
    assert not missing, f"问题建工合同应命中 {sorted(missing)}"


def test_clean_construction_exempts_all_new_g_rules():
    findings, _, _ = evaluate(CONSTR_OK, {}, "建设工程合同")
    hit = _ids(findings) & G_NEW
    assert not hit, f"完备建工合同不应命中 {sorted(hit)}"


@pytest.mark.parametrize("ctype", ["服务合同", "采购合同", "借款合同", "租赁合同"])
def test_g_rules_do_not_leak_to_other_types(ctype):
    findings, _, _ = evaluate(CONSTR_BAD, {}, ctype)
    leak = _ids(findings) & G_NEW
    assert not leak, f"G 规则不应命中 {ctype}: {sorted(leak)}"


# ---------- 逐条独立性（参数化触发样本） ----------

@pytest.mark.parametrize("rid,text", [
    ("J04", "借款合同约定按复利计息，利滚利计算。"),
    ("J05", "借款合同：乙方向甲方借款10万元，年利率10%，另收管理费5000元。"),
    ("J10", "借款利率按月息3分计算。"),
    ("G04", "双方另行签署阴阳合同，规避中标合同备案。"),
    ("G08", "乙方挂靠有资质的单位，借用其资质承包本工程。"),
])
def test_each_match_rule_fires(rid, text):
    findings, _, _ = evaluate(text, {}, "借款合同" if rid.startswith("J") else "建设工程合同")
    assert rid in _ids(findings), f"{rid} 未命中其专属样本"


# ---------- 结构与法条 ----------

def test_new_rules_have_revisions_and_valid_basis():
    for rid in sorted(J_NEW | G_NEW):
        assert rid in REVISIONS, f"{rid} 缺少修订草案"
        rule = next(r for r in RULES if r["id"] == rid)
        for bid in rule.get("basis", []):
            assert bid in KB.by_id, f"{rid} 引用的法条 {bid} 不在知识库"


def test_new_rule_severity_levels():
    expect = {
        "J04": "high", "J05": "high", "J06": "low", "J07": "low",
        "J08": "low", "J09": "low", "J10": "medium", "J11": "low",
        "G04": "high", "G05": "medium", "G06": "low", "G07": "medium",
        "G08": "high", "G09": "low", "G10": "low", "G11": "medium",
    }
    for rid, sev in expect.items():
        rule = next(r for r in RULES if r["id"] == rid)
        assert rule["severity"] == sev, f"{rid} 严重级别应为 {sev}"

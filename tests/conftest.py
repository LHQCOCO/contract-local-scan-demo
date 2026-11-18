"""导出功能单元测试的公共夹具。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


CONTRACT_TEXT = "\n".join([
    "软件开发服务合同",
    "甲方：星海科技有限公司",
    "乙方：云图软件有限公司",
    "第一条 甲方应在乙方交付并验收后90日内支付合同价款。",
    "第二条 乙方交付的软件验收标准为甲方主观满意。",
    "第三条 因本合同产生的争议由甲方所在地人民法院管辖。",
])

SEVERITY_LABEL = {"high": "高风险", "medium": "中风险", "low": "低风险"}

# 锚定的证据：出现在正文段落中
ANCHORED_EVIDENCE = "甲方应在乙方交付并验收后90日内支付合同价款"
# 跨段落的证据：前半句在第二条，后半句在第三条，单段匹配会失败
CROSS_EVIDENCE = "乙方交付的软件验收标准为甲方主观满意。第三条 因本合同"
ABSENCE_EVIDENCE = "—（合同中未见相关条款）"


def make_finding(fid, title, severity, evidence, revision="", advice="建议修改相关条款。"):
    """按 risks.Finding.to_dict 的结构构造一条命中风险。"""
    return {
        "id": fid,
        "title": title,
        "category": "付款与结算",
        "severity": severity,
        "severity_label": SEVERITY_LABEL[severity],
        "evidence": evidence,
        "analysis": f"{title}的风险说明。",
        "advice": advice,
        "revision": revision,
        "basis": [{
            "id": "civil-509",
            "law": "中华人民共和国民法典",
            "label": "第五百零九条",
            "chapter": "合同履行",
            "text": "当事人应当按照约定全面履行自己的义务。",
        }],
        "confidence": 0.9,
    }


@pytest.fixture
def sample_result():
    """一份完整但最小的审阅结果：2 条可锚定、2 条无法锚定。"""
    findings = [
        make_finding(
            "R05", "付款期限过长", "high", ANCHORED_EVIDENCE,
            revision="甲方应在验收合格后30日内支付合同价款的80%，余款于质保期满后支付。",
        ),
        make_finding(
            "R08", "验收标准主观", "medium", CROSS_EVIDENCE,
            revision="验收标准为双方确认的《需求规格说明书》所列可量化指标，甲方应在收到交付物后10个工作日内书面反馈。",
        ),
        make_finding(
            "R12", "缺失争议解决条款", "medium", ABSENCE_EVIDENCE,
            revision="因本合同产生的争议，双方应先行协商；协商不成的，提交乙方所在地人民法院诉讼解决。",
        ),
        make_finding("R15", "账期表述模糊", "low", "90日", revision=""),
    ]
    return {
        "filename": "软件开发服务合同.txt",
        "ext": "txt",
        "chars": len(CONTRACT_TEXT),
        "scan_suspect": False,
        "pages": 1,
        "size": 1200,
        "title_guess": "软件开发服务合同",
        "text": CONTRACT_TEXT,
        "warnings": [],
        "fields": [{
            "key": "contract_type", "label": "合同类型", "value": "软件开发服务合同",
            "source": "rule", "hint": "按标题识别",
        }],
        "values": {"contract_name": "软件开发服务合同"},
        "contract_type": "软件开发服务合同",
        "contract_name": "软件开发服务合同",
        "party_a": "星海科技有限公司",
        "party_b": "云图软件有限公司",
        "amount_total": "100 万元",
        "findings": findings,
        "score": 78,
        "level": "high",
        "counts": {"high": 1, "medium": 2, "low": 1},
        "statutes": [],
        "summary": "测试用审阅结论。",
        "summary_source": "rule",
        "llm_used": False,
        "llm_error": "",
        "engine": {"rules": 46, "kb": 100, "model": "规则引擎"},
        "created_at": 1_700_000_000_000,
    }

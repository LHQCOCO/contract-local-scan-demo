"""规则引擎：内置合同风险规则，分级、评分并附本地法条依据。"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .kb import KB
from .revisions import build_revision

SEVERITY = {
    "high": ("高风险", 16),
    "medium": ("中风险", 8),
    "low": ("低风险", 3),
}


@dataclass
class Finding:
    id: str
    title: str
    category: str
    severity: str
    evidence: str
    analysis: str
    advice: str
    basis_ids: list
    confidence: float
    source: dict | None = None   # {"type": "builtin"} 或 {"type": "custom", "pack_name": ...}
    related: list | None = None  # 与内置规则同条款关联（如 C- 规则 related=["R04"]）

    def to_dict(self) -> dict:
        label, _ = SEVERITY[self.severity]
        basis = []
        for aid in self.basis_ids:
            art = KB.by_id.get(aid)
            if not art:
                continue
            basis.append({
                "id": art["id"],
                "law": art["law"],
                "label": art["label"],
                "chapter": art["chapter"],
                "text": art["text"],
            })
        return {
            "id": self.id,
            "title": self.title,
            "category": self.category,
            "severity": self.severity,
            "severity_label": label,
            "evidence": self.evidence or "—（合同中未见相关条款）",
            "analysis": self.analysis,
            "advice": self.advice,
            "revision": build_revision(basis),
            "basis": basis,
            "confidence": self.confidence,
            "source": self.source or {"type": "builtin"},
            "related": self.related or [],
        }


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


# 中文整十百分比：用于「百分之三十」这类写法
_CN_TENS = {"十": 10, "二十": 20, "三十": 30, "四十": 40,
            "五十": 50, "六十": 60, "七十": 70, "八十": 80, "九十": 90}
_CN_TENS_GROUP = "|".join(sorted(_CN_TENS, key=len, reverse=True))


def _pct_hits(compact: str, pattern: str) -> list[float]:
    """按正则中的命名组 v 提取百分比数值，供阈值比较。"""
    return [float(m.group("v")) for m in re.finditer(pattern, compact)]


# ---------- 企业自定义规则注册 ----------

_EXTRA_RULES: list[dict] = []
LAST_WARNINGS: list[str] = []  # 上一次 evaluate 中因异常被跳过的自定义规则


def register_custom(rules: list[dict]) -> None:
    """替换当前参与评估的自定义规则集（包增删/启停后由 app 层调用）。"""
    global _EXTRA_RULES
    _EXTRA_RULES = list(rules)


def total_rules() -> int:
    return len(RULES) + len(_EXTRA_RULES)


def _cmp(v: float, op: str, limit: float) -> bool:
    return {">=": v >= limit, "<=": v <= limit, ">": v > limit, "<": v < limit}.get(op, False)


def _threshold_rule(rule: dict, text: str, compact: str, params: dict | None) -> tuple[bool, str]:
    """自定义 threshold 规则：从合同文本提取数值，与阈值口径比较。"""
    th = rule.get("threshold") or {}
    key = th.get("param_key", "")
    if key.startswith("custom:"):
        limit = th.get("value")  # 包内私有参数：值内嵌在规则里
    else:
        limit = (params or {}).get(key, th.get("value"))
    try:
        limit = float(limit)
    except (TypeError, ValueError):
        return False, ""
    pattern = th.get("value_pattern", "")
    op = th.get("op", ">=")
    for v in _pct_hits(compact, pattern):
        if _cmp(v, op, limit):
            return True, _sentence(text, pattern)
    return False, ""


def _sentence(text: str, pattern: str, width: int = 150) -> str:
    for s in re.split(r"\n|。|；|;", text):
        s = s.strip()
        if len(s) < 3:
            continue
        if re.search(pattern, s):
            return s[:width] + ("…" if len(s) > width else "")
    compact = _compact(text)
    m = re.search(pattern, compact)
    if m:
        left = compact[:m.start()]
        cut = max(left.rfind("。"), left.rfind("；"), left.rfind("："))
        start = cut + 1 if cut >= 0 and m.start() - cut < 60 else max(0, m.start() - 40)
        end = min(len(compact), m.end() + 80)
        nxt = min([x for x in (compact.find("。", end), compact.find("；", end)) if x >= 0] or [end])
        end = nxt + 1 if nxt < len(compact) else end
        return ("…" if start else "") + compact[start:end] + ("…" if end < len(compact) else "")
    return ""


RULES: list[dict] = [
    # —— 主体与合同效力 ——
    dict(id="R01", title="签约主体信息不完整", category="主体与效力", severity="medium", mode="check",
         basis=["mfd-470"],
         analysis="合同未清晰载明双方主体全称或身份信息，可能导致责任主体难以确定，影响合同履行与追责。",
         advice="补充双方营业执照名称、统一社会信用代码、法定代表人或负责人、联系地址与联系方式，并与盖章主体核对一致。"),
    dict(id="R02", title="合同价款缺失或约定不明", category="价款与支付", severity="high", mode="check",
         basis=["mfd-470", "mfd-511"],
         analysis="价款属于合同必备条款。金额缺失或使用“待定、另议”等表述，将导致付款义务与违约金基数无法确定。",
         advice="明确合同总价、币种、含税/不含税、税率及金额大写；如为框架合同，应约定计价方式与结算上限。"),
    dict(id="R02B", title="框架合同未设结算上限", category="价款与支付", severity="medium", mode="check",
         basis=["mfd-470", "mfd-511"],
         analysis="框架合同仅约定暂定金额与“按实际结算”，缺少采购上限或预算封顶，可能导致实际交易超出授权额度。",
         advice="约定框架期内累计采购上限、单价调整机制与超额审批流程，并明确订单的书面确认方式。"),
    dict(id="R03", title="付款条件显著后置", category="价款与支付", severity="medium", mode="match",
         patterns=[r"验收(?:合格|通过)?后.{0,12}(?:60|90|120|180|三百六十)日",
                   r"收到(?:发票|乙方发票|供方发票)后.{0,12}(?:60|90|120)日",
                   r"质保期满后.{0,20}(?:支付|付清)"],
         basis=["mfd-509"],
         analysis="付款节点全部后置且账期较长，会使乙方长期垫资，回款风险与资金成本显著上升。",
         advice="设置预付款或进度款节点（如 30% 预付、40% 交付、30% 验收），并约定逾期付款利息与暂停履行权。"),
    dict(id="R04", title="违约金比例过高", category="违约责任", severity="high", mode="check",
         basis=["mfd-585"],
         analysis="约定违约金过分高于实际损失的，法院或仲裁机构可依当事人请求予以调低；过高违约金也会削弱条款的可执行性。",
         advice="将违约金与可证明的实际损失挂钩，通常不超过合同总价的 20%–30%；区分逾期履行与根本违约设置不同标准。"),
    dict(id="R05", title="违约金标准偏低", category="违约责任", severity="medium", mode="check",
         basis=["mfd-585"],
         analysis="违约金过低将不足以覆盖资金占用与追偿成本，实际违约时难以形成有效约束。",
         advice="将逾期违约金调整为日万分之三至万分之五，并保留按实际损失索赔的权利。"),
    dict(id="R06", title="定金比例超过法定上限", category="价款与支付", severity="high", mode="check",
         basis=["mfd-586"],
         analysis="定金数额不得超过主合同标的额的 20%，超过部分不产生定金效力，约定过高可能引发返还争议。",
         advice="将定金比例控制在合同总价的 20% 以内，超出部分改为预付款并明确其性质。"),
    dict(id="R07", title="一方享有单方解除权", category="权利义务", severity="high", mode="match",
         patterns=[r"(?:甲方|乙方|公司|采购方|委托方).{0,25}(?:有权|可以|可).{0,15}(?:随时|单方|无需通知|无条件).{0,10}(?:解除|终止)",
                   r"(?:任何一方|双方).{0,10}(?:有权|可).{0,10}(?:单方|随时).{0,10}(?:解除|终止)"],
         basis=["mfd-563", "mfd-497"],
         analysis="单方任意解除权会打破双方权利义务平衡；若属格式条款，可能因不合理地免除自身责任、限制对方主要权利而无效。",
         advice="将解除权限定为法定解除或重大违约情形，并设置书面通知、合理整改期与解除后的结算机制。"),
    dict(id="R08", title="绝对化免责条款", category="权利义务", severity="high", mode="check",
         patterns=[r"(?:不承担|免除).{0,6}(?:任何|全部|一切)?(?:责任|赔偿责任)", r"概不负责"],
         basis=["mfd-506", "mfd-497"],
         analysis="造成对方人身损害，或因故意、重大过失造成对方财产损失的免责条款无效；绝对免责还可能构成不合理免除己方责任。",
         advice="删除“概不负责”类表述，按过错程度分配责任，并对人身损害与故意/重大过失情形作除外约定。"),
    dict(id="R09", title="不可抗力范围过宽", category="权利义务", severity="medium", mode="match",
         patterns=[r"不可抗力.{0,40}(?:包括|包含).{0,60}(?:政策|市场|价格|经营|资金)变化",
                   r"(?:任何|一切)原因(?:导致|造成).{0,10}(?:不能履行|延误)"],
         basis=["mfd-590"],
         analysis="将政策变化、市场波动等商业风险纳入不可抗力，可能被用于逃避本应承担的履约责任。",
         advice="按“不能预见、不能避免、不能克服”界定不可抗力，明确通知期限、证明文件与后果处理。"),
    dict(id="R10", title="未约定保密义务", category="保密与知识产权", severity="medium", mode="absence",
         markers=[r"保密", r"商业秘密"],
         skip_types=["保密协议", "劳动合同", "软件许可合同", "技术开发合同", "建设工程合同"],
         basis=["mfd-501", "mfd-509"],
         analysis="合同履行中难免接触对方商业信息，缺少保密条款将使泄露后的救济缺乏合同依据。",
         advice="补充保密信息范围、保密期限、例外情形、违约赔偿与返还/销毁义务。"),
    dict(id="R11", title="争议解决条款缺失", category="争议解决", severity="medium", mode="absence",
         markers=[r"仲裁", r"人民法院", r"管辖"],
         basis=["mfd-470"],
         analysis="未约定争议解决方式与管辖地，发生纠纷时需另行确定，增加时间与程序成本。",
         advice="明确选择诉讼或仲裁（二者择一），写明具体法院/仲裁机构及适用法律。"),
    dict(id="R12", title="管辖约定偏向一方", category="争议解决", severity="low", mode="match",
         patterns=[r"(?:由|在).{0,12}(?:甲方|乙方|供方|买方|用人单位)所在地.{0,12}(?:法院|人民法院)",
                   r"(?:约定|指定).{0,10}(?:甲方|乙方).{0,6}(?:所在地|住所地).{0,10}(?:法院|人民法院)"],
         basis=["mfd-470"],
         analysis="约定由一方所在地法院管辖，会增加另一方的诉讼成本；同时不得违反级别管辖与专属管辖规定。",
         advice="改为被告住所地、合同履行地法院管辖，或选择双方认可的中立仲裁机构。"),
    dict(id="R13", title="自动续约/自动顺延条款", category="期限与终止", severity="medium", mode="match",
         patterns=[r"(?:自动续约|自动续期|自动顺延)", r"期满后.{0,15}自动.{0,10}(?:续|延)"],
         basis=["mfd-470", "mfd-563"],
         analysis="自动续约可能使一方在未重新议价的情况下被长期锁定，且容易忽略退出窗口。",
         advice="改为到期前书面确认续约，设置提前 60 日异议通知期与续约价格调整机制。"),
    dict(id="R14", title="合同期限未明确", category="期限与终止", severity="medium", mode="check",
         basis=["mfd-470"],
         analysis="缺少明确的起止时间或服务期限，将影响履约进度、逾期认定与合同终止时点。",
         advice="明确生效条件、履行起止日期；长期合同应设置里程碑与阶段性验收。"),
    dict(id="R15", title="未约定通知与送达地址", category="争议解决", severity="low", mode="absence",
         markers=[r"送达", r"通知地址", r"联系地址"],
         basis=["mfd-509"],
         analysis="缺少送达条款时，解除通知、催告函等文书可能因送达争议而不产生预期效力。",
         advice="约定联系人、电话、邮箱与送达地址，并明确拒收、变更地址的处理规则。"),
    dict(id="R16", title="知识产权归属不明", category="保密与知识产权", severity="medium", mode="check",
         basis=["mfd-843", "mfd-861"],
         analysis="涉及开发、设计或成果交付，却未约定知识产权归属与许可范围，可能在成果交付后产生权属争议。",
         advice="明确背景知识产权与新增成果的归属、许可地域/期限/用途，以及第三方侵权责任承担。"),
    dict(id="R17", title="排他/最惠待遇条款", category="权利义务", severity="medium", mode="match",
         patterns=[r"(?:独家|排他|排它|最惠).{0,15}(?:合作|供应|采购|待遇|价格)",
                   r"(?:享有|享有.{0,4}权利).{0,10}(?:独家|排他|最惠国|最惠)"],
         basis=["mfd-470"],
         analysis="排他或最惠条款会限制经营自由，并可能触及反垄断合规边界，需评估商业必要性。",
         advice="限定排他范围与期限，设置最低采购量或业绩对赌，并保留合规审查与终止权。"),
    dict(id="R18", title="发票作为付款前置条件", category="价款与支付", severity="low", mode="match",
         patterns=[r"(?:收到|取得).{0,6}发票后.{0,15}(?:付款|支付)",
                   r"(?:付款|支付).{0,10}(?:以|须|应).{0,10}(?:收到|取得).{0,4}发票.{0,10}(?:为|作为).{0,4}(?:前提|条件)"],
         basis=["mfd-509"],
         analysis="以发票作为付款前置条件，可能被用于延迟付款；开票义务与付款义务属于不同法律关系。",
         advice="明确开票时间与付款时间各自独立，避免以发票瑕疵为由无限期拖延付款。"),
    dict(id="R19", title="逾期付款不计违约责任", category="违约责任", severity="medium", mode="match",
         patterns=[r"逾期付款.{0,20}(?:不承担|无需|不计|免除).{0,10}(?:违约|利息|责任)",
                   r"(?:逾期|迟延付款).{0,15}(?:违约金|利息|罚息|责任).{0,10}(?:免除|不承担|不计|不计算|不支付)"],
         basis=["mfd-577", "mfd-585"],
         analysis="免除逾期付款责任，会削弱付款约束，并使守约方资金损失难以追偿。",
         advice="约定逾期付款按日计息，并赋予守约方暂停履行或解除合同的权利。"),
    dict(id="R20", title="合同变更权单方化", category="权利义务", severity="medium", mode="match",
         patterns=[r"(?:甲方|公司|采购方).{0,25}(?:有权|可).{0,12}(?:变更|调整).{0,10}(?:合同|价格|服务内容|范围)",
                   r"(?:单方|随时).{0,10}(?:变更|调整).{0,10}(?:合同|条款|价格|范围|内容)"],
         basis=["mfd-543"],
         analysis="单方变更权使合同核心条件处于不确定状态，对方难以据此安排履约资源。",
         advice="变更须经双方书面确认；如保留单方调整权，应限定情形并给予对方退出或价格调整机制。"),
    dict(id="R21", title="签署日期缺失", category="主体与效力", severity="low", mode="check",
         basis=["mfd-502"],
         analysis="缺少签署日期，会影响合同成立时间、履行期限起算与诉讼时效认定。",
         advice="在落款处补充双方签署日期，并确保盖章与签署主体一致。"),

    # —— 劳动用工 ——
    dict(id="L01", title="试用期超过法定上限", category="劳动合规", severity="high", mode="check_labor",
         basis=["ldhtf-19"],
         analysis="试用期长度与合同期限强相关，超过法定上限的部分无效，用人单位还可能面临赔偿风险。",
         advice="按合同期限设置试用期（1 年以内不超过 1 个月、1–3 年不超过 2 个月、3 年以上不超过 6 个月），且同一劳动者只能约定一次。"),
    dict(id="L02", title="试用期工资低于法定标准", category="劳动合规", severity="high", mode="check",
         basis=["ldhtf-20"],
         analysis="试用期工资不得低于本单位相同岗位最低档工资或约定工资的 80%，且不得低于当地最低工资标准。",
         advice="将试用期工资调整为不低于转正工资的 80%，并核对当地最低工资标准。"),
    dict(id="L03", title="竞业限制期限超过两年", category="劳动合规", severity="high", mode="match",
         patterns=[r"竞业限制.{0,30}(?:三年|3年|四年|4年|五年|5年|超过二年|超过2年)",
                   r"(?:竞业限制|竞业禁止).{0,4}(?:期限|时间|期间).{0,40}(?:三年|3年|四年|4年|五年|5年)"],
         basis=["ldhtf-24"],
         analysis="竞业限制期限不得超过二年，超过部分对劳动者不发生效力。",
         advice="将竞业限制期限压缩至 2 年以内，并明确地域、业务范围与补偿标准。"),
    dict(id="L04", title="竞业限制未约定经济补偿", category="劳动合规", severity="high", mode="check_labor",
         basis=["ldhtf-23"],
         analysis="劳动者履行竞业限制义务的，用人单位应在竞业限制期限内按月给予经济补偿；长期不支付可能导致竞业限制失效。",
         advice="明确按月补偿金额（一般不低于离职前十二个月平均工资的 30%）、支付时间与停付后果。"),
    dict(id="L05", title="收取押金或扣押证件", category="劳动合规", severity="high", mode="match",
         patterns=[r"押金", r"保证金", r"扣押.{0,8}(?:身份证|证件)", r"收取财物"],
         basis=["ldhtf-9"],
         analysis="用人单位不得扣押劳动者身份证件，不得要求提供担保或以其他名义收取财物。",
         advice="删除押金、保证金及扣押证件条款，已收取的应全额退还。"),
    dict(id="L06", title="社会保险约定缺失或以补贴代替", category="劳动合规", severity="high", mode="check_labor",
         basis=["ldhtf-17", "ldf-72"],
         analysis="依法参加社会保险并缴费是用人单位的法定义务，不能通过“社保补贴”“自愿放弃”等方式免除。",
         advice="恢复依法缴纳五险，删除以现金补贴替代社保的约定，并补缴历史欠缴部分。"),
    dict(id="L07", title="单方调岗降薪条款", category="劳动合规", severity="medium", mode="match",
         patterns=[r"(?:公司|甲方|用人单位).{0,25}(?:有权|根据).{0,15}(?:调整|变更).{0,12}(?:岗位|工作地点|薪酬|工资)",
                   r"(?:根据.{0,6}需要|客观情况.{0,4}变化).{0,10}(?:调整|变更).{0,10}(?:岗位|薪酬|劳动.{0,2}条件)"],
         basis=["ldhtf-35"],
         analysis="变更劳动合同约定内容应协商一致并采用书面形式，单方调岗降薪易引发劳动争议。",
         advice="约定调岗须具备合理理由并与劳动者协商一致，薪酬调整应签署书面变更协议。"),
    dict(id="L08", title="加班费已包含在工资", category="劳动合规", severity="medium", mode="match",
         patterns=[r"加班费.{0,20}(?:包含|已含|不再另付|不再支付)",
                   r"(?:工资|薪酬).{0,15}(?:包含|已含).{0,6}(?:加班费|加班工资)"],
         basis=["ldf-44"],
         analysis="加班费属于法定劳动报酬，约定“已包含”不能当然免除加班费支付义务。",
         advice="明确加班认定与审批流程，按法定标准另行计发加班费或依法安排调休。"),
    dict(id="L09", title="工伤责任约定自负", category="劳动合规", severity="high", mode="match",
         patterns=[r"工伤.{0,20}(?:自负|自行负责|不承担|概不负责)",
                   r"(?:因公负伤|工伤).{0,15}(?:甲方|公司|用人单位).{0,15}(?:不承担|概不负责|不予承担)"],
         basis=["ldf-72"],
         analysis="工伤保险待遇属于法定保障，约定由劳动者自行承担工伤责任无效。",
         advice="删除工伤自负条款，依法缴纳工伤保险并及时申报工伤。"),
    dict(id="L10", title="用人单位可随时解除合同", category="劳动合规", severity="high", mode="match",
         patterns=[r"(?:公司|甲方|用人单位).{0,20}(?:可|有权).{0,10}(?:立即|随时).{0,8}(?:解除|辞退)",
                   r"(?:严重违反|不称职).{0,10}(?:甲方|公司|用人单位).{0,15}(?:可|有权).{0,10}(?:辞退|解除)"],
         basis=["ldhtf-39", "ldhtf-38"],
         analysis="用人单位解除劳动合同须符合法定情形与程序，约定“随时解除”可能构成违法解除并需支付赔偿金。",
         advice="按劳动合同法第 39–41 条列明解除情形，完善规章制度、考核与书面通知程序。"),
    dict(id="L11", title="劳动者违约金超出法定范围", category="劳动合规", severity="medium", mode="check_labor",
         basis=["ldhtf-25", "ldhtf-22"],
         analysis="劳动合同约定了劳动者违约金；依劳动合同法第二十五条，除专项培训服务期与竞业限制两种情形外，用人单位不得与劳动者约定由劳动者承担违约金。",
         advice="删除其他情形的劳动者违约金条款；确需保留的，核查是否属于服务期或竞业限制，并符合对应法定要件。"),
    dict(id="L12", title="约定放弃缴纳社保", category="劳动合规", severity="high", mode="match",
         patterns=[r"放弃.{0,4}(?:社保|社会保险)", r"自愿不缴.{0,2}社保", r"社保.{0,4}(?:补贴|补助).{0,6}(?:代替|折现|自行缴纳)"],
         basis=["shbxf-58", "ldf-72"],
         analysis="约定劳动者自愿放弃社保或以补贴代替缴纳；缴纳社保是法定义务，不因双方约定而免除，此类条款无效且用人单位仍面临补缴、滞纳金与赔偿风险。",
         advice="删除放弃社保条款，依法自用工之日起三十日内为劳动者办理社保登记并足额缴纳。"),
    dict(id="L13", title="末位淘汰解除条款", category="劳动合规", severity="high", mode="match",
         patterns=[r"末位淘汰", r"(?:排名|考核).{0,4}(?:最后|末位|倒数).{0,8}(?:解除|辞退|离职|淘汰)"],
         basis=["ldhtf-40", "ldhtf-39"],
         analysis="约定以考核末位直接解除劳动合同；末位不等于不胜任工作，依劳动合同法第四十条，即使不胜任也须先培训或调岗，仍不胜任方可解除，直接末位淘汰构成违法解除。",
         advice="删除末位淘汰表述；改为客观胜任力标准 + 培训/调岗程序 + 仍不胜任的解除路径。"),
    dict(id="L14", title="限制结婚生育条款", category="劳动合规", severity="high", mode="match",
         patterns=[r"(?:合同期内|在职期间|服务期内).{0,6}(?:不得|不准).{0,2}(?:结婚|生育|怀孕)",
                   r"(?:怀孕|生育|结婚).{0,4}(?:解除|辞退|视为自动离职|自动离职)"],
         basis=["l2adb9980-43", "lfb1c2a71-27"],
         analysis="约定合同期内不得结婚生育或以婚育为由解除合同；依妇女权益保障法第四十三条、就业促进法第二十七条，此类条款侵犯平等就业权，自始无效。",
         advice="删除全部婚育限制条款；不得将婚育状况作为录用、续约或解除的考量因素。"),
    dict(id="L15", title="工作地点约定过宽", category="劳动合规", severity="medium", mode="check_labor",
         basis=["ldhtf-17"],
         analysis="工作地点约定为「全国/全省」或「服从公司调配」，实质赋予用人单位单方变更工作地点的权利，司法实践中通常不被支持，仍可能被认定为未明确约定工作地点。",
         advice="写明具体城市或区域；确需多地安排的，列明可能的工作地点清单及异地补贴标准。"),
    dict(id="L16", title="特殊工时制未经审批", category="劳动合规", severity="low", mode="match",
         patterns=[r"综合.{0,2}工时", r"不定时工作制"],
         basis=["ldf-39"],
         analysis="约定综合工时制或不定时工作制；依劳动法第三十九条，实行特殊工时制须经劳动行政部门批准，未经批准的约定不能对抗法定标准工时。",
         advice="核查特殊工时审批文件是否已取得及在有效期内；未获批的岗位按标准工时执行并计付加班费。"),
    dict(id="L17", title="工资支付日期缺失", category="劳动合规", severity="low", mode="absence",
         markers=["(?:每月|当月).{0,4}日.{0,2}(?:支付|发放|发薪|发|结算)",
                  "(?:发薪日|工资日|支付日|工资发放日|发薪日).{0,2}为.{0,4}(?:每月|当月).{0,4}日"],
         basis=["ldf-50", "l2aa79ff6-7"],
         analysis="未约定工资支付日期；依劳动法第五十条与工资支付暂行规定第七条，工资必须按约定日期按月支付，日期不明易引发拖欠争议。",
         advice="写明每月固定发薪日；遇节假日提前至最近工作日支付的规则一并约定。"),
    dict(id="L18", title="服务期违约金未见费用依据", category="劳动合规", severity="medium", mode="check_labor",
         basis=["ldhtf-22"],
         analysis="约定了服务期与违约金，却未见专项培训费用的金额或计算依据；依劳动合同法第二十二条，违约金数额不得超过用人单位提供的培训费用。",
         advice="写明培训费用的构成与金额（含差旅、住宿等分摊方式），违约金按未履行服务期比例递减。"),

    # —— 数据与个人信息 ——
    dict(id="P01", title="处理个人信息未告知或缺乏合法性基础", category="数据与个人信息", severity="high", mode="check_privacy",
         basis=["grxxbhf-13", "grxxbhf-17"],
         analysis="处理个人信息应具备合法性基础，并向个人真实、准确、完整告知处理目的、方式与期限。",
         advice="补充告知同意条款，区分一般信息与敏感个人信息，取得单独同意并保留记录。"),
    dict(id="P02", title="个人信息/数据出境未约定合规程序", category="数据与个人信息", severity="high", mode="match",
         patterns=[r"(?:出境|境外|跨境).{0,20}(?:提供|传输|存储|处理)",
                   r"(?:向.{0,4}境外|境外.{0,4}转移|海外.{0,4}传输|跨境.{0,4}处理).{0,20}(?:个人信息|数据|客户信息)"],
         basis=["grxxbhf-38", "sjaqf-31"],
         analysis="向境外提供个人信息应通过安全评估、标准合同或认证等法定路径，未约定将使出境行为缺乏合规基础。",
         advice="明确数据出境清单与目的地，完成安全评估/标准合同备案，并约定境外接收方的保护义务。"),
    dict(id="P03", title="缺少数据安全保护措施", category="数据与个人信息", severity="medium", mode="check_data",
         basis=["sjaqf-27"],
         analysis="处理数据应建立全流程安全管理制度并采取技术措施，合同未作约定将使安全责任边界不清。",
         advice="补充数据分类分级、访问控制、加密、日志留存、安全事件通知与应急处置条款。"),

    dict(id="P04", title="一次性概括授权处理个人信息", category="数据与个人信息", severity="medium", mode="match",
         patterns=[r"(?:一次性|概括性|不可撤销的).{0,4}(?:授权|同意).{0,8}(?:个人信息|信息|数据)",
                   r"(?:同意|授权).{0,4}(?:永久|无限期|不可撤销).{0,4}(?:使用|处理|收集).{0,4}(?:个人信息|信息|数据)"],
         basis=["grxxbhf-14"],
         analysis="个人同意须在充分知情前提下自愿、明确作出，一次性概括且不可撤销的授权实质剥夺了信息主体的撤回权与选择权。",
         advice="按处理目的分项取得同意并保留撤回通道；敏感个人信息应取得单独同意，删除“不可撤销”表述。"),
    dict(id="P05", title="个人信息可任意共享给第三方", category="数据与个人信息", severity="high", mode="match",
         patterns=[r"(?:有权|可以|可自行).{0,4}将.{0,4}(?:您的|用户|个人)?.{0,2}(?:个人信息|信息|数据).{0,6}(?:共享|提供|披露|转让)",
                   r"与(?:关联公司|合作伙伴|第三方).{0,2}共享.{0,4}(?:个人信息|数据)"],
         basis=["grxxbhf-23"],
         analysis="向第三方提供个人信息须告知接收方名称、处理目的与方式并取得单独同意，概括共享条款使信息流向完全失控。",
         advice="列明接收方清单、共享目的与数据范围，约定单独同意机制与接收方违约责任。"),

    # —— 刑事与欺诈风险 ——
    dict(id="X01", title="签约主体真实性存疑", category="刑事风险", severity="high", mode="match",
         patterns=[r"伪造.{0,6}(?:公章|印章|资质|授权)", r"虚假(?:主体|公司|资质|项目)"],
         basis=["xf-224"],
         analysis="以虚构主体、伪造印章等方式签订合同，可能触及合同诈骗等刑事风险。",
         advice="核验营业执照、授权委托书与用章记录，并通过对公账户付款；留存尽调证据。"),
    dict(id="X02", title="廉洁与反商业贿赂条款缺失", category="刑事风险", severity="medium", mode="absence",
         markers=[r"廉洁", r"反商业贿赂", r"(?:不得|禁止|严禁).{0,4}(?:行贿|受贿|贿赂|索取财物)",
                  r"不正当利益", r"(?:不得|禁止|严禁).{0,8}(?:回扣|好处费|礼金|礼品|有价证券)"],
         basis=["fbzdtjzf-8", "xf-164"],
         analysis="合同未约定反商业贿赂与廉洁条款的，员工收受回扣、向对方人员行贿等行为发生时，企业既缺乏合同层面的解除与索赔依据，也难以证明己方已尽合规义务。",
         advice="补充廉洁条款：禁止任何形式的回扣、礼金、有价证券与不正当利益，明确违反方的违约金、解除权与举报渠道。"),
    dict(id="X03", title="职务侵占与挪用资金风险表述", category="刑事风险", severity="high", mode="match",
         patterns=[r"(?:以|通过).{0,4}(?:账外|小金库|账外账|体外循环).{0,6}(?:方式|账户|资金).{0,4}(?:结算|支付|入账|收付)",
                   r"(?:虚开|虚报|虚构).{0,4}(?:增值税发票|发票|费用|成本).{0,6}(?:报销|入账|抵扣|列支)",
                   r"(?<!不)(?<!得)(?<!禁)(?<!止)(?<!严)(?:个人|私人).{0,4}(?:占用|占有|挪用|拆借).{0,4}(?:公司|单位|企业).{0,4}(?:资金|款项)",
                   r"(?:可|允许|同意).{0,4}挪用.{0,4}(?:资金|款项)"],
         basis=["xf-271", "xf-272"],
         analysis="合同中出现账外资金、虚开发票、个人占用公司资金等表述，可能构成职务侵占或挪用资金罪的事实线索，也会使企业陷入刑事合规风险。",
         advice="删除账外、虚开、挪用等违规表述，约定所有款项通过对公账户收付、发票合规、资金专款专用，并设置内部审批与审计条款。"),
    dict(id="X04", title="以回扣或账外方式输送利益", category="刑事风险", severity="high", mode="match",
         patterns=[r"(?:按|依照|根据).{0,6}(?:合同金额|交易额|价款|总价).{0,8}(?:%|百分之).{0,4}(?:支付|作为|返).{0,4}回扣",
                   r"(?<!不)(?<!得)(?<!禁)(?<!止)(?<!严)(?:向|给).{0,8}(?:经办人|负责人|采购|员工|工作人员).{0,8}(?:支付|给予|返).{0,4}回扣",
                   r"(?:支付|给予|给付|索取|收取).{0,4}好处费",
                   r"(?<!不)(?<!得)(?<!禁)(?<!止)(?<!严)账外.{0,6}(?:支付|给付|给予|收受).{0,6}(?:好处费|回扣|佣金|返点|手续费)"],
         basis=["xf-164", "xf-393", "fbzdtjzf-8"],
         analysis="在合同中明示或默许回扣、账外好处费等安排，可能构成对非国家工作人员行贿罪或单位行贿罪，相关责任人面临刑事责任。",
         advice="删除回扣、好处费等条款；佣金、返利应如实入账并依法纳税，禁止以任何账外方式给付或收受利益。"),
    dict(id="X05", title="关联交易与利益冲突回避机制缺失", category="刑事风险", severity="low", mode="absence",
         markers=[r"利益冲突", r"关联交易", r"关联方", r"(?:回避|披露义务|回避机制)"],
         basis=["xf-166", "xf-165"],
         analysis="涉及关联方或工作人员近亲属的交易未建立披露与回避机制的，可能演变为为亲友非法牟利、非法经营同类营业等职务犯罪，企业内部监督失效。",
         advice="约定利益冲突申报、关联方披露与回避审批程序，重大关联交易须经合规审查或决策机构批准。"),
    dict(id="X06", title="礼品宴请等变相贿赂未作限制", category="刑事风险", severity="low", mode="absence",
         markers=["礼品", "(?:不得|禁止).{0,4}(?:送礼|收礼|宴请|旅游|娱乐)"],
         basis=["xf-163", "xf-164"],
         analysis="未限制礼品、宴请、旅游等变相利益输送的，实践中极易被作为商业贿赂的隐蔽通道，达到数额标准时可能触犯非国家工作人员受贿/行贿罪。",
         advice="明确礼品与宴请的金额上限、审批程序与禁止情形（如现金、购物卡、高消费娱乐），并保留费用凭证与审批记录。"),

    # —— 租赁合同 ——
    dict(id="Z01", title="租赁期限超过二十年", category="租赁合同", severity="high", mode="match",
         patterns=[r"租赁期限.{0,15}(?:二十[一二三四五六七八九]|30|三十|40|四十|50|五十|60|六十)年",
                   r"租赁期.{0,10}(?:超过|长于)二十年"],
         only_types=["租赁合同"], skip_types=["劳动合同"],
         basis=["mfd-705"],
         analysis="租赁期限不得超过二十年，超过部分无效；超长期限还可能影响解约与租金调整灵活性。",
         advice="将单次租赁期限控制在二十年以内，超过部分通过续订合同处理，并约定租金调整机制。"),
    dict(id="Z02", title="押金约定过高", category="租赁合同", severity="medium", mode="match",
         patterns=[r"(?:押金|保证金).{0,20}(?:三|3|四|4|五|5|六|6)个?月",
                   r"(?:押金|保证金).{0,20}(?:人民币|RMB)?\s*(\d+)\s*(?:万元|元).*超过.{0,4}(?:两个月|2个月|房租.{0,4}月)"],
         only_types=["租赁合同"],
         basis=["mfd-705"],
         analysis="押金超过两个月租金将显著加重承租人负担，且在退租时易产生返还争议。",
         advice="将押金控制在一至两个月租金，明确押金抵扣范围、退还条件与逾期退还利息。"),
    dict(id="Z03", title="转租约定缺失", category="租赁合同", severity="medium", mode="absence",
         markers=[r"转租", r"分租", r"转借"],
         only_types=["租赁合同"],
         basis=["mfd-716"],
         analysis="未约定转租规则时，承租人擅自转租可能导致合同解除与损失赔偿争议。",
         advice="明确是否允许转租、转租的书面同意程序，以及擅自转租的违约责任。"),
    dict(id="Z04", title="维修责任约定不明", category="租赁合同", severity="medium", mode="absence",
         markers=[r"维修", r"修缮", r"维护"],
         only_types=["租赁合同"],
         basis=["mfd-710"],
         analysis="租赁物维修责任不清，将导致故障时双方推诿，影响租赁物正常使用。",
         advice="区分自然损耗与人为损坏，明确出租人维修义务、响应期限及费用承担。"),
    dict(id="Z05", title="装修添附归属未约定", category="租赁合同", severity="medium", mode="absence",
         markers=["(?:装修|装饰装修).{0,6}(?:归属|补偿|折价|无偿|添附|归|处理)",
                  "(?:装修|装饰装修|装潢).{0,10}(?:添附|残值|处理)"],
         only_types=["租赁合同"],
         basis=["mfd-715"],
         analysis="未约定租赁期间装修装饰的归属与补偿；依民法典第七百一十五条，承租人经出租人同意可以改善或增设他物，租期届满时装修如何处理无约定极易起争议。",
         advice="约定装修须书面同意及方案报备；租期届满或提前解约时装修的归属、折价补偿或恢复原状责任逐项写明。"),
    dict(id="Z06", title="优先承租权未约定", category="租赁合同", severity="low", mode="absence",
         markers=["优先(?:承|续)租权",
                  "同等条件.{0,4}优先(?:承|续)租"],
         only_types=["租赁合同"],
         basis=["mfd-734"],
         analysis="未约定租期届满后的优先承租安排；依民法典第七百三十四条，租赁期限届满，房屋承租人享有以同等条件优先承租的权利。",
         advice="写明承租人行使优先承租权的通知时限、同等条件的确认方式及出租人的通知义务。"),
    dict(id="Z07", title="房屋权属与抵押查封未披露", category="租赁合同", severity="medium", mode="absence",
         markers=["产权.{0,4}(?:证明|清晰|无争议)", "权属"],
         only_types=["租赁合同"],
         basis=["mfd-725"],
         analysis="未见出租人对房屋权属、抵押查封状况的披露与承诺；房屋在租期内被处置时，依民法典第七百二十五条买卖不破租赁，但租赁设立在抵押或查封之后的，承租人权益难以保障。",
         advice="要求出租人提供产权证复印件并书面承诺如实披露抵押、查封情况；因权属瑕疵导致无法使用的违约责任写明。"),
    dict(id="Z08", title="物业水电等费用承担未约定", category="租赁合同", severity="low", mode="absence",
         markers=["物业费", "各项费用.{0,4}承担"],
         only_types=["租赁合同"],
         basis=["mfd-709"],
         analysis="未约定物业费、水电燃气、网络等费用的承担方与结算方式；费用承担是租赁履约中最频繁的日常争议点。",
         advice="逐项列明各项费用的承担方、缴费方式与抄表结算日；起租日前后的欠费清缴责任一并写明。"),
    dict(id="Z09", title="退租交还标准未约定", category="租赁合同", severity="low", mode="absence",
         markers=["恢复原状", "返还.{0,2}房屋"],
         only_types=["租赁合同"],
         basis=["mfd-733"],
         analysis="未约定租期届满或解除时的房屋交还标准；依民法典第七百三十三条，承租人应返还租赁物并保持符合约定或租赁物性质使用后的状态。",
         advice="写明交还状态（含可拆除与不可拆除装修的处理）、验收流程与交接确认书；逾期腾退的占用费标准一并约定。"),
    dict(id="Z10", title="免租装修期未约定", category="租赁合同", severity="low", mode="absence",
         markers=[r"免租期", r"装修期", r"免租装修"],
         only_types=["租赁合同"],
         basis=["mfd-709"],
         analysis="承租用于经营且需装修的，未约定免租装修期的，装修期间即起算租金将加重承租人负担，也易因起租日认定产生争议。",
         advice="约定免租装修期的起止日期与期限，写明免租期内物业费、水电费的承担方式。"),
    dict(id="Z11", title="租赁登记备案未约定", category="租赁合同", severity="low", mode="absence",
         markers=[r"备案", r"登记.{0,4}备案", r"租赁登记"],
         only_types=["租赁合同"],
         basis=["mfd-706"],
         analysis="未约定租赁合同登记备案安排；依民法典第七百零六条，未办理登记备案不影响合同效力，但部分城市入学、居住证、经营许可等事项要求提供备案证明。",
         advice="约定双方配合办理租赁登记备案的义务、时限与费用承担；以房屋办理经营证照的，备案配合义务尤为重要。"),

    # —— 借款合同 ——
    dict(id="J01", title="借款利率超过法定上限", category="借款合同", severity="high", mode="match",
         patterns=[r"(?:年|月)利率.{0,12}(?:2[4-9]%|3[0-9]%|[4-9]\d%|百分之二十[四五六七八九]|百分之[三四五六七八九]十)",
                   r"(?:年利率|月利率).{0,12}(?:LPR|市场报价利率).{0,12}(?:4|四)倍以上"],
         only_types=["借款合同"],
         basis=["mfd-680"],
         analysis="禁止高利放贷，借款利率不得违反国家有关规定；超过司法保护上限的部分不予支持。",
         advice="将年利率控制在合同成立时一年期 LPR 的四倍以内，并明确逾期利率与复利规则。"),
    dict(id="J02", title="预先在本金中扣除利息", category="借款合同", severity="high", mode="match",
         patterns=[r"利息.{0,10}(?:预先|提前).{0,10}(?:扣除|扣收)", r"本金中.{0,6}扣除.{0,6}利息",
                   r"放款时.{0,10}(?:扣除|扣收).{0,10}利息"],
         only_types=["借款合同"],
         basis=["mfd-670"],
         analysis="借款利息不得预先在本金中扣除；预先扣除的，应按实际借款数额返还并计息。",
         advice="按约定本金全额发放，利息按期另行收取，不得在放款时预扣。"),
    dict(id="J03", title="借款期限约定不明", category="借款合同", severity="medium", mode="absence",
         markers=[r"借款期限", r"还款期限", r"返还期限", r"到期日"],
         only_types=["借款合同"],
         basis=["mfd-675"],
         analysis="未约定还款期限的，借款人可随时返还，贷款人可催告合理期限返还，增加回款不确定性。",
         advice="明确借款起止日期、分期还款计划与提前还款规则。"),
    dict(id="J04", title="约定复利（利滚利）", category="借款合同", severity="high", mode="match",
         patterns=[r"复利", r"利滚利", r"利息.{0,4}计入.{0,2}本金", r"结息.{0,4}(?:转|计入).{0,2}本"],
         only_types=["借款合同"],
         basis=["l1575d9ff-27"],
         analysis="约定将利息计入本金重复计息；依民间借贷司法解释第二十七条，前期利率超过合同成立时一年期贷款市场报价利率四倍的部分，不得计入后期借款本金。",
         advice="删除复利条款或明确仅按单利计息；确需复利的，核查前后两期合计利率是否突破法定上限。"),
    dict(id="J05", title="以服务费名义变相加息", category="借款合同", severity="high", mode="check",
         only_types=["借款合同"],
         basis=["mfd-680", "l1575d9ff-29"],
         analysis="合同在利息之外另收服务费/咨询费/管理费；依民间借贷司法解释第二十九条，利息与各类费用总计超过合同成立时一年期贷款市场报价利率四倍的部分，法院不予支持。",
         advice="将利息与各项费用合并计算年化成本并对照法定上限；无实际服务对应的费用条款建议删除。"),
    dict(id="J06", title="逾期利率未约定", category="借款合同", severity="low", mode="absence",
         markers=[r"逾期.{0,4}(?:利率|利息|罚息)", r"罚息"],
         only_types=["借款合同"],
         basis=["mfd-676", "l1575d9ff-28"],
         analysis="未约定逾期利率；依民法典第六百七十六条，借款人逾期应按约定或国家有关规定支付逾期利息，无约定时主张空间受限。",
         advice="写明逾期利率（不得超过法定上限）或逾期罚息计算方式；与违约金并用时的合计上限一并注意。"),
    dict(id="J07", title="借款用途未约定", category="借款合同", severity="low", mode="absence",
         markers=[r"借款用途", r"贷款用途", r"资金用途", r"用途"],
         only_types=["借款合同"],
         basis=["mfd-673"],
         analysis="未约定借款用途；依民法典第六百七十三条，借款人未按约定用途使用借款的，贷款人可停止发放、提前收回或解除合同。",
         advice="写明借款用途及借款人定期报告义务；约定挪用借款的救济措施（提前到期、加收利息）。"),
    dict(id="J08", title="提前还款规则未约定", category="借款合同", severity="low", mode="absence",
         markers=["提前还款", "提前归还"],
         only_types=["借款合同"],
         basis=["mfd-677"],
         analysis="未约定提前还款规则；依民法典第六百七十七条，借款人提前返还借款的，除另有约定外按实际借款期间计算利息，易因利息结算起争议。",
         advice="写明是否允许提前还款、是否收取违约金或补偿金，以及利息按实际占用期间结算的规则。"),
    dict(id="J09", title="还款抵充顺序未约定", category="借款合同", severity="low", mode="absence",
         markers=["抵充", "先本后息"],
         only_types=["借款合同"],
         basis=["mfd-561"],
         analysis="未约定部分还款时的抵充顺序；依民法典第五百六十一条，无约定时按费用、利息、主债务顺序抵充，与出借人预期可能不符。",
         advice="写明还款优先抵充顺序（费用→利息→本金或另行约定），分期还款的列明还款计划表。"),
    dict(id="J10", title="利率表述不规范", category="借款合同", severity="medium", mode="match",
         patterns=[r"月息.{0,2}[一二三四五六七八九\d]分", r"日息", r"[一二三四五六七八九\d]厘.{0,2}利", r"年化.{0,2}费率"],
         only_types=["借款合同"],
         basis=["mfd-680"],
         analysis="利率以「月息几分/日息/厘」等口径表述，未换算为年化利率，易因计息口径理解分歧产生争议，也不便对照法定上限。",
         advice="统一换算为年化利率（%）表述，并写明计息方式（单利/复利）、计息基数与结息周期。"),
    dict(id="J11", title="借款交付凭证缺失", category="借款合同", severity="low", mode="absence",
         markers=[r"转账", r"银行流水", r"交付.{0,4}(?:凭证|方式)", r"借据", r"收据", r"放款"],
         only_types=["借款合同"],
         basis=["mfd-679"],
         analysis="未约定借款交付方式与凭证保留；自然人之间的借款合同自贷款人提供借款时成立，缺乏交付凭证将影响借款事实的证明。",
         advice="约定以银行转账方式交付并注明款项性质，保留转账凭证；现金交付的应要求借款人出具收据。"),

    # —— 建设工程合同 ——
    dict(id="G01", title="违法转包或肢解分包", category="建设工程合同", severity="high", mode="match",
         patterns=[r"(?:全部|整体|主体结构).{0,10}(?:转包|分包给第三人)", r"肢解.{0,6}(?:发包|分包)"],
         only_types=["建设工程合同"],
         basis=["mfd-791"],
         analysis="承包人不得将全部工程转包或肢解后分包，主体结构须由承包人自行完成。",
         advice="禁止转包与违法分包，明确分包须经发包人同意且仅限非主体、非关键性工作。"),
    dict(id="G02", title="工程款支付进度约定不明", category="建设工程合同", severity="medium", mode="absence",
         markers=[r"进度款", r"预付款", r"竣工结算", r"工程款支付"],
         only_types=["建设工程合同"],
         basis=["mfd-807"],
         analysis="工程款支付节点不清，易导致承包人垫资压力过大及竣工结算争议。",
         advice="约定预付款、进度款、竣工结算款的支付比例、节点与逾期利息。"),
    dict(id="G03", title="质量保修责任缺失", category="建设工程合同", severity="medium", mode="absence",
         markers=[r"保修", r"质量保证", r"质保", r"缺陷责任"],
         only_types=["建设工程合同"],
         basis=["mfd-801"],
         analysis="未约定质量保修范围与期限，工程交付后出现质量问题时责任难以落实。",
         advice="按法定最低保修期限约定保修范围、期限、返修程序与质量保证金返还。"),
    dict(id="G04", title="中标合同外另签协议（黑白合同）", category="建设工程合同", severity="high", mode="match",
         patterns=[r"另行签(?:订|署).{0,6}(?:协议|合同)", r"阴阳合同", r"(?:备案|中标)合同.{0,6}(?:之外|以外).{0,6}(?:补充|变更|调整)"],
         only_types=["建设工程合同"],
         basis=["zbtbf-46"],
         analysis="涉嫌在中标/备案合同之外另行订立实质性内容不同的协议；依招标投标法第四十六条，招标人和中标人不得再行订立背离合同实质性内容的其他协议。",
         advice="删除与中标合同实质性内容（价款、工期、质量）不一致的条款；确需变更的，依法办理变更手续并留存依据。"),
    dict(id="G05", title="质量保证金扣留与返还未约定", category="建设工程合同", severity="medium", mode="absence",
         markers=[r"质保金", r"质量保证金", r"保修金"],
         only_types=["建设工程合同"],
         basis=["mfd-801"],
         analysis="未约定质量保证金的扣留比例、返还条件与期限；质保金是工程款结算尾款争议的高发点，无约定时返还时间易长期悬置。",
         advice="写明质保金比例、缺陷责任期起算与期限、返还条件及逾期返还的利息；与保修责任的衔接一并写清。"),
    dict(id="G06", title="工期顺延与签证程序缺失", category="建设工程合同", severity="low", mode="absence",
         markers=[r"工期顺延", r"顺延", r"签证", r"工期索赔"],
         only_types=["建设工程合同"],
         basis=["mfd-804"],
         analysis="未约定工期顺延情形与签证确认程序；停工、窝工、设计变更时缺乏顺延依据，工期延误责任难以划分。",
         advice="列明可顺延工期的情形（不可抗力、甲方原因停工、设计变更等），约定签证提出、确认与时限的程序条款。"),
    dict(id="G07", title="农民工工资专户安排缺失", category="建设工程合同", severity="medium", mode="absence",
         markers=[r"农民工工资", r"工资专用账户", r"实名制"],
         only_types=["建设工程合同"],
         basis=["l95a8365d-26"],
         analysis="未见农民工工资专用账户安排；依保障农民工工资支付条例第二十六条，施工总承包单位应当开设农民工工资专用账户，专项用于支付农民工工资。",
         advice="约定总包单位开设工资专用账户、建设单位人工费拨付比例与周期、实名制管理与工资代发流程。"),
    dict(id="G08", title="挂靠或借用资质施工", category="建设工程合同", severity="high", mode="match",
         patterns=[r"挂靠", r"借用.{0,2}资质", r"以.{0,6}名义.{0,4}(?:承包|承揽|施工)"],
         only_types=["建设工程合同"],
         basis=["mfd-791"],
         analysis="涉嫌无资质方借用有资质方名义承揽工程；挂靠施工合同无效，且双方均面临行政处罚与安全质量连带责任。",
         advice="核查实际施工主体的资质与劳动关系；存在挂靠安排的应终止并重构合同关系。"),
    dict(id="G09", title="工程价款优先受偿权未见", category="建设工程合同", severity="low", mode="absence",
         markers=[r"优先受偿", r"折价.{0,4}(?:工程|价款)", r"拍卖.{0,4}工程"],
         only_types=["建设工程合同"],
         basis=["mfd-807"],
         analysis="未见工程价款优先受偿的安排；依民法典第八百零七条，发包人逾期不支付价款的，承包人可就工程折价或拍卖价款优先受偿，行使期限为应付款之日起十八个月。",
         advice="写明发包人逾期付款时承包人有权依法行使优先受偿权；注意十八个月的行使期限，逾期即丧失。"),
    dict(id="G10", title="竣工验收与结算程序缺失", category="建设工程合同", severity="low", mode="absence",
         markers=[r"竣工验收", r"验收.{0,4}(?:合格|程序)", r"结算"],
         only_types=["建设工程合同"],
         basis=["mfd-799"],
         analysis="未约定竣工验收程序与结算流程；依民法典第七百九十九条，验收合格后方可交付使用，验收与结算程序不明是工程款拖欠争议的主要成因。",
         advice="写明竣工验收标准、时限与逾期视为认可的处理，以及结算资料提交、审核期限与异议程序。"),
    dict(id="G11", title="安全文明施工责任未划分", category="建设工程合同", severity="medium", mode="absence",
         markers=[r"安全.{0,2}施工", r"安全生产", r"文明施工", r"安全责任"],
         only_types=["建设工程合同"],
         basis=["jzf-45"],
         analysis="未划分施工现场安全文明施工责任；依建筑法第四十五条，施工现场安全由建筑施工企业负责，总包与分包之间的责任划分不明将放大事故风险。",
         advice="写明安全责任主体、安全措施费用列支、总包对分包的安全管理责任及事故报告与处理程序。"),

    # —— 保密协议（NDA） ——
    dict(id="N01", title="保密信息范围未界定", category="保密协议", severity="medium", mode="absence",
         markers=[r"保密信息.{0,8}(?:指|包括|范围|定义|含义)",
                  r"(?:商业秘密|保密资料|保密内容).{0,8}(?:指|包括|范围|定义)"],
         only_types=["保密协议"],
         basis=["fbzdtjzf-10", "mfd-501"],
         analysis="保密协议的核心是界定何为保密信息。范围未界定的，发生泄露时难以举证某项信息属于保密客体，条款可能形同虚设。",
         advice="以「概括定义 + 列举 + 附件清单」界定保密信息：技术信息、经营信息、客户名单、财务数据等，并约定载体形式与标识方式。"),
    dict(id="N02", title="保密期限未约定", category="保密协议", severity="medium", mode="absence",
         markers=[r"保密期限", r"保密期", r"保密义务.{0,10}(?:期限|持续|有效)"],
         only_types=["保密协议"],
         basis=["mfd-509"],
         analysis="未约定保密期限的，接收方保密义务的起止不明；实践中一般约定 2–5 年，核心商业秘密可约定至其公开时止。",
         advice="明确保密义务自披露之日起持续若干年；对构成商业秘密的信息，可约定至该信息进入公有领域时止。"),
    dict(id="N03", title="保密例外情形缺失", category="保密协议", severity="medium", mode="absence",
         markers=[r"例外", r"除外", r"已为公众(?:知悉|所知)", r"(?:公知|公开).{0,4}信息",
                  r"独立(?:开发|获得)", r"(?:依法|依法院|依行政|强制).{0,4}披露"],
         only_types=["保密协议"],
         basis=["fbzdtjzf-10"],
         analysis="缺少公认例外情形（已公开、在先持有、独立开发、依法强制披露）的保密条款过于绝对，既不公平也难以执行。",
         advice="补充四类标准例外，并约定强制披露时的提前通知义务与最小必要披露原则。"),
    dict(id="N04", title="违约金畸高", category="保密协议", severity="high", mode="match",
         patterns=[r"违约金.{0,12}(?:人民币)?\s*(?:[5-9]\d|[1-9]\d{2,})\s*万",
                   r"一次性(?:赔偿|支付).{0,8}(?:[5-9]\d|[1-9]\d{2,})\s*万"],
         only_types=["保密协议"],
         basis=["mfd-585"],
         analysis="保密协议约定 50 万元以上的定额违约金，远超一般泄密损失，法院可依请求调低；过高违约金反而削弱条款威慑的可信度。",
         advice="改为「实际损失赔偿 + 适度违约金」结构，违约金与实际损失挂钩；损失计算可约定参照许可使用费或侵权获利。"),
    dict(id="N05", title="返还与销毁义务缺失", category="保密协议", severity="low", mode="absence",
         markers=[r"返还", r"销毁", r"删除", r"归还"],
         only_types=["保密协议"],
         basis=["mfd-509"],
         analysis="合作终止后保密信息及其载体仍留存在接收方手中，是泄密的高发环节；未约定返还/销毁义务将使后续追责缺乏依据。",
         advice="约定终止或收到书面要求后若干日内返还或销毁全部载体与复制件，并出具书面确认；确需留存的继续保密。"),
    dict(id="N06", title="夹带竞业限制或禁止招揽条款", category="保密协议", severity="high", mode="match",
         patterns=[r"竞业(?:限制|禁止)", r"不得.{0,4}(?:聘用|雇佣|雇用|招揽|挖角|挖聘)"],
         only_types=["保密协议"],
         basis=["ldhtf-24", "mfd-497"],
         analysis="保密协议中夹带竞业限制或禁止招揽，实质扩大了签约方的义务边界；竞业限制依法有主体、期限（≤2 年）与经济补偿的强制要求。",
         advice="删除夹带条款；确有需要的另行签署竞业限制协议，限定人员范围、地域与期限，并约定按月支付经济补偿。"),
    dict(id="N07", title="保密义务单方化失衡", category="保密协议", severity="medium", mode="match",
         patterns=[r"仅.{0,4}(?:乙方|接收方|一方|丙方).{0,12}(?:承担|负有|履行).{0,4}保密",
                   r"(?:甲方|披露方).{0,8}(?:无需|无须|不承担|不负有).{0,4}保密"],
         only_types=["保密协议"],
         basis=["mfd-496", "mfd-497"],
         analysis="双方互相披露信息却只约定一方承担保密义务，权利义务明显失衡；若属格式条款，存在被认定无效的风险。",
         advice="改为双向保密义务，或明确仅单方披露的背景与合理性；双向披露时应适用相同的义务、例外与违约责任。"),
    dict(id="N08", title="知识产权与许可归属未声明", category="保密协议", severity="low", mode="absence",
         markers=[r"知识产权", r"许可", r"授权", r"所有权"],
         only_types=["保密协议"],
         basis=["mfd-501"],
         analysis="未声明披露不构成知识产权转让或许可的，接收方可能主张默示许可，甚至据披露内容抢注专利或商标。",
         advice="补充「不授予任何权利」声明：披露不构成转让或许可，接收方不得据以申请知识产权。"),

    # —— 服务合同（咨询/运营/委托服务） ——
    dict(id="S01", title="服务范围与验收标准缺失", category="服务合同", severity="high", mode="absence",
         markers=[r"服务(?:内容|范围)", r"验收(?:标准|方式|流程)", r"成果(?:交付|验收)"],
         only_types=["服务合同"],
         basis=["mfd-470", "mfd-511"],
         analysis="服务合同未界定服务范围与验收标准，交付成果是否合格全凭一方主观判断，结算与违约认定缺乏依据。",
         advice="以附件列明服务内容清单、交付物明细与客观验收标准，并约定验收流程、异议期与默示合格规则。"),
    dict(id="S02", title="验收结算以单方主观满意为准", category="服务合同", severity="high", mode="match",
         patterns=[r"(?:验收|结算|付款).{0,8}以.{0,6}(?:甲方|委托方|客户).{0,4}(?:满意|认可)",
                   r"(?:甲方|委托方).{0,4}(?:满意|认可).{0,4}(?:后|时).{0,4}(?:付款|支付|结算|验收)"],
         only_types=["服务合同"],
         basis=["mfd-511", "mfd-509"],
         analysis="以委托方主观满意作为验收与付款条件，服务方完成工作后仍可能被无限期拒付，回款风险极高。",
         advice="将验收标准客观化（服务清单、KPI、交付物确认单），约定异议期与逾期视为验收合格。"),
    dict(id="S03", title="服务费用包含范围不明", category="服务合同", severity="low", mode="absence",
         markers=[r"差旅", r"税费", r"报销", r"费用(?:包含|包括|不含|分担)"],
         only_types=["服务合同"],
         basis=["mfd-511"],
         analysis="服务费是否包含差旅、税费、第三方采购等未明确的，履约中极易因零星费用产生争议。",
         advice="明确服务费包含范围：差旅报销标准、税费承担、第三方费用审批流程，避免“一口价”模糊表述。"),
    dict(id="S04", title="服务人员单方更换无约束", category="服务合同", severity="medium", mode="match",
         patterns=[r"(?:有权|可以).{0,4}(?:自行|单方|随时).{0,2}更换.{0,4}(?:人员|团队|成员)",
                   r"更换(?:服务|项目|派驻)人员.{0,8}(?:无需|无须).{0,4}(?:通知|同意|征得)"],
         only_types=["服务合同"],
         basis=["mfd-509"],
         analysis="服务合同高度依赖具体团队，允许服务方任意更换人员的，服务质量与连续性无法保障。",
         advice="锁定核心服务人员名单与资质，更换须经委托方书面同意，并保证接替人员资质不低于原人员。"),
    dict(id="S05", title="委托方配合义务与服务期限顺延缺失", category="服务合同", severity="medium", mode="absence",
         markers=[r"配合", r"协助", r"提供(?:资料|条件|场地|必要)"],
         only_types=["服务合同"],
         basis=["mfd-509"],
         analysis="服务交付依赖委托方及时提供资料与确认，未约定配合义务与顺延机制的，进度延误责任将全部归于服务方。",
         advice="约定委托方提供资料、反馈确认的时限，以及因其迟延导致服务期限相应顺延、费用调整的处理规则。"),
    dict(id="S06", title="完全排除间接损失的责任限制", category="服务合同", severity="medium", mode="match",
         patterns=[r"不承担.{0,6}(?:任何|一切).{0,4}(?:间接|直接|可得利益|预期利益|利润).{0,4}(?:损失|损害)",
                   r"任何情况下.{0,4}(?:均|都).{0,2}不承担.{0,6}(?:损失|责任)"],
         only_types=["服务合同"],
         basis=["mfd-584"],
         analysis="绝对化的责任排除条款使违约成本归零，守约方实际损失无法追偿；格式条款情形下还有无效风险。",
         advice="设置与合同金额挂钩的合理责任上限，并对数据泄露、知识产权侵权等重大风险作除外约定。"),
    dict(id="S07", title="服务质量保证与返工补救缺失", category="服务合同", severity="low", mode="absence",
         markers=[r"返工", r"重做", r"补救", r"整改", r"质量保证"],
         only_types=["服务合同"],
         basis=["mfd-577"],
         analysis="未约定服务不合格的返工、重做与补救机制的，验收不通过时缺乏除拒付外的中间救济路径。",
         advice="约定服务瑕疵的免费返工/重做义务、整改期限，以及经整改仍不合格时的减价或解除权。"),
    dict(id="S08", title="转委托与分包未作限制", category="服务合同", severity="low", mode="absence",
         markers=[r"转委托", r"分包", r"转包", r"第三方.{0,4}(?:履行|完成|执行)"],
         only_types=["服务合同"],
         basis=["mfd-923"],
         analysis="未限制转委托与分包的，服务可能被套层转包给不具备资质或保密能力的第三方，质量与数据安全失控。",
         advice="约定未经书面同意不得将核心服务转委托或分包；经同意的，服务方对第三方行为承担连带责任。"),

    # —— 技术开发/转让/服务合同 ——
    dict(id="T01", title="技术成果归属未约定或约定失衡", category="技术合同", severity="high", mode="check_industry",
         industry="tech_dev", only_types=["技术开发合同", "技术转让合同", "技术服务合同", "软件开发合同"],
         basis=["mfd-847", "mfd-859"],
         analysis="委托开发未约定成果归属的，申请专利权利归研究开发方；合作开发归合作方共有。约定不明将导致权属争议。",
         advice="明确约定申请专利/软件著作权的归属、双方实施许可权、改进成果归属以及第三方维权责任。"),
    dict(id="T02", title="源代码/交付物不明确", category="技术合同", severity="high", mode="match",
         industry="tech_dev",
         patterns=[r"交付.{0,20}(?:成果|作品|系统|软件).{0,20}(?:不包括|不含|不含源代码|仅含目标代码)",
                   r"仅.{0,8}(?:交付|提供).{0,8}(?:可执行程序|目标文件|二进制)"],
         basis=["mfd-852"],
         analysis="软件开发类合同中仅约定交付可执行文件而不含源代码，将使委托方丧失二次开发、自主运维与安全审计能力。",
         advice="明确交付物清单：源代码、编译脚本、部署文档、测试报告、数据库 schema，并约定源代码托管与移交时点。"),
    dict(id="T03", title="技术验收标准缺失", category="技术合同", severity="medium", mode="absence",
         industry="tech_dev",
         markers=[r"验收标准", r"验收条件", r"交付验收", r"测试标准", r"性能指标"],
         only_types=["技术开发合同", "软件开发合同", "技术服务合同"],
         basis=["mfd-852"],
         analysis="缺少量化的验收标准，将导致交付时双方对「是否完成」各执一词，拖延付款与项目收尾。",
         advice="明确功能清单、性能指标（响应时间、并发、可用性）、安全要求、文档交付及验收测试流程与异议期。"),
    dict(id="T04", title="技术服务人员资质/人数未约定", category="技术合同", severity="low", mode="absence",
         industry="tech_dev",
         markers=[r"(?:工程师|开发人员|技术人员|架构师).{0,6}(?:人数|数量|配置)", r"驻场.{0,6}(?:人员|团队)"],
         only_types=["技术服务合同", "软件开发合同", "技术开发合同"],
         basis=["mfd-846"],
         analysis="未明确技术服务团队的人员配置、驻场天数或资质要求，可能导致交付质量不稳定。",
         advice="约定核心人员角色、最低人数、驻场/远程比例、关键人员变更流程及人员资质文件要求。"),
    dict(id="T05", title="反向工程禁止条款过严", category="技术合同", severity="medium", mode="match",
         industry="tech_dev",
         patterns=[r"(?:禁止|不得).{0,8}(?:反向工程|逆向工程|反编译|反汇编)",
                   r"(?:任何|全部).{0,8}(?:反向工程|逆向).{0,8}(?:一律|绝对).{0,8}(?:禁止|不允许)"],
         only_types=["技术开发合同", "软件开发合同", "软件许可合同"],
         basis=["mfd-850"],
         analysis="过度禁止反向工程可能妨碍兼容性研发、安全审计与合法权益行使；合法的反向工程属于合理使用。",
         advice="限定禁止范围（不得用于侵犯知识产权或商业秘密），明确允许的安全审计、兼容性测试场景。"),
    dict(id="T06", title="技术转让未约定技术培训与后续服务", category="技术合同", severity="medium", mode="absence",
         industry="tech_dev",
         markers=[r"技术培训", r"培训服务", r"后续服务", r"技术支持", r"运维"],
         only_types=["技术转让合同", "技术许可合同"],
         basis=["mfd-846"],
         analysis="技术转让后受让方常因缺乏实施经验而无法落地，未约定培训和技术服务将使转让费打水漂。",
         advice="约定培训次数/时长/对象、技术服务响应时间、保修期（Warranty Period）及升级/更新条款。"),
    dict(id="T07", title="软件许可证类型/范围未明确", category="技术合同", severity="medium", mode="absence",
         industry="tech_dev",
         markers=[r"许可类型", r"许可证类型", r"(?:独占|排他|普通).{0,8}(?:许可|license)",
                  r"使用期限", r"用户数", r"并发数"],
         only_types=["软件许可合同", "技术转让合同"],
         basis=["mfd-851"],
         analysis="软件许可未明确是独占/排他/普通许可，以及使用期限、用户数、部署方式，将使被许可方的权利边界模糊。",
         advice="明确许可类型（独占/非独占）、使用地域、期限、用户/设备/并发限制、转授权是否允许及违约计费方式。"),
    dict(id="T08", title="技术开发风险分担不明", category="技术合同", severity="medium", mode="absence",
         industry="tech_dev",
         markers=[r"(?:风险|失败|无法实现).{0,8}(?:分担|承担|责任)", r"(?:技术|开发).{0,8}(?:风险|失败)"],
         only_types=["技术开发合同", "合作开发合同"],
         basis=["mfd-858"],
         analysis="技术开发存在固有失败风险，未约定风险分担机制将导致失败后双方推诿费用损失与责任。",
         advice="约定技术风险（无法克服的技术困难）与商业风险的区分、损失分担比例、阶段性里程碑与终止权。"),

    # —— 采购/买卖合同 ——
    dict(id="C01", title="质量标准与检验期间未约定", category="采购合同", severity="high", mode="absence",
         markers=[r"质量标准", r"技术标准", r"检验期间", r"验收期间", r"质量异议"],
         only_types=["采购合同", "买卖合同", "供需合同"],
         basis=["mfd-621", "mfd-622"],
         analysis="货物买卖未约定质量标准与检验期限，买方收到瑕疵货物后可能因逾检验期而丧失异议权。",
         advice="明确引用国标/行标/企标编号、检验方法、检验期间（外观瑕疵短、隐蔽瑕疵可延长）及异议书面流程。"),
    dict(id="C02", title="付款与交货顺序未挂钩", category="采购合同", severity="medium", mode="absence",
         markers=[r"(?:先付|后付|款到|货到).{0,8}(?:发货|交货|付款)", r"(?:付款|支付).{0,15}(?:发货|交货|验收)"],
         only_types=["采购合同", "买卖合同", "供需合同"],
         basis=["mfd-510", "mfd-626"],
         analysis="未明确付款与交货的先后顺序，容易引发「卖方不发货/买方不付款」的同时履行抗辩僵局。",
         advice="设置合理的交易链条，如预付定金 → 发货前验货 → 货到验收后付款，并约定银行保函或信用证。"),
    dict(id="C03", title="所有权保留约定不当", category="采购合同", severity="medium", mode="match",
         patterns=[r"所有权.{0,12}(?:自|于).{0,20}(?:付清|全部货款|全部价款).{0,8}(?:转移|归|属于|所有)",
                   r"(?:付清|全部).{0,12}(?:货款|价款).{0,12}(?:前|之前).{0,12}(?:所有权)"],
         only_types=["采购合同", "买卖合同"],
         basis=["mfd-641"],
         analysis="所有权保留虽然是合法的卖方权益保护手段，但未登记可能无法对抗善意第三人，且不得违反强制性规定。",
         advice="明确所有权保留范围、登记义务、买方处分限制以及卖方取回权的行使条件与程序。"),
    dict(id="C04", title="知识产权担保/侵权责任未约定", category="采购合同", severity="high", mode="absence",
         markers=[r"(?:卖方|供方|乙方).{0,12}(?:保证|担保|承诺).{0,20}(?:不侵犯|无知识产权).{0,8}(?:权利|侵权)",
                  r"(?:知识产权|第三方).{0,8}(?:侵权|索赔).{0,8}(?:责任|承担)"],
         only_types=["采购合同", "买卖合同"],
         basis=["mfd-637"],
         analysis="卖方交付的货物侵犯第三方知识产权时，买方可能被卷入诉讼；合同未约定卖方的担保与赔偿义务将使买方承担全部风险。",
         advice="补充知识产权瑕疵担保条款：卖方承诺不侵权、承担全部诉讼费用与赔偿、授权买方处理纠纷并追偿。"),
    dict(id="C05", title="货物毁损灭失风险转移未约定", category="采购合同", severity="medium", mode="absence",
         markers=[r"(?:风险|毁损|灭失).{0,8}(?:转移|承担|分担)", r"交付.{0,8}(?:风险)"],
         only_types=["采购合同", "买卖合同"],
         basis=["mfd-604"],
         analysis="货物在运输途中毁损灭失的风险，依法自交付时起由买受人承担；合同另有约定的从其约定，但约定不明会引发争议。",
         advice="明确风险转移时点（工厂交货/装车/到达买方仓库/签收），以及运输方式、保险购买方和索赔流程。"),
    dict(id="C06", title="供应商/买方违约金不对等", category="采购合同", severity="medium", mode="match",
         patterns=[r"(?:卖方|供方|乙方).{0,30}(?:违约金|逾期).{0,30}(?:买方|甲方|采购方).{0,30}(?:违约金|逾期)",
                   r"(?:供方|卖方).{0,30}违约金.{0,15}(?:高于|远超).{0,15}(?:买方|甲方).{0,30}违约金"],
         only_types=["采购合同", "买卖合同", "供需合同"],
         basis=["mfd-585", "mfd-497"],
         analysis="违约金条款在格式采购合同中常偏向买方，卖方违约金显著高于买方违约金，构成格式条款权利义务失衡。",
         advice="按风险对等原则设置双方违约金，区分逾期付款/逾期交货不同情形，避免使用固定百分比的「一刀切」条款。"),
    dict(id="C07", title="验收标准主观化", category="采购合同", severity="high", mode="match",
         patterns=[r"(?:验收|检验).{0,10}(?:以|凭|根据).{0,6}(?:甲方|买方|采购方).{0,6}(?:满意|认可)",
                   r"(?:甲方|买方|采购方).{0,6}(?:满意|认可).{0,4}(?:后|时).{0,4}(?:付款|验收|支付)",
                   r"主观满意"],
         only_types=["采购合同", "买卖合同", "供需合同"],
         basis=["mfd-511", "mfd-620"],
         analysis="以「买方满意」作为验收标准，等于把验收权完全交给付款方的主观判断，卖方交付后可能被无限期拒收拒付。",
         advice="验收标准应客观化：引用质量标准编号、双方封样或技术参数表；约定检验期与逾期默示合格规则。"),
    dict(id="C08", title="交货地点与运费承担不明", category="采购合同", severity="medium", mode="absence",
         markers=[r"(?:交货|交付|送货).{0,4}(?:地点|地址)", r"运费", r"运输费用", r"物流费"],
         only_types=["采购合同", "买卖合同", "供需合同"],
         basis=["mfd-603"],
         analysis="交付地点不明将连带导致风险转移时点、运费承担、验收地点全部不确定，是买卖合同最高频的争议源头之一。",
         advice="写明交货地点、运输方式与运费承担方；约定「货交承运人」或「送达买方仓库」等清晰节点。"),
    dict(id="C09", title="包装要求未约定", category="采购合同", severity="low", mode="absence",
         markers=["包装", "封装"],
         only_types=["采购合同", "买卖合同"],
         basis=["mfd-619"],
         analysis="精密仪器、易碎品、出口货物等对包装有特殊要求；未约定包装方式的，货损后责任难以划分。",
         advice="约定包装标准（国标/行标/出口标准）、标识要求与包装费用归属，以及包装不当致损的赔偿责任。"),
    dict(id="C10", title="质保期与售后责任未约定", category="采购合同", severity="medium", mode="absence",
         markers=[r"质保", r"保修", r"三包", r"质量保证期", r"售后(?:服务|维修)"],
         only_types=["采购合同", "买卖合同", "供需合同"],
         basis=["mfd-617"],
         analysis="设备类采购缺少质保期与售后响应约定，货物验收后出现质量问题时，维修、更换、退货均缺乏合同依据。",
         advice="约定质保期限、响应时限、免费维修更换范围，以及经多次维修仍不合格时的退货与索赔权。"),
    dict(id="C11", title="分批交货与溢短装未约定", category="采购合同", severity="low", mode="absence",
         markers=[r"分批", r"分次(?:交货|交付|发货)", r"溢短装", r"(?:溢|短)(?:装|交)"],
         only_types=["采购合同", "买卖合同"],
         basis=["mfd-633"],
         analysis="大宗或长期供货通常分批交付，未约定批次、数量溢短装与单批违约的影响范围，容易因一批瑕疵引发整单争议。",
         advice="以附件列明批次时间表，约定 ±百分比溢短装与多退少补规则，并明确单批不合格不影响其他批次效力。"),
    dict(id="C12", title="单方扣款/抵销权过宽", category="采购合同", severity="medium", mode="match",
         patterns=[r"(?:甲方|买方|采购方).{0,10}有权.{0,10}(?:从|自).{0,6}(?:应付)?.{0,4}(?:货款|款项|价款).{0,4}中.{0,2}(?:扣除|扣减|抵扣|扣罚)",
                   r"(?:甲方|买方).{0,8}(?:直接|自行|单方).{0,4}(?:扣款|扣除|抵扣)"],
         only_types=["采购合同", "买卖合同", "供需合同"],
         basis=["mfd-568", "mfd-569"],
         analysis="约定买方可单方从货款中扣除违约金、罚款或索赔款，使卖方债权长期处于不确定状态；法定抵销须以到期债务为限。",
         advice="改为凭双方确认的结算单或生效法律文书抵扣，并设置提前书面通知与异议期。"),
    dict(id="C13", title="采购定价与比价机制未约定", category="采购合同", severity="low", mode="absence",
         markers=["比价", "市场价"],
         only_types=["采购合同", "供需合同"],
         basis=["mfd-509"],
         analysis="采购价格缺少比价、询价或价格复核安排时，难以为高价采购和利益输送提供内控依据，事后也难以举证价格公允。",
         advice="约定比价或招标程序：单笔超过约定金额的采购应取得不少于三家报价并留痕，价格调整需经审批复核。"),

    # —— 运输合同 ——
    dict(id="Y01", title="货物保险责任未约定", category="运输合同", severity="medium", mode="absence",
         markers=[r"保险", r"投保", r"运费险", r"货物保险"],
         only_types=["运输合同", "物流合同", "货运代理合同"],
         basis=["mfd-832"],
         analysis="运输途中货物毁损灭失风险较高，未约定投保义务将使损失索赔依赖于责任认定，增加追偿成本。",
         advice="明确投保主体（托运人/承运人）、投保金额（按货值的 %）、保险险种（一切险/平安险）及理赔配合义务。"),
    dict(id="Y02", title="运输时效/延误责任未明确", category="运输合同", severity="medium", mode="absence",
         markers=[r"(?:运输|送达|交付).{0,8}(?:时效|时限|期间|天数)",
                  r"(?:延误|迟延|超时).{0,8}(?:责任|赔偿|违约金)"],
         only_types=["运输合同", "物流合同"],
         basis=["mfd-825"],
         analysis="未约定运输时效与延误赔偿，承运人可以长期拖延而无需承担违约责任，尤其在生鲜/医药等高时效要求货物中损失重大。",
         advice="约定「门到门」时效天数、起算时点、延误分级赔偿（如每超 24h 按运费 % 赔偿），以及不可抗力例外。"),
    dict(id="Y03", title="危险品/特殊货物特殊要求缺失", category="运输合同", severity="medium", mode="check_industry",
         industry="transport",
         basis=["mfd-836", "aqscf-36"],
         analysis="运输合同涉及危险品、冷链、精密仪器等特殊货物，却未约定包装、标识、装卸、保管的特殊要求，易引发安全事故与货损争议。",
         advice="针对特殊货物列明包装标准、警示标识、温湿度范围、装卸设备资质、随车文件及应急预案。"),
    dict(id="Y04", title="收货人权限不明（签收即免责陷阱）", category="运输合同", severity="medium", mode="match",
         patterns=[r"签收.{0,8}(?:视为|即).{0,8}(?:合格|无异议|完好)",
                   r"(?:收货人|甲方).{0,20}(?:签收|盖章).{0,20}(?:后|即).{0,20}(?:不得|无权).{0,20}(?:提出异议|索赔)"],
         only_types=["运输合同", "物流合同"],
         basis=["mfd-622", "mfd-826"],
         analysis="约定收货人签字即视为货物无异议，剥夺了买方对隐蔽瑕疵的验货与索赔权利，属于不合理的格式免责。",
         advice="区分外观瑕疵与隐蔽瑕疵的异议期（外观当场/隐蔽 7-15 天），签收仅表示「收到」而非「合格」。"),

    dict(id="Y05", title="未保价货物按运费倍数限额赔偿", category="运输合同", severity="high", mode="match",
         patterns=[r"未保价.{0,12}(?:按|依照).{0,4}运费.{0,4}(?:的)?.{0,2}(?:一|二|两|三|\d)?倍.{0,4}(?:赔偿|赔付|为限)",
                   r"(?:最高|限额)赔偿.{0,6}(?:不超过|为).{0,4}运费.{0,4}(?:的)?.{0,2}\d?倍",
                   r"赔偿(?:责任|金额).{0,4}以运费.{0,4}(?:为限|为上限)"],
         only_types=["运输合同", "物流合同", "货运代理合同"],
         basis=["mfd-833", "mfd-497"],
         analysis="未保价货物灭失仅按运费数倍赔偿，高值货物损失将绝大部分由托运方自行承担；若属格式条款且未尽提示说明，限额约定可能无效。",
         advice="高值货物务必保价运输并留存货值凭证；或改为按货物实际价值赔偿，删除运费倍数限额条款。"),
    dict(id="Y06", title="托运申报与货物信息义务缺失", category="运输合同", severity="low", mode="absence",
         markers=[r"品名", r"申报", r"货物(?:名称|信息|性质)", r"(?:件数|重量|体积)"],
         only_types=["运输合同", "物流合同", "货运代理合同"],
         basis=["mfd-825"],
         analysis="未约定托运人准确申报货物品名、性质、重量等信息的义务，错报瞒报导致事故或罚款时责任无法划分。",
         advice="约定托运人如实申报义务、错报瞒报的赔偿责任，以及承运人查验权与拒运权。"),
    dict(id="Y07", title="留置权范围过宽", category="运输合同", severity="medium", mode="match",
         patterns=[r"(?:有权|可以).{0,2}留置.{0,8}(?:全部|所有|任何).{0,2}(?:货物|物品|财产)",
                   r"未(?:付清|支付).{0,4}(?:任何|费用|款项).{0,4}(?:即|均).{0,2}(?:可|有权).{0,2}留置"],
         only_types=["运输合同", "物流合同"],
         basis=["mfd-836"],
         analysis="留置权依法以与未付费用相应的货物为限，约定对全部货物甚至无关批次货物行使留置，超出法定范围的部分不受保护。",
         advice="将留置范围限定为与欠付费用价值相当的货物，并约定留置后的通知、保管与处置程序。"),
    dict(id="Y08", title="延误与货损免责过宽", category="运输合同", severity="high", mode="match",
         patterns=[r"(?:延误|延迟|迟到).{0,8}不承担.{0,4}(?:任何)?.{0,2}(?:责任|赔偿|损失)",
                   r"(?:货损|货差|灭失|损坏).{0,6}(?:概不负责|一律不赔|不承担责任)"],
         only_types=["运输合同", "物流合同", "货运代理合同"],
         basis=["mfd-832", "mfd-497"],
         analysis="承运人对运输途中货损原则上承担赔偿责任，绝对化免责条款排除法定责任，格式条款情形下存在无效风险。",
         advice="删除绝对免责表述，按不可抗力、货物自然性质、托运人过错等法定免责事由逐项列明，并约定索赔时限。"),

    # —— 房地产/土地开发 ——
    dict(id="F01", title="土地出让/转让价款构成不明", category="房地产合同", severity="high", mode="absence",
         markers=[r"(?:土地|出让金|转让费).{0,8}(?:构成|包含|范围)",
                  r"(?:出让金|地价款).{0,8}(?:是否|是否包含).{0,8}(?:拆迁|补偿|税费)"],
         only_types=["土地出让合同", "土地转让合同", "房地产合作开发合同"],
         basis=["mfd-359", "tdglf-55"],
         analysis="土地出让/转让价格是否包含拆迁补偿、市政配套、税费等构成不明，可能使受让方在合同总价之外承担大额隐性成本。",
         advice="详细列示价款构成（出让金/拆迁补偿/安置费/市政配套/契税），明确「大包干」或「据实结算」模式。"),
    dict(id="F02", title="规划/审批/开工期限未约定", category="房地产合同", severity="medium", mode="absence",
         markers=[r"(?:规划|开工|竣工).{0,8}(?:期限|时间|日期)", r"(?:审批|报建).{0,8}(?:义务|责任|期限)"],
         only_types=["房地产合作开发合同", "委托代建合同"],
         basis=["fdcglf-26", "jzf-8"],
         analysis="房地产合作开发项目涉及大量行政审批（立项、规划、用地、施工许可），未约定各方审批义务与时限将导致项目推进停滞。",
         advice="按里程碑方式列示：立项→用地审批→规划许可→施工许可→开工→预售→竣工，每步约定责任人与完成时限。"),
    dict(id="F03", title="商品房预售资金监管未提及", category="房地产合同", severity="medium", mode="absence",
         markers=[r"预售资金.{0,8}(?:监管|账户|专用)", r"(?:购房款|房价款).{0,8}(?:存入|进入).{0,8}(?:监管|专用)"],
         only_types=["商品房预售合同"],
         basis=["fdcglf-45"],
         analysis="商品房预售款应存入银行专用监管账户，用于本项目建设；未提及监管可能构成违规挪用，也增加购房人资金风险。",
         advice="明确预售资金监管银行、监管账户号、资金用途（仅用于本项目建设）、解除监管条件以及监管协议编号。"),
    dict(id="F04", title="房地产开发未约定土地交付条件", category="房地产合同", severity="medium", mode="absence",
         markers=[r"(?:土地|地块|宗地).{0,8}(?:交付|移交|交付条件)", r"(?:四通一平|五通一平|七通一平)"],
         only_types=["土地出让合同", "土地转让合同"],
         basis=["fdcglf-17"],
         analysis="土地交付未约定「通平」条件（通水、通电、通路、通讯、场地平整等），受让方可能在接收后需投入巨额基础设施费用。",
         advice="明确交付时点、「X通一平」具体内容、市政接口位置、土地权属无瑕疵承诺以及未达条件的赔偿机制。"),

    dict(id="F05", title="预售许可等证照信息未载明", category="房地产合同", severity="high", mode="absence",
         markers=[r"预售许可证", r"(?:不动产权证|国有土地使用证).{0,4}(?:编号|证号)", r"(?:规划许可证|施工许可证)"],
         only_types=["商品房预售合同"],
         basis=["fdcglf-45"],
         analysis="商品房预售以取得预售许可证为法定条件，合同未载明证照编号的，无法核验销售合法性，买受人钱房两空风险极高。",
         advice="要求出卖人在合同中载明预售许可证、土地证、规划与施工许可证编号，并通过住建部门官网核验后付款。"),
    dict(id="F06", title="面积差异处理未约定", category="房地产合同", severity="medium", mode="absence",
         markers=[r"面积(?:误差|差异)", r"面积补差", r"多退少补", r"(?:建筑面积|套内).{0,6}(?:误差|差异|退|补)"],
         only_types=["商品房预售合同"],
         basis=["l0a4555e0-27"],
         analysis="期房实测面积与暂测面积常有出入，未约定面积误差处理方式的，交房时极易就补差价或退房产生争议。",
         advice="约定实测面积与约定面积误差比绝对值的处理规则（如 3% 以内据实结算、超出部分买受人有权退房）。"),
    dict(id="F07", title="逾期交房与办证责任未约定", category="房地产合同", severity="high", mode="absence",
         markers=[r"(?:交付|交房).{0,6}(?:日期|期限|时间)", r"(?:不动产权证|权属证书|房产证).{0,6}(?:办理|登记|取得)"],
         only_types=["商品房预售合同"],
         basis=["ld399ad90-14", "mfd-577"],
         analysis="未约定交房日期、办证期限及逾期责任的，开发商迟延交房、拖延办证时买受人缺乏违约金与解除权依据。",
         advice="明确交房日期、交付条件、不动产权证办理期限，约定按日计算的逾期违约金与超期解除权。"),
    dict(id="F08", title="广告宣传与规划变更约束缺失", category="房地产合同", severity="low", mode="absence",
         markers=[r"广告", r"宣传", r"规划变更", r"设计变更", r"(?:样板间|样板房)"],
         only_types=["商品房预售合同"],
         basis=["ld399ad90-3"],
         analysis="销售广告中就配套设施、装修标准等具体承诺可构成合同内容，未将其纳入合同并约定规划变更通知与救济的，收房与宣传不符时难以索赔。",
         advice="将楼书、沙盘、样板间承诺的关键内容写入合同附件，约定规划/设计变更的通知义务与买受人解除权。"),

    # —— 股权/投资/合伙 ——
    dict(id="E01", title="股权转让未处理其他股东优先购买权", category="股权投资", severity="high", mode="absence",
         markers=[r"优先购买权", r"其他股东.{0,6}同意", r"过半数同意"],
         only_types=["股权转让合同"], industry="equity",
         basis=["gsf-84"],
         analysis="向股东以外的人转让股权，未书面通知其他股东并处理优先购买权，其他股东可主张以同等条件购买，导致交易被撤销或陷入诉讼。",
         advice="补充其他股东放弃优先购买权的书面声明，或约定通知义务的履行情况与违约后果；受让方应将其作为付款前置条件。"),
    dict(id="E02", title="未实缴出资股权的转让责任未划分", category="股权投资", severity="medium", mode="check_industry",
         only_types=["股权转让合同"], industry="equity",
         basis=["gsf-88"],
         analysis="转让认缴未实缴的股权，未约定出资义务归属；依公司法第八十八条，出资不足时转让人与受让人可能承担连带责任。",
         advice="明确认缴出资的实缴义务归属与价款折算安排；受让方应核查目标公司出资证明与章程记载。"),
    dict(id="E03", title="认缴出资缴付期限未约定", category="股权投资", severity="medium", mode="absence",
         markers=[r"出资.{0,4}(?:期限|时间|缴付|缴足)", r"实缴.{0,4}(?:期限|时间)"],
         only_types=["增资协议"], industry="equity",
         basis=["gsf-47"],
         analysis="增资认缴未约定缴付期限；依公司法第四十七条，认缴出资应自公司成立/增资之日起五年内缴足，逾期可能被要求加速到期。",
         advice="明确各期出资金额与缴付日期，约定逾期出资的违约金、失权后果与验资安排。"),
    dict(id="E04", title="回购/对赌条款需人工核查", category="股权投资", severity="medium", mode="match",
         patterns=[r"对赌", r"估值调整", r"业绩承诺.{0,30}(?:未|不).{0,4}(?:达成|完成|达标).{0,30}回购", r"股权回购"],
         only_types=["股权转让合同", "增资协议"], industry="equity", basis=["mfd-904", "mfd-905"],
         analysis="合同含回购/对赌/估值调整安排。此类条款的效力与可执行性高度依赖个案（回购主体、是否完成减资程序等），规则无法替代判断。",
         advice="逐项核对：回购触发条件是否可量化、回购价格与利率、回购主体（公司回购受公司法与减资程序限制）、履行期限；金额重大时请执业律师复核。"),
    dict(id="E05", title="普通合伙的无限连带责任提示", category="股权投资", severity="low", mode="check_industry",
         only_types=["合伙协议"], industry="equity",
         basis=["hhzf-2", "hhzf-39"],
         analysis="普通合伙人对合伙企业债务承担无限连带责任，责任范围不限于认缴出资额；签署前应确认合伙形式与责任条款。",
         advice="核实合伙类型与合伙人身份（GP/LP）；如为普通合伙，评估个人资产敞口并考虑责任限制安排。"),
    dict(id="E06", title="合伙份额对外转让的同意机制缺失", category="股权投资", severity="low", mode="absence",
         markers=[r"一致同意", r"其他合伙人.{0,6}同意", r"优先购买"],
         only_types=["合伙协议"], industry="equity",
         basis=["hhzf-22"],
         analysis="未约定合伙份额对外转让的同意机制；依合伙企业法第二十二条，向合伙人以外的人转让财产份额须经其他合伙人一致同意（协议另有约定除外）。",
         advice="明确份额转让的同意门槛、通知程序与其他合伙人优先购买权，避免后续转让僵局。"),
    dict(id="E07", title="退伙与财产结算未约定", category="股权投资", severity="low", mode="absence",
         markers=[r"退伙"],
         only_types=["合伙协议"], industry="equity",
         basis=["hhzf-45", "hhzf-52"],
         analysis="未约定退伙事由、退伙程序与财产份额退还方式；发生退伙时只能按法定规则结算，易对退还金额与时间产生争议。",
         advice="约定退伙事由、提前通知期、财产审计与退还方式、退还期限及退伙人对既往债务的承担范围。"),
    dict(id="E08", title="价款支付与股东变更登记未衔接", category="股权投资", severity="medium", mode="absence",
         markers=[r"变更登记", r"工商变更", r"股东名册", r"变更手续"],
         only_types=["股权转让合同", "增资协议"], industry="equity",
         basis=["gsf-86"],
         analysis="未约定股东名册变更与工商变更登记的办理时限及与付款节点的衔接；未办理变更登记不得对抗第三人，受让方权利存在瑕疵。",
         advice="将变更登记设为付款节点（如先变更后付尾款），明确双方配合义务、办理时限与逾期违约责任。"),
    dict(id="E09", title="存在股权代持安排", category="股权投资", severity="medium", mode="match",
         patterns=[r"(?:股权|股份).{0,4}代持", r"代持.{0,4}(?:协议|安排)", r"隐名.{0,2}(?:股东|出资)", r"名义股东"],
         industry="equity", basis=["mfd-904"],
         analysis="合同涉及股权代持。代持协议的效力、显名条件与实际出资人权利保护均有较大不确定性，且可能被用于规避监管或债务。",
         advice="核查代持原因是否合法、显名条件与程序、分红与表决权安排、名义股东擅自处分的防范；强监管行业或涉外代持务必交执业律师复核。"),

    # —— 担保/金融 ——
    dict(id="D01", title="保证方式未约定（默认按一般保证）", category="担保金融", severity="medium", mode="check_industry",
         only_types=["保证合同", "担保合同"], industry="guarantee",
         basis=["mfd-686", "mfd-687"],
         analysis="有保证条款却未写明一般保证还是连带责任保证；依民法典第六百八十六条，约定不明按一般保证处理，债权人须先向债务人追偿才能找保证人，回收路径明显变长。",
         advice="债权人角度应明确写「连带责任保证」；保证人角度则相反。双方都应确认条款与商业意图一致。"),
    dict(id="D02", title="保证期间未约定", category="担保金融", severity="medium", mode="absence",
         markers=[r"保证期间"],
         only_types=["保证合同", "担保合同"], industry="guarantee",
         basis=["mfd-692"],
         analysis="未约定保证期间，依民法典第六百九十二条为主债务履行期届满之日起六个月；债权人逾期不主张，保证责任消灭。",
         advice="明确保证期间（通常主债务到期后 1-3 年），并登记到期日，避免保证责任过期失效。"),
    dict(id="D03", title="疑似流押条款", category="担保金融", severity="high", mode="match",
         patterns=[r"(?:到期|届满).{0,12}(?:不还|未还|不履行).{0,20}抵押.{0,4}(?:财产|物|房产|车).{0,6}归.{0,8}(?:债权|甲方)",
                   r"抵押.{0,4}(?:财产|物).{0,6}直接.{0,4}归.{0,8}(?:债权|甲方)"],
         only_types=["抵押合同", "担保合同"], industry="guarantee",
         basis=["mfd-401"],
         analysis="约定债务人不履行到期债务时抵押财产直接归债权人所有，属于流押条款；依民法典第四百零一条不发生所有权转移效力，只能依法就抵押财产优先受偿。",
         advice="删除流押表述，改为约定抵押权实现方式（协议折价、拍卖、变卖）与处置程序。"),
    dict(id="D04", title="不动产抵押未约定登记", category="担保金融", severity="high", mode="check_industry",
         only_types=["抵押合同", "担保合同"], industry="guarantee",
         basis=["mfd-402"],
         analysis="以房屋、土地等不动产抵押却未约定办理抵押登记；依民法典第四百零二条，不动产抵押权自登记时设立，未登记则抵押权不成立。",
         advice="明确登记机关、办理时限与费用承担，并将他项权证交付作为放款或交易的前置条件。"),
    dict(id="D05", title="疑似流质条款", category="担保金融", severity="high", mode="match",
         patterns=[r"(?:到期|届满).{0,12}(?:不还|未还|不履行).{0,20}质押.{0,4}(?:财产|物|股权|权利).{0,6}归.{0,8}(?:债权|甲方)",
                   r"质押.{0,4}(?:财产|物).{0,6}直接.{0,4}归.{0,8}(?:债权|甲方)"],
         only_types=["质押合同", "担保合同"], industry="guarantee",
         basis=["mfd-428"],
         analysis="约定债务人不履行到期债务时质押财产直接归债权人所有，属于流质条款；依民法典第四百二十八条不发生所有权转移效力，只能依法优先受偿。",
         advice="删除流质表述，改为约定质权实现方式（折价、拍卖、变卖）与处置程序。"),
    dict(id="D06", title="公司对外担保未见决议安排", category="担保金融", severity="medium", mode="check_industry",
         only_types=["保证合同", "抵押合同", "质押合同", "担保合同"], industry="guarantee",
         basis=["gsf-15"],
         analysis="以公司名义对外提供担保，却未提及股东会/董事会决议；依公司法第十五条公司对外担保须经决议，相对人未尽审查义务时担保可能对公司不发生效力。",
         advice="将公司决议文件作为合同附件或生效条件；债权人应留存决议与章程审查记录。"),
    dict(id="D07", title="保证范围未约定", category="担保金融", severity="low", mode="absence",
         markers=[r"保证范围", r"担保范围"],
         only_types=["保证合同", "担保合同"], industry="guarantee",
         basis=["mfd-691"],
         analysis="未约定保证范围；依民法典第六百九十一条，默认包括主债权及利息、违约金、损害赔偿金和实现债权的费用。",
         advice="明确是否包含利息、违约金与实现债权的费用（律师费、诉讼费、保全费），避免解释争议。"),
    dict(id="D08", title="疑似独立担保条款", category="担保金融", severity="medium", mode="match",
         patterns=[r"独立担保", r"独立保函", r"担保.{0,6}(?:效力|责任).{0,6}独立于", r"不受主合同.{0,10}(?:效力|无效).{0,6}影响"],
         only_types=["保证合同", "抵押合同", "质押合同", "担保合同"], industry="guarantee",
         basis=["mfd-682"],
         analysis="约定担保独立于主合同效力；依民法典第六百八十二条，主合同无效则担保合同无效，国内交易中的独立担保约定通常不被认可（合规独立保函除外）。",
         advice="删除独立担保表述；如确需独立付款承诺，改用符合规定的独立保函格式。"),
    dict(id="D09", title="债务转移未见债权人同意", category="担保金融", severity="low", mode="check_industry",
         only_types=["借款合同", "保证合同", "担保合同"], industry="guarantee",
         basis=["mfd-551"],
         analysis="涉及债务转移/债务承担安排，却未见债权人同意；依民法典第五百五十一条，债务转移未经债权人同意对债权人不发生效力。",
         advice="取得债权人书面同意并作为合同附件；担保人是否继续承担担保责任也应一并书面确认。"),
    # —— 消费/生活服务 ——
    dict(id="W01", title="预付费用退费规则缺失", category="消费生活", severity="high", mode="absence",
         only_types=["消费服务合同"], industry="consumer",
         markers=["退费", "未消费.{0,4}退"],
         basis=["xfzqybhf-53"],
         analysis="收取预付费用却未约定退费条件与余额处理；依消费者权益保护法第五十三条，经营者未按约定提供商品或服务的，应按消费者要求履行或退回预付款。",
         advice="写明未消费余额的退费比例、时限与手续费；已上课时/已用次数的折算方式一并写清。"),
    dict(id="W02", title="预付卡过期作废条款", category="消费生活", severity="high", mode="match",
         only_types=["消费服务合同"], industry="consumer",
         patterns=["(?:余额|余款|剩余.{0,2}费用).{0,8}(?:不退|作废|清零)",
                   "(?:过期|到期|有效期满).{0,6}(?:作废|不退|失效|清零)"],
         basis=["xfzqybhf-26", "xfzqybhf-53"],
         analysis="约定余额过期作废/到期不退，属于排除消费者权利、加重消费者责任的格式条款，依消费者权益保护法第二十六条可能被认定无效。",
         advice="删除作废/不退表述；如需设有效期，应约定到期前提醒与余额退还或延期机制。"),
    dict(id="W03", title="人身伤害免责条款", category="消费生活", severity="high", mode="match",
         only_types=["消费服务合同"], industry="consumer",
         patterns=["(?:受伤|摔伤|意外|伤害|事故|突发.{0,2}疾病).{0,12}(?:概不负责|不承担责任|自行承担|与本.{0,4}(?:店|馆|中心|机构|公司)无关)",
                   "(?:任何|一切).{0,2}(?:责任|后果).{0,4}概不(?:承担|负责)"],
         basis=["mfd-506", "xfzqybhf-26"],
         analysis="以格式条款免除自身造成消费者人身损害的责任；依民法典第五百零六条，造成对方人身损害的免责条款无效。",
         advice="删除人身伤害免责表述；改为按双方过错依法划分责任，并明确经营场所的安全保障义务与应急处理流程。"),
    dict(id="W04", title="「最终解释权」条款", category="消费生活", severity="medium", mode="match",
         only_types=["消费服务合同"], industry="consumer",
         patterns=["最终解释权.{0,6}(?:归|属)", "解释权.{0,4}归.{0,6}(?:店|公司|商家|甲方|本)"],
         basis=["xfzqybhf-26"],
         analysis="「最终解释权归商家」实质赋予经营者单方解释格式条款的特权；依消费者权益保护法第二十六条，此类条款对消费者不公平、不合理，司法实践中不被支持。",
         advice="删除该条款；条款有歧义时按通常理解解释，格式条款有两种以上解释的应作出不利于提供方的解释。"),
    dict(id="W05", title="培训「包过」承诺需核查", category="消费生活", severity="medium", mode="match",
         only_types=["消费服务合同"], industry="consumer",
         patterns=["包过|保过", "不过.{0,2}(?:全退|退款|退费)", "(?:未|不)通过.{0,6}退还.{0,4}(?:全部|全额|学费)"],
         basis=["xfzqybhf-20"],
         analysis="含「包过/不过退款」类效果承诺；应核查退款触发条件、证明材料与时限是否写清，避免承诺落空后无据可依。",
         advice="将承诺书面化：未通过情形、退款比例、申请时限与所需证明材料逐项写明，并与宣传口径保持一致。"),
    dict(id="W06", title="旅游转团安排未约定", category="消费生活", severity="low", mode="absence",
         only_types=["旅游合同"], industry="consumer",
         markers=["转团", "拼团", "委托.{0,4}旅行社", "组团社"],
         basis=["l4b0a348c-60", "l4b0a348c-63"],
         analysis="未见转团/拼团或委托其他旅行社履行的安排；依旅游法第六十条，旅行社委托其他旅行社履行包价旅游合同应经旅游者同意。",
         advice="写明是否允许转团拼团、承接旅行社的资质与责任承继；未经同意转团的违约责任一并约定。"),
    dict(id="W07", title="旅游行程与购物安排不明确", category="消费生活", severity="low", mode="absence",
         only_types=["旅游合同"], industry="consumer",
         markers=["行程安排", "行程单", "购物.{0,4}(?:安排|场所|次数)", "自费项目"],
         basis=["l4b0a348c-63"],
         analysis="未见行程单、购物或自费项目安排；依旅游法第六十三条，包价旅游合同应载明行程安排以及购物、另行付费项目等具体内容。",
         advice="以附件列明每日行程、住宿餐饮标准、购物场所与停留时长、自费项目及价格，并约定不得临时增加。"),
    dict(id="W08", title="服务效果承诺待核查", category="消费生活", severity="low", mode="match",
         only_types=["消费服务合同"], industry="consumer",
         patterns=["无效.{0,2}退款", "(?:保证|承诺).{0,6}(?:效果|疗效|瘦身|美白|治愈)"],
         basis=["xfzqybhf-20"],
         analysis="含「无效退款/效果保证」类承诺；依消费者权益保护法第二十条，经营者应真实、全面提供商品或服务信息，不得作虚假或引人误解的宣传。",
         advice="核查承诺的衡量标准与退款条件是否客观可证；无明确标准的，建议删除或改为可量化的服务次数与内容。"),
    dict(id="W09", title="摄影服务底片与肖像使用未约定", category="消费生活", severity="low", mode="check_industry",
         only_types=["消费服务合同"], industry="consumer",
         basis=["mfd-1019"],
         analysis="涉及摄影/拍摄服务，却未约定底片交付与肖像（影像）使用范围；依民法典第一千零一十九条，未经肖像权人同意不得制作、使用、公开其肖像。",
         advice="写明底片（原片）是否全送、精修张数与交付时限；商家将影像用于宣传展示的，须经客户书面同意并可随时撤回。"),
    # —— 经营/商业服务 ——
    dict(id="B01", title="特许经营信息披露未见", category="经营服务", severity="medium", mode="absence",
         only_types=["特许经营合同"], industry="business",
         markers=["信息披露", "披露.{0,4}(?:信息|材料)", "三十日.{0,4}(?:书面|前)"],
         basis=["l11f8b93a-21", "l11f8b93a-22"],
         analysis="未见特许人信息披露安排；依商业特许经营管理条例第二十一、二十二条，特许人应在签约前至少30日以书面形式披露规定信息，隐瞒或提供虚假信息的，被特许人可解除合同。",
         advice="要求特许人在签约前30日书面披露经营资源、费用、诉讼与经营情况等信息，并将披露文件作为合同附件。"),
    dict(id="B02", title="加盟冷静期未约定", category="经营服务", severity="medium", mode="absence",
         only_types=["特许经营合同"], industry="business",
         markers=["冷静期", "一定期限.{0,4}单方解除", "单方解除.{0,4}合同"],
         basis=["l11f8b93a-12"],
         analysis="未约定被特许人的单方解除期限；依商业特许经营管理条例第十二条，特许经营合同应当约定被特许人在合同订立后一定期限内可以单方解除合同。",
         advice="增加冷静期条款：明确约定被特许人可书面单方解除的期限（自合同签订之日起算），已发生费用的结算方式一并写明。"),
    dict(id="B03", title="加盟费/保证金退还条件缺失", category="经营服务", severity="medium", mode="absence",
         only_types=["特许经营合同"], industry="business",
         markers=["(?:保证金|加盟费|押金).{0,8}(?:退还|返还)", "退还.{0,4}(?:保证金|加盟费|押金)"],
         basis=["l11f8b93a-11"],
         analysis="收取加盟费/保证金却未约定退还条件；合同终止后各项费用是否退还是加盟纠纷的高发点。",
         advice="逐项写明各项费用的性质（可否退还）、退还条件与时限；保证金应约定扣除情形与余额返还期限。"),
    dict(id="B04", title="区域保护范围不清", category="经营服务", severity="low", mode="absence",
         only_types=["特许经营合同", "代理经销合同"], industry="business",
         markers=["区域保护", "商圈保护", "保护范围", "独家.{0,2}(?:区域|代理|经销)", "经营区域"],
         basis=["l11f8b93a-11"],
         analysis="未见独家/区域经营的保护范围界定；区域边界不清是加盟与经销纠纷的高发争议。",
         advice="以行政区划或公里半径明确保护范围，写明保护期内特许人/厂家不得自营或另授他人的义务及违约责任。"),
    dict(id="B05", title="居间报酬与跳单安排缺失", category="经营服务", severity="low", mode="absence",
         only_types=["居间合同"], industry="business",
         markers=["跳单", "绕开", "私下.{0,2}(?:成交|交易|签约)", "报酬.{0,4}(?:支付|给付)"],
         basis=["mfd-961", "mfd-965"],
         analysis="未见居间报酬支付节点或跳单处理；依民法典第九百六十五条，委托人绕开中介人直接订立合同的，仍应向中介人支付报酬。",
         advice="写明报酬金额/比例与支付时点；增加跳单条款：委托期限内绕开居间人成交的仍按约支付报酬。"),
    dict(id="B06", title="广告合规责任未划分", category="经营服务", severity="medium", mode="absence",
         only_types=["广告合同"], industry="business",
         markers=["广告内容.{0,4}(?:真实|合法|合规)", "素材.{0,4}(?:审核|查验)", "内容.{0,4}(?:合规|合法).{0,4}责任", "证明文件"],
         basis=["ggf-4", "ggf-34"],
         analysis="未划分广告内容合规责任；依广告法第四条，广告主对内容真实性负责；第三十四条，广告发布者、经营者应查验证明文件、核对广告内容。",
         advice="约定广告主保证素材真实合法并承担相应责任，发布方履行查验义务；违规下架或行政处罚的费用与损失承担写明。"),
    dict(id="B07", title="物业费标准与调价机制缺失", category="经营服务", severity="low", mode="absence",
         only_types=["物业服务合同"], industry="business",
         markers=["物业费.{0,4}(?:标准|单价|每平)", "收费.{0,2}标准", "调价", "费用.{0,2}调整"],
         basis=["mfd-944"],
         analysis="未见物业费标准或调整机制；依民法典第九百四十四条，业主应按约定支付物业费，收费标准与调整程序不明易生争议。",
         advice="写明物业费单价、计费面积与支付周期；调价须经法定程序（业主共同决定）并提前公示。"),
    dict(id="B08", title="仓储验收与保险安排缺失", category="经营服务", severity="low", mode="absence",
         only_types=["仓储合同"], industry="business",
         markers=["保险", "验收", "入库.{0,2}(?:检验|验收)", "仓单"],
         basis=["mfd-904", "mfd-909"],
         analysis="未见入库验收或货物保险安排；依民法典第九百零九条，保管人发现入库货物与约定不符的应及时通知存货人；货值较高时保险缺失风险集中。",
         advice="写明入库验收标准与异议时限、仓单签发；约定保险投保方、保额与理赔配合义务。"),
    dict(id="B09", title="终止后交接安排缺失", category="经营服务", severity="low", mode="absence",
         only_types=["特许经营合同", "代理经销合同", "广告合同"], industry="business",
         markers=["终止后.{0,6}(?:交接|移交|返还|删除)", "(?:合同|协议)终止.{0,4}(?:交接|移交)", "客户.{0,2}(?:资料|数据).{0,4}(?:移交|返还|删除|销毁)"],
         basis=["mfd-558"],
         analysis="未见合同终止后的交接安排；依民法典第五百五十八条，合同终止后当事人应依诚信原则履行通知、协助、保密、旧物回收等后合同义务。",
         advice="约定终止后的交接期限（明确天数与起算日），在该期限内完成客户资料、账号权限、库存物料的交接或删除，并签署交接确认书。"),
    # ============================================================
    # —— SaaS 云服务合同（SAAS 前缀）——
    # ============================================================
    dict(id="SAAS01", title="SLA 可用性承诺缺失或模糊", category="云服务", severity="high", mode="absence",
         only_types=["SaaS云服务合同"], industry="saas",
         markers=["服务等级.{0,4}协议", "SLA", "可用性.{0,4}(?:承诺|保证|约定)", "停机.{0,4}(?:赔偿|补偿|赔付)"],
         basis=["mfd-510"],
         analysis="SaaS 合同未见明确的服务等级承诺（可用性、响应时间、恢复目标）或停机赔偿机制。依《网络安全法》与《民法典》服务合同章，提供云服务的经营者应保障服务连续性并对中断承担违约责任。",
         advice="约定可用性指标（如 99.9%）、年度允许停机时长、P1/P2/P3 故障响应与恢复时限、超过 SLA 的月度账单折扣或退款公式。"),
    dict(id="SAAS02", title="数据导出与终止后删除权缺失", category="云服务", severity="high", mode="absence",
         only_types=["SaaS云服务合同"], industry="saas",
         markers=["(?:数据|业务数据|客户数据).{0,4}(?:导出|下载|迁移)", "终止后.{0,4}(?:删除|清除|销毁)", "数据.{0,2}可移植"],
         basis=["mfd-510", "mfd-651"],
         analysis="未见用户数据导出或合同终止后的数据删除/销毁义务。依《个人信息保护法》，个人信息处理者应提供可移植性机制；合同终止后继续留存数据构成侵权。",
         advice="约定终止后在约定期限内以机器可读格式（CSV/JSON/API）导出全部用户数据，并在导出完成后按约定期限从生产与备份系统彻底删除；删除完成后提供书面确认。"),
    dict(id="SAAS03", title="强制升级/变更通知期过短或无", category="云服务", severity="medium", mode="absence",
         only_types=["SaaS云服务合同"], industry="saas",
         markers=["(?:版本|功能|API|服务).{0,2}(?:变更|升级|下架|下线).{0,4}(?:通知|提前)", "提前.{0,4}(?:天|日).{0,2}(?:通知|告知)"],
         basis=["mfd-510"],
         analysis="未见功能变更、API 升级或服务下架的提前通知安排；云计算行业惯例为重大变更至少提前 30 日通知，便于客户调整系统或迁移。",
         advice="约定：重大功能/API 变更提前 30 日书面通知，破坏性变更（breaking change）提前 60 日并提供兼容期；服务下架提前 90 日并协助数据导出。"),
    dict(id="SAAS04", title="安全审计权与合规证明缺失", category="云服务", severity="medium", mode="absence",
         only_types=["SaaS云服务合同"], industry="saas",
         markers=["(?:安全|合规|SOC.{0,2}2|ISO.{0,2}27001|等保).{0,4}(?:证明|证书|报告)", "审计.{0,4}(?:权利|权|安排|机制)", "(?:渗透|入侵).{0,4}测试"],
         basis=["mfd-510", "mfd-651"],
         analysis="未见服务商的安全合规证明（如 SOC 2、ISO 27001、等保三级）或用户审计权条款；企业级 SaaS 采购中此项通常被列为门槛条件。",
         advice="要求服务商提供最新 SOC 2 Type II 或等保三级认证扫描件；约定用户在合理通知下可对服务商的数据处理场所进行审计或委托第三方审计。"),
    dict(id="SAAS05", title="定价/续费条款不透明", category="云服务", severity="medium", mode="absence",
         only_types=["SaaS云服务合同"], industry="saas",
         markers=["(?:计费|定价|续费|涨价).{0,6}(?:透明|公开|书面|协商|提前)", "(?:自动.{0,2}续费|默认.{0,2}续费|连续.{0,2}订阅).{0,6}(?:通知|提示)"],
         basis=["mfd-510"],
         analysis="未见定价公式、用量账单明细或自动续费的提前通知机制；SaaS 领域常见争议包括用量超标扣费无预警、续费金额突然上涨等。",
         advice="约定具体计费单元（按用户数/调用量/存储量）与单价；用量账单按月提供明细；价格调整提前 60 日通知且新价格仅适用于下一计费周期；自动续费需提前 7 日短信或邮件提醒。"),

    # ============================================================
    # —— 跨境合同（CR 前缀 Cross-border）——
    # ============================================================
    dict(id="CR01", title="适用法律与争议解决未明确或对我方不利", category="跨境合规", severity="high", mode="absence",
         only_types=["跨境合同"], industry="crossborder",
         markers=["适用.{0,2}法律|准据.{0,2}法|governing.{0,2}law", "(?:仲裁|诉讼|管辖).{0,4}(?:地|地点|机构)"],
         basis=["mfd-501", "mfd-503"],
         analysis="跨境合同未见明确的适用法律与争议解决安排。依冲突法规则，当事人未约定时将由法院按最密切联系原则推定，可能适用对我方不利的外域法；管辖不明也将增加跨境维权成本。",
         advice="优先约定适用中华人民共和国法律；仲裁选择 CIETAC 或 HKIMAC（中英双语、程序成熟、仲裁裁决可依《纽约公约》在多数国家执行）；如必须外域法，需评估对方司法实践中的合同执行先例。"),
    dict(id="CR02", title="数据跨境传输未约定合规路径", category="跨境合规", severity="high", mode="absence",
         only_types=["跨境合同"], industry="crossborder",
         markers=["(?:数据|个人信息|客户信息).{0,4}(?:出境|跨境.{0,2}传输|跨境.{0,2}提供|transfer.*abroad)", "(?:标准.{0,2}合同|SCC|标准合同模板|个人信息出境.{0,2}标准合同)",
                  "(?:数据|个人信息|客户信息).{0,4}(?:传输|提供|存储|处理).{0,8}(?:境外|海外|美国|欧盟|境外.{0,4}子公司|第三方)"],
         basis=["mfd-651", "mfd-653"],
         analysis="跨境场景下涉及个人信息或重要数据的，依《个人信息保护法》与《数据安全法》需进行安全评估或订立标准化合同；合同中未约定传输合规路径可能导致数据出境行为违法。",
         advice="明确数据出境的触发条件、接收方所在国家/地区、所涉数据类型与量级；约定采用国家网信办发布的《个人信息出境标准合同》并完成备案；涉及重要数据的应说明已履行国家安全审查程序。"),
    dict(id="CR03", title="制裁合规（OFAC/出口管制）条款缺失", category="跨境合规", severity="medium", mode="absence",
         only_types=["跨境合同"], industry="crossborder",
         markers=["(?:制裁|OFAC|黑名单|出口管制|两用物项|dual.{0,2}use).{0,4}(?:合规|筛查|核查|不违反)", "被制裁.{0,2}名单|出口.{0,2}许可证",
                  "(?:制裁|OFAC|黑名单|出口管制|合规).{0,4}(?:核查|检查|审查|机制|不涉及|不违反)",
                  "(?:不涉及|未违反).{0,8}(?:制裁|OFAC|出口管制|黑名单)"],
         basis=["mfd-501"],
         analysis="跨境贸易中涉及美国/欧盟制裁名单（OFAC SDN List、欧盟制裁清单）或中国两用物项出口管制的，应设合规承诺与免责条款；违反制裁法可能被巨额罚款或刑事追责。",
         advice="双方承诺不涉及被制裁的个人/实体；出口涉及管制物项或技术的，卖方承诺已取得出口许可证；因一方违反制裁法导致合同无法履行的，另一方有权无责终止。"),
    dict(id="CR04", title="汇率风险与外汇管制未约定", category="跨境合规", severity="medium", mode="absence",
         only_types=["跨境合同"], industry="crossborder",
         markers=["(?:汇率|外汇|兑换|结算.{0,4}币种|local.{0,2}currency)", "外汇.{0,4}(?:管制|限制|审批|登记)",
                  "(?:外汇.{0,2}风险|汇率.{0,2}风险|汇兑.{0,2}风险)"],
         basis=["mfd-503"],
         analysis="跨境合同以外币计价时，人民币大幅波动可能导致汇兑损失；同时对方所在国外汇管制可能影响付款汇出。依《外汇管理条例》，经常项目外汇收支需真实合规，资本项目需审批。",
         advice="约定计价币种（人民币/美元/欧元）与汇率锁定机制（如付款日中国银行外汇牌价中间价）；对方所在国外汇管制导致延迟付款的，约定宽限期；大额资本项目需约定双方各自履行外汇审批义务。"),
    dict(id="CR05", title="跨境税务（常设机构/转让定价）条款缺失", category="跨境合规", severity="low", mode="absence",
         only_types=["跨境合同"], industry="crossborder",
         markers=["(?:常设机构|PE|permanent.{0,2}establishment|转让定价|transfer.{0,2}pricing|预提.{0,2}税|withholding.{0,2}tax)"],
         basis=["mfd-503"],
         analysis="跨境服务/许可合同如构成对方国家的常设机构，将在当地产生企业所得税纳税义务；特许权使用费的预提税通常由付款方代扣代缴但可能影响净额。依中-外双边税收协定，收入性质不同预提税率亦不同（通常 6–10%）。",
         advice="约定服务履行地不构成对方常设机构的具体安排；明确各方税负承担边界（预提税由付款方承担/代收代缴，收款方保证净额）；涉及技术许可的，建议在合同中明确许可性质（特许权使用费 vs 服务费）以适用协定优惠税率。"),

    # ============================================================
    # —— 劳动合同 AI 新场景（LAI 前缀）——
    # ============================================================
    dict(id="LAI01", title="AI 绩效考核/评价未约定合法性基础", category="劳动合规", severity="high", mode="absence",
         only_types=["劳动合同"], industry="labor",
         markers=["(?:AI|人工智能|算法|数据.{0,2}分析).{0,6}(?:绩效|考核|评价|评估|打分|排序)",
                  "(?:AI|人工智能|算法).{0,4}绩效.{0,2}(?:排名|排序|考核|评价|智能)",
                  "(?:自动|智能|算法).{0,4}(?:评分|打分|排名|排序).{0,4}(?:绩效|考核)",
                  "人工智能.{0,4}(?:决定|裁决|输出).{0,4}(?:排名|末位|淘汰)"],
         basis=["mfd-651"],
         analysis="使用 AI/算法进行绩效评价、排名或淘汰决策的，依《个人信息保护法》第二十四条与《生成式人工智能服务管理暂行办法》，应告知劳动者算法逻辑、提供申诉渠道，并进行公平性评估；合同中未约定的，相关决策可被主张无效。",
         advice="在劳动合同或配套规章制度中明确：① AI 评价的参与范围与不参与场景（如仅用于排序而非直接决定奖惩）；② 告知劳动者评价所依据的数据源；③ 提供独立复核渠道与人工干预机制；④ 定期发布算法公平性评估报告。"),
    dict(id="LAI02", title="算法监控/电子围栏未明确边界", category="劳动合规", severity="medium", mode="absence",
         only_types=["劳动合同"], industry="labor",
         markers=["(?:算法|AI|人工智能|大数据|电子.{0,2}围栏|行为.{0,2}分析|摄像头.{0,2}识别|生物.{0,2}识别).{0,6}(?:监控|考勤|跟踪|轨迹)", "(?:位置|轨迹|出行|网络.{0,2}活动).{0,4}(?:监控|记录|收集)"],
         basis=["mfd-651"],
         analysis="通过算法、生物识别或位置追踪对劳动者进行监控的，依《个人信息保护法》与《劳动合同法》，应取得劳动者单独同意、限定监控范围与目的，并避免对人格尊严的过度侵入（如卫生间、更衣室等私密区域不得监控）。",
         advice="明确监控方式（仅工作设备/特定时段/特定区域）、收集的信息范围、存储期限与访问权限；劳动者不同意的不得作为解除合同理由；生物识别信息仅在考勤确有必要时使用并提供替代方案。"),
    dict(id="LAI03", title="禁止 AI 训练劳动者工作成果", category="劳动合规", severity="medium", mode="match",
         only_types=["劳动合同"], industry="labor",
         patterns=["(?:乙方|劳动者|员工).{0,4}(?:同意|授权|许可|允许).{0,8}(?:公司|用人单位|甲方).{0,4}(?:使用|训练|喂入|用于|迭代).{0,4}(?:AI|大模型|人工智能|模型|算法)",
                   "(?:工作.{0,2}成果|产出|文本|作品|代码|文档).{0,6}(?:用于.{0,4}(?:训练|喂入|迭代|优化).{0,4}(?:AI|模型|人工智能|算法))",
                   "(?:AI|大模型|人工智能|算法).{0,4}(?:训练|喂入|迭代).{0,4}(?:乙方|劳动者|员工|工作.{0,2}成果)"],
         basis=["mfd-558", "mfd-651"],
         analysis="合同约定劳动者同意用人单位将其工作产出用于 AI 模型训练的，可能涉及：① 未取得单独同意的个人信息处理违法；② 违反著作权法中职务作品归属规则；③ 训练数据的版权/保密义务冲突。",
         advice="删除该条款或改为：用人单位使用工作产出训练 AI 的，应另行订立书面协议、明确数据用途、约定训练后的模型不得包含可识别的个人特征，并向劳动者披露模型迭代计划。"),

    # ============================================================
    # —— AI 模型服务合同（AMS 前缀 AI Model Service）——
    # ============================================================
    dict(id="AMS01", title="训练数据合规来源未声明", category="AI合规", severity="high", mode="absence",
         only_types=["AI模型服务合同"], industry="ai",
         markers=["(?:训练.{0,2}数据|数据集|dataset).{0,4}(?:来源|合规|授权|许可|版权|知识产权|合法性)",
                  "(?:个人信息|用户.{0,2}数据).{0,4}(?:用于.{0,4}训练|训练.{0,4}使用)"],
         basis=["mfd-651"],
         analysis="AI 模型服务商未声明训练数据的合规来源。依《生成式人工智能服务管理暂行办法》，提供生成式 AI 服务的应保证训练数据合法、不侵犯知识产权与个人信息权益；使用未授权数据训练的模型可能被监管机关责令停产整改。",
         advice="要求服务商提供训练数据合规声明：① 数据集构成（公开/授权/自采）；② 个人信息处理的合法性基础；③ 知识产权授权范围；④ 针对版权争议的赔偿机制与证据留存期。"),
    dict(id="AMS01B", title="训练数据合法性被服务商明示不担保", category="AI合规", severity="high", mode="match",
         only_types=["AI模型服务合同"], industry="ai",
         patterns=[r"(?:不保证|不声明|不负责|不对.{0,4}负责|不承担.{0,4}责任|不承诺).{0,6}(?:训练.{0,2}数据|数据集|版权|合法性|合规)",
                   r"(?:训练.{0,2}数据|数据集).{0,6}(?:不保证|不声明|不负责|不承担.{0,4}责任).{0,6}(?:合法性|合规|版权)"],
         basis=["mfd-651"],
         analysis="合同条款明确约定服务商对训练数据的合法性、版权合规性不承担责任，等于把合规风险全部转嫁给甲方。依《生成式人工智能服务管理暂行办法》，提供者应保证训练数据合法，此类免责条款可能被认定无效。",
         advice="删除该免责条款；要求服务商提供真实合规声明并对其真实性承担法律责任；补充版权争议的赔偿上限（不低于年度服务费的 2–3 倍）与证据留存义务。"),
    dict(id="AMS02", title="模型偏见/歧视责任未约定", category="AI合规", severity="high", mode="absence",
         only_types=["AI模型服务合同"], industry="ai",
         markers=["(?:偏见|bias|歧视|公平|反歧视|算法.{0,2}歧视).{0,4}(?:测试|评估|审计|治理|责任)", "(?:有害|违规|违法|不当).{0,4}输出.{0,4}(?:责任|赔偿|补偿)"],
         basis=["mfd-651"],
         analysis="AI 模型产生种族/性别/地域歧视性输出或有害内容时，依《生成式人工智能服务管理暂行办法》与《个人信息保护法》，提供者应承担相应行政与民事责任；合同中未约定责任划分可能导致全部风险由甲方承担。",
         advice="约定：① 服务商须完成算法公平性评测并提供报告；② 服务商对模型输出的违法/有害内容承担首要责任并建立实时过滤机制；③ 争议场景下的数据留存与根因分析义务；④ 责任上限不低于年度服务费的约定倍数，并写明计算口径。"),
    dict(id="AMS03", title="可解释性/审计权缺失", category="AI合规", severity="medium", mode="absence",
         only_types=["AI模型服务合同"], industry="ai",
         markers=["(?:可解释|解释性|explainability|审计.{0,4}(?:权|能力|机制)|可追溯|traceability)", "(?:模型.{0,2}版本|版本.{0,2}控制|model.{0,2}version).{0,4}(?:披露|告知|通知)"],
         basis=["mfd-651"],
         analysis="黑盒模型难以判断决策正确性，发生争议时无法举证。依《生成式人工智能服务管理暂行办法》，提供者应增强可解释性；企业客户在合规要求场景（如信贷审批、医疗辅助）下必须获取模型决策的审计路径。",
         advice="约定：① 服务商提供模型决策的可解释性工具或审计日志 API；② 模型版本变更的提前通知（≥30 日）与变更摘要；③ 重要决策场景下的人工复核接口与权限；④ 甲方保留对服务商进行独立审计的权利。"),
    dict(id="AMS04", title="API 安全与滥用防护条款不足", category="AI合规", severity="medium", mode="absence",
         only_types=["AI模型服务合同"], industry="ai",
         markers=["(?:API|接口).{0,4}(?:鉴权|访问.{0,2}控制|限流|熔断|密钥|OAuth|JWT)", "(?:滥用|abuse|攻击|DOS|爬虫).{0,4}(?:防护|监测|处置|通知)"],
         basis=["mfd-651"],
         analysis="AI 模型 API 通常有极高的计算成本与安全敏感，未约定访问控制、限流阈值、密钥轮换或滥用通知机制可能遭受密钥泄露盗刷或恶意攻击。",
         advice="约定：① API 密钥签发与定期轮换机制；② 每个 API Key 的调用频次/Token 用量上限；③ 异常调用的实时告警与自动熔断；④ 服务商对 API 安全漏洞承担修复义务；⑤ 密钥被盗用后的损失赔付公式。"),

]


COMMERCIAL_ONLY = {"R02", "R03", "R04", "R05", "R06", "R17", "R19"}


def _type_gate(rule: dict, ctype: str, industry_rules: dict | None = None) -> bool:
    # 行业开关：用户在设置页关闭了某行业规则，跳过所有该 industry 标记的规则
    if industry_rules and rule.get("industry"):
        if industry_rules.get(rule["industry"]) is False:
            return False
    rid = rule["id"]
    # 前缀门禁：旧规则按合同类型过滤
    if rid.startswith("L") and ctype != "劳动合同":
        return False
    if ctype == "劳动合同" and rid in {*COMMERCIAL_ONLY, "R07", "R08"}:
        return False
    if ctype == "保密协议" and rid in {"R02", "R03", "R04", "R05", "R06"}:
        return False
    if rule["mode"].startswith("check_labor"):
        return ctype == "劳动合同"
    # only_types 门禁
    if rule.get("only_types") and ctype not in rule["only_types"]:
        return False
    return True


def _check_special(rule: dict, text: str, values: dict, ctype: str, params: dict | None = None) -> tuple[bool, str]:
    rid = rule["id"]
    p = params or {}
    if rid == "R01":
        a, b = values.get("party_a", ""), values.get("party_b", "")
        return (not a or not b or len(a) < 2 or len(b) < 2), ""
    if rid == "R02":
        amt = values.get("amount_total", "")
        if not amt:
            return True, ""
        if re.search(r"框架", text) and re.search(r"暂定|以实际结算为准", amt + text[:6000]):
            return False, ""
        if re.search(r"待定|另议|暂定|以实际结算为准", amt + text[:6000]):
            return True, _sentence(text, r"金额.{0,12}(?:待定|另议|暂定)|以实际结算为准")
        return False, ""
    if rid == "R02B":
        return bool(re.search(r"框架", text) and re.search(r"暂定|以实际结算为准|按实际结算", text)), _sentence(text, r"暂定|以实际结算|按实际结算")
    if rid == "R04":  # 违约金过高 → 参数化阈值
        threshold = float(p.get("penalty_high_pct", 30))
        compact = _compact(text)
        num_pats = (
            r"违约金.{0,30}?(?P<v>\d+(?:\.\d+)?)\s*[%％]",
            r"(?P<v>\d+(?:\.\d+)?)\s*[%％]\s*的?违约金",
            r"按(?:合同总|合同价|总价款).{0,10}(?P<v>\d+(?:\.\d+)?)\s*[%％].{0,10}(?:支付|计算)",
        )
        if any(v >= threshold for pat in num_pats for v in _pct_hits(compact, pat)):
            return True, _sentence(text, r"违约金")
        cn_pats = (
            rf"违约金.{{0,30}}?百分之({_CN_TENS_GROUP})",
            rf"百分之({_CN_TENS_GROUP}).{{0,10}}?的?违约金",
        )
        for pat in cn_pats:
            cm = re.search(pat, compact)
            if cm and _CN_TENS[cm.group(1)] >= threshold:
                return True, _sentence(text, r"违约金")
        return False, ""
    if rid == "R05":  # 违约金过低 → 参数化阈值
        low = float(p.get("penalty_low_pct_perday", 0.03))
        compact = _compact(text)
        if re.search(r"违约金.{0,20}?日?万分之(?:零点[一二三四五六七八九]?|一|二)", compact):
            return True, _sentence(text, r"违约金")
        num_pats = (
            r"违约金.{0,20}?日利率\s*(?P<v>\d+(?:\.\d+)?)\s*[%％]",
            r"日利率\s*(?P<v>\d+(?:\.\d+)?)\s*[%％].{0,20}?违约金",
        )
        if any(v < low for pat in num_pats for v in _pct_hits(compact, pat)):
            return True, _sentence(text, r"违约金")
        return False, ""
    if rid == "R06":  # 定金比例过高 → 参数化阈值
        threshold = float(p.get("deposit_max_pct", 20))
        compact = _compact(text)
        num_pats = (
            r"定金.{0,15}?(?P<v>\d+(?:\.\d+)?)\s*[%％]",
            r"(?P<v>\d+(?:\.\d+)?)\s*[%％]\s*的?定金",
        )
        if any(v > threshold for pat in num_pats for v in _pct_hits(compact, pat)):
            return True, _sentence(text, r"定金")
        cm = re.search(rf"定金.{{0,15}}?百分之({_CN_TENS_GROUP})", compact)
        if cm and _CN_TENS[cm.group(1)] > threshold:
            return True, _sentence(text, r"定金")
        return False, ""
    if rid == "R14":
        if values.get("term_start") and values.get("term_end"):
            return False, ""
        if re.search(r"(?:合同|服务|合作)(?:期限|有效期)", text):
            return False, ""
        return True, ""
    if rid == "R16":
        if ctype not in {"服务合同", "保密协议", "其他合同"}:
            return False, ""
        if not re.search(r"(?:开发|设计|交付|成果|定制)", text):
            return False, ""
        if re.search(r"知识产权|著作权|成果归属|所有权归属", text):
            return False, ""
        return True, ""
    if rid == "R21":
        return (not values.get("signing_date")), ""
    if rid == "L01":  # 试用期上限 → 参数化
        max_months = p.get("probation_max_months", 6)
        m = re.search(r"试用期.{0,12}(\d+|一|二|三|四|五|六|七|八|九|十)(?:个月)", text)
        if not m:
            return False, ""
        raw = m.group(1)
        cn = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
        months = int(raw) if raw.isdigit() else cn.get(raw, 0)
        if months > max_months:
            return True, _sentence(text, r"试用期")
        # 结合合同期限判断
        if months > 2 and re.search(r"合同期限.{0,15}(?:一年|1年|两年|2年)", text):
            return True, _sentence(text, r"试用期")
        return False, ""
    if rid == "L02":  # 试用期工资下限 → 参数化
        min_pct = float(p.get("probation_salary_pct", 80))
        compact = _compact(text)
        pat = r"试用期.{0,30}?(?:工资|薪资|报酬).{0,15}?(?P<v>\d+(?:\.\d+)?)\s*[%％]"
        if any(v < min_pct for v in _pct_hits(compact, pat)):
            return True, _sentence(text, r"试用期")
        cm = re.search(
            rf"试用期.{{0,30}}?(?:工资|薪资|报酬).{{0,15}}?百分之({_CN_TENS_GROUP})", compact)
        if cm and _CN_TENS[cm.group(1)] < min_pct:
            return True, _sentence(text, r"试用期")
        return False, ""
    if rid == "L03":  # 竞业限制上限 → 参数化
        max_months = p.get("compete_max_months", 24)
        # 超过 max_months 月 → 正则匹配 三年/3年 及以上
        upper_year = max_months // 12 + 1  # 如 24个月=2年, 上限3年开始触发
        patterns = [
            rf"竞业限制.{{0,30}}(?:{upper_year}年以上|三年|3年|四年|4年|五年|5年|超过二年|超过2年)",
        ]
        for pat in patterns:
            if re.search(pat, _compact(text)):
                return True, _sentence(text, pat)
        return False, ""
    if rid == "L04":
        if not re.search(r"竞业限制", text):
            return False, ""
        if re.search(r"经济补偿|补偿金|按月.{0,6}补偿", text):
            return False, ""
        return True, _sentence(text, r"竞业限制")
    if rid == "L06":
        if re.search(r"(?:社保|社会保险).{0,20}(?:补贴|现金|自行缴纳|放弃)", text) or re.search(r"自愿放弃.{0,8}社保", text):
            return True, _sentence(text, r"(?:社保|社会保险).{0,20}(?:补贴|现金|自行|放弃)")
        if not re.search(r"(?:社会保险|社保|五险)", text):
            return True, ""
        return False, ""
    if rid == "R08":
        for pattern in rule["patterns"]:
            if re.search(pattern, _compact(text)):
                ev = _sentence(text, pattern)
                if "不可抗力" in ev or "互不承担" in ev or "违约" in ev:
                    return False, ""
                return True, ev
        return False, ""
    if rid == "Z01":  # 租赁期限上限 → 参数化
        max_years = p.get("lease_max_years", 20)
        patterns = [
            rf"租赁期限.{{0,15}}(?:二十[一二三四五六七八九]|30|三十|40|四十|50|五十|60|六十)年",
            rf"租赁期.{{0,10}}(?:超过|长于){max_years}年",
        ]
        for pat in patterns:
            if re.search(pat, _compact(text)):
                return True, _sentence(text, pat)
        return False, ""
    if rid == "J01":  # 借款利率上限 → 参数化
        mult = p.get("lpr_multiplier", 4)
        patterns = [
            rf"(?:年|月)利率.{{0,12}}(?:2[4-9]%|3[0-9]%|[4-9]\d%|百分之二十[四五六七八九]|百分之[三四五六七八九]十)",
            rf"(?:年利率|月利率).{{0,12}}(?:LPR|市场报价利率).{{0,12}}(?:{mult}|{mult+1}|四|五)倍以上",
        ]
        for pat in patterns:
            if re.search(pat, _compact(text)):
                return True, _sentence(text, pat)
        return False, ""
    if rid == "P01":
        if not re.search(r"个人信息|人脸识别|员工信息|个人数据", text):
            return False, ""
        if re.search(r"告知|同意|授权", text):
            return False, ""
        return True, _sentence(text, r"个人信息|身份证")
    if rid == "P03":
        if not re.search(r"数据处理|数据共享|数据安全|个人信息处理|数据存储", text):
            return False, ""
        if re.search(r"加密|访问控制|权限管理|安全措施|安全管理制度|脱敏", text):
            return False, ""
        return True, ""
    # check_industry 模式：需要结合合同类型和文本特征
    if rid == "T01":
        if ctype not in {"技术开发合同", "技术转让合同", "技术服务合同", "软件开发合同"}:
            return False, ""
        if re.search(r"(?:技术成果|成果|知识产权|著作权|专利).{0,8}(?:归|归属|由|属于|享有)", text):
            return False, ""
        return True, ""
    if rid == "T03":
        if ctype not in {"技术开发合同", "软件开发合同", "技术服务合同"}:
            return False, ""
        if re.search(r"验收标准|验收条件|交付验收|性能指标", text):
            return False, ""
        return True, ""
    if rid == "Y03":
        # 运输合同涉及危险品的特殊检查
        if ctype not in {"运输合同", "物流合同"}:
            return False, ""
        if re.search(r"(?:危险品|危险货物|特殊货物|冷链|精密|易碎)", text):
            if re.search(r"(?:包装|标识|装卸|应急|温湿度).{0,8}(?:要求|标准|规范)", text):
                return False, ""
            return True, ""
        return False, ""
    if rid == "F01":
        if ctype not in {"土地出让合同", "土地转让合同", "房地产合作开发合同"}:
            return False, ""
        if re.search(r"(?:出让金|地价款|转让费).{0,8}(?:构成|包含范围|明细)", text):
            return False, ""
        return True, ""
    # —— 股权/合伙自定义判断 ——
    if rid == "E02":
        if re.search(r"认缴|未实缴|未届.{0,2}出资", text) and not re.search(r"出资.{0,4}责任|由.{0,8}承担.{0,6}出资|实缴义务", text):
            return True, _sentence(text, r"认缴|未实缴")
        return False, ""
    if rid == "E05":
        if ctype == "合伙协议" and not re.search(r"有限合伙", text):
            return True, ""
        return False, ""
    # —— 担保/金融自定义判断 ——
    if rid == "D01":
        if re.search(r"保证人|保证合同|提供.{0,2}保证|承担.{0,2}保证", text) and not re.search(r"一般保证|连带责任保证|连带保证", text):
            return True, _sentence(text, r"保证")
        return False, ""
    if rid == "D04":
        if re.search(r"房产|房屋|土地|不动产|建筑物", text) and re.search(r"抵押", text) and not re.search(r"抵押登记|办理.{0,6}登记|登记机关", text):
            return True, _sentence(text, r"抵押")
        return False, ""
    if rid == "D06":
        if re.search(r"公司.{0,8}(?:提供担保|提供.{0,2}保证|为保证人|提供.{0,2}抵押|提供.{0,2}质押)", text) and not re.search(r"股东会决议|董事会决议|股东决定|内部决议|决议文件", text):
            return True, _sentence(text, r"提供担保|为保证人")
        return False, ""
    if rid == "D09":
        if re.search(r"债务.{0,4}(?:转移|转让|承担)|承担.{0,4}债务", text) and not re.search(r"债权人.{0,4}同意", text):
            return True, _sentence(text, r"债务.{0,4}(?:转移|转让|承担)")
        return False, ""
    if rid == "J05":
        if re.search(r"服务费|咨询费|管理费|手续费", text) and re.search(r"利息|利率", text):
            return True, _sentence(text, r"服务费|咨询费|管理费|手续费")
        return False, ""
    if rid == "L11":
        if re.search(r"违约金", text) and not re.search(r"服务期|专项培训|竞业", text):
            return True, _sentence(text, r"违约金")
        return False, ""
    if rid == "L15":
        if re.search(r"工作地点|工作地|工作区域", text) and re.search(
                r"全国|全省|服从.{0,6}(?:安排|调配|调动)|(?:公司|甲方).{0,4}(?:各|所有|下属).{0,2}(?:分支|机构|门店|办公)", text):
            return True, _sentence(text, r"工作地点|工作地")
        return False, ""
    if rid == "L18":
        if re.search(r"服务期", text) and re.search(r"违约金", text) and not re.search(r"培训费", text):
            return True, _sentence(text, r"服务期")
        return False, ""
    if rid == "W09":
        if re.search(r"摄影|拍摄|婚纱|婚庆|跟拍|写真", text) and not re.search(r"底片|原片|肖像|影像.{0,4}使用", text):
            return True, _sentence(text, r"摄影|拍摄|写真")
        return False, ""
    return False, ""


def evaluate(text: str, values: dict, ctype: str, params: dict | None = None,
             industry_rules: dict | None = None) -> tuple[list[dict], int, str]:
    findings: list[Finding] = []
    compact = _compact(text)
    LAST_WARNINGS.clear()
    for rule in (*RULES, *_EXTRA_RULES):
        is_custom = rule["id"].startswith("C-")
        # 类型/行业门禁
        if not _type_gate(rule, ctype, industry_rules):
            continue
        if rule.get("skip_types") and ctype in rule["skip_types"]:
            continue
        # only_types 也在 _type_gate 里处理了，这里再兜底一次
        if rule.get("only_types") and ctype not in rule["only_types"]:
            continue
        mode = rule["mode"]
        try:
            triggered, ev = False, ""
            if mode in {"check", "check_labor", "check_privacy", "check_data", "check_industry"}:
                if is_custom:
                    continue  # 自定义规则不走内置硬编码分支（校验期已限制 mode）
                triggered, ev = _check_special(rule, text, values, ctype, params)
            elif mode == "match":
                for ptn in rule["patterns"]:
                    if re.search(ptn, compact):
                        triggered = True
                        ev = _sentence(text, ptn)
                        break
            elif mode == "absence":
                # 混合语义：marker 命中率 ≥ 40% → 保守不触发（合同片段相关内容已覆盖主要项）；
                # 命中率 < 40% 或 = 0% → 触发（相关内容覆盖不足或完全没写）。
                # 平衡了：all() 过严导致漏检，和 any() 对完备合同片段的误触发。
                total_markers = len(rule["markers"])
                hit_count = sum(1 for p in rule["markers"] if re.search(p, compact))
                hit_rate = hit_count / total_markers if total_markers > 0 else 0
                if hit_rate < 0.4:
                    triggered = True
                    if hit_count > 0:
                        missing_markers = [p for p in rule["markers"] if not re.search(p, compact)]
                        ev = f"相关条款中未见：{missing_markers[0]}"
                    else:
                        ev = ""  # 整段都没写相关内容
            elif mode == "threshold":
                triggered, ev = _threshold_rule(rule, text, compact, params)
        except Exception as e:
            # 单条自定义规则异常（如极端文本下的坏正则）不拖垮整体评估
            if is_custom:
                LAST_WARNINGS.append(f"自定义规则 {rule['id']} 评估异常已跳过：{e.__class__.__name__}")
                continue
            raise
        if not triggered:
            continue
        findings.append(Finding(
            id=rule["id"], title=rule["title"], category=rule["category"],
            severity=rule["severity"], evidence=ev, analysis=rule["analysis"],
            advice=rule["advice"], basis_ids=rule["basis"],
            confidence=0.9 if mode == "match" else 0.82,
            source=({"type": "custom", "pack_name": rule.get("pack_name", "")}
                    if is_custom else {"type": "builtin"}),
            related=list(rule.get("related") or []),
        ))

    order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: (order[f.severity], f.id))
    raw = sum(SEVERITY[f.severity][1] for f in findings)
    score = min(98, 5 + raw)
    level = "high" if score >= 55 else "medium" if score >= 28 else "low"
    out = [f.to_dict() for f in findings]
    return _mark_divergence(out), score, level


def _ev_norm(ev: str) -> str:
    return re.sub(r"[\s…—()（）\"'“”]", "", ev or "")


def _ev_overlap(a: str, b: str) -> bool:
    """两条证据句是否指向同一条款：一方包含另一方，或字符二元组 Jaccard ≥ 0.6。"""
    a, b = _ev_norm(a), _ev_norm(b)
    if len(a) < 10 or len(b) < 10:
        return False
    if a in b or b in a:
        return True
    sa = {a[i:i + 2] for i in range(len(a) - 1)}
    sb = {b[i:i + 2] for i in range(len(b) - 1)}
    return len(sa & sb) / max(1, len(sa | sb)) >= 0.6


def _mark_divergence(findings: list[dict]) -> list[dict]:
    """把内置与自定义 finding 按 related 链接与证据句重合归组。

    组内同时存在内置与自定义且严重级别不同 → 标记 divergence，
    前端在该条款处展示「口径分歧」提示卡，两套结论并列交由专业人士复核。
    """
    n = len(findings)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    by_id = {f["id"]: i for i, f in enumerate(findings)}
    for i, f in enumerate(findings):
        for rid in f.get("related") or []:
            if rid in by_id:
                union(i, by_id[rid])
    for i in range(n):
        for j in range(i + 1, n):
            a, b = findings[i], findings[j]
            if a["source"]["type"] == b["source"]["type"]:
                continue
            if _ev_overlap(a.get("evidence", ""), b.get("evidence", "")):
                union(i, j)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    gid = 0
    for members in groups.values():
        kinds = {findings[i]["source"]["type"] for i in members}
        if kinds != {"builtin", "custom"}:
            continue
        if len({findings[i]["severity"] for i in members}) < 2:
            continue  # 口径一致：并列展示即可，无需分歧提示
        gid += 1
        for i in members:
            f = findings[i]
            peers = [{"id": findings[j]["id"], "title": findings[j]["title"],
                      "severity": findings[j]["severity"],
                      "source": findings[j]["source"]} for j in members if j != i]
            f["divergence"] = True
            f["divergence_group"] = f"dg{gid}"
            f["divergence_peers"] = peers
    return findings


def counts(findings: list[dict]) -> dict:
    return {
        "high": sum(1 for f in findings if f["severity"] == "high"),
        "medium": sum(1 for f in findings if f["severity"] == "medium"),
        "low": sum(1 for f in findings if f["severity"] == "low"),
    }


# ---------- LLM 辅助判别 ----------

# 争议规则名单（假阳性概率高，或语义判定比正则更准的规则）
_SUSPICIOUS_RULES = {
    "R07", "R12", "R18", "R19", "R20",   # 通用规则的语境判断
    "CR01", "CR03", "CR05",             # 跨境 absence 规则
    "LAI01", "LAI02", "LAI03",          # 劳动合同 AI 新场景
    "AMS01", "AMS01B",                  # AI 模型服务
}
# 新场景合同类型（规则迭代期，全部 finding 都过 LLM 复核）
_NEW_TYPES = {"SaaS云服务合同", "跨境合同", "AI模型服务合同"}


def _should_llm_verify(finding: dict, ctype: str) -> bool:
    """触发门槛：决定这条 finding 是否需要 LLM 复核。"""
    if finding.get("source", {}).get("type") == "custom":
        # 自定义规则全部过 LLM（用户自己写的，正则更不稳）
        return True
    if ctype in _NEW_TYPES:
        return True
    if finding.get("id", "") in _SUSPICIOUS_RULES:
        return True
    # absence + 高风险 → 必触发
    if finding.get("id", "").startswith(("SAA", "CR", "LAI", "AMS")):
        return True
    return False


def _extract_context(text: str, evidence: str, margin: int = 150) -> str:
    """从合同原文截取 evidence 附近的上下文（±margin 字）。"""
    if not evidence or evidence == "—（合同中未见相关条款）":
        # absence 模式没 evidence，取合同开头 + 末尾各 300 字代表"全文都没写"
        head = text[:300]
        tail = text[-300:] if len(text) > 600 else ""
        return head + ("\n【……中间内容……】\n" if tail else "") + tail
    idx = text.find(evidence)
    if idx < 0:
        # evidence 里的空格/标点可能跟原文不同，用 compact 找
        idx = _compact(text).find(_compact(evidence[:min(40, len(evidence))]))
        if idx < 0:
            return evidence[:200]
    start = max(0, idx - margin)
    end = min(len(text), idx + len(evidence) + margin)
    return ("…" if start > 0 else "") + text[start:end] + ("…" if end < len(text) else "")


def llm_verify_findings(findings: list[dict], text: str, ctype: str,
                         verify_fn=None) -> tuple[list[dict], dict]:
    """对符合触发门槛的 finding 调本地模型复核。

    不删除任何 finding，只加 llm_verified / llm_confidence / llm_reason 字段。
    返回 (updated_findings, stats)；stats 里记录触发数 / 成功数 / 失败数供 app 层打点。
    verify_fn 注入方便测试（mock），默认调 cls.llm.verify_clause。
    """
    if verify_fn is None:
        from . import llm as _llm
        verify_fn = _llm.verify_clause

    triggered = 0
    ok = 0
    failed = 0

    for f in findings:
        if not _should_llm_verify(f, ctype):
            continue
        triggered += 1
        context = _extract_context(text, f.get("evidence", ""))
        try:
            result = verify_fn(
                context=context,
                rule_id=f["id"],
                rule_title=f["title"],
                mode="match" if "match" in f.get("id", "") else "absence",  # 简化传递
                severity_label=f.get("severity_label", ""),
            )
        except Exception:
            result = None
        if result is None:
            failed += 1
            continue
        ok += 1
        f["llm_verified"] = result["verdict"]
        f["llm_confidence"] = result["confidence"]
        f["llm_reason"] = result["reason"]

    return findings, {"triggered": triggered, "ok": ok, "failed": failed}

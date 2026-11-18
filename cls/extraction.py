"""关键条款抽取：规则引擎优先，本地 qwen2.5 模型增强，冲突时以模型结果为准。"""
from __future__ import annotations

import re
from datetime import datetime

FIELD_SPEC = [
    ("contract_name", "合同名称", "合同抬头，用于归档与检索"),
    ("contract_no", "合同编号", "合同唯一标识"),
    ("contract_type", "合同类型", "用于匹配对应领域的风险规则"),
    ("party_a", "甲方 / 用人单位", "签约主体，关系责任承担"),
    ("party_b", "乙方 / 劳动者", "签约主体，关系责任承担"),
    ("amount_total", "合同金额", "价款或报酬，关系付款与违约金基数"),
    ("payment_terms", "付款方式", "付款节点、比例与前置条件"),
    ("term_start", "履行开始", "合同生效与起算日"),
    ("term_end", "履行结束", "合同到期与终止日"),
    ("breach_liability", "违约责任", "违约金比例、赔偿范围"),
    ("dispute_resolution", "争议解决", "诉讼或仲裁及管辖地"),
    ("confidentiality", "保密义务", "商业秘密与保密期限"),
    ("ip_ownership", "知识产权", "成果归属与许可范围"),
    ("probation", "试用期", "期限与工资（劳动合同适用）"),
    ("personal_info", "个人信息 / 数据", "告知同意、共享与出境"),
    ("signing_date", "签订日期", "合同签署时间"),
]

LABELS = {k: (l, d) for k, l, d in FIELD_SPEC}

PARTY_A = re.compile(
    r"(?:甲方|委托方|采购方|发包方|发包人|用人单位|聘用单位|出卖人|出租人|转让方|"
    r"许可方|托运人|借款人|定作人|披露方)[（(]?[^\n：:]{0,10}[）)]?\s*[：:]\s*([^\n]{2,70})")
PARTY_B = re.compile(
    r"(?:乙方|受托方|供应方|供应商|承包方|承包人|劳动者|员工|买受人|承租人|受让方|"
    r"被许可方|承运人|贷款人|承揽人|接收方)[（(]?[^\n：:]{0,10}[）)]?\s*[：:]\s*([^\n]{2,70})")
CONTRACT_NO = re.compile(r"(?:合同编号|合同编码|协议编号|编号)\s*[：:]\s*([A-Za-z0-9\u4e00-\u9fff\-_/（）()]{2,40})")
AMOUNT = re.compile(
    r"(?:合同(?:总)?金额|合同总价款|合同价款|总价款|总金额|价款合计|服务费|报酬|总价|价款|"
    r"借款金额|贷款金额|借款本金|工程(?:总)?造价|工程款|运费|(?:年)?租金)[^\n。；]{0,20}?"
    r"((?:人民币|RMB|USD|美元|港元|港币)?\s*(?:¥|￥)?\s*(?:"
    r"(?:\d{1,3}(?:[,\，]\d{3})+|\d{4,}(?!\s*年))(?:\.\d{1,2})?\s*(?:万元|万|元|圆|美元|港元|港币)?"
    r"|\d+(?:\.\d{1,2})?\s*(?:万元|万|元|圆|美元|港元|港币)"
    r"|[零壹贰叁肆伍陆柒捌玖拾佰仟一二三四五六七八九十百千万亿]{1,}[元圆]"
    r"))"
)
DATE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
TERM_RANGE = re.compile(
    r"(?:自|从)\s*(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)?\s*(?:起|开始)?\s*(?:至|到)\s*"
    r"(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)\s*(?:止|结束)?"
)
SIGN = re.compile(r"(?:签订|签署|订立)(?:日期|时间|于)\s*[：:]?\s*(\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日)")
PROBATION = re.compile(r"试用期[^\n。；]{0,60}")
SALARY = re.compile(
    r"(?:月工资|月薪|劳动报酬|工资标准|月基本工资)[^\n。；]{0,15}?"
    r"((?:人民币|RMB)?\s*(?:¥|￥)?\s*(?:\d{1,3}(?:[,，]\d{3})+|\d{3,})(?:\.\d{1,2})?\s*(?:万元|元)?(?:\s*/\s*月)?)"
)
IP = re.compile(r"[^\n。；]{0,30}(?:知识产权|著作权|专利|成果归属|所有权归属)[^\n。；]{0,60}")
PERSONAL = re.compile(r"[^\n。；]{0,30}(?:个人信息|身份证|人脸识别|数据出境|个人信息保护)[^\n。；]{0,70}")


def _norm_date(m) -> str:
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).strftime("%Y-%m-%d")
    except Exception:
        return ""


def _sentences(text: str):
    parts = re.split(r"\n|。|；", text)
    return [p.strip() for p in parts if p.strip()]


_CLAUSE_NO = re.compile(r"^[（(]?(?:第[一二三四五六七八九十百\d]{1,3}条|[一二三四五六七八九十百\d]{1,3}[）)、.．])\s*")


def _strip_no(sentence: str) -> str:
    """去掉句首的条款序号（“二、”“（三）”“第十二条”），避免抽取结果带序号。"""
    return _CLAUSE_NO.sub("", sentence).strip()


def _pick(text: str, kws, maxlen: int = 120) -> str:
    best = ""
    for s in _sentences(text):
        if any(k in s for k in kws):
            s = _strip_no(s)
            if len(s) > maxlen:
                s = s[:maxlen] + "…"
            if len(s) > len(best):
                best = s
            if len(s) >= 40:
                break
    return best


def _pick_payment(text: str, maxlen: int = 120) -> str:
    """付款方式：优先匹配含支付动作的句子，排除纯金额/发票条款。"""
    best = ""
    for s in _sentences(text):
        if not any(k in s for k in ("付款", "支付", "结算", "预付", "账期", "分期付款")):
            continue
        s = _strip_no(s)
        head = s[:14]
        # 排除以金额声明开头的句子（如“合同总金额为……”）
        if re.search(r"(合同(?:总)?金额|总价款|合同价款|总金额|价款合计)", head):
            continue
        if len(s) > maxlen:
            s = s[:maxlen] + "…"
        if len(s) > len(best):
            best = s
        if len(s) >= 40:
            break
    return best


def _pick_ip(text: str, maxlen: int = 120) -> str:
    """知识产权：命中“第七条 知识产权”标题时，取下一条实质条款；否则取含关键词的实质句。"""
    sents = _sentences(text)
    heading_re = re.compile(r"^第[一二三四五六七八九十百千零〇]+[条章节]\s*(?:知识产权|著作权|专利|成果归属|所有权归属)?\s*$")
    kw = re.compile(r"(知识产权|著作权|专利|成果归属|所有权归属|授权许可)")
    for i, s in enumerate(sents):
        core = s.strip()
        if heading_re.match(core) and kw.search(core):
            # 取下一条实质条款
            for nxt in sents[i + 1:]:
                nc = nxt.strip()
                if nc and len(nc) >= 8 and not heading_re.match(nc):
                    return (nc[:maxlen] + "…") if len(nc) > maxlen else nc
        if kw.search(core) and len(core) >= 8 and not heading_re.match(core):
            return (s[:maxlen] + "…") if len(s) > maxlen else core
    return ""


def detect_type(text: str) -> str:
    """关键词速判：返回最可能的合同类型，32 种。"""
    cands = detect_type_candidates(text)
    return cands[0] if cands else "其他合同"


def detect_type_candidates(text: str) -> list[str]:
    """关键词候选集：收集所有命中的合同类型，按优先级返回 [best, alt1, alt2, ...]，最多 5 个。

    单候选直接用；多候选时可由调用方交给本地模型消歧。
    无命中返回 []。
    """
    t = text[:2500]
    hits: list[str] = []

    def add(name: str) -> None:
        if name not in hits:
            hits.append(name)

    # —— 专属/排他性最强的类型（优先级最高）——
    if re.search(r"(商品房预售|预售许可证)", t): add("商品房预售合同")
    if re.search(r"(土地出让|国有建设用地使用权出让)", t): add("土地出让合同")
    if re.search(r"(土地转让|土地使用权转让|国有建设用地使用权转让)", t): add("土地转让合同")
    if re.search(r"(房地产合作开发|合作开发房地产|合作建房)", t): add("房地产合作开发合同")
    if re.search(r"(委托代建|代建管理)", t): add("委托代建合同")
    if re.search(r"(股权转让|股权.{0,4}转让|出资额转让)", t): add("股权转让合同")
    if re.search(r"(增资协议|增资扩股|增资入股)", t): add("增资协议")
    if re.search(r"(合伙协议|合伙经营|入伙协议|退伙协议)", t): add("合伙协议")
    if re.search(r"(保证合同|保证担保)", t): add("保证合同")
    if re.search(r"(抵押合同|抵押担保)", t): add("抵押合同")
    if re.search(r"(质押合同|质押担保)", t): add("质押合同")
    if re.search(r"(特许经营|加盟合同|连锁加盟|加盟协议)", t): add("特许经营合同")

    # —— 劳动/人事（排他性强，优先于新场景）——
    # 理由：AI 绩效考核/AI 训练劳动者成果等 AI 新场景本质是劳动合同下的条款，
    # 应归为劳动合同并走 LAI01-03 规则，而非 AI模型服务合同
    if re.search(r"(劳动合同|用人单位|劳动者|试用期)", t): add("劳动合同")

    # —— 新场景：SaaS 云服务 / 跨境合同 / AI 模型服务 ——
    # AI 模型服务关键词收紧为"大模型/LLM/GPT + 推理/API/服务/平台"组合，
    # 避免劳动合同里提"AI 绩效考核"被误判
    if re.search(r"(SaaS|云服务|云平台|云托管|订阅(?:服务)?|按月.*付费|按年.*付费)", t):
        add("SaaS云服务合同")
    if re.search(r"(跨境|跨国|境外|海外|离岸|跨境支付|跨境数据|跨境传输|制裁|OFAC|出口管制)", t):
        add("跨境合同")
    if re.search(r"(大模型|LLM|GPT|Claude|PaLM|GPT-4|GPT-7)", t) \
       or re.search(r"(AI.{0,2}模型|生成式.{0,2}AI|人工智能.{0,2}服务).{0,10}(?:推理|API|服务|平台|部署|云端|云端服务)", t):
        add("AI模型服务合同")

    # —— 保密（排除含采购/服务费的混合体）——
    if re.search(r"(保密协议|保密合同|NDA)", t) and not re.search(r"(采购|买卖|服务费)", t):
        add("保密协议")

    # —— 技术类（先于通用服务识别）——
    if re.search(r"(软件许可)", t): add("软件许可合同")
    if re.search(r"(软件开发|APP开发|系统开发|小程序开发)", t): add("软件开发合同")
    if re.search(r"(技术开发|委托开发|技术合作开发)", t): add("技术开发合同")
    if re.search(r"(技术转让)", t): add("技术转让合同")
    if re.search(r"(技术许可)", t): add("技术许可合同")
    if re.search(r"(技术服务)", t): add("技术服务合同")

    # —— 运输/物流（先于采购识别）——
    if re.search(r"(货运代理|货代)", t): add("货运代理合同")
    if re.search(r"(物流合同|物流服务|仓配一体)", t): add("物流合同")
    if re.search(r"(运输合同|货物运输|承运人|托运人|货运合同|多式联运)", t): add("运输合同")

    # —— 服务/咨询（通用兜底，优先级低）——
    if re.search(r"(咨询服务)", t): add("咨询服务合同")
    if re.search(r"(旅游合同|旅行社|旅游服务|跟团游|出境游)", t): add("旅游合同")
    if re.search(r"(代理合同|经销合同|代理商|经销商|区域代理|独家代理|总代理)", t): add("代理经销合同")
    if re.search(r"(居间合同|中介合同|中介服务)", t): add("居间合同")
    if re.search(r"(物业服务合同|物业管理)", t): add("物业服务合同")
    if re.search(r"(广告合同|广告发布|广告代理|推广服务)", t): add("广告合同")
    if re.search(r"(仓储合同|仓储服务|保管合同)", t): add("仓储合同")
    if re.search(r"(健身|私教|瑜伽|游泳|培训班|早教|美容|美发|美甲|会员卡|预付卡|课程包|家政|保洁|月嫂|摄影|婚纱|写真|宠物)", t):
        add("消费服务合同")

    # —— 采购/租赁/借款/建设工程（通用兜底）——
    if re.search(r"(采购|供货|买卖|购销|货物)", t): add("采购合同")
    if re.search(r"(租赁|出租|承租)", t): add("租赁合同")
    if re.search(r"(借款|贷款|融资)", t): add("借款合同")
    if re.search(r"(建设工程|施工|工程款)", t): add("建设工程合同")
    if re.search(r"(委托开发|软件开发|技术服务|咨询服务|服务合同|承揽)", t): add("服务合同")

    return hits[:5]


def llm_disambiguate_type(text: str, candidates: list[str], timeout: int = 8) -> str | None:
    """轻量二阶段消歧：让本地模型在候选集中选最贴切的合同类型。

    prompt 被严格限制只在候选里选，不允许自由发挥；输出格式 `答案:X` 解析。
    模型不可用 / 超时 / 解析失败时返回 None，由调用方回退到关键词首选。

    安全网：LLM 只能升级类型（选更具体、更长的候选），不能降级。
    关键词匹配的首选 candidates[0] 优先级最高——LLM 只有在选出的候选比它更长（更具体）时才接受。
    """
    if len(candidates) <= 1:
        return None  # 单候选无需消歧

    top = candidates[0]  # 关键词首选，最具体的那个

    import json as _json
    from . import llm as _llm
    # 避免循环依赖：动态 import config
    import importlib
    try:
        _cfg_mod = importlib.import_module(".config", __package__ or "cls")
        cfg = _cfg_mod.load_config()
    except Exception:
        return None
    model = cfg.get("model") or "qwen2.5:3b"
    st = _llm.status()
    if not (st.get("online") and st.get("model_ready")):
        return None

    prompt = (
        "你是合同分类助手。请从以下候选中选出最贴切的合同类型名称。\n"
        "原则：(1) 优先选择更具体、更长、含行业限定词的类型名；"
        "(2) 候选列表按关键词匹配置信度从高到低排序，排在前面的置信度更高；"
        "(3) 只能从候选列表里选，不允许自己发挥。\n\n"
        f"候选列表（置信度从高到低）：{'、'.join(candidates)}\n\n"
        f"合同开头：{text[:1500]}\n\n"
        "输出严格格式：答案:【类型名】"
    )

    try:
        import urllib.request as _ur
        url = cfg["ollama_url"].rstrip("/") + "/api/generate"
        payload = _json.dumps({
            "model": model, "prompt": prompt, "stream": False, "options": {"temperature": 0}
        }).encode()
        req = _ur.Request(url, data=payload, headers={"Content-Type": "application/json"})
        with _ur.urlopen(req, timeout=timeout) as resp:
            data = _json.loads(resp.read())
        ans = str(data.get("response", "")).strip()
        # 解析 "答案:【XXX】" 或 "答案:XXX"
        picked = None
        for c in candidates:
            if c in ans:
                picked = c
                break
        if picked is None:
            return None
        # 安全网（1）：LLM 不能把关键词的好结果降级
        # 只有当 picked 比 top 更具体（名字更长）时才接受消歧结果
        if len(picked) < len(top):
            return None  # 让调用方保持 candidates[0]
        # 安全网（2）：保护带专属规则的新场景类型
        # 关键词首选如果是带专属规则的新类型（only_types=该类型的规则），LLM 消歧不能把它换掉
        _PROTECTED_TYPES = {"SaaS云服务合同", "跨境合同", "AI模型服务合同"}
        if top in _PROTECTED_TYPES and picked != top:
            return None
        return picked
    except Exception:
        return None


def extract_rules(text: str) -> dict:
    lines = text.splitlines()
    out: dict[str, str] = {}
    out["contract_type"] = detect_type(text)

    title = ""
    for line in lines[:12]:
        line = line.strip()
        if 4 <= len(line) <= 60 and re.search(r"(合同|协议|契约)", line) and not re.search(r"(编号|甲方|乙方)", line):
            title = line
            break
    out["contract_name"] = title

    m = CONTRACT_NO.search(text)
    out["contract_no"] = m.group(1).strip() if m else ""

    m = PARTY_A.search(text)
    out["party_a"] = m.group(1).strip(" ：:") if m else ""
    m = PARTY_B.search(text)
    out["party_b"] = m.group(1).strip(" ：:") if m else ""

    amounts = AMOUNT.findall(text)
    out["amount_total"] = amounts[0].strip() if amounts else ""
    if out["contract_type"] == "劳动合同":
        sm = SALARY.search(text)
        out["amount_total"] = sm.group(1).strip() if sm else ""

    terms = TERM_RANGE.search(text)
    if terms and terms.group(2):
        out["term_start"] = _norm_date(DATE.search(terms.group(1))) if terms.group(1) else ""
        out["term_end"] = _norm_date(DATE.search(terms.group(2)))
    else:
        d = DATE.search(text)
        out["term_start"] = _norm_date(d) if d else ""
        out["term_end"] = ""

    m = SIGN.search(text)
    if m:
        out["signing_date"] = _norm_date(DATE.search(m.group(1)))
    else:
        # 结尾落款处常见 “2026 年 3 月 1 日”
        tail_dates = DATE.findall(text[-1200:])
        out["signing_date"] = ""
        if tail_dates:
            mm = DATE.search(text[-1200:])
            out["signing_date"] = _norm_date(mm)

    out["payment_terms"] = _pick_payment(text)
    out["breach_liability"] = _pick(text, ["违约金", "违约责任", "赔偿损失", "违约方"])
    out["dispute_resolution"] = _pick(text, ["仲裁", "人民法院", "管辖", "争议解决", "诉讼"])
    out["confidentiality"] = _pick(text, ["保密", "商业秘密"], 100)
    m = PROBATION.search(text)
    out["probation"] = m.group(0) if m else ""
    out["ip_ownership"] = _pick_ip(text)
    m = PERSONAL.search(text)
    out["personal_info"] = m.group(0).strip() if m else ""
    return out


LLM_MAP = {
    "contract_type": "contract_type",
    "contract_name": "contract_name",
    "party_a": "party_a",
    "party_b": "party_b",
    "amount_total": "amount_total",
    "term_start": "term_start",
    "term_end": "term_end",
    "payment_terms": "payment_terms",
    "breach_liability": "breach_liability",
    "dispute_resolution": "dispute_resolution",
    "confidentiality": "confidentiality",
    "ip_ownership": "ip_ownership",
    "probation": "probation",
    "personal_info": "personal_info",
    "signing_date": "signing_date",
}

EMPTY = {"", "未约定", "未提及", "无", "none", "null", "n/a", "不适用"}


def merge(llm: dict, rules: dict) -> tuple[dict, dict]:
    values, sources = {}, {}
    vague = {"法院", "诉讼", "仲裁", "起诉", "人民法院", "法院管辖"}
    # 规则引擎基于全文；模型只看到头尾片段，这些条款类字段模型答得更短时保留规则结果
    detail_keys = {"breach_liability", "dispute_resolution", "payment_terms",
                   "confidentiality", "ip_ownership", "personal_info"}
    for key in LABELS:
        rv = (rules.get(key) or "").strip()
        lv = (llm.get(key) or "").strip() if isinstance(llm.get(key), str) else ""
        # 争议解决：LLM 返回过于笼统（如仅“法院”）时，回退到更具体的规则结果
        if key == "dispute_resolution" and lv and lv in vague and len(rv) > len(lv):
            values[key] = rv
            sources[key] = "rule"
        elif lv and lv.lower() not in EMPTY:
            if key in detail_keys and rv and len(rv) > len(lv):
                values[key] = rv
                sources[key] = "rule"
            else:
                values[key] = lv
                sources[key] = "llm"
        elif rv:
            values[key] = rv
            sources[key] = "rule"
        else:
            values[key] = ""
            sources[key] = "none"
    for key in ("term_start", "term_end", "signing_date"):
        v = values.get(key, "")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
            continue
        m = DATE.search(v)
        if m:
            values[key] = _norm_date(m)
    return values, sources


def build_fields(values: dict, sources: dict) -> list[dict]:
    is_labor = values.get("contract_type") == "劳动合同"
    fields = []
    for key, label, hint in FIELD_SPEC:
        if key == "amount_total" and is_labor:
            label = "劳动报酬（月薪）"
            hint = "月工资标准，关系经济补偿与社保缴费基数"
        v = values.get(key, "")
        src = sources.get(key, "none")
        fields.append({
            "key": key,
            "label": label,
            "hint": hint,
            "value": v or "未识别",
            "source": src,
            "confidence": {"llm": 0.88, "rule": 0.74}.get(src, 0.0),
        })
    return fields


def extract(text: str, llm_result: dict | None = None) -> tuple[list[dict], dict]:
    rules = extract_rules(text)
    llm = llm_result or {}
    values, sources = merge(llm, rules)
    return build_fields(values, sources), values


def summary_text(values: dict, findings: list[dict]) -> str:
    typ = values.get("contract_type") or "合同"
    a, b = values.get("party_a"), values.get("party_b")
    parties = f"{a or '甲方未明确'} 与 {b or '乙方未明确'}" if (a or b) else "合同主体信息不完整"
    high = [f for f in findings if f["severity"] == "high"]
    med = [f for f in findings if f["severity"] == "medium"]
    if high:
        head = f"这是一份{typ}，{parties}。存在 {len(high)} 项高风险条款"
        if med:
            head += f"、{len(med)} 项中风险条款"
        head += f"，首要问题为「{high[0]['title']}」。"
        head += "建议在签署前完成条款修订，否则一旦发生争议，责任与损失分配将明显不利。"
    elif med:
        head = f"这是一份{typ}，{parties}。整体结构基本完整，但有 {len(med)} 项中风险条款需要关注"
        head += f"，优先处理「{med[0]['title']}」。"
    else:
        head = f"这是一份{typ}，{parties}。未触发高风险规则，条款结构相对完整，仍建议复核金额、期限与签署主体。"
    return head

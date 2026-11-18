"""导出：专业 PDF 审阅报告 + Excel 字段/风险清单。"""
from __future__ import annotations

import io
import re
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (BaseDocTemplate, Frame, PageTemplate, Paragraph, NextPageTemplate,
                                PageBreak, KeepTogether, Spacer, Table, TableStyle, CondPageBreak)

FONT = "STSong-Light"
pdfmetrics.registerFont(UnicodeCIDFont(FONT))

INK = colors.HexColor("#16202B")
INK_SOFT = colors.HexColor("#59636F")
PAPER = colors.HexColor("#FBFAF7")
GOLD = colors.HexColor("#A8844F")
LINE = colors.HexColor("#D9D3C7")
HIGH = colors.HexColor("#A3372F")
MED = colors.HexColor("#B07A2A")
LOW = colors.HexColor("#4F6B52")
LEVEL = {
    "high": ("高风险", HIGH),
    "medium": ("中风险", MED),
    "low": ("低风险", LOW),
}

# 中文 CID 字体缺字会把“·”渲染成三角块，统一替换为全角竖线
_UNSAFE_CHARS = {"·": "｜", "\u00a0": " ", "\u200b": ""}


def _t(value) -> str:
    """净化进 PDF 的文本：替换 CID 字体缺字符号，避免导出件出现乱码方框。"""
    text = str(value if value is not None else "")
    for bad, good in _UNSAFE_CHARS.items():
        text = text.replace(bad, good)
    return text


# —— 批注定位：把风险落到合同正文的某个段落 ——
# 每条规则命中后只有“证据”（可定位）与“缺失条款”（无原文）两种情形。
# 缺失条款没有原文可锚定时，按业务语义给出建议补充位置，避免全部堆到文末附录。

# (风险侧关键词, 正文侧关键词, 位置偏好)  0=宜靠前(主体/签署) 1=宜靠后(责任/争议)
_ANCHOR_LEXICON = [
    (("主体", "营业执照", "法定代表人", "签约方", "当事人"),
     ("甲方", "乙方", "出卖人", "买受人", "委托方", "受托方", "发包方", "承包方"), 0),
    (("签署", "签订", "日期", "份数", "送达", "通知"),
     ("签署", "盖章", "签字", "一式", "份", "送达", "通知", "地址"), 1),
    (("价款", "金额", "付款", "支付", "结算", "账期", "定金", "发票", "价格", "报酬", "费用"),
     ("价款", "金额", "付款", "支付", "结算", "定金", "发票", "价格", "报酬", "费用", "租金", "运费", "总价"), 1),
    (("交付", "验收", "质量", "检验", "瑕疵", "包装", "规格", "数量", "标的", "交货", "运费"),
     ("交付", "验收", "质量", "检验", "瑕疵", "包装", "规格", "数量", "标的", "货物", "房屋",
      "送至", "送货", "交货", "运输", "运费", "风险转移"), 1),
    (("质保", "保修", "售后", "三包"),
     ("质保", "保修", "售后", "三包", "质量保证", "维修"), 1),
    (("违约", "赔偿", "损失", "罚则", "解除", "责任"),
     ("违约", "赔偿", "损失", "罚则", "解除", "扣除", "责任"), 1),
    (("争议", "管辖", "仲裁", "诉讼", "司法"),
     ("争议", "仲裁", "诉讼", "管辖", "法院", "调解"), 1),
    (("保密", "商业秘密"),
     ("保密", "秘密", "商业秘密"), 1),
    (("知识产权", "著作权", "专利", "商标", "侵权", "源代码", "成果归属", "技术成果"),
     ("知识产权", "著作权", "专利", "商标", "侵权", "源代码", "成果"), 1),
    (("期限", "有效期", "终止", "届满", "续期", "续约"),
     ("期限", "有效期", "终止", "届满", "续期", "续约", "合作结束", "年内"), 1),
    (("个人信息", "数据", "隐私", "告知", "同意", "出境"),
     ("个人信息", "数据", "隐私", "告知", "同意", "出境"), 1),
    (("工资", "试用期", "社会保险", "工时", "加班", "劳动", "岗位", "薪酬"),
     ("工资", "试用期", "社会保险", "工时", "加班", "劳动", "岗位", "薪酬"), 1),
    (("廉洁", "贿赂", "回扣", "账外", "合规", "利益冲突", "关联交易", "职务侵占", "挪用", "礼品", "宴请"),
     ("廉洁", "贿赂", "回扣", "账外", "合规", "利益冲突", "关联", "发票", "报销", "招待"), 1),
    (("面积", "房屋", "产权", "预告登记", "备案", "保修", "规划"),
     ("面积", "房屋", "产权", "登记", "备案", "保修", "规划", "交付"), 1),
    (("运输", "承运", "托运", "货损", "留置", "运费", "保险"),
     ("运输", "承运", "托运", "货损", "留置", "运费", "保险"), 1),
    (("担保", "保证", "抵押", "质押", "反担保"),
     ("担保", "保证", "抵押", "质押", "反担保"), 1),
    (("股权", "股东", "出资", "增资", "估值", "回购", "分红"),
     ("股权", "股东", "出资", "增资", "估值", "回购", "分红"), 1),
    (("技术", "开发", "源代码", "成果"),
     ("技术", "开发", "源代码", "成果", "验收"), 1),
    (("租金", "租赁", "押金", "维修", "转租"),
     ("租金", "租赁", "押金", "维修", "转租"), 1),
]


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _paragraphs(text: str) -> tuple[list[str], list[str]]:
    """返回 (原文段落, 去空白后的段落)，用于批注定位。"""
    paras = [ln.strip() for ln in re.split(r"\n+", text or "") if ln.strip()]
    return paras, [_norm(p) for p in paras]


def _is_missing_evidence(evidence: str) -> bool:
    """证据为空或为“合同中未见相关条款”时，说明这是缺失条款型风险。"""
    ev = (evidence or "").strip()
    if not ev:
        return True
    return ev.startswith("—") or "合同中未见相关条款" in ev or "未检索到" in ev


def _fuzzy_fragment(core: str, para: str) -> str:
    """在段落中寻找与证据最长的公共片段，容忍截断、标点差异与拼接。"""
    for size in (12, 10, 8):
        for i in range(max(0, len(core) - size + 1)):
            frag = core[i:i + size]
            if frag in para:
                return frag
    return ""


def _match_evidence(evidence: str, paras: list[str], compacts: list[str]) -> tuple[int, str]:
    """把证据定位到段落，返回 (段落下标, 命中片段)；失败返回 (-1, "")。"""
    core = _norm(evidence).strip("—…-（）()")
    if not core:
        return -1, ""
    # 1) 整段证据命中
    for i, c in enumerate(compacts):
        if len(core) >= 4 and core in c:
            return i, core
    # 2) 证据被省略号截断（A…B）时，取最长可命中片段
    frags = sorted((f for f in re.split(r"[…]+", core) if len(f) >= 4), key=len, reverse=True)
    for frag in frags:
        for i, c in enumerate(compacts):
            if frag in c:
                return i, frag
    # 3) 短证据（如“90日”“100万元”）以包含关系兜底
    if len(core) >= 2:
        for i, c in enumerate(compacts):
            if core in c:
                return i, core
    # 4) 模糊匹配：容忍标点、全半角和局部改写
    best, best_i = "", -1
    for i, c in enumerate(compacts):
        frag = _fuzzy_fragment(core, c)
        if len(frag) > len(best):
            best, best_i = frag, i
    if len(best) >= 8:
        return best_i, best
    return -1, ""


def _suggest_position(finding: dict, compacts: list[str], usage: dict | None = None) -> int:
    """为“缺失条款”型风险选择建议补充位置：按语义命中段落，否则落到文末。

    usage 记录各段落已承载的批注数，单段最多优先落 2 条，避免批注全挤在一段。
    """
    if not compacts:
        return -1
    usage = usage if usage is not None else {}
    main = f"{finding.get('title', '')}{finding.get('category', '')}"
    weak = f"{finding.get('analysis', '')}{finding.get('advice', '')}"
    last = len(compacts) - 1
    tail = list(range(max(0, last - 2), last + 1))
    # 签署日期、合同份数、通知送达等，天然属于合同尾部签署区
    if any(k in main + weak for k in ("签署日期", "签订日期", "合同份数", "一式", "送达", "通知")):
        if usage.get(last, 0) < 4:
            return last
        return min(tail, key=lambda i: (usage.get(i, 0), -i))
    # 首段若只是合同标题，不作为缺失条款的建议补充位置
    candidates = list(range(len(compacts)))
    if len(candidates) > 2 and len(compacts[0]) <= 24 and not re.search(r"[：:，,；;。]", compacts[0]):
        candidates = candidates[1:]
    scores = [0.0] * len(compacts)
    for risk_kws, body_kws, bias in _ANCHOR_LEXICON:
        # 标题与类别是规则的主题，权重高；分析建议里常顺带提到“违约金”等词，只作弱信号
        if any(k in main for k in risk_kws):
            strength = 1.0
        elif any(k in weak for k in risk_kws):
            strength = 0.35
        else:
            continue
        for i, c in enumerate(compacts):
            hits = sum(1 for k in body_kws if k in c)
            if not hits:
                continue
            pos = i / last if last else 0.0
            # 宜靠后的条款（违约、争议、廉洁）越靠后权重越高，反之亦然
            weight = 1.0 + 0.8 * (pos if bias else 1.0 - pos)
            scores[i] += hits * weight * strength
    # 语义命中的段落按得分优先，且同段不超过 2 条
    for i in sorted(candidates, key=lambda x: (-scores[x], -x)):
        if scores[i] > 0 and usage.get(i, 0) < 2:
            return i
    # 没有语义命中（或命中段已排满）：落在正文末尾，取使用次数最少的一段
    return min(tail, key=lambda i: (usage.get(i, 0), -i))


def locate_findings(result: dict) -> list[dict]:
    """为每条风险计算定位信息，供 DOCX 批注与 XLSX 清单复用。

    返回每项：{index, para, mode, evidence, snippet}
    mode: anchor=命中原文，missing=缺失条款（建议补充位置），none=无法定位
    """
    paras, compacts = _paragraphs(result.get("text") or "")
    located = []
    usage: dict[int, int] = {}
    for f in result.get("findings") or []:
        ev = f.get("evidence") or ""
        if _is_missing_evidence(ev):
            idx = _suggest_position(f, compacts, usage)
            located.append({
                "finding": f, "index": idx, "mode": "missing" if idx >= 0 else "none",
                "evidence": "", "snippet": compacts[idx][:40] if idx >= 0 else "",
            })
        else:
            idx, frag = _match_evidence(ev, paras, compacts)
            located.append({
                "finding": f, "index": idx, "mode": "anchor" if idx >= 0 else "none",
                "evidence": frag, "snippet": compacts[idx][:40] if idx >= 0 else "",
            })
        if idx >= 0:
            usage[idx] = usage.get(idx, 0) + 1
    return located


def _location_label(loc: dict) -> str:
    """人类可读的定位描述，用于 Excel 清单与报告。"""
    if loc["index"] < 0:
        return "全文（未定位到具体段落）"
    return f"第 {loc['index'] + 1} 段：{loc['snippet']}"


def _comment_lines(loc: dict) -> list[str]:
    f = loc["finding"]
    if loc["mode"] == "missing":
        head = f"【缺失条款 · {f.get('severity_label', '')}】{f.get('title', '')}"
        lines = [head, "本合同中未检索到对应约定，建议在当前位置补充相应条款。"]
    else:
        lines = [f"【{f.get('severity_label', '')}】{f.get('title', '')}"]
    lines.append(f"风险分析：{f.get('analysis', '')}")
    lines.append(f"修改建议：{f.get('advice', '')}")
    if f.get("revision"):
        lines.append(f"建议条款：{f['revision']}")
    basis = "；".join(f"{b.get('law', '')}{b.get('label', '')}" for b in (f.get("basis") or []))
    if basis:
        lines.append(f"法律依据：{basis}")
    return lines


def _styles():
    base = getSampleStyleSheet()
    s = {
        "h1": ParagraphStyle("h1", parent=base["Title"], fontName=FONT, fontSize=24, leading=31, textColor=INK),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontName=FONT, fontSize=14, leading=21, textColor=INK,
                            spaceBefore=14, spaceAfter=7, keepWithNext=1),
        "score": ParagraphStyle("score", parent=base["BodyText"], fontName=FONT, fontSize=22, leading=27,
                                textColor=INK),
        "body": ParagraphStyle("body", parent=base["BodyText"], fontName=FONT, fontSize=9.6, leading=15.5,
                               textColor=colors.HexColor("#2C3742")),
        "small": ParagraphStyle("small", parent=base["BodyText"], fontName=FONT, fontSize=8, leading=12,
                                textColor=INK_SOFT),
        "quote": ParagraphStyle("quote", parent=base["BodyText"], fontName=FONT, fontSize=9, leading=14,
                                textColor=colors.HexColor("#4A4238"), leftIndent=8, rightIndent=8),
        "label": ParagraphStyle("label", parent=base["BodyText"], fontName=FONT, fontSize=8, leading=11,
                                textColor=INK_SOFT),
        "meta": ParagraphStyle("meta", parent=base["BodyText"], fontName=FONT, fontSize=9, leading=13,
                               textColor=colors.HexColor("#EFEAE0"), alignment=TA_CENTER),
        "cover": ParagraphStyle("cover", parent=base["Title"], fontName=FONT, fontSize=30, leading=40,
                                textColor=colors.HexColor("#F6F1E7"), alignment=TA_CENTER),
    }
    return s


def _page(canvas, doc):
    canvas.saveState()
    w, h = A4
    canvas.setFillColor(PAPER)
    canvas.rect(0, 0, w, h, fill=1, stroke=0)
    if doc.page > 1:
        canvas.setStrokeColor(LINE)
        canvas.setLineWidth(0.5)
        canvas.line(20 * mm, h - 15 * mm, w - 20 * mm, h - 15 * mm)
        canvas.setFont(FONT, 7.4)
        canvas.setFillColor(INK_SOFT)
        canvas.drawString(20 * mm, h - 12.5 * mm, _t("Contract LocalScan · 本地合同审阅报告"))
        canvas.drawRightString(w - 20 * mm, h - 12.5 * mm, datetime.now().strftime("%Y-%m-%d"))
        canvas.line(20 * mm, 14 * mm, w - 20 * mm, 14 * mm)
        canvas.drawString(20 * mm, 9.5 * mm, _t("数据完全在本机处理 · 未上传任何云端服务"))
        canvas.drawRightString(w - 20 * mm, 9.5 * mm, f"第 {doc.page - 1} 页")
    canvas.restoreState()


# 章节标题下方至少保留该高度（pt）的正文空间，不足则整节移到下一页。
# 阈值含标题自身（spaceBefore 14 + leading 21），确保标题文字距页底 ≥130pt。
SECTION_MIN_ROOM = 170


def _section(title: str, s: dict, blocks: list) -> list:
    """章节标题 + 首个正文块：同组不跨页，且页底余量不足时整节换页。"""
    return [CondPageBreak(SECTION_MIN_ROOM), KeepTogether([Paragraph(title, s["h2"])] + blocks)]


def build_pdf(result: dict) -> bytes:
    s = _styles()
    buf = io.BytesIO()
    doc = BaseDocTemplate(buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm,
                          topMargin=22 * mm, bottomMargin=20 * mm,
                          title=f"合同审阅报告 - {result['filename']}", author="Contract LocalScan")
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="main")
    doc.addPageTemplates([
        PageTemplate(id="cover", frames=[frame], onPage=_cover_page),
        PageTemplate(id="body", frames=[frame], onPage=_page),
    ])

    story = _cover(story_start=True, result=result, s=s)
    story.append(NextPageTemplate("body"))
    story.append(PageBreak())
    story.append(Spacer(1, 8))
    # 每个章节标题都与首个正文块同组（不跨页），并在页底余量不足时整节换页
    summary_flow = _summary(result, s)
    story.extend(_section("一、审阅结论", s, [summary_flow[0]]))
    story.extend(summary_flow[1:])
    fields_flow = _fields_table(result, s)
    story.extend(_section("二、关键条款抽取", s, fields_flow))
    head3 = Paragraph("三、风险清单", s["h2"])
    story.append(CondPageBreak(SECTION_MIN_ROOM))
    if not result["findings"]:
        story.append(KeepTogether([head3, Paragraph(
            "本次审阅未触发高风险规则，建议仍由法务复核签署主体与金额条款。", s["body"])]))
    for i, f in enumerate(result["findings"], 1):
        story.append(_finding_block(i, f, s, lead=[head3] if i == 1 else None))
    arts = []
    for art in result.get("statutes", [])[:6]:
        arts.append(Paragraph(
            f"<b>{art['law']} {art['label']}</b>　<font color='#8A8377'>{art.get('chapter', '')}</font>",
            s["small"]))
        arts.append(Paragraph(art["text"][:320] + ("…" if len(art["text"]) > 320 else ""), s["body"]))
        arts.append(Spacer(1, 4))
    if not arts:
        arts = [Paragraph("本次未检索到与该合同类型高度匹配的法条，可结合第三部分的风险分析人工复核。", s["body"])]
    story.extend(_section("四、相关法条（本地知识库检索）", s, arts[:2]))
    story.extend(arts[2:])
    story.extend(_section("五、免责声明", s, [Paragraph(
        "本报告由 Contract LocalScan 基于内置规则引擎与本地法规知识库自动生成，用于合同风险预审，不构成正式法律意见。"
        "规则识别存在漏判与误判可能，重大交易请交由执业律师复核。全部合同文本、抽取结果与报告均在本机处理与保存，未上传任何云端服务。",
        s["small"])]))

    doc.build(story)
    return buf.getvalue()


def _cover_page(canvas, doc):
    w, h = A4
    canvas.saveState()
    canvas.setFillColor(PAPER)
    canvas.rect(0, 0, w, h, fill=1, stroke=0)
    canvas.setFillColor(INK)
    canvas.rect(0, h - 120 * mm, w, 120 * mm, fill=1, stroke=0)
    canvas.setFillColor(GOLD)
    canvas.rect(0, h - 121.2 * mm, w, 1.2 * mm, fill=1, stroke=0)
    # 品牌标记
    canvas.setStrokeColor(colors.HexColor("#C7A66A"))
    canvas.setLineWidth(0.8)
    canvas.circle(w / 2, h - 42 * mm, 9 * mm, stroke=1, fill=0)
    canvas.setFont(FONT, 13)
    canvas.setFillColor(colors.HexColor("#E8DCC4"))
    canvas.drawCentredString(w / 2, h - 46.5 * mm, "LS")
    canvas.setFont(FONT, 9)
    canvas.setFillColor(colors.HexColor("#9BA6B2"))
    canvas.drawCentredString(w / 2, h - 58 * mm, "C O N T R A C T   L O C A L S C A N")
    canvas.setFont(FONT, 27)
    canvas.setFillColor(colors.HexColor("#F6F1E7"))
    canvas.drawCentredString(w / 2, h - 76 * mm, "合同审阅报告")
    canvas.setFont(FONT, 10)
    canvas.setFillColor(colors.HexColor("#B9C2CC"))
    canvas.drawCentredString(w / 2, h - 88 * mm, _t("CONTRACT RISK REVIEW · LOCAL ONLY"))
    canvas.restoreState()


def _cover(story_start, result, s):
    w = A4[0] - 40 * mm
    story = []
    story.append(Spacer(1, 120 * mm + 6))
    story.append(Paragraph(result.get("contract_name") or result["filename"], s["h1"]))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        _t(f"文件：{result['filename']}　·　{result.get('chars', 0)} 字　·　"
           f"生成时间：{datetime.fromtimestamp(result.get('created_at', 0) / 1000).strftime('%Y-%m-%d %H:%M')}"),
        s["small"]))
    label, color = LEVEL[result["level"]]
    score = result["score"]
    tbl = Table([
        [Paragraph("<font color='#8A8377'>风险指数</font>", s["label"]),
         Paragraph("<font color='#8A8377'>风险等级</font>", s["label"]),
         Paragraph("<font color='#8A8377'>风险分布</font>", s["label"])],
        [Paragraph(f"{score}<font size='9' color='#8A8377'>　/ 100</font>", s["score"]),
         Paragraph(f"<font color='#{color.hexval()[2:]}'>{label}</font>", s["body"]),
         Paragraph(_t(f"高风险 {result['counts']['high']} · 中风险 {result['counts']['medium']} · 低风险 {result['counts']['low']}"), s["body"])],
    ], colWidths=[w * 0.28, w * 0.22, w * 0.5])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F1EDE3")),
        ("BOX", (0, 0), (-1, -1), 0.6, LINE),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, LINE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(Spacer(1, 10))
    story.append(tbl)
    story.append(Spacer(1, 12))
    return story


def _ext_method(result):
    if result.get("llm_used"):
        return "本地 qwen2.5 模型 + 规则引擎"
    if result.get("llm_error"):
        return f"规则引擎（LLM 降级：{str(result['llm_error'])[:24]}）"
    return "规则引擎（本地模型未启用）"


def _party_labels(contract_type):
    """根据合同类型返回甲乙方身份标签"""
    ct = (contract_type or "")
    if "劳动" in ct:
        return "甲方 / 用人单位", "乙方 / 劳动者"
    if any(k in ct for k in ("采购", "买卖", "购销")):
        return "甲方 / 采购方", "乙方 / 供应方"
    if any(k in ct for k in ("服务", "咨询", "委托", "开发", "设计")):
        return "甲方 / 委托方", "乙方 / 服务方"
    if "租赁" in ct:
        return "甲方 / 出租方", "乙方 / 承租方"
    if "借款" in ct or "贷款" in ct:
        return "甲方 / 出借方", "乙方 / 借款方"
    if "承揽" in ct or "加工" in ct:
        return "甲方 / 定作方", "乙方 / 承揽方"
    if "建设" in ct or "工程" in ct:
        return "甲方 / 发包方", "乙方 / 承包方"
    return "甲方", "乙方"


def _summary(result, s):
    out = [Paragraph(result.get("summary", ""), s["body"])]
    pa_label, pb_label = _party_labels(result.get("contract_type"))
    meta = [
        ["合同类型", result.get("contract_type") or "未识别", pa_label, result.get("party_a") or "未识别"],
        [pb_label, result.get("party_b") or "未识别", "合同金额", result.get("amount_total") or "未识别"],
        ["抽取方式", _ext_method(result),
         "数据流向", "0 KB 上传云端"],
    ]
    data = []
    for row in meta:
        data.append([
            Paragraph(f"<font color='#8A8377'>{row[0]}</font>", s["small"]),
            Paragraph(str(row[1])[:120], s["body"]),
            Paragraph(f"<font color='#8A8377'>{row[2]}</font>", s["small"]),
            Paragraph(str(row[3])[:120], s["body"]),
        ])
    tbl = Table(data, colWidths=[26 * mm, 55 * mm, 26 * mm, 55 * mm])
    tbl.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, LINE),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, LINE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    out.append(Spacer(1, 6))
    out.append(tbl)
    return out


def _fields_table(result, s):
    rows = [[Paragraph("<font color='#FFFFFF'>字段</font>", s["small"]),
             Paragraph("<font color='#FFFFFF'>抽取结果</font>", s["small"]),
             Paragraph("<font color='#FFFFFF'>来源</font>", s["small"])]]
    pa_label, pb_label = _party_labels(result.get("contract_type"))
    label_map = {"甲方 / 用人单位": pa_label, "乙方 / 劳动者": pb_label}
    for f in result["fields"]:
        src = {"llm": "qwen2.5", "rule": "规则", "none": "未识别"}[f["source"]]
        flabel = label_map.get(f["label"], f["label"])
        rows.append([Paragraph(flabel, s["small"]), Paragraph(str(f["value"])[:800], s["body"]),
                     Paragraph(src, s["small"])])
    tbl = Table(rows, colWidths=[30 * mm, 110 * mm, 22 * mm], repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), INK),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F7F4EC")]),
        ("GRID", (0, 0), (-1, -1), 0.4, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return [tbl]


def _finding_block(i, f, s, lead=None):
    """风险条目：表头 + 正文，整体不可分页。

    lead 用于把“三、风险清单”标题并入首条，避免标题单独留在页底。
    """
    label, color = LEVEL[f["severity"]]
    head = Table([[Paragraph(f"<font color='#FFFFFF'>{label}</font>", s["small"]),
                   Paragraph(f"<b>{i}. {f['title']}</b>　<font color='#8A8377'>{f['category']}</font>", s["body"])]],
                 colWidths=[18 * mm, 144 * mm])
    head.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), color),
        ("BACKGROUND", (1, 0), (1, 0), colors.HexColor("#F4F0E7")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
    ]))
    body = []
    if _is_missing_evidence(f.get("evidence", "")):
        body.append(Paragraph(
            "<font color='#8A8377'>条款原文</font>　本合同中未检索到对应约定（<b>缺失条款</b>，建议补充）",
            s["quote"]))
    else:
        body.append(Paragraph(
            f"<font color='#8A8377'>条款原文</font>　“{_t(f['evidence'])}”", s["quote"]))
    body.append(Paragraph(f"<font color='#8A8377'>风险分析</font>　{_t(f['analysis'])}", s["body"]))
    body.append(Paragraph(f"<font color='#8A8377'>修改建议</font>　{_t(f['advice'])}", s["body"]))
    if f.get("revision"):
        body.append(Paragraph(f"<font color='#8A8377'>条款修订建议</font>　{_t(f['revision'])}", s["body"]))
    if f["basis"]:
        refs = "；".join(f"{b['law']}{b['label']}" for b in f["basis"])
        body.append(Paragraph(f"<font color='#8A8377'>法律依据</font>　{_t(refs)}", s["small"]))
    wrap = Table([[body]], colWidths=[162 * mm])
    wrap.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, LINE),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    return KeepTogether(list(lead or []) + [head, wrap, Spacer(1, 8)])


def build_docx(result: dict) -> bytes:
    """生成带 Word 批注的修订版合同：批注锚定到命中条款所在段落。"""
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor

    name = result.get("contract_name") or result.get("filename") or "合同"
    level_label = {"high": "高风险", "medium": "中风险", "low": "低风险"}.get(result.get("level"), "低风险")
    findings = result.get("findings") or []

    doc = Document()
    sec = doc.sections[0]
    sec.left_margin = sec.right_margin = Cm(2.6)
    sec.top_margin = sec.bottom_margin = Cm(2.4)

    normal = doc.styles["Normal"]
    normal.font.name = "宋体"
    normal.font.size = Pt(10.5)
    normal.element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), "宋体")

    def cn(run):
        run._element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), "宋体")

    def para(text="", size=10.5, bold=False, color=None, align=None, space_after=4):
        p = doc.add_paragraph()
        if align is not None:
            p.alignment = align
        p.paragraph_format.space_after = Pt(space_after)
        p.paragraph_format.line_spacing = 1.35
        if text:
            r = p.add_run(text)
            r.bold = bold
            r.font.size = Pt(size)
            r.font.name = "宋体"
            cn(r)
            if color:
                r.font.color.rgb = color
        return p

    para(f"《{name}》审阅批注版", size=17, bold=True, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=6)
    para("合同风险批注 · 全部处理均在本机完成", size=9,
         color=RGBColor(0x8A, 0x83, 0x77), align=WD_ALIGN_PARAGRAPH.CENTER, space_after=14)
    meta = (f"风险指数 {result.get('score', '—')} · {level_label} · 命中 {len(findings)} 条 · "
            f"{result.get('contract_type', '')}")
    para(meta, size=9.5, color=RGBColor(0x59, 0x63, 0x6F), space_after=4)
    para("使用说明：批注通过 Word 批注功能标注在对应条款段落，请在 Word / WPS 中打开并开启“显示批注”。",
         size=9, color=RGBColor(0x8A, 0x83, 0x77), space_after=4)
    para("风险提示：本批注由本地规则与模型生成，供审阅参考，不替代律师正式意见。",
         size=9, color=RGBColor(0x8A, 0x83, 0x77), space_after=10)
    doc.add_paragraph()

    body = doc.add_paragraph()
    r = body.add_run("合同正文")
    r.bold = True
    r.font.size = Pt(12)
    cn(r)
    body.paragraph_format.space_after = Pt(8)

    paras, compacts = [], []
    for line in re.split(r"\n+", result.get("text") or ""):
        line = line.strip()
        if not line:
            continue
        p = para(line)
        paras.append(p)
        compacts.append(re.sub(r"\s+", "", line))

    anchored, missing, unanchored = 0, 0, []
    for loc in locate_findings(result):
        f = loc["finding"]
        idx = loc["index"]
        if idx < 0 or idx >= len(paras) or not paras[idx].runs:
            unanchored.append(loc)
            continue
        lines = _comment_lines(loc)
        try:
            doc.add_comment(paras[idx].runs, text="\n".join(lines),
                            author="Contract LocalScan", initials="CLS")
        except Exception:
            # 打包环境若 python-docx 批注模板缺失，降级为段落后内联批注块
            p = doc.add_paragraph()
            p.paragraph_format.left_indent = Cm(0.5)
            run = p.add_run(f"  ⚑ {lines[0]}")
            run.font.size = Pt(9)
            run.font.color.rgb = RGBColor(0x8A, 0x6D, 0x3B)
            run.bold = True
            for ln in lines[1:]:
                r2 = p.add_run("\n" + ln)
                r2.font.size = Pt(9)
                r2.font.color.rgb = RGBColor(0x8A, 0x83, 0x77)
        anchored += 1
        if loc["mode"] == "missing":
            missing += 1

    if unanchored:
        doc.add_page_break()
        h = doc.add_paragraph()
        hr = h.add_run("附：未能定位到正文段落的风险批注")
        hr.bold = True
        hr.font.size = Pt(12)
        cn(hr)
        h.paragraph_format.space_after = Pt(8)
        para(f"以下 {len(unanchored)} 条风险无法对应到正文段落（通常是合同正文为空或无可锚定文本），统一列示如下。",
             size=9, color=RGBColor(0x8A, 0x83, 0x77), space_after=8)
        for loc in unanchored:
            f = loc["finding"]
            p = doc.add_paragraph()
            p.paragraph_format.space_after = Pt(8)
            p.paragraph_format.line_spacing = 1.35
            t = p.add_run(f"【{f.get('severity_label', '')}】{f.get('title', '')}\n")
            t.bold = True
            t.font.size = Pt(10.5)
            cn(t)
            detail = f"修改建议：{f.get('advice', '')}"
            if f.get("revision"):
                detail += f"\n建议条款：{f['revision']}"
            d = p.add_run(detail)
            d.font.size = Pt(9.5)
            cn(d)

    summary = doc.add_paragraph()
    text = f"共命中 {len(findings)} 条风险，其中 {anchored} 条已在正文生成批注"
    if missing:
        text += f"（含 {missing} 条缺失条款，已标注建议补充位置）"
    text += "。"
    if unanchored:
        text += f"另有 {len(unanchored)} 条无法定位段落，列于附录。"
    sr = summary.add_run(text)
    sr.font.size = Pt(9)
    sr.font.color.rgb = RGBColor(0x8A, 0x83, 0x77)
    cn(sr)

    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def build_xlsx(result: dict) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    head_fill = PatternFill("solid", fgColor="16202B")
    head_font = Font(color="FFFFFF", bold=True, size=10)
    thin = Side(style="thin", color="D9D3C7")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    def style_sheet(ws, headers, widths):
        ws.append(headers)
        for i, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(i)].width = w
        for cell in ws[1]:
            cell.fill = head_fill
            cell.font = head_font
            cell.alignment = Alignment(vertical="center", horizontal="left")
        ws.row_dimensions[1].height = 24
        ws.freeze_panes = "A2"
        for row in ws.iter_rows(min_row=1, max_row=ws.max_row, max_col=len(headers)):
            for cell in row:
                cell.border = border
                cell.alignment = Alignment(vertical="top", wrap_text=True)

    ws = wb.active
    ws.title = "审阅概览"
    style_sheet(ws, ["项目", "内容"], [24, 70])
    overview = [
        ("文件名称", result["filename"]),
        ("合同名称", result.get("contract_name") or ""),
        ("合同类型", result.get("contract_type")),
        ("风险指数（满分 100）", result["score"]),
        ("风险等级", LEVEL[result["level"]][0]),
        ("高风险 / 中风险 / 低风险", f"{result['counts']['high']} / {result['counts']['medium']} / {result['counts']['low']}"),
    ]
    pa_label, pb_label = _party_labels(result.get("contract_type"))
    overview.extend([
        (pa_label, result.get("party_a")),
        (pb_label, result.get("party_b")),
        ("合同金额", result.get("amount_total")),
        ("审阅时间", datetime.fromtimestamp(result["created_at"] / 1000).strftime("%Y-%m-%d %H:%M")),
        ("抽取方式", _ext_method(result)),
        ("审阅结论", result.get("summary")),
        ("免责声明", "本清单由本地规则与模型自动生成，仅供合同审阅参考，不构成法律意见，亦不替代执业律师的正式审查。"),
    ])
    for row in overview:
        ws.append(row)

    ws2 = wb.create_sheet("关键字段")
    style_sheet(ws2, ["字段", "内容", "来源", "说明"], [22, 60, 14, 45])
    pa_label_x, pb_label_x = _party_labels(result.get("contract_type"))
    label_map_x = {"甲方 / 用人单位": pa_label_x, "乙方 / 劳动者": pb_label_x}
    for f in result["fields"]:
        ws2.append([label_map_x.get(f["label"], f["label"]), f["value"], {"llm": "qwen2.5", "rule": "规则", "none": "未识别"}[f["source"]], f["hint"]])

    ws3 = wb.create_sheet("风险清单")
    style_sheet(ws3, ["序号", "风险等级", "风险标题", "类别", "条款原文", "条款定位",
                      "风险分析", "修改建议", "条款修订建议", "法律依据", "处理方式"],
                [7, 10, 26, 16, 44, 32, 46, 46, 56, 28, 10])
    sev_fill = {
        "高风险": PatternFill("solid", fgColor="A3372F"),
        "中风险": PatternFill("solid", fgColor="B07A2A"),
        "低风险": PatternFill("solid", fgColor="4F6B52"),
    }
    sev_font = Font(color="FFFFFF", bold=True, size=10)
    for i, loc in enumerate(locate_findings(result), 1):
        f = loc["finding"]
        if loc["mode"] == "missing":
            evidence, action = "（缺失条款）合同中未检索到相关约定，建议补充", "补充新增"
        else:
            evidence, action = f["evidence"], "修改条款"
        ws3.append([i, f["severity_label"], f["title"], f["category"], evidence,
                    _location_label(loc), f["analysis"], f["advice"],
                    f.get("revision", ""), "；".join(f"{b['law']}{b['label']}" for b in f["basis"]),
                    action])
        cell = ws3.cell(row=i + 1, column=2)
        if f["severity_label"] in sev_fill:
            cell.fill = sev_fill[f["severity_label"]]
            cell.font = sev_font
            cell.alignment = Alignment(vertical="center", horizontal="center")
    ws3.auto_filter.ref = f"A1:K{max(1, len(result['findings']) + 1)}"

    ws4 = wb.create_sheet("相关法条")
    style_sheet(ws4, ["法律", "条文", "章节", "内容", "匹配度"], [20, 12, 34, 90, 10])
    for a in result.get("statutes", []):
        ws4.append([a["law"], a["label"], a.get("chapter", ""), a["text"], a.get("score", "")])

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()

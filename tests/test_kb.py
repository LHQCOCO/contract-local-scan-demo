"""法规知识库：分片加载、数据完整性、中英文检索与法域路由。"""
from __future__ import annotations

import importlib.util
from collections import Counter
from pathlib import Path

import pytest

from cls.kb import KB, KnowledgeBase, _stem, _tokens, detect_lang


def _load_fetch_module():
    """按文件路径加载脚本，避免为 scripts 目录引入包结构。"""
    path = Path(__file__).resolve().parents[1] / "scripts" / "fetch_statutes.py"
    spec = importlib.util.spec_from_file_location("fetch_statutes", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FETCH = _load_fetch_module()


def test_shards_loaded_and_stats_consistent():
    stats = KB.stats()
    assert stats["total"] == len(KB.articles)
    assert stats["total"] >= 8000
    assert len(stats["laws"]) == len(KB.laws) >= 60
    assert "zh" in stats["languages"] and "en" in stats["languages"]

    juris = {j["id"]: j for j in stats["jurisdictions"]}
    assert juris["CN"]["laws"] >= 55
    assert juris["INTL"]["laws"] >= 4
    assert sum(j["count"] for j in stats["jurisdictions"]) == stats["total"]


def test_articles_have_valid_law_and_unique_id():
    law_ids = {l["id"] for l in KB.laws}
    ids = [a["id"] for a in KB.articles]
    assert len(ids) == len(set(ids)), "存在重复条文 id"
    for art in KB.articles:
        assert art["law_id"] in law_ids
        assert art["text"].strip()
        assert art["id"].startswith(art["law_id"] + "-")
        assert "\ufffd" not in art["text"], f"{art['id']} 含替换字符"


def test_article_numbers_are_contiguous():
    for law in KB.laws:
        if law.get("jurisdiction") != "CN":
            continue
        nos = sorted(a["no"] for a in KB.articles if a["law_id"] == law["id"])
        assert nos == list(range(1, max(nos) + 1)), f"{law['short']} 条文缺号"


def test_chinese_search_hits_expected_laws():
    cases = [
        ("建设工程 竣工验收", "jzf", "建筑法"),
        ("软件开发 知识产权 归属", "mfd", "民法典"),
        ("个人信息 告知 同意", "grxxbhf", "个人信息保护法"),
        ("消费者 七日 无理由退货", "xfzqybhf", "消费者权益保护法"),
        ("保险 理赔 保险人", "bxf", "保险法"),
        ("公司 股权 转让", "gsf", "公司法"),
        ("合同诈骗 印章 贿赂", "xf", "刑法"),
        ("涉外 法律适用", "swmgsgxflsyyf", "涉外民事关系法律适用法"),
    ]
    for query, law_id, short in cases:
        hits = KB.search(query, law_ids=[law_id], top_k=3)
        assert hits, f"「{query}」未命中任何法条"
        assert hits[0]["law"] == short


def test_english_search_hits_international_rules():
    cisg_fm = KB.search("force majeure impediment exemption", law_ids=["cisg"], top_k=3)
    assert cisg_fm and cisg_fm[0]["id"] == "cisg-79"

    cisg_breach = KB.search("fundamental breach avoidance", law_ids=["cisg"], top_k=3)
    labels = {a["id"] for a in cisg_breach}
    assert "cisg-25" in labels

    nyc = KB.search("arbitration award recognition enforcement", law_ids=["nyc"], top_k=3)
    assert nyc and nyc[0]["jurisdiction"] == "INTL"

    incoterms = KB.search("trade term FOB risk transfer", law_ids=["incoterms"], top_k=3)
    assert incoterms and incoterms[0]["id"].startswith("incoterms-FOB")


def test_cross_language_bridge_from_english_query():
    hits = KB.search("intellectual property copyright ownership", law_ids=["zzf"], top_k=3)
    assert hits, "英文知识产权查询未通过桥接命中著作权法"


def test_judicial_interpretations_and_rules_are_searchable():
    """司法解释 / 部门规章入库后，口语化查询应优先命中对应解释而非仅法典。"""
    hits = KB.search("民间借贷利率", top_k=4)
    assert any("民间借贷" in a["law"] for a in hits)

    hits = KB.search("带薪年休假 折算", top_k=4)
    assert any("年休假" in a["law"] for a in hits)


def test_loose_parsed_old_regulations_are_present():
    """以「一、」「（一）」编号的老旧行政法规经兜底解析入库，条文连续。"""
    laws = {l["short"]: l for l in KB.laws}
    for short in ("国务院关于口岸开放的若干规定", "保护海底电缆规定", "国务院关于高级专家退休问题的补充规定"):
        assert short in laws, f"缺少 {short}"
        assert laws[short]["count"] >= 2


def test_browse_query_returns_full_law_in_order():
    """单独检索法律名：按条文顺序返回整部法律，而不是 BM25 命中的零散条文。"""
    browsed = KB.browse_query("劳动合同法")
    assert browsed is not None
    items, total = browsed
    assert total == 98 and len(items) == 98
    assert [a["no"] for a in items] == list(range(1, 99))
    assert all(a["law_id"] == "ldhtf" for a in items)

    # 带书名号/空格也应进入整法浏览
    assert KB.browse_query("《劳动合同法》") is not None
    # 法律名 + 其他关键词仍是普通检索
    assert KB.browse_query("劳动合同法 试用期") is None
    assert KB.browse_query("试用期") is None


def test_free_text_query_ranks_relevant_law_first():
    """口语化自由检索：首条应落在语义对应的法律，而不是仅因字面重合上榜。"""
    ad = KB.search("互联网广告被恶意屏蔽", top_k=14)
    assert ad and ad[0]["law"] == "广告法"
    assert any(a["law"] == "反不正当竞争法" for a in ad)

    labor = KB.search("劳动法 高职院校", top_k=8)
    laws = {a["law"] for a in labor}
    assert {"职业教育法", "就业促进法"} & laws
    assert "民法典" not in laws, "违约损害赔偿类条文不应混入职业教育检索"


def test_detect_lang():
    assert detect_lang("This Agreement shall be governed by the laws of Singapore.") == "en"
    assert detect_lang("本合同自双方签字盖章之日起生效。") == "zh"


def test_route_laws_by_contract_type():
    labor = KB.route_laws("用人单位与劳动者约定试用期", "劳动合同")
    assert labor[0] == "ldhtf"
    assert "ldf" in labor
    assert "cisg" not in labor

    sale = KB.route_laws(
        "Seller shall deliver the goods FOB Shanghai. Any dispute shall be settled by arbitration.",
        "Sales Contract",
    )
    assert "cisg" in sale
    assert "incoterms" in sale
    assert "nyc" in sale or "mlarb" in sale

    software = KB.route_laws("乙方交付软件源代码，著作权归甲方所有。", "服务合同")
    assert "zzf" in software
    assert "cisg" not in software

    domestic = KB.route_laws("因本合同产生的争议由甲方所在地人民法院管辖。", "服务合同")
    assert "nyc" not in domestic
    assert "msssf" in domestic


def test_upicc_bilingual_and_hardship():
    law_ids = {l["id"] for l in KB.laws}
    assert {"upicc", "upicc-en"} <= law_ids

    zh = KB.search("情势变更 艰难情形 重新谈判", law_ids=["upicc"], top_k=3)
    assert zh and {"upicc-6.2.1", "upicc-6.2.2", "upicc-6.2.3"} <= {a["id"] for a in zh}

    en = KB.search("hardship change of circumstances renegotiation", law_ids=["upicc-en"], top_k=3)
    assert en and {"upicc-en-6.2.1", "upicc-en-6.2.2", "upicc-en-6.2.3"} <= {a["id"] for a in en}


def test_new_statutes_are_searchable():
    cases = [
        ("破产 清算 管理人", "qycpf", "企业破产法"),
        ("上市公司 信息披露", "zqf", "证券法"),
        ("土地征收 补偿", "tdglf", "土地管理法"),
        ("工伤保险 社会保险", "shbxf", "社会保险法"),
        ("行政处罚 罚款", "xzcff", "行政处罚法"),
        ("网络安全 等级保护", "wlaqf", "网络安全法"),
        ("报关 关税 海关", "hgf", "海关法"),
        ("快递 寄递", "yzf", "邮政法"),
        ("经营者集中 市场支配", "fldf", "反垄断法"),
        ("排污 污染物", "hjbhf", "环境保护法"),
    ]
    for query, law_id, short in cases:
        hits = KB.search(query, law_ids=[law_id], top_k=3)
        assert hits, f"「{query}」未命中任何法条"
        assert hits[0]["law"] == short


def test_route_laws_covers_new_statutes():
    cases = [
        ("债务人资不抵债，申请破产清算", "破产", "qycpf"),
        ("政府采购项目公开招标", "政府采购", "zfcgf"),
        ("房屋租赁，承租人支付租金", "租赁合同", "fdcglf"),
        ("银行发放贷款，借款人还本付息", "借款合同", "syyhf"),
        ("涉嫌合同犯罪，公安机关侦查", "服务合同", "xsssf"),
        ("快递企业寄递物品", "快递服务合同", "yzf"),
        ("外商投资企业设立", "投资协议", "wstzf"),
        ("受托人管理信托财产", "信托合同", "xtf"),
    ]
    for text, contract_type, law_id in cases:
        assert law_id in KB.route_laws(text, contract_type), f"「{text}」未路由到 {law_id}"


def test_search_for_contract_dedupes_upicc_languages():
    text = (
        "HARDSHIP. If performance becomes excessively burdensome due to a change of circumstances, "
        "the parties shall renegotiate. Any dispute shall be settled by arbitration."
    )
    hits = KB.search_for_contract(text, "Commercial Contract", top_k=8)
    upicc_laws = [a["law_id"] for a in hits if a["law_id"].startswith("upicc")]
    assert len(upicc_laws) <= 1


def test_search_for_contract_respects_top_k_and_is_diverse():
    text = (
        "软件开发服务合同。甲方支付价款，乙方交付软件源代码并保证知识产权归属甲方。"
        "任何一方违约应支付违约金。因不可抗力不能履行的，免除责任。"
        "争议由上海仲裁委员会仲裁。"
    )
    hits = KB.search_for_contract(text, "服务合同", top_k=6)
    assert 0 < len(hits) <= 6
    assert len({a["id"] for a in hits}) == len(hits)
    assert all(0 < a["score"] <= 96 for a in hits)
    assert hits == sorted(hits, key=lambda a: -a["score"])


def test_law_metadata_and_counts_match_articles():
    ids = [law["id"] for law in KB.laws]
    assert len(ids) == len(set(ids)), "法律 id 重复"
    for law in KB.laws:
        for key in ("id", "name", "short", "category", "jurisdiction"):
            assert str(law.get(key, "")).strip(), f"{law.get('id')} 缺少 {key}"
        actual = sum(1 for a in KB.articles if a["law_id"] == law["id"])
        assert law["count"] == actual, f"{law['short']} count={law['count']} 与实际 {actual} 不符"


def test_new_statutes_registered_with_expected_counts():
    expect = {
        "qycpf": ("企业破产法", "商事", 136),
        "zqf": ("证券法", "金融", 226),
        "zfcgf": ("政府采购法", "政府采购", 88),
        "tdglf": ("土地管理法", "土地与房地产", 87),
        "fdcglf": ("城市房地产管理法", "土地与房地产", 73),
        "wlaqf": ("网络安全法", "数据与个人信息", 81),
        "hgf": ("海关法", "外贸与海关", 102),
        "xsssf": ("刑事诉讼法", "程序", 308),
        "aqscf": ("安全生产法", "安全生产", 119),
        "yzf": ("邮政法", "运输", 87),
        "grdzf": ("个人独资企业法", "商事", 48),
        "wstzf": ("外商投资法", "涉外", 42),
    }
    laws = {law["id"]: law for law in KB.laws}
    for law_id, (short, category, count) in expect.items():
        assert law_id in laws, f"缺少法律 {law_id}"
        assert laws[law_id]["short"] == short
        assert laws[law_id]["category"] == category
        assert laws[law_id]["count"] == count


def test_concept_expansion_handles_colloquial_insolvency():
    hits = KB.search("资不抵债", law_ids=["qycpf"], top_k=3)
    assert hits, "口语化「资不抵债」未命中企业破产法"
    assert any("破产" in a["text"] or "重整" in a["text"] for a in hits)


def test_topic_coverage_supplements_secondary_law():
    text = "房屋租赁合同，出租人将房屋交付承租人使用，承租人按月支付租金，租赁期限两年。"
    hits = KB.search_for_contract(text, "租赁合同", top_k=8)
    law_ids = {a["law_id"] for a in hits}
    assert "mfd" in law_ids
    assert "fdcglf" in law_ids, "租赁场景未带出城市房地产管理法"


def test_search_for_contract_covers_multiple_laws_without_monopoly():
    text = "建设工程施工合同，施工单位落实安全生产责任，排查事故隐患，工程竣工验收后支付工程款。"
    hits = KB.search_for_contract(text, "施工合同", top_k=8)
    law_ids = {a["law_id"] for a in hits}
    assert {"jzf", "aqscf"} <= law_ids, f"施工场景覆盖面不足：{law_ids}"
    counts = Counter(a["law_id"] for a in hits)
    assert max(counts.values()) <= 2, f"单一法律占比过高：{counts}"


def test_plain_service_contract_does_not_route_new_statutes():
    text = (
        "软件开发服务合同。甲方支付价款，乙方交付软件源代码并保证知识产权归属甲方。"
        "任何一方违约应支付违约金。争议由甲方所在地人民法院管辖。"
    )
    routed = set(KB.route_laws(text, "服务合同"))
    unrelated = {
        "qycpf", "zqf", "xtf", "syyhf", "tdglf", "fdcglf", "dwmyf", "hgf",
        "wlaqf", "fldf", "shbxf", "aqscf", "hjbhf", "spaqf", "ypglf",
        "sszsgf", "kjf", "ggf", "jgf", "pmf", "myhkf", "yzf", "xsssf",
        "xzssf", "xzcff", "xzxkf", "xzfyf", "gjpcf", "rmtjf", "grdzf",
        "wstzf", "zfcgf",
    }
    assert not (routed & unrelated), f"普通服务合同误路由：{routed & unrelated}"


def test_cn2int_converts_article_numbers():
    assert FETCH.cn2int("十") == 10
    assert FETCH.cn2int("二十") == 20
    assert FETCH.cn2int("一百二十八") == 128
    assert FETCH.cn2int("二百") == 200
    assert FETCH.cn2int("一千零一") == 1001


def test_parse_tolerates_spacing_in_article_number(tmp_path):
    md = tmp_path / "测试法.md"
    md.write_text(
        "# 测试法\n"
        "## 第一编 总则\n"
        "### 第一章 一般规定\n"
        "第一条 第一条正文内容。\n"
        "第二条 第二条正文内容。\n"
        "第三 条 第三条正文内容。\n",
        encoding="utf-8",
    )
    spec = ("测试目录", "测试法.md", "testf", "测试法", "测试", "2026-01-01")
    law, arts = FETCH.parse(md, spec)
    assert [a["no"] for a in arts] == [1, 2, 3]
    assert arts[-1]["label"] == "第三条"
    assert arts[-1]["text"].startswith("第三条正文内容")
    assert "总则" in arts[0]["chapter"] and "一般规定" in arts[0]["chapter"]
    assert law["count"] == 3


def test_parse_keeps_reference_line_inside_current_article(tmp_path):
    md = tmp_path / "测试法.md"
    md.write_text(
        "# 测试法\n"
        "## 第一章 总则\n"
        "第一条 依照本法处理。\n"
        "第五百零九条 引用条款不得单独成条。\n"
        "第二条 正常条文内容。\n",
        encoding="utf-8",
    )
    spec = ("测试目录", "测试法.md", "testf", "测试法", "测试", "2026-01-01")
    law, arts = FETCH.parse(md, spec)
    assert len(arts) == 2
    assert "第五百零九条" in arts[0]["text"]


def test_parse_rejects_empty_article_text(tmp_path):
    md = tmp_path / "测试法.md"
    md.write_text("# 测试法\n第一条 有正文内容。\n第二条\n", encoding="utf-8")
    spec = ("测试目录", "测试法.md", "testf", "测试法", "测试", "2026-01-01")
    with pytest.raises(RuntimeError, match="正文为空"):
        FETCH.parse(md, spec)


def test_search_handles_blank_query_unknown_law_and_zero_top_k():
    assert KB.search("   ") == []
    assert KB.search("违约金", law_ids=["not-a-law"]) == []
    assert KB.search("违约金", top_k=0) == []


def test_search_by_article_label_returns_exact_article():
    query = "第五百七十七条"
    hits = KB.search(query, law_ids=["mfd"], top_k=2)
    assert hits[0]["id"] == "mfd-577"
    assert hits[0]["label"] == query
    assert hits[0]["score"] == 96.0


def test_label_query_returns_only_exact_label_articles():
    query = "第十条"
    hits = KB.search(query, top_k=8)
    assert len(hits) >= 2
    assert all(a["label"] == query for a in hits), "法条号检索应只返回标签完全一致的条文"


def test_label_query_respects_law_filter():
    hits = KB.search("第十条", law_ids=["rmtjf"], top_k=2)
    assert hits[0]["id"] == "rmtjf-10"
    assert hits[0]["label"] == "第十条"


def test_label_query_hits_article_without_bm25_overlap():
    # 民法典第一条正文不含「第一条」，BM25 得分为 0，必须靠标签命中
    hits = KB.search("第一条", law_ids=["mfd"], top_k=1)
    assert hits[0]["id"] == "mfd-1"
    assert hits[0]["label"] == "第一条"


def test_label_query_raw_score_includes_bonus():
    hits = KB.search("第一条", law_ids=["mfd"], top_k=1, raw=True)
    assert hits[0]["raw"] >= 100.0


def test_nonexistent_label_does_not_fabricate_article():
    query = "第二千条"
    hits = KB.search(query, top_k=5)
    assert all(a["label"] != query for a in hits), "不存在的法条号不得伪造结果"


def test_arabic_article_number_is_not_exact_label_lookup():
    # 阿拉伯数字「第577条」不匹配中文条文号正则，退化为普通 BM25 检索
    assert KB.search("第577条", law_ids=["mfd"], top_k=3) == []


def test_partial_label_is_not_exact_lookup():
    hits = KB.search("第五百七十七", law_ids=["mfd"], top_k=3)
    assert hits[0]["label"] != "第五百七十七条"


def test_label_query_supports_thousand_and_zero_digits():
    assert KB.search("第一千二百六十条", top_k=1)[0]["id"] == "mfd-1260"
    assert KB.search("第一千零一条", top_k=1)[0]["id"] == "mfd-1001"


def test_label_query_trailing_whitespace_still_matches():
    assert KB.search("第五百七十七条 ", law_ids=["mfd"], top_k=1)[0]["id"] == "mfd-577"


def test_label_query_internal_space_is_not_exact_lookup():
    hits = KB.search("第 一条", law_ids=["mfd"], top_k=1)
    assert hits[0]["label"] != "第一条"


def test_embedded_label_in_sentence_is_not_exact_lookup():
    hits = KB.search("根据民法典第五百七十七条 违约责任", law_ids=["mfd"], top_k=2)
    assert hits[0]["label"] != "第五百七十七条"


def test_label_query_exact_results_precede_other_matches():
    query = "第一条"
    # 全量法典收录后每部法律均有「第一条」，名额需大于精确命中数才能看到普通命中
    hits = KB.search(query, top_k=1500)
    exact = [a for a in hits if a["label"] == query]
    non = [a for a in hits if a["label"] != query]
    assert exact and non, "应有精确命中也应有普通命中填充剩余名额"
    # 精确标签条文的分数应整体高于普通命中
    assert min(a["score"] for a in exact) > max(a["score"] for a in non)
    assert hits.index(non[0]) > hits.index(exact[-1])


def test_label_query_results_are_cached():
    first = [a["id"] for a in KB.search("第五百七十七条", law_ids=["mfd"], top_k=5)]
    second = [a["id"] for a in KB.search("第五百七十七条", law_ids=["mfd"], top_k=5)]
    assert first == second


def test_label_query_with_multiple_law_filters():
    hits = KB.search("第一条", law_ids=["mfd", "xf"], top_k=10)
    exact = [a for a in hits if a["label"] == "第一条"]
    assert {a["law_id"] for a in exact} == {"mfd", "xf"}
    assert all(a["law_id"] in {"mfd", "xf"} for a in exact)


def test_label_query_empty_law_ids_means_all_laws():
    hits = KB.search("第一条", law_ids=[], top_k=10)
    assert len({a["law_id"] for a in hits}) >= 2


def test_label_query_top_k_capped_by_available():
    hits = KB.search("第一条", top_k=10000)
    assert 0 < len(hits) <= KB.total


def test_label_query_survives_chapter_guard_penalty():
    # mfd-286 章节含「物业服务」，查询不含物业关键词，章节域会施加 0.28 惩罚，
    # 但法条号 +100 加权仍应使其被命中
    hits = KB.search("第二百八十六条", law_ids=["mfd"], top_k=1)
    assert hits[0]["id"] == "mfd-286"


def test_search_for_contract_does_not_use_label_lookup():
    hits = KB.search_for_contract("第一条", "服务合同", top_k=8)
    assert all(a["label"] != "第一条" for a in hits)


def test_label_query_excerpt_is_nonempty():
    hits = KB.search("第一条", law_ids=["mfd"], top_k=1)
    assert hits[0]["excerpt"].strip()


def test_parser_article_regex_matches_thousand_digit():
    import re
    m = FETCH.ART_RE.match("第一千零一条 千位条文。")
    assert m is not None
    digits = re.sub(r"\s", "", m.group(1))
    assert FETCH.cn2int(digits) == 1001


def test_label_query_trailing_punctuation_is_not_exact_lookup():
    # 标点不在正则字符集内，fullmatch 失败，退化为普通检索
    hits = KB.search("第五百七十七条！", law_ids=["mfd"], top_k=1)
    assert hits[0]["label"] != "第五百七十七条"


def test_embedded_citation_does_not_hijack_contract_analysis():
    hits = KB.search_for_contract(
        "乙方违约，应依据民法典第五百七十七条承担违约责任。", "服务合同", top_k=5
    )
    assert hits[0]["label"] != "第五百七十七条"


def test_duplicate_law_ids_in_filter_produce_no_extra_results():
    dup = [a["id"] for a in KB.search("第一条", law_ids=["mfd", "mfd"], top_k=3)]
    single = [a["id"] for a in KB.search("第一条", law_ids=["mfd"], top_k=3)]
    assert dup == single


def test_article_regex_is_line_anchored():
    assert FETCH.ART_RE.match("正文里的第一条 内容") is None
    assert FETCH.ART_RE.match("  第一条 正文".strip()) is not None


def test_cn2int_single_unit_numerals():
    assert FETCH.cn2int("十") == 10
    assert FETCH.cn2int("百") == 100
    assert FETCH.cn2int("千") == 1000


def test_detect_lang_boundaries():
    assert detect_lang("") == "zh"
    assert detect_lang("123 ABC") == "en"
    assert detect_lang("合同 ABC") == "zh"
    assert detect_lang("中文合同") == "zh"


def test_tokens_drop_stopwords_and_single_chars():
    assert _tokens("THE of a 的 和") == []
    tokens = _tokens("The Seller's obligations 合同 的 违约金")
    assert "seller" in tokens and "obligation" in tokens
    assert "违约金" in tokens
    assert "the" not in tokens and "of" not in tokens


def test_stem_normalizes_common_contract_forms():
    assert _stem("parties") == "party"
    assert _stem("obligations") == "obligation"
    assert _stem("deliveries") == "delivery"
    assert _stem("delivering") == "deliver"
    assert _stem("delivered") == "deliver"
    assert _stem("seller's") == "seller"
    assert _stem("bus") == "bus"


def test_snippet_boundaries():
    short = "短文本"
    assert KB.snippet(short, ["短"]) == short
    exact = "甲" * 150
    assert KB.snippet(exact, ["甲"]) == exact

    no_match = "甲" * 200
    out = KB.snippet(no_match, ["不存在"])
    assert out.endswith("…") and not out.startswith("…")
    assert len(out) == 151

    tail_match = "乙" * 150 + "关键词"
    out = KB.snippet(tail_match, ["关键词"])
    assert out.startswith("…") and "关键词" in out


def test_route_laws_empty_input_and_dedup():
    assert KB.route_laws("", "") == ["mfd"]
    ids = KB.route_laws("建设工程招标投标", "施工合同")
    assert len(ids) == len(set(ids))
    assert all(i in KB.law_names for i in ids)


def test_search_for_contract_top_k_and_fallback():
    hits = KB.search_for_contract("本合同自双方签署之日起生效。", "服务合同", top_k=3)
    assert 0 < len(hits) <= 3
    assert hits == sorted(hits, key=lambda a: -a["score"])

    one = KB.search_for_contract(
        "房屋租赁，承租人支付租金，出租人交付房屋。", "租赁合同", top_k=1
    )
    assert len(one) == 1


def test_empty_knowledge_base_is_safe(tmp_path):
    empty = KnowledgeBase(paths=[tmp_path / "missing.json"])
    assert empty.total == 0
    assert empty.stats()["jurisdictions"] == []
    assert empty.stats()["languages"] == []
    assert empty.search("违约金") == []
    assert empty.search_for_contract("合同", "服务合同") == []


def test_clean_normalizes_markdown_noise():
    assert FETCH.clean("**第一条**\u3000内容\u200b") == "第一条 内容"
    assert FETCH.clean("a  b\nc") == "a b c"


def test_cn2int_zero_and_empty():
    assert FETCH.cn2int("") == 0
    assert FETCH.cn2int("零") == 0
    assert FETCH.cn2int("〇") == 0


def test_parse_single_article_law(tmp_path):
    md = tmp_path / "测试法.md"
    md.write_text("# 测试法\n第一条 唯一正文。\n", encoding="utf-8")
    spec = ("测试目录", "测试法.md", "testf", "测试法", "测试", "2026-01-01")
    law, arts = FETCH.parse(md, spec)
    assert law["count"] == 1
    assert len(arts) == 1
    assert arts[0]["id"] == "testf-1"
    assert arts[0]["chapter"] == ""


def test_parse_ignores_non_section_markdown_headings(tmp_path):
    md = tmp_path / "测试法.md"
    md.write_text(
        "# 测试法\n"
        "## 第一章 总则\n"
        "第一条 正文一。\n"
        "## 附则\n"
        "第二条 正文二。\n",
        encoding="utf-8",
    )
    spec = ("测试目录", "测试法.md", "testf", "测试法", "测试", "2026-01-01")
    _, arts = FETCH.parse(md, spec)
    assert [a["label"] for a in arts] == ["第一条", "第二条"]


def test_specs_are_well_formed_and_unique():
    specs = FETCH.SPECS
    assert len(specs) >= 50
    ids = [s[2] for s in specs]
    assert len(ids) == len(set(ids))
    assert len({(s[0], s[1]) for s in specs}) == len(specs)
    assert all(len(s) == 6 and s[1].endswith(".md") for s in specs)
    assert all(i in KB.law_names for i in ids)


def test_intl_law_notes_propagate_to_articles():
    """Incoterms 命中时必须携带 ICC 商标/版权注记（合规要求）。"""
    res = KB.search("Incoterms FOB", top_k=3)
    assert res, "Incoterms 检索应命中"
    assert any("国际商会" in a.get("law_note", "") for a in res)
    # 中国法条不携带注记
    zh = KB.search("违约责任", law_ids=["mfd"], top_k=2)
    assert all("law_note" not in a for a in zh)

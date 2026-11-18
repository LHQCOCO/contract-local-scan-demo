"""本地法规知识库：多法域语料加载 + BM25 + 中英概念桥接检索。"""
from __future__ import annotations

import json
import math
import re
import threading
import time
from collections import Counter
from pathlib import Path

import jieba

from cls.config import RESOURCE_DIR

DATA_DIR = RESOURCE_DIR / "data"
SHARDS = [
    DATA_DIR / "statutes.json",
    # 完整法库按体积切成若干分片（每片 < 10MB），避免仓库里出现超大单文件
    *sorted(DATA_DIR.glob("statutes_extra_*.json")),
    DATA_DIR / "statutes_intl.json",
    DATA_DIR / "statutes_upicc.json",
]

# 合同语境下的概念扩展：让口语化条款也能命中法条术语
CONCEPT_MAP = {
    "违约金": ["违约金", "损失赔偿", "违约责任", "liquidated damages", "penalty"],
    "赔偿": ["赔偿", "损失", "违约责任", "indemnity", "damages", "liability"],
    "解除": ["解除", "终止", "法定解除", "termination", "terminate", "avoidance"],
    "试用期": ["试用期", "劳动合同", "工资", "probation"],
    "竞业": ["竞业限制", "保密", "经济补偿", "non-compete"],
    "保密": ["保密义务", "商业秘密", "竞业限制", "confidentiality", "trade secret"],
    "个人信息": ["个人信息", "告知", "同意", "敏感个人信息", "personal data", "consent"],
    "隐私": ["隐私权", "个人信息", "保密", "privacy"],
    "数据": ["数据安全", "数据处理", "数据出境", "安全保护义务", "data protection"],
    "出境": ["跨境", "境外提供", "数据出境", "安全评估", "cross-border", "transfer"],
    "定金": ["定金", "担保", "标的额", "deposit", "earnest money"],
    "押金": ["押金", "财物", "担保", "security deposit"],
    "格式条款": ["格式条款", "提示说明", "免除责任", "standard terms", "boilerplate"],
    "免责": ["免责条款", "免除责任", "无效", "exemption", "limitation of liability"],
    "不可抗力": ["不可抗力", "免除责任", "通知", "证明", "force majeure", "impediment"],
    "管辖": ["争议解决", "仲裁", "法院", "jurisdiction", "governing law"],
    "仲裁": ["仲裁", "争议解决", "arbitration", "arbitral", "award"],
    "自动续约": ["续订", "期限", "合同终止", "renewal", "automatic renewal"],
    "社保": ["社会保险", "缴纳", "劳动报酬", "social insurance"],
    "工伤": ["工伤", "社会保险", "赔偿责任", "work injury"],
    "调岗": ["变更劳动合同", "协商一致", "书面", "transfer of position"],
    "诈骗": ["合同诈骗", "骗取", "非法占有", "fraud"],
    "发票": ["价款", "支付", "履行", "invoice"],
    "交付": ["交付", "交货", "验收", "delivery", "acceptance"],
    "验收": ["验收", "检验", "交付", "inspection", "acceptance"],
    "保证": ["保证", "担保", "陈述", "warranty", "representation", "guarantee"],
    "知识产权": ["知识产权", "著作权", "专利", "商标", "intellectual property", "copyright", "patent", "trademark"],
    "责任限制": ["责任限制", "免责", "赔偿上限", "limitation of liability", "cap"],
    "违约": ["违约", "违约责任", "损失赔偿", "breach", "damages"],
    "根本违约": ["根本违约", "fundamental breach", "avoidance", "解除合同"],
    "预期违约": ["预期违约", "anticipatory breach"],
    "情势变更": ["情势变更", "艰难情形", "重新谈判", "hardship"],
    "艰难情形": ["艰难情形", "情势变更", "重新谈判", "hardship"],
    "开发": ["技术开发", "开发合同", "技术成果", "委托开发", "合作开发"],
    "软件": ["计算机软件", "软件著作权", "技术开发", "源代码", "software", "source code"],
    "归属": ["归属", "享有", "技术成果", "权利归属", "ownership"],
    "许可": ["许可", "许可使用", "许可合同", "license", "licence"],
    "转让": ["转让", "权利转移", "assignment", "transfer"],
    "诈骗": ["合同诈骗", "诈骗", "非法占有", "欺诈", "fraud"],
    "印章": ["伪造印章", "伪造", "变造", "印章", "forgery"],
    "贿赂": ["贿赂", "回扣", "职务侵占", "非国家工作人员受贿", "bribery", "corruption"],
    "资不抵债": ["破产", "清算", "重整", "管理人", "insolvency"],
    "重整": ["重整", "破产", "清算", "债权人会议"],
    "烂尾": ["商品房", "预售", "房地产", "交付"],
    "土地": ["土地", "出让", "征收", "建设用地", "land"],
    "房地产": ["房地产", "商品房", "不动产", "预售", "real estate"],
    "证券": ["证券", "股票", "债券", "上市公司", "securities"],
    "信托": ["信托", "受托人", "信托财产", "trust"],
    "借款": ["借款", "贷款", "利息", "金融机构", "loan"],
    "借贷": ["借款", "贷款", "民间借贷", "利率", "利息"],
    "利率": ["利率", "利息", "借款", "贷款"],
    "民间借贷": ["借款", "贷款", "利率", "利息", "高利贷"],
    "海关": ["海关", "报关", "关税", "进出口", "customs"],
    "安全生产": ["安全生产", "事故", "隐患", "safety"],
    "环境": ["环境保护", "排污", "污染", "environment"],
    "食品": ["食品安全", "生产经营", "food safety"],
    "药品": ["药品", "医疗器械", "pharmaceutical"],
    "纳税": ["税收", "纳税", "发票", "税务"],
    "广告": ["广告", "虚假宣传", "广告发布", "广告主", "互联网广告", "advertising"],
    "弹窗": ["弹窗", "广告", "干扰", "影响用户"],
    "屏蔽": ["屏蔽", "拦截", "妨碍", "干扰", "技术手段", "不正当竞争", "流量劫持", "插入链接", "目标跳转"],
    "恶意": ["恶意", "妨碍", "干扰", "不正当"],
    "高职院校": ["职业教育", "职业学校", "职业院校", "高等职业学校", "实习", "就业"],
    "高职": ["职业教育", "职业学校", "职业院校", "高等职业学校", "就业"],
    "职业院校": ["职业学校", "职业院校", "职业教育", "实习"],
    "职业教育": ["职业教育", "职业学校", "职业院校", "实习", "培训"],
    "就业": ["就业", "职业介绍", "招聘", "平等就业", "就业促进"],
    "实习": ["实习", "职业学校", "职业教育", "劳动"],
    "拍卖": ["拍卖", "竞买", "auction"],
    "航空": ["航空", "航班", "承运人", "aviation"],
    "邮政": ["邮政", "快递", "寄递", "postal"],
    "独资": ["个人独资", "投资人", "sole proprietorship"],
    "外资": ["外商投资", "外资", "foreign investment"],
}

# 英文条款用语 → 中文法条术语（跨语言检索桥）
EN_BRIDGE = {
    ("indemn", "liabilit"): ["赔偿", "责任", "损失", "liability", "indemnity"],
    ("limit", "liabilit"): ["责任限制", "赔偿上限", "limitation of liability", "cap"],
    ("terminat",): ["解除", "终止", "terminate", "termination"],
    ("force majeure", "impediment"): ["不可抗力", "force majeure", "impediment", "exempt", "exemption"],
    ("confidential", "trade secret"): ["保密", "商业秘密", "confidentiality"],
    ("intellectual property", "copyright", "patent", "trademark"): ["知识产权", "著作权", "专利", "商标"],
    ("arbitrat", "award"): ["仲裁", "arbitration", "arbitral"],
    ("govern", "jurisdiction"): ["管辖", "适用法律", "governing law", "jurisdiction"],
    ("deliver", "delivery"): ["交付", "交货", "delivery"],
    ("acceptance", "inspection"): ["验收", "检验", "acceptance", "inspection"],
    ("payment", "invoice", "price"): ["付款", "价款", "发票", "payment", "invoice"],
    ("liquidated damages", "penalty"): ["违约金", "liquidated damages", "penalty"],
    ("incoterm", "fob", "cif", "ddp", "exw"): ["贸易术语", "交付", "incoterms", "delivery"],
    ("warrant", "representation"): ["保证", "陈述", "warranty", "representation"],
    ("sale of goods", "cisg"): ["货物销售", "买卖合同", "sale of goods"],
    ("hardship",): ["艰难情形", "情势变更", "重新谈判", "hardship"],
}
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_EN_RE = re.compile(r"[A-Za-z][A-Za-z'-]{1,}")
_EN_STOP = {
    "the", "and", "or", "of", "to", "in", "for", "on", "with", "by", "from", "as", "at",
    "is", "are", "be", "been", "being", "was", "were", "shall", "will", "may", "must",
    "that", "this", "these", "those", "such", "any", "all", "each", "other", "its", "it",
    "if", "then", "than", "not", "no", "but", "which", "who", "whom", "their", "there",
    "here", "herein", "hereof", "hereunder", "party", "parties", "contract", "agreement",
    "force", "law", "laws", "court", "case", "time", "person", "goods", "right", "rights",
}
STOP = set("的了和与及或就都而在对其也为以等这那有是不在被将把向从由".replace(" ", ""))

ARTICLE_ID_RE = re.compile(r"^([a-z]+)-(\d+[A-Z]?)$")

# 2021-01-01 民法典施行后被取代的法律及其配套司法解释前缀。
# 名称中包含这些子串的条文默认降权——但若查询词显式点名了该旧法（如「婚姻法 财产分割」）则不降权。
DEPRECATED_PREFIXES: list[tuple[str, str]] = [
    # (法律名称子串, 用户查询可能显式点名该旧法的关键词)
    ("《婚姻法》", "婚姻法"),
    ("《合同法》", "合同法"),
    ("《物权法》", "物权法"),
    ("《民法通则》", "民法通则"),
    ("《民法总则》", "民法总则"),
    ("《继承法》", "继承法"),
    ("《收养法》", "收养法"),
    ("《担保法》", "担保法"),
    ("《侵权责任法》", "侵权责任法"),
    ("《中外合资经营企业法》", "中外合资"),
    ("《中外合作经营企业法》", "中外合作"),
    ("《外资企业法》", "外资企业法"),
]

# 章节域惩罚：合同没有相关语境时，避免「建设工程验收」「知识产权质权」等条文误入
CHAPTER_GUARDS = [
    (re.compile(r"建设工程|施工合同"), re.compile(r"建设|施工|竣工|发包|承包|工程|装修|监理")),
    (re.compile(r"质权|抵押权|留置权|担保物权"), re.compile(r"质押|抵押|留置|担保|出质|保证")),
    (re.compile(r"婚姻家庭|继承"), re.compile(r"婚姻|继承|遗嘱|夫妻|离婚")),
    (re.compile(r"物业服务"), re.compile(r"物业|业主")),
    (re.compile(r"海事|船舶"), re.compile(r"船舶|海事|海运|提单|承运")),
    (re.compile(r"劳动合同|劳动"), re.compile(r"劳动|用人|员工|工资|试用期|竞业|社保|工伤")),
]


def _stem(word: str) -> str:
    """轻量词形归一：只处理合同英语里高频的复数与 -ing/-ed。"""
    w = word.lower().strip("'")
    if w.endswith("'s"):
        w = w[:-2]
    if len(w) > 4 and w.endswith("ies"):
        w = w[:-3] + "y"
    elif len(w) > 3 and w.endswith("es") and not w.endswith("ses"):
        w = w[:-2]
    elif len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        w = w[:-1]
    if len(w) > 6 and w.endswith("ing"):
        w = w[:-3]
    elif len(w) > 5 and w.endswith("ed"):
        w = w[:-2]
    return w


def _tokens(text: str) -> list[str]:
    out: list[str] = []
    for w in _EN_RE.findall(text):
        w = _stem(w)
        if len(w) < 2 or w in _EN_STOP:
            continue
        out.append(w)
    cjk = "".join(_CJK_RE.findall(text))
    if cjk:
        for w in jieba.cut_for_search(cjk):
            w = w.strip()
            if len(w) < 2 or w in STOP:
                continue
            out.append(w)
    return out


def _core_tokens(text: str) -> list[str]:
    """查询侧的粗粒度分词：不做 search 模式的叠加切分，避免「互联网/互联/联网」重复计数。"""
    out: list[str] = []
    for w in _EN_RE.findall(text):
        w = _stem(w)
        if len(w) >= 2 and w not in _EN_STOP:
            out.append(w)
    cjk = "".join(_CJK_RE.findall(text))
    if cjk:
        for w in jieba.cut(cjk):
            w = w.strip()
            if len(w) >= 2 and w not in STOP:
                out.append(w)
    return out


def detect_lang(text: str) -> str:
    cjk = len(_CJK_RE.findall(text))
    en = len(_EN_RE.findall(text))
    return "en" if en > cjk * 1.2 else "zh"


class KnowledgeBase:
    def __init__(self, paths: list[Path] | None = None):
        self.laws: list[dict] = []
        self.articles: list[dict] = []
        for path in (paths or SHARDS):
            path = Path(path)
            if not path.exists():
                continue
            raw = json.loads(path.read_text(encoding="utf-8"))
            for law in raw["laws"]:
                law.setdefault("lang", "zh")
                law.setdefault("jurisdiction", "CN")
                self.laws.append(law)
            for art in raw["articles"]:
                art.setdefault("lang", "zh")
                art.setdefault("jurisdiction", "CN")
                self.articles.append(art)
        self.by_id = {a["id"]: a for a in self.articles}
        self.law_names = {l["id"]: l for l in self.laws}
        # 法律名（全称 / 简称）→ law_id，用于把「劳动法」「广告法」这类查询落回具体法律
        self._by_short: dict[str, list[str]] = {}
        self._aliases: dict[str, list[str]] = {}
        for law in self.laws:
            self._by_short.setdefault(law["short"], []).append(law["id"])
            for key in ("short", "name"):
                alias = (law.get(key) or "").strip()
                if alias.startswith("中华人民共和国"):
                    alias = alias[len("中华人民共和国"):].strip()
                if len(alias) < 2:
                    continue
                ids = self._aliases.setdefault(alias, [])
                if law["id"] not in ids:
                    ids.append(law["id"])
        self._alias_sorted = sorted(self._aliases, key=len, reverse=True)
        # 法权注记（版权/商标声明）透传到该法律的每条检索结果
        for art in self.articles:
            note = self.law_names.get(art["law_id"], {}).get("note")
            if note:
                art["law_note"] = note
        self._tf: list[Counter] = []
        self._lens: list[int] = []
        self._df: Counter = Counter()
        for art in self.articles:
            doc = _tokens(art["text"] + " " + art.get("chapter", "") + " " + art["law"])
            tf = Counter(doc)
            self._tf.append(tf)
            self._lens.append(len(doc))
            for w in tf:
                self._df[w] += 1
        self._avg = sum(self._lens) / max(1, len(self._lens))
        self._n = len(self._tf)
        self._len_sum = sum(self._lens)
        # 自定义包：pack_id → 条文下标；删除用墓碑标记，不压缩主数组（避免重建 6 万条索引）
        self._dead: set[int] = set()
        self._custom_idx: dict[str, list[int]] = {}
        self._lock = threading.Lock()
        self._cache: dict = {}

    def add_custom(self, pack_id: str, articles: list[dict], laws: list[dict]) -> None:
        """增量并入自定义法条：只追加索引并更新 df/avg，不重建主索引。"""
        with self._lock:
            idxs = []
            for law in laws:
                law.setdefault("lang", "zh")
                law.setdefault("short", law.get("name", ""))
                law.setdefault("count", len(articles))
                if law["id"] in self.law_names:
                    continue
                self.laws.append(law)
                self.law_names[law["id"]] = law
                self._by_short.setdefault(law["short"], []).append(law["id"])
                alias = (law.get("name") or "").strip()
                if len(alias) >= 2:
                    ids = self._aliases.setdefault(alias, [])
                    if law["id"] not in ids:
                        ids.append(law["id"])
            for art in articles:
                art.setdefault("lang", "zh")
                if art["id"] in self.by_id:
                    continue
                self.articles.append(art)
                self.by_id[art["id"]] = art
                doc = _tokens(art["text"] + " " + art.get("chapter", "") + " " + art["law"])
                tf = Counter(doc)
                self._tf.append(tf)
                self._lens.append(len(doc))
                self._len_sum += len(doc)
                for w in tf:
                    self._df[w] += 1
                idxs.append(len(self._tf) - 1)
            self._custom_idx[pack_id] = idxs
            self._n = len(self._tf) - len(self._dead)
            self._avg = self._len_sum / max(1, self._n)
            self._alias_sorted = sorted(self._aliases, key=len, reverse=True)
            self._cache.clear()

    def remove_custom(self, pack_id: str) -> bool:
        """墓碑删除自定义法条：下标计入 _dead，检索时跳过，df/n/avg 同步回退。"""
        with self._lock:
            idxs = self._custom_idx.pop(pack_id, None)
            if idxs is None:
                return False
            law_ids: set[str] = set()
            for i in idxs:
                art = self.articles[i]
                self._dead.add(i)
                self.by_id.pop(art["id"], None)
                for w, c in self._tf[i].items():
                    self._df[w] -= c
                    if self._df[w] <= 0:
                        del self._df[w]
                self._len_sum -= self._lens[i]
                law_ids.add(art["law_id"])
            self._n = len(self._tf) - len(self._dead)
            self._avg = self._len_sum / max(1, self._n)
            for lid in law_ids:
                law = self.law_names.pop(lid, None)
                if not law:
                    continue
                if law in self.laws:
                    self.laws.remove(law)
                short = law.get("short")
                if short in self._by_short:
                    self._by_short[short] = [x for x in self._by_short[short] if x != lid]
                    if not self._by_short[short]:
                        del self._by_short[short]
                for key in ("short", "name"):
                    alias = (law.get(key) or "").strip()
                    if alias in self._aliases:
                        self._aliases[alias] = [x for x in self._aliases[alias] if x != lid]
                        if not self._aliases[alias]:
                            del self._aliases[alias]
            self._alias_sorted = sorted(self._aliases, key=len, reverse=True)
            self._cache.clear()
            return True

    @property
    def total(self) -> int:
        return len(self.articles) - len(self._dead)

    def stats(self) -> dict:
        groups = {}
        for law in self.laws:
            key = law.get("jurisdiction", "CN")
            g = groups.setdefault(key, {"id": key, "count": 0, "laws": 0})
            g["count"] += law.get("count", 0)
            g["laws"] += 1
        names = {"CN": "中国法", "INTL": "国际规则"}
        for g in groups.values():
            g["name"] = names.get(g["id"], g["id"])
        return {
            "total": self.total,
            "laws": self.laws,
            "categories": sorted({l["category"] for l in self.laws}),
            "jurisdictions": sorted(groups.values(), key=lambda g: -g["count"]),
            "languages": sorted({l.get("lang", "zh") for l in self.laws}),
        }

    def get(self, ids) -> list[dict]:
        if isinstance(ids, str):
            ids = [ids]
        return [self.by_id[i] for i in ids if i in self.by_id]

    def _detect_laws(self, query: str) -> tuple[list[str], str]:
        """识别查询里出现的法律全称/简称，返回 (law_ids, 去掉法律名后的剩余查询)。"""
        if not self._alias_sorted:
            return [], query
        mask = [False] * len(query)
        ids: list[str] = []
        for alias in self._alias_sorted:
            if alias not in query:
                continue
            start = 0
            while True:
                p = query.find(alias, start)
                if p < 0:
                    break
                start = p + 1
                if any(mask[p:p + len(alias)]):
                    continue
                for k in range(p, p + len(alias)):
                    mask[k] = True
                for lid in self._aliases[alias]:
                    if lid not in ids:
                        ids.append(lid)
        rest = "".join(ch for ch, m in zip(query, mask) if not m)
        return ids, rest

    def _topic_laws(self, query: str) -> list[str]:
        """按查询语义识别应优先展示的法域（只做加权，不做硬过滤）。"""
        out: list[str] = []
        for pattern, shorts in self.QUERY_TOPICS:
            if not re.search(pattern, query, re.I):
                continue
            for short in shorts:
                for lid in self._by_short.get(short, []):
                    if lid not in out:
                        out.append(lid)
        return out

    def _expand(self, query: str) -> list[str]:
        extra = []
        for key, words in CONCEPT_MAP.items():
            if key in query:
                extra.extend(words)
        low = query.lower()
        for keys, words in EN_BRIDGE.items():
            if any(k in low for k in keys):
                extra.extend(words)
        return extra

    BROWSE_CAP = 400  # 整法浏览单次最多返回条数（民法典 1260 条之外的法律均可完整展示）

    def browse_law(self, law_id: str) -> list[dict]:
        """整法浏览：按条文顺序返回一部法律的全部条文。"""
        arts = [a for a in self.articles if a["law_id"] == law_id]
        arts.sort(key=lambda a: a.get("no", 0))
        out = []
        for a in arts[: self.BROWSE_CAP]:
            art = dict(a)
            art["raw"] = 0.0
            art["score"] = 96.0
            art["excerpt"] = self.snippet(art["text"], [])
            out.append(art)
        return out

    def browse_query(self, query: str, law_ids=None) -> tuple[list[dict], int] | None:
        """查询词只是一部法律的全称/简称（可带书名号、空格、标点）时，按整法浏览返回。
        返回 (items, 该法总条数)；非整法查询返回 None。"""
        query = (query or "").strip()
        if not query:
            return None
        named_ids, rest = self._detect_laws(query)
        if not named_ids or re.sub(r"[\s,，。;；、/《》<>()（）:：.\-—'\"'\"]", "", rest):
            return None
        if law_ids:
            named_ids = [i for i in named_ids if i in law_ids]
            if not named_ids:
                return None
        items: list[dict] = []
        total = 0
        for lid in named_ids:
            total += sum(1 for a in self.articles if a["law_id"] == lid)
            items.extend(self.browse_law(lid))
        return items, total

    def search(self, query: str, law_ids=None, top_k: int = 8, raw: bool = False) -> list[dict]:
        query = (query or "").strip()
        if not query or top_k <= 0:
            return []
        cache_key = (query, tuple(law_ids or ()), top_k, raw)
        with self._lock:
            if cache_key in self._cache:
                return self._cache[cache_key]

        # 查询里的法律名（如「劳动法」「广告法」）单独识别，并从关键词里剔除
        named_ids, rest = self._detect_laws(query)
        named = set(named_ids)
        topic = set(self._topic_laws(query))

        q_tokens = _tokens(query + " " + " ".join(self._expand(query)))
        q_terms = Counter(q_tokens)
        # 覆盖率用的粗粒度关键词：避免片段的叠加切分把覆盖率算虚高
        core = [t for t in dict.fromkeys(_core_tokens(rest)) if t]
        # 空格分隔的短语整体命中（比单词命中更能说明相关）
        phrases = [
            p for p in re.split(r"[\s,，。;；、/]+", query)
            if len(p) >= 2 and not self._detect_laws(p)[0]
        ]

        k1, b = 1.45, 0.72
        scores = []
        q_low = query.lower()
        q_lang = detect_lang(query)
        # 形如「第五百七十七条」的输入按法条编号精确检索，即使条文不含查询词也要命中
        label_query = re.fullmatch(r"第[一二三四五六七八九十百千零〇]+条", query)
        for i, tf in enumerate(self._tf):
            if i in self._dead:
                continue  # 已删除的自定义条文（墓碑）
            art = self.articles[i]
            lid = art["law_id"]
            if law_ids and lid not in law_ids:
                continue
            s = 0.0
            dl = self._lens[i] or 1
            for term, qn in q_terms.items():
                f = tf.get(term, 0)
                if not f:
                    continue
                df = self._df.get(term, 0)
                idf = math.log(1 + (self._n - df + 0.5) / (df + 0.5))
                s += qn * idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / self._avg))
            label_exact = bool(label_query) and art["label"] == query
            if s <= 0 and not label_exact:
                continue
            chapter = art.get("chapter", "")
            # 旧法条降权：名称/章节以已废止法律开头的条文，除非查询词显式点名了该旧法
            deprecated = False
            for prefix, key in DEPRECATED_PREFIXES:
                if (prefix in art["law"] or prefix in chapter) and key not in query:
                    deprecated = True
                    break
            if deprecated:
                s *= 0.35
            for chap_re, guard_re in CHAPTER_GUARDS:
                if chap_re.search(chapter) and not guard_re.search(query):
                    s *= 0.28
                    break
            # 中英对照语料优先返回与查询同语言的版本
            if art.get("lang") == q_lang:
                s *= 1.15
            # 覆盖率惩罚 + 统计命中多少个关键词（供泛法条惩罚用）
            matched_core_terms = 0
            if core:
                total_idf = matched_idf = 0.0
                for t in core:
                    df = self._df.get(t, 0)
                    idf = math.log(1 + (self._n - df + 0.5) / (df + 0.5)) if df else math.log(1 + self._n)
                    total_idf += idf
                    if tf.get(t):
                        matched_idf += idf
                        matched_core_terms += 1
                coverage = matched_idf / (total_idf or 1)
                s *= 0.55 + 0.45 * coverage
                # 只要查询有多个 core 词、但这条没全命中，就额外轻压一档——避免只沾部分关键词的泛法条排到前面
                if 1 < matched_core_terms < len(core) and lid not in named and lid not in topic:
                    s *= 0.65
            # 泛法条惩罚：只命中 1 个低信息泛词（如「不可抗力」出现在船舶吨税法）、且未被其他加权覆盖的
            if core and matched_core_terms <= 1 and lid not in named and lid not in topic:
                s *= 0.5
            low = art["text"].lower()
            # 短语整体命中 / 整串命中加权
            for p in phrases:
                if p.lower() in low:
                    s += 5.0
            if len(query) >= 4 and q_low in low:
                s += 8.0
            # 明确点名了某部法律：该法条文优先
            if lid in named:
                s = (s + 6.0) * 1.30
            # 语义相关的法域优先（如「广告屏蔽」→ 广告法 / 反不正当竞争法）
            if lid in topic:
                s *= 1.55
            if label_exact:
                s += 100.0
            elif query in art["label"]:
                s += 6.0
            scores.append((s, i))

        scores.sort(reverse=True)
        top_raw = scores[0][0] if scores else 0
        result = []
        per_law: Counter = Counter()
        cap = max(4, top_k // 3)  # 展示态防止单一法典霸屏
        for s, i in scores:
            # 展示态下跳过明显弱于首条的结果，避免「勉强沾边」占位（但不硬 break，后面可能还有好的）
            if not raw and not label_query and len(result) >= 2 and s < top_raw * 0.45:
                continue
            lid = self.articles[i]["law_id"]
            if not raw and not label_query and per_law[lid] >= cap:
                continue
            art = dict(self.articles[i])
            art["raw"] = round(s, 2)
            if raw:
                art["score"] = round(s, 2)
            else:
                ratio = min(1.0, s / (top_raw + 1e-6))
                art["score"] = round(min(96.0, 96.0 * ratio ** 1.35), 1)
            art["excerpt"] = self.snippet(art["text"], q_tokens)
            per_law[lid] += 1
            result.append(art)
            if len(result) >= top_k:
                break
        with self._lock:
            if len(self._cache) > 128:
                self._cache.clear()
            self._cache[cache_key] = result
        return result

    @staticmethod
    def snippet(text: str, tokens, size: int = 150) -> str:
        if len(text) <= size:
            return text
        pos = -1
        for t in tokens:
            if len(t) >= 2:
                p = text.lower().find(t.lower())
                if p >= 0 and (pos < 0 or p < pos):
                    pos = p
        start = max(0, pos - 40) if pos >= 0 else 0
        return ("…" if start else "") + text[start:start + size].strip() + ("…" if start + size < len(text) else "")

    # 查询语义 → 应优先展示的法域（按法律简称索引，语料调整后仍可用）
    QUERY_TOPICS = [
        (r"广告|宣传|弹窗|屏蔽|拦截|流量|推广|营销|刷单",
         ["广告法", "反不正当竞争法", "消费者权益保护法", "互联网广告管理办法"]),
        (r"高职|院校|职业学校|职业院校|职业教育|实习|毕业生|就业|招聘|劳动|用工|员工|工资|试用期|竞业",
         ["劳动法", "劳动合同法", "就业促进法", "职业教育法", "社会保险法"]),
        (r"个人信息|隐私|数据|人脸|算法|网络安全|出境|跨境传输",
         ["个人信息保护法", "数据安全法", "网络安全法"]),
        (r"股权|股东|出资|章程|公司|董事|监事",
         ["公司法"]),
        (r"房屋|租赁|租金|物业|商品房|不动产|二手房",
         ["民法典", "城市房地产管理法"]),
        (r"建设工程|施工|竣工|工程款|监理|总承包",
         ["建筑法", "民法典", "招标投标法"]),
        (r"借款|贷款|借贷|利息|利率|高利贷|融资|金融",
         ["民法典", "商业银行法"]),
        (r"著作权|专利|商标|知识产权|软件|源代码",
         ["著作权法", "专利法", "商标法", "民法典"]),
        (r"破产|清算|重整|资不抵债",
         ["企业破产法"]),
        (r"食品|药品|医疗器械",
         ["食品安全法", "药品管理法"]),
        (r"税务|发票|偷税|增值税|纳税",
         ["税收征收管理法", "发票管理办法"]),
        (r"仲裁|诉讼|管辖|法院|争议解决",
         ["民事诉讼法", "仲裁法"]),
    ]

    TOPICS = [
        (r"违约金|违约责任|liquidated damages|penalty clause", "违约金 违约责任 损失赔偿"),
        (r"保密|商业秘密|confidential|trade secret", "保密义务 商业秘密"),
        (r"知识产权|著作权|成果归属|intellectual property|copyright|patent|trademark", "知识产权 技术成果 归属"),
        (r"试用期|probation", "试用期 劳动合同 工资"),
        (r"竞业限制|non-compete|noncompete", "竞业限制 经济补偿"),
        (r"解除|终止|terminat|avoidance", "合同解除 法定解除 终止"),
        (r"付款|支付|结算|invoice|payment", "价款 支付 履行 付款"),
        (r"个人信息|人脸识别|personal data|privacy", "个人信息 告知 同意 敏感个人信息"),
        (r"数据|出境|境外|data transfer|cross-border", "数据安全 数据处理 跨境 出境"),
        (r"不可抗力|force majeure|impediment", "不可抗力 免除责任"),
        (r"仲裁|人民法院|管辖|arbitrat|jurisdiction|governing law", "争议解决 仲裁 管辖"),
        (r"定金|押金|deposit|earnest money", "定金 担保 标的额"),
        (r"社会保险|社保|工伤|social insurance", "社会保险 工伤保险 缴纳"),
        (r"交付|交货|验收|delivery|acceptance|inspection", "交付 验收 履行"),
        (r"FOB|CIF|DDP|EXW|Incoterms", "贸易术语 交付 风险转移"),
        (r"责任限制|赔偿上限|limitation of liability|indemnif", "责任限制 赔偿 免责"),
        (r"招标|投标|中标|tender|bid", "招标 投标 中标"),
        (r"股权|股东|章程|shareholder|equity", "公司 股权 股东 出资"),
        (r"建设工程|施工|竣工|construction", "建设工程 施工 验收"),
        (r"消费|产品|质量|consumer|product", "消费者 产品 质量"),
        (r"软件开发|软件许可|源代码|service level|SLA|software", "技术开发 软件 著作权 归属 委托作品 合作开发"),
        (r"保险|保单|insurance", "保险 理赔 保险人 保险金"),
        (r"运输|海运|提单|承运|shipping|bill of lading", "运输 提单 承运人 责任"),
        (r"票据|汇票|本票|支票|bill of exchange", "票据 汇票 背书"),
        (r"诈骗|伪造|印章|贿赂|回扣|职务侵占|fraud|forgery|bribery", "合同诈骗 伪造 贿赂 职务侵占"),
        (r"情势变更|艰难情形|hardship", "情势变更 艰难情形 重新谈判"),
        (r"涉外|跨境|适用法律|foreign|governing law", "涉外 法律适用 国际"),
        (r"破产|清算|重整|bankrupt|insolvency", "破产 清算 重整 管理人"),
        (r"政府采购|government procurement", "政府采购 招标 采购"),
        (r"土地出让|土地征收|建设用地|土地使用权|land", "土地 出让 征收 建设用地"),
        (r"房地产|商品房|不动产|real estate|property", "房地产 商品房 不动产 预售"),
        (r"租赁|出租|承租人|租金|物业|lease|landlord|tenant|premises", "租赁 出租 承租人 租金 物业"),
        (r"证券|股票|债券|上市公司|securities|stock|bond", "证券 发行 信息披露 上市公司"),
        (r"信托|受托人|trust", "信托 受托人 信托财产"),
        (r"贷款|借款|存款|loan|borrow|deposit", "借款 贷款 利息 银行"),
        (r"对外贸易|进出口|关税|海关|customs|import|export", "对外贸易 海关 关税 进出口"),
        (r"网络安全|等级保护|cyber security", "网络安全 等级保护 网络运营者"),
        (r"垄断|经营者集中|monopol", "垄断 经营者集中 市场支配"),
        (r"社会保险|社保|养老|工伤|social insurance", "社会保险 工伤 养老保险"),
        (r"安全生产|安全事故|work safety", "安全生产 事故 隐患排查"),
        (r"环境保护|排污|污染|environment|pollution", "环境保护 排污 污染"),
        (r"食品安全|food safety", "食品安全 生产经营"),
        (r"药品|医疗器械|drug|pharma", "药品 医疗器械 生产经营"),
        (r"偷税|逃税|虚开发票|税务|tax", "税收 纳税 发票 会计"),
        (r"广告|虚假宣传|advertis", "广告 虚假宣传"),
        (r"政府定价|明码标价|价格欺诈|price", "价格 定价 明码标价"),
        (r"拍卖|竞买|auction", "拍卖 竞买"),
        (r"航空|航班|民航|aviation", "航空 运输 承运人"),
        (r"邮政|快递|寄递|courier", "邮政 快递 寄递"),
        (r"刑事|犯罪|侦查|criminal", "犯罪 侦查 刑事责任"),
        (r"行政诉讼|行政案件|administrative litigation", "行政诉讼 行政行为"),
        (r"行政处罚|罚款|administrative penalty", "行政处罚 罚款"),
        (r"行政许可|行政审批|许可证|license", "行政许可 许可"),
        (r"行政复议|administrative review", "行政复议"),
        (r"国家赔偿|state compensation", "国家赔偿"),
        (r"人民调解|调解协议|mediation", "人民调解 调解协议"),
        (r"个人独资|sole proprietorship", "个人独资 投资人"),
        (r"外商投资|外资|foreign investment", "外商投资 外资"),
    ]

    TOPICS_EN = [
        (r"force majeure|impediment|act of god", "force majeure impediment exemption"),
        (r"fundamental breach|terminat|avoidance|cancel", "fundamental breach avoidance termination"),
        (r"deliver|shipment|acceptance|inspection", "delivery shipment acceptance inspection goods"),
        (r"payment|price|invoice|letter of credit", "payment price invoice"),
        (r"arbitrat|tribunal|award|dispute", "arbitration award recognition enforcement"),
        (r"govern|jurisdiction|applicable law", "governing law jurisdiction applicable law"),
        (r"incoterm|FOB|CIF|CFR|DDP|EXW|DAP", "trade terms FOB CIF risk transfer delivery"),
        (r"sale of goods|seller|buyer|goods|merchandise", "sale of goods contract international"),
        (r"confidential|trade secret|non-disclosure", "confidentiality trade secret"),
        (r"intellectual property|copyright|patent|trademark|licen", "intellectual property copyright patent trademark license"),
        (r"indemnif|liabilit|damages", "indemnity limitation of liability damages breach"),
        (r"interim measure|injunction|preservation", "interim measure interim award"),
        (r"hardship|change of circumstances|renegotiat", "hardship change of circumstances renegotiation"),
    ]

    ROUTES = [
        (r"建设工程|施工|总承包|工程款|竣工|监理|construction", ["jzf", "zbtbf"]),
        (r"招标|投标|中标|tender|bid", ["zbtbf"]),
        (r"采购|供货|买卖|设备|货物|出卖|买方|卖方|sale of goods|purchase|supply|seller|buyer|goods|merchandise", ["cpzlf"]),
        (r"技术开发|软件开发|软件|源代码|委托开发|合作开发|技术服务|著作权|专利|商标|intellectual property|copyright|patent|trademark|software", ["zzf", "zlf", "sbf", "fbzdtjzf"]),
        (r"股权|投资|并购|合伙|章程|股东|equity|shareholder|merger", ["gsf", "hhzf"]),
        (r"保单|保险合同|保险金|保险人|保险公司|insurance", ["bxf"]),
        (r"运输|海运|货运|提单|承运|shipping|carrier|bill of lading", ["hsf"]),
        (r"票据|汇票|本票|支票|背书|bill of exchange", ["pjf"]),
        (r"电商|平台|网店|在线|e-commerce|platform", ["dzswf", "dzqzf"]),
        (r"消费|产品|质量|售后|consumer|product quality", ["xfzqybhf", "cpzlf"]),
        (r"仲裁|诉讼|管辖|争议", ["zcf", "msssf"]),
        (r"涉外|境外|外国|跨境|进出口|外贸|国际贸易|适用法律|foreign|international|cross-border|import|export|arbitrat", ["swmgsgxflsyyf", "cisg", "nyc", "mlarb", "upicc"]),
        (r"电子签名|电子合同|电子签章|electronic signature|e-signature", ["dzqzf"]),
        (r"FOB|CIF|CFR|DDP|EXW|Incoterms|freight", ["incoterms", "cisg"]),
        (r"诈骗|伪造|变造|印章|贿赂|回扣|职务侵占|挪用|走私|洗钱|非法集资|fraud|forgery|bribery|corruption|embezzlement|money laundering", ["xf", "fbzdtjzf"]),
        # 商事、金融与财税
        (r"破产|清算|重整|和解协议|管理人|bankrupt|insolvency|liquidation|restructur", ["qycpf"]),
        (r"证券|股票|债券|上市公司|信息披露|securities|stock|bond|listed company", ["zqf", "gsf"]),
        (r"信托|受托人|信托财产|trust|trustee", ["xtf"]),
        (r"贷款|借款|银行借款|存款|金融机构|loan|borrow|deposit|credit facility", ["syyhf", "mfd"]),
        (r"偷税|逃税|虚开发票|税务登记|税收征收|企业所得税|tax evasion", ["sszsgf", "kjf"]),
        # 土地、房地产与工程采购
        (r"土地出让|土地划拨|土地征收|建设用地|宅基地|土地使用权|land use|land transfer|expropriat", ["tdglf", "fdcglf"]),
        (r"房地产|商品房|预售|不动产|房屋买卖|房屋租赁|物业|real estate|property|premises|landlord|tenant", ["fdcglf", "mfd"]),
        (r"政府采购|政府购买|财政性资金|government procurement|public procurement", ["zfcgf", "zbtbf"]),
        # 外贸、海关与运输
        (r"对外贸易|进出口|关税|海关|报关|外贸|foreign trade|import|export|customs|tariff", ["dwmyf", "hgf"]),
        (r"航空|航班|机场|民航|aviation|airline|flight", ["myhkf", "hsf"]),
        (r"邮政|快递|寄递|postal|courier", ["yzf"]),
        # 数据、竞争、劳动与安全环保
        (r"网络安全|等级保护|网络运营者|网络产品|cyber security|network security", ["wlaqf", "sjaqf"]),
        (r"垄断|经营者集中|市场支配地位|卡特尔|monopol|cartel|merger control", ["fldf"]),
        (r"社会保险|社保|养老保险|工伤保险|失业保险|social insurance|pension", ["shbxf", "ldf"]),
        (r"安全生产|生产安全事故|隐患排查|work safety|occupational safety", ["aqscf"]),
        (r"环境保护|排污|污染物|环评|environment|pollution|emission", ["hjbhf"]),
        (r"食品安全|食品生产|食品经营|food safety", ["spaqf"]),
        (r"药品|医疗器械|drug|pharmaceutical|medical device", ["ypglf"]),
        # 市场监管
        (r"广告|虚假宣传|advertis", ["ggf"]),
        (r"政府定价|政府指导价|明码标价|价格欺诈|哄抬价格|price fixing|price gouging", ["jgf"]),
        (r"拍卖|竞买|拍卖人|auction", ["pmf"]),
        # 程序与行政救济
        (r"刑事|犯罪|侦查|公诉|取保候审|criminal|prosecution", ["xsssf", "xf"]),
        (r"行政诉讼|行政案件|行政行为|administrative litigation", ["xzssf"]),
        (r"行政处罚|罚款|吊销许可证|administrative penalty", ["xzcff"]),
        (r"行政许可|行政审批|许可证|administrative license", ["xzxkf"]),
        (r"行政复议|administrative review", ["xzfyf"]),
        (r"国家赔偿|赔偿义务机关|state compensation", ["gjpcf"]),
        (r"人民调解|调解协议|mediation", ["rmtjf"]),
        # 企业形式与外资
        (r"个人独资|独资企业|sole proprietorship", ["grdzf"]),
        (r"外商投资|外资企业|外商|foreign investment|foreign investor", ["wstzf"]),
    ]

    def route_laws(self, text: str, contract_type: str = "") -> list[str]:
        """按合同类型与正文关键词选择法域，避免全库盲搜。"""
        if "劳动" in (contract_type or "") or re.search(r"劳动|用人单位|员工|试用期|工资|social insurance|employee", text):
            law_ids = ["ldhtf", "ldf", "ldzycjbzf"]
        else:
            law_ids = ["mfd"]
        for pattern, ids in self.ROUTES:
            if re.search(pattern, text, re.I):
                law_ids.extend(ids)
        if detect_lang(text) == "en":
            # 英文合同通常涉及法律适用问题；货物买卖与仲裁再补充对应国际规则
            law_ids.append("swmgsgxflsyyf")
            law_ids.append("upicc-en")
            if re.search(r"seller|buyer|goods|merchandise|shipment|carriage|delivery", text, re.I):
                law_ids.extend(["cisg", "incoterms"])
            if re.search(r"arbitrat|tribunal|award|enforcement", text, re.I):
                law_ids.extend(["nyc", "mlarb"])
        seen, out = set(), []
        for i in law_ids:
            if i in seen or i not in self.law_names:
                continue
            seen.add(i)
            out.append(i)
        # 企业导入的法条包体量小，全量并入路由，保证自定义制度始终参与检索
        try:
            from . import custom
            for _pid, _arts, laws in custom.collect_statutes():
                for law in laws:
                    if law["id"] in self.law_names and law["id"] not in seen:
                        seen.add(law["id"])
                        out.append(law["id"])
        except Exception:
            pass  # 自定义包异常不影响内置法域路由
        return out or ["mfd"]

    def search_for_contract(self, text: str, contract_type: str, top_k: int = 8) -> list[dict]:
        """按命中主题分别检索并合并，保证法条覆盖面而非单一主题刷屏。"""
        law_ids = self.route_laws(text, contract_type)
        topics_src = self.TOPICS_EN if detect_lang(text) == "en" else self.TOPICS
        topics = [q for pat, q in topics_src if re.search(pat, text, re.I)]
        if not topics:
            return self.search(contract_type or "合同", law_ids=law_ids, top_k=top_k)
        per = 6
        merged, seen = [], set()
        covered: set[str] = set()
        for q in topics:
            batch = self.search(q, law_ids=law_ids, top_k=per, raw=True)
            for art in batch:
                covered.add(art["law_id"])
                if art["id"] in seen:
                    continue
                seen.add(art["id"])
                merged.append(art)
            # 防止单一法典霸榜：对尚未命中的路由法律补检，但只收该主题下足够相关的条文
            floor = (batch[0]["raw"] * 0.6) if batch else 0
            for lid in law_ids:
                if lid in covered:
                    continue
                for art in self.search(q, law_ids=[lid], top_k=1, raw=True):
                    if art["id"] in seen or art["raw"] < floor:
                        continue
                    seen.add(art["id"])
                    covered.add(lid)
                    merged.append(art)
        best = max((a["score"] for a in merged), default=0) or 1
        for art in merged:
            art["score"] = round(min(96.0, art["score"] / best * 96), 1)
        merged.sort(key=lambda a: -a["score"])
        # 中英文对照条目（如 UNIDROIT 通则）按 group 去重，保留更匹配的一种语言
        picked, per_law, seen_group = [], Counter(), set()
        for art in merged:
            group = art.get("group") or art["id"]
            if group in seen_group:
                continue
            if per_law[art["law_id"]] >= 2:
                continue
            seen_group.add(group)
            per_law[art["law_id"]] += 1
            picked.append(art)
            if len(picked) >= top_k:
                break
        return picked


KB = KnowledgeBase()


def warmup():
    t0 = time.time()
    KB.search("违约金 违约责任", top_k=3)
    return round((time.time() - t0) * 1000)

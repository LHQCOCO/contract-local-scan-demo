"""企业自定义规则与法条包：导入解析、校验、存储、阈值重算。

三层判断模型中的「自定义」侧实现：
- 法条层：导入的条文（法典/地方制度/企业规章）进入知识库，可检索、可被引用为出处；
- 规则层：审查清单与规范句派生的检查点进入规则引擎，与内置规则并列产出 finding；
- 阈值层：导入包检测到的阈值接管 rule_params，来源可溯、停用可回退。
"""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from pathlib import Path

from . import config
from .config import DATA_DIR, DEFAULT_CONFIG

CUSTOM_DIR = DATA_DIR / "custom"
INDEX_PATH = CUSTOM_DIR / "index.json"
_LOCK = threading.Lock()

MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_ITEMS = 500          # 单包条文/规则上限，超出提示拆分
MAX_ARTICLE_CHARS = 4000
MAX_PATTERN_CHARS = 300
MAX_TITLE_CHARS = 80

SEVERITIES = ("high", "medium", "low")
MODES = ("match", "absence", "threshold")
OPS = (">=", "<=", ">", "<")

# 阈值参数的取值范围与类型（与 app.py _RULE_PARAM_SPEC 保持一致；
# 放在这里避免 custom 反向依赖 app 造成循环导入）
RULE_PARAM_SPEC = {
    "penalty_high_pct": (1, 100, float),
    "penalty_recommend_pct": (1, 100, float),
    "penalty_low_pct_perday": (0.001, 5, float),
    "deposit_max_pct": (1, 100, float),
    "probation_salary_pct": (50, 100, float),
    "probation_max_months": (1, 12, int),
    "compete_max_months": (1, 60, int),
    "lease_max_years": (1, 70, int),
    "lpr_multiplier": (1, 10, float),
}

# 阈值语义表：主题词 → (内置参数, 关联内置规则, 认可单位, 方向)
# direction: cap=上限（超过即违规），floor=下限（低于即违规）
_THRESHOLD_TOPICS = [
    {"key": "penalty_high_pct", "rule": "R04", "unit": "%", "dir": "cap",
     "topic": re.compile(r"违约金|违约赔偿"),
     "guard": re.compile(r"每日|日利率|万分之|按日")},
    {"key": "deposit_max_pct", "rule": "R06", "unit": "%", "dir": "cap",
     "topic": re.compile(r"定金"), "guard": None},
    {"key": "probation_salary_pct", "rule": "L02", "unit": "%", "dir": "floor",
     "topic": re.compile(r"试用期.{0,6}(?:工资|薪资|报酬)"), "guard": None},
    {"key": "probation_max_months", "rule": "L01", "unit": "月", "dir": "cap",
     "topic": re.compile(r"试用期"), "guard": re.compile(r"工资|薪资|报酬")},
    {"key": "compete_max_months", "rule": "L03", "unit": "月", "dir": "cap",
     "topic": re.compile(r"竞业(?:限制|禁止)"), "guard": None},
    {"key": "lease_max_years", "rule": "Z01", "unit": "年", "dir": "cap",
     "topic": re.compile(r"租赁(?:期限|期间)"), "guard": None},
]

# 数值口径句：上限型与下限型
_CAP_RE = re.compile(
    r"(?:上限|不得超过|不超过|不应超过|最高(?:为|不超过)|控制在|至多为)\D{0,12}(\d+(?:\.\d+)?)\s*(%|％|个百分点|个月|月|年)")
_FLOOR_RE = re.compile(
    r"(?:下限|不得低于|不低于|至少(?:为|达到)|最少为)\D{0,12}(\d+(?:\.\d+)?)\s*(%|％|个月|月|年)")
# 倒装：「20% 为上限」「80% 以上」
_CAP_REV_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(%|％|个月|月|年)\s*(?:为上限|作为上限|以内|之内)")
_FLOOR_REV_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(%|％)\s*(?:为下限|以上)")

# 规范句式：禁止型（出现即违规）与强制型（缺失即违规）
_FORBID_RE = re.compile(r"(?:不得|严禁|禁止|不应)(?:约定|规定|载明|要求|收取|设定|设置|采用|排除)?")
_MAND_RE = re.compile(r"(?:应当|必须|须|应)(?:约定|载明|包含|具备|明确|规定|书面)")

_ARTICLE_HEAD = re.compile(r"(?m)^[ \t]*(第[一二三四五六七八九十百千零〇两\d]+条)")
_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}

# 嵌套量词（如 (.+)+、(\w*){2,}）是 ReDoS 的典型来源，导入时直接拒绝
_REDOS_RE = re.compile(r"\([^)]*(?:[+*]|\{\d+,\})[^)]*\)\s*(?:[+*]|\{\d+(?:,\d*)?\})")

_ID_SAFE = re.compile(r"[^A-Za-z0-9_-]")


class CustomError(ValueError):
    """导入校验失败：message 面向用户，errors 逐条带行号。"""

    def __init__(self, message: str, errors: list[str] | None = None):
        super().__init__(message)
        self.errors = errors or []


def _cn_to_int(s: str) -> int | None:
    if not s:
        return None
    if s.isdigit():
        return int(s)
    units = {"十": 10, "百": 100, "千": 1000}
    total, num = 0, 0
    for ch in s:
        if ch in _CN_DIGITS:
            num = _CN_DIGITS[ch]
        elif ch in units:
            total += (num or 1) * units[ch]
            num = 0
        else:
            return None
    return total + num or None


def new_pack_id() -> str:
    return "pack_" + uuid.uuid4().hex[:8]


def _safe_id(raw: str) -> str:
    return _ID_SAFE.sub("-", raw or "")[:40]


# ---------- 文档切条 ----------

def split_articles(text: str) -> list[dict]:
    """把制度/法典文档切成条文。优先按「第X条」切；无条号时按空行分段。

    返回 [{label, article_no, text}]；超过 MAX_ITEMS 报错而不是静默截断，
    让用户明确拆分文件。
    """
    heads = list(_ARTICLE_HEAD.finditer(text))
    if not heads:
        parts = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        if not parts:
            raise CustomError("未能从文档中识别出任何条文或段落。")
        if len(parts) > MAX_ITEMS:
            raise CustomError(f"识别出 {len(parts)} 段，超过单包 {MAX_ITEMS} 条上限，请拆分后分批导入。")
        return [{"label": f"条目 {i + 1}", "article_no": str(i + 1),
                 "text": p[:MAX_ARTICLE_CHARS]} for i, p in enumerate(parts)]
    articles = []
    for i, h in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        body = re.sub(r"[ \t]+", " ", text[h.end():end]).strip()
        if not body:
            continue
        no = _cn_to_int(h.group(1)[1:-1])
        articles.append({"label": h.group(1), "article_no": str(no if no is not None else i + 1),
                         "text": body[:MAX_ARTICLE_CHARS]})
    if not articles:
        raise CustomError("文档中存在「第X条」标记但条文内容均为空。")
    if len(articles) > MAX_ITEMS:
        raise CustomError(f"识别出 {len(articles)} 条，超过单包 {MAX_ITEMS} 条上限，请拆分后分批导入。")
    return articles


# ---------- 阈值识别与规范句派生 ----------

def _unit_ok(unit: str, want: str) -> bool:
    if want == "%":
        return unit in ("%", "％")
    if want == "月":
        return unit in ("个月", "月")
    if want == "年":
        return unit == "年"
    return False


def _unit_to_months(value: float, unit: str) -> float:
    return value * 12 if unit == "年" else value


def detect_threshold(text: str) -> dict | None:
    """从条文中识别可接管的内置阈值，返回 {param_key, value, rule_id, dir} 或 None。

    只识别数值口径明确（数字+单位+上限/下限词）的句子，识别不出就交给
    预览页由用户手工勾选，不做猜测。
    """
    compact = re.sub(r"\s+", "", text)
    for t in _THRESHOLD_TOPICS:
        if not t["topic"].search(compact):
            continue
        if t["guard"] and t["guard"].search(compact):
            continue
        cap = _CAP_RE.search(compact) or _CAP_REV_RE.search(compact)
        floor = _FLOOR_RE.search(compact) or _FLOOR_REV_RE.search(compact)
        m = floor if t["dir"] == "floor" else cap
        if not m:
            continue
        try:
            value = float(m.group(1))
        except ValueError:
            continue
        unit = m.group(2)
        if t["unit"] == "月":
            if unit not in ("个月", "月", "年"):
                continue
            value = _unit_to_months(value, unit)
        elif not _unit_ok(unit, t["unit"]):
            continue
        lo, hi, cast = RULE_PARAM_SPEC[t["key"]]
        try:
            value = cast(value)
        except (TypeError, ValueError):
            continue
        if not (lo <= float(value) <= hi):
            continue
        return {"param_key": t["key"], "value": value, "rule_id": t["rule"], "dir": t["dir"]}
    return None


def _norm_pattern_from(article_text: str, verb_re: re.Pattern, span: int = 26) -> str:
    """提取规范句中动词后的核心短语作为合同文本的匹配模式（转义为字面量）。"""
    m = verb_re.search(article_text)
    if not m:
        return ""
    tail = article_text[m.end():].strip()
    tail = re.split(r"[。；;\n]", tail)[0].strip("，,：: ")
    if not (4 <= len(tail) <= span):
        return ""
    return re.escape(tail)


def derive_rules(articles: list[dict], pack_id: str, pack_name: str,
                 article_ids: dict[str, str]) -> tuple[list[dict], dict]:
    """从条文中派生建议规则草稿与阈值接管项。

    article_ids: {article_no: 法条 id}，用于把规则的 basis 指回来源条文。
    返回 (rule_drafts, threshold_overrides)。
    """
    drafts: list[dict] = []
    overrides: dict = {}
    seq = 0
    for art in articles:
        text = art["text"]
        basis = [article_ids[art["article_no"]]] if art["article_no"] in article_ids else []
        th = detect_threshold(text)
        if th:
            seq += 1
            key = th["param_key"]
            overrides[key] = th["value"]
            unit = "%" if RULE_PARAM_SPEC[key][2] is float and "pct" in key else ""
            value_pattern = (rf"(?P<v>\d+(?:\.\d+)?)\s*[%％]" if unit == "%"
                             else r"(?P<v>\d+(?:\.\d+)?)\s*(?:个月|月|年)")
            drafts.append({
                "id": f"C-{pack_id}-{seq:03d}", "title": f"{art['label']} 阈值口径",
                "category": "企业自定义", "severity": "medium", "mode": "threshold",
                "threshold": {"param_key": key, "op": ">" if th["dir"] == "cap" else "<",
                              "value_pattern": value_pattern},
                "related": [th["rule_id"]], "basis": basis,
                "analysis": f"{pack_name}{art['label']}：{_short(text, 80)}",
                "advice": "按导入制度中载明的口径执行；本规则与内置规则并列输出，分歧时请由专业人士复核。",
                "draft": True, "source_pack": pack_id,
            })
            continue
        pat = _norm_pattern_from(text, _FORBID_RE)
        if pat:
            seq += 1
            drafts.append({
                "id": f"C-{pack_id}-{seq:03d}", "title": f"{art['label']} 禁止性约定",
                "category": "企业自定义", "severity": "medium", "mode": "match",
                "patterns": [pat], "related": [], "basis": basis,
                "analysis": f"{pack_name}{art['label']}禁止该约定：{_short(text, 80)}",
                "advice": "删除或改写该条款，使其符合导入制度的要求；如有特例请由专业人士复核。",
                "draft": True, "source_pack": pack_id,
            })
            continue
        pat = _norm_pattern_from(text, _MAND_RE)
        if pat:
            seq += 1
            drafts.append({
                "id": f"C-{pack_id}-{seq:03d}", "title": f"{art['label']} 必备条款",
                "category": "企业自定义", "severity": "medium", "mode": "absence",
                "markers": [pat], "related": [], "basis": basis,
                "analysis": f"{pack_name}{art['label']}要求合同包含该内容：{_short(text, 80)}",
                "advice": "补入相应条款；如本合同类型不适用，可在预览时不启用该规则。",
                "draft": True, "source_pack": pack_id,
            })
    return drafts, overrides


def _short(text: str, width: int) -> str:
    text = re.sub(r"\s+", "", text)
    return text[:width] + ("…" if len(text) > width else "")


# ---------- JSON 清单校验 ----------

def _check_regex(pat: str, where: str, errors: list[str]) -> bool:
    if not isinstance(pat, str) or not pat.strip():
        errors.append(f"{where}：模式不能为空")
        return False
    if len(pat) > MAX_PATTERN_CHARS:
        errors.append(f"{where}：模式超过 {MAX_PATTERN_CHARS} 字上限")
        return False
    if _REDOS_RE.search(pat):
        errors.append(f"{where}：模式含嵌套量词，存在性能风险，请改写")
        return False
    try:
        re.compile(pat)
    except re.error as e:
        errors.append(f"{where}：正则无效（{e}）")
        return False
    return True


def validate_rule(raw: dict, pack_id: str, seq: int, errors: list[str]) -> dict | None:
    """校验一条 JSON 导入的规则，返回规范化结果；失败记录 errors 并返回 None。"""
    where = f"第 {seq} 条规则"
    if not isinstance(raw, dict):
        errors.append(f"{where}：必须是对象")
        return None
    title = raw.get("title")
    if not isinstance(title, str) or not (2 <= len(title.strip()) <= MAX_TITLE_CHARS):
        errors.append(f"{where}：title 需为 2–{MAX_TITLE_CHARS} 字")
        return None
    severity = raw.get("severity", "medium")
    if severity not in SEVERITIES:
        errors.append(f"{where}：severity 只能是 {'/'.join(SEVERITIES)}")
        return None
    mode = raw.get("mode")
    if mode not in MODES:
        errors.append(f"{where}：mode 只能是 {'/'.join(MODES)}")
        return None

    rule = {
        "id": f"C-{pack_id}-{seq:03d}", "title": title.strip(),
        "category": "企业自定义", "severity": severity, "mode": mode,
        "analysis": str(raw.get("analysis", ""))[:500],
        "advice": str(raw.get("advice", ""))[:500],
        "related": [], "basis": [], "source_pack": pack_id,
    }
    ok = True
    if mode == "match":
        pats = raw.get("patterns")
        if not isinstance(pats, list) or not pats or len(pats) > 10:
            errors.append(f"{where}：match 模式需要 1–10 个 patterns")
            ok = False
        else:
            for i, p in enumerate(pats):
                ok = _check_regex(p, f"{where} patterns[{i}]", errors) and ok
            rule["patterns"] = [p for p in pats if isinstance(p, str)]
    elif mode == "absence":
        markers = raw.get("markers")
        if not isinstance(markers, list) or not markers or len(markers) > 10:
            errors.append(f"{where}：absence 模式需要 1–10 个 markers")
            ok = False
        else:
            for i, p in enumerate(markers):
                ok = _check_regex(p, f"{where} markers[{i}]", errors) and ok
            rule["markers"] = [p for p in markers if isinstance(p, str)]
    else:  # threshold
        th = raw.get("threshold")
        if not isinstance(th, dict):
            errors.append(f"{where}：threshold 模式需要 threshold 对象")
            ok = False
        else:
            key = th.get("param_key", "")
            if not (isinstance(key, str) and (key in RULE_PARAM_SPEC or
                    re.fullmatch(r"custom:[a-z_][a-z0-9_]{0,30}", key))):
                errors.append(f"{where}：param_key 须为内置阈值参数或 custom:<小写命名>")
                ok = False
            if th.get("op") not in OPS:
                errors.append(f"{where}：op 只能是 {'/'.join(OPS)}")
                ok = False
            vp = th.get("value_pattern", "")
            if not (isinstance(vp, str) and "(?P<v>" in vp):
                errors.append(f"{where}：value_pattern 需含命名组 (?P<v>…)")
                ok = False
            elif not _check_regex(vp, f"{where} value_pattern", errors):
                ok = False
            if key.startswith("custom:"):
                try:
                    float(th.get("value"))
                except (TypeError, ValueError):
                    errors.append(f"{where}：custom: 参数须在 threshold.value 内嵌数值")
                    ok = False
            if ok:
                rule["threshold"] = {"param_key": key, "op": th["op"], "value_pattern": vp}
                if "value" in th:
                    rule["threshold"]["value"] = float(th["value"])
    for fld, pat, limit in (("related", r"[A-Z]{1,3}\d{2}", 10),
                            ("basis", r"[\w-]{2,60}", 20)):
        v = raw.get(fld)
        if v is None:
            continue
        if not isinstance(v, list) or len(v) > limit or not all(
                isinstance(x, str) and re.fullmatch(pat, x) for x in v):
            errors.append(f"{where}：{fld} 格式不正确")
            ok = False
        else:
            rule[fld] = list(v)
    only = raw.get("only_types")
    if only is not None:
        if isinstance(only, list) and all(isinstance(x, str) and 2 <= len(x) <= 20 for x in only):
            rule["only_types"] = only[:10]
        else:
            errors.append(f"{where}：only_types 需为 2–20 字的类型名列表")
            ok = False
    return rule if ok else None


def validate_statute(raw: dict, pack_id: str, seq: int, jurisdiction: str,
                     law_name: str, errors: list[str]) -> dict | None:
    """校验一条 JSON 导入的法条。"""
    where = f"第 {seq} 条法条"
    if not isinstance(raw, dict):
        errors.append(f"{where}：必须是对象")
        return None
    text = str(raw.get("text", "")).strip()
    if not (5 <= len(text) <= MAX_ARTICLE_CHARS):
        errors.append(f"{where}：text 需为 5–{MAX_ARTICLE_CHARS} 字")
        return None
    label = str(raw.get("label", "")).strip() or f"第{seq}条"
    article_no = str(raw.get("article_no", seq)).strip()[:20]
    law = str(raw.get("law", law_name)).strip() or law_name
    return {
        "id": f"{jurisdiction.lower()}-{pack_id}-{seq}",
        "law_id": f"{jurisdiction.lower()}-{pack_id}",
        "law": law[:MAX_TITLE_CHARS], "label": label[:30], "article_no": article_no,
        "text": text, "chapter": str(raw.get("chapter", ""))[:60],
        "jurisdiction": jurisdiction, "source_pack": pack_id,
    }


# ---------- 存储 ----------

def _read_index() -> list[dict]:
    if not INDEX_PATH.exists():
        return []
    try:
        data = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _write_index(packs: list[dict]) -> None:
    CUSTOM_DIR.mkdir(parents=True, exist_ok=True)
    tmp = INDEX_PATH.with_name("index.json.tmp")
    tmp.write_text(json.dumps(packs, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(INDEX_PATH)


def _pack_path(pack_id: str) -> Path:
    return CUSTOM_DIR / f"{_safe_id(pack_id)}.json"


def list_packs() -> list[dict]:
    """索引摘要列表（不含条文/规则正文）。"""
    with _LOCK:
        return [dict(p) for p in _read_index()]


def get_pack(pack_id: str) -> dict | None:
    path = _pack_path(pack_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and data.get("id") == pack_id else None
    except Exception:
        return None


def put_pack(pack: dict) -> None:
    """整包落盘并更新索引（原子写）。"""
    summary = {k: pack[k] for k in
               ("id", "name", "kind", "jurisdiction", "enabled", "imported_at",
                "source_file") if k in pack}
    summary["statutes"] = len(pack.get("statutes") or [])
    summary["rules"] = len(pack.get("rules") or [])
    summary["threshold_overrides"] = dict(pack.get("threshold_overrides") or {})
    with _LOCK:
        CUSTOM_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _pack_path(pack["id"]).with_suffix(".json.tmp")
        tmp.write_text(json.dumps(pack, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(_pack_path(pack["id"]))
        idx = [p for p in _read_index() if p["id"] != pack["id"]]
        idx.append(summary)
        _write_index(idx)


def remove_pack(pack_id: str) -> dict | None:
    with _LOCK:
        pack = get_pack(pack_id)
        try:
            _pack_path(pack_id).unlink()
        except OSError:
            pass
        _write_index([p for p in _read_index() if p["id"] != pack_id])
        return pack


def set_enabled(pack_id: str, enabled: bool) -> dict | None:
    with _LOCK:
        pack = get_pack(pack_id)
        if not pack:
            return None
        pack["enabled"] = bool(enabled)
        CUSTOM_DIR.mkdir(parents=True, exist_ok=True)
        tmp = _pack_path(pack_id).with_suffix(".json.tmp")
        tmp.write_text(json.dumps(pack, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(_pack_path(pack_id))
        idx = _read_index()
        for p in idx:
            if p["id"] == pack_id:
                p["enabled"] = bool(enabled)
        _write_index(idx)
        return pack


def enabled_packs() -> list[dict]:
    """启用的完整包列表，按导入时间排序（后导入的阈值覆盖先前的）。"""
    with _LOCK:
        ids = [p["id"] for p in _read_index() if p.get("enabled")]
    packs = [p for p in (get_pack(i) for i in ids) if p]
    return sorted(packs, key=lambda p: p.get("imported_at", 0))


# ---------- 与三套体系的汇聚 ----------

def collect_rules() -> list[dict]:
    """所有启用包的规则（附包名，供 finding 展示来源）。"""
    out = []
    for pack in enabled_packs():
        for r in pack.get("rules") or []:
            if r.get("draft"):
                continue  # 草稿未被勾选确认时不参与评估
            r = dict(r, pack_name=pack["name"])
            out.append(r)
    return out


def collect_statutes() -> list[tuple[str, list[dict], list[dict]]]:
    """[(pack_id, articles, laws)]：启用包的知识库入库素材。"""
    out = []
    for pack in enabled_packs():
        statutes = pack.get("statutes") or []
        if not statutes:
            continue
        law = {"id": statutes[0]["law_id"], "name": pack["name"],
               "jurisdiction": pack.get("jurisdiction", "ENT"),
               "source_pack": pack["id"]}
        out.append((pack["id"], statutes, [law]))
    return out


def recompute_params() -> dict:
    """按「默认值 + 启用包覆盖（后导入者优先）」重算 rule_params 并落盘。"""
    overrides: dict = {}
    for pack in enabled_packs():
        for key, value in (pack.get("threshold_overrides") or {}).items():
            if key not in RULE_PARAM_SPEC:
                continue
            lo, hi, cast = RULE_PARAM_SPEC[key]
            try:
                num = cast(float(value))
            except (TypeError, ValueError):
                continue
            if lo <= float(num) <= hi:
                overrides[key] = {"value": num, "pack_id": pack["id"], "pack_name": pack["name"]}
    effective = dict(DEFAULT_CONFIG["rule_params"])
    effective.update({k: o["value"] for k, o in overrides.items()})
    cfg = config.load_config()
    # 与现状一致时不重复落盘（启动恢复路径会调用本函数）
    if cfg.get("rule_params") != effective or cfg.get("threshold_overrides") != overrides:
        config.save_rule_params(effective, overrides)
    return effective


def pack_threshold_diff(overrides: dict) -> list[dict]:
    """把 {param_key: value} 转成预览用 diff：当前值 → 新值。"""
    cfg = config.load_config()
    current = cfg.get("rule_params") or DEFAULT_CONFIG["rule_params"]
    existing = cfg.get("threshold_overrides") or {}
    diff = []
    for key, value in (overrides or {}).items():
        if key not in RULE_PARAM_SPEC:
            continue
        diff.append({"param_key": key, "from": current.get(key), "to": value,
                     "replaces": (existing.get(key) or {}).get("pack_name", "")})
    return diff


def now_ts() -> float:
    return time.time()

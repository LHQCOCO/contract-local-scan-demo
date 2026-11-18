"""Contract LocalScan · 本地合同智能审阅服务。

启动：python3 app.py  （默认 http://127.0.0.1:8765）
所有解析、推理、检索、存储均在本机完成。
"""
from __future__ import annotations

import copy
import http.server
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
from urllib.parse import urlparse
import uuid
import webbrowser
from datetime import datetime
from pathlib import Path

# 开发实例与打包实例用不同标识：两者同时运行时，若标识相同，后启动的一方会把
# 对方误判为「已有实例」而直接退出，导致打包版在开发服务开着时永远打不开。
APP_MARKER = "contract-local-scan" if getattr(sys, "frozen", False) else "contract-local-scan-dev"


def _probe_instance(preferred: int = 8765, span: int = 20) -> int | None:
    """探测候选端口上是否已有本应用实例，返回其端口号。

    必须在 ``from cls import ...`` 之前调用：仅加载知识库就要 10 秒以上，
    放到导入之后会失去秒开的意义。本地回环未监听时立即失败，不拖慢启动。
    """
    for port in range(preferred, preferred + span):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=0.4) as r:
                data = json.loads(r.read())
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("app") == APP_MARKER:
            return port
    return None


def _acquire_lock(path: Path) -> tuple[str, object]:
    """抢占单实例文件锁，返回 (状态, 文件对象)，状态为 ok / busy / unsupported。

    端口探测只能发现「已开始监听」的实例。启动预热期（约 10 秒）端口尚未监听，
    这个窗口内重复双击会拉起第二个进程，最终两个实例分别占用 8765 和 8766。
    文件锁正好兜住这段窗口：进程退出或崩溃时 fd 关闭，锁自动释放。
    """
    try:
        import fcntl
    except ImportError:  # Windows 无 fcntl，退化为仅靠端口探测
        return "unsupported", None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(path, "a+")
    except OSError:
        # 锁文件建不了（目录只读、权限受限、卷异常等）时不能当成「已有实例在跑」，
        # 否则用户会卡在 30 秒空转后打不开程序。退化为仅端口探测。
        return "unsupported", None
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return "ok", fh
    except BlockingIOError:  # 仅此一种情况表示确实被另一个进程持有
        fh.close()
        return "busy", None
    except OSError:
        fh.close()
        return "unsupported", None


PORT_PREFERRED = 8765
PORT_SPAN = 20
READY = threading.Event()  # 知识库预热完成前为 False，启动页据此轮询
SERVING = False  # 仅在命令行启动时置 True；测试导入时不走启动页


def _port_available(port: int) -> bool:
    """判断端口是否真的可以独占。绑定成功后立即释放，实际占用由服务进程完成。

    macOS 下 SO_REUSEADDR 允许两个 socket 绑同一端口，仅靠 bind 试探会把
    「已被另一个实例占用」误判为空闲（开发实例与打包实例同时运行时就会出现，
    最终两个进程抢同一端口）。绑定试探通过后，再探一次 /api/ping 确认无人应答。
    """
    with socket.socket() as s:
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
    return not _has_ping_listener(port)


def _has_ping_listener(port: int) -> bool:
    """端口上是否已有本系列应用（任一实例）在应答。"""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=0.3) as r:
            data = json.loads(r.read())
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and "app" in data


def _ephemeral_port() -> int:
    """向系统申请一个临时端口号。"""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---- 预启动页：在重模块导入之前顶班的最小 HTTP 服务 ----
# Flask、PDF/OCR、分词与知识库的导入合计约 10 秒。如果等导入完再监听端口、再开浏览器，
# 用户只能对着一颗跳动的 Dock 图标干等。这里用标准库先占住端口返回启动页，
# 浏览器 1 秒内就有画面；导入完成后关闭它，由正式服务接管同一端口。
_PREFLIGHT = None
_BROWSER_OPENED = False


class _PreflightHandler(http.server.BaseHTTPRequestHandler):
    def _reply(self, code: int, ctype: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler 约定命名
        if self.path in ("/", "/index.html"):
            self._reply(200, "text/html; charset=utf-8", _splash_bytes())
        elif self.path.startswith("/api/ping"):
            # ready 恒为 False：正式服务接管前，页面保持在启动页轮询
            body = json.dumps({"app": APP_MARKER, "ready": False}).encode()
            self._reply(200, "application/json", body)
        else:
            self._reply(404, "text/plain; charset=utf-8", b"starting")

    def log_message(self, *args):  # 静默，避免污染打包版的日志
        pass


def _splash_bytes() -> bytes:
    from cls import config as _config
    root = _config.RESOURCE_DIR if _config.FROZEN else Path(__file__).resolve().parent
    return (root / "templates" / "splash.html").read_bytes()


def _start_preflight(port: int) -> bool:
    global _PREFLIGHT, _BROWSER_OPENED
    try:
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), _PreflightHandler)
    except OSError:
        return False
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _PREFLIGHT = (srv, port)
    if getattr(sys, "frozen", False):
        webbrowser.open(f"http://127.0.0.1:{port}")
        _BROWSER_OPENED = True
    return True


def _stop_preflight() -> None:
    global _PREFLIGHT
    srv, _PREFLIGHT = _PREFLIGHT, None
    if srv is None:
        return
    try:
        srv[0].shutdown()
        srv[0].server_close()
    except Exception:  # noqa: BLE001 - 让出端口失败不应阻塞正式服务
        pass


def _candidate_ports(preferred: int = PORT_PREFERRED, span: int = PORT_SPAN):
    """按顺序给出候选端口。首选区间全被占用时改为向系统申请临时端口，
    保证服务一定能绑上，不会因端口耗尽而启动失败。"""
    for port in range(preferred, preferred + span):
        yield port
    yield _ephemeral_port()


def _wait_ping(port: int, timeout: float = 20.0, alive=None) -> bool:
    """轮询 /api/ping，确认该端口上响应的是本应用，而非其他程序。
    alive 为服务线程，线程提前退出说明绑定失败，立即返回而不空等。"""
    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{port}/api/ping"
    while time.time() < deadline:
        if alive is not None and not alive.is_alive():
            return False
        try:
            with urllib.request.urlopen(url, timeout=0.5) as r:
                data = json.loads(r.read())
            if isinstance(data, dict) and data.get("app") == APP_MARKER:
                return True
        except (OSError, ValueError):
            pass
        time.sleep(0.2)
    return False


if __name__ == "__main__":
    # 单实例保护（一）：已有实例在监听，直接打开现有页面并退出，避免重复实例与端口漂移。
    # 这段刻意置于业务模块导入之前（pytest 导入本模块时 __name__ 不为 "__main__"，不会触发）。
    _running = _probe_instance()
    if _running is not None:
        print(f"已有实例在运行，直接打开 http://127.0.0.1:{_running}")
        if getattr(sys, "frozen", False):  # 打包版才有必要唤起浏览器，命令行模式打印提示即可
            webbrowser.open(f"http://127.0.0.1:{_running}/")
        sys.exit(0)

    # 单实例保护（二）：端口未监听，但可能另一个进程正处于启动预热期。用文件锁兜住。
    from cls.config import DATA_DIR
    _lock_state, _instance_lock = _acquire_lock(DATA_DIR / "instance.lock")
    if _lock_state == "busy":
        print("本程序正在启动中，就绪后会自动打开页面，请稍候……")
        for _ in range(60):  # 最多等待 30 秒
            time.sleep(0.5)
            _running = _probe_instance()
            if _running is not None:
                # 不调用 webbrowser.open：主实例预热完成后会自行打开页面，
                # 此处再打开一次会产生两个重复标签页。
                print(f"已有实例就绪，页面将由该实例自动打开（http://127.0.0.1:{_running}）")
                sys.exit(0)
        print("等待已有实例超时，请稍后重新打开。")
        sys.exit(0)

    # 秒开：先用标准库把一个极轻的启动页顶到端口上，浏览器立刻打开；
    # 下面的重依赖导入完成后，再关闭预启动页，由正式服务接管同一端口，
    # 启动页会在 /api/ping 变为 ready 后自动刷新成工作台。
    _preflight_port = None
    for _cand in _candidate_ports():
        if not _port_available(_cand):
            continue
        if _start_preflight(_cand):
            _preflight_port = _cand
            print(f"启动页已就绪 http://127.0.0.1:{_cand}（后台继续加载引擎）")
            break

from flask import Flask, jsonify, render_template, request, send_file

from cls import config, custom, extraction, llm, netguard, parsing, reporting, risks, store
from cls.kb import KB, warmup

netguard.install()  # 运行期外联自检：记录并拦截任何指向本机之外的连接

if config.FROZEN:
    app = Flask(__name__,
                static_folder=str(config.RESOURCE_DIR / "static"),
                template_folder=str(config.RESOURCE_DIR / "templates"))
else:
    app = Flask(__name__, static_folder="static", template_folder="templates")
# 8 份 × 25 MB + 表单余量
app.config["MAX_CONTENT_LENGTH"] = 205 * 1024 * 1024
MAX_FILES = 8  # 单次审阅最多 8 份合同（前端 addFiles 同步限制，此处为后端兜底）

# 启动页与工作台已同源同端口，保留 CORS 头是为了兼容从其他 localhost 端口
# 打开的页面（例如用户手动改过地址）在调试时的请求。
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _is_local_origin(origin: str) -> bool:
    """按主机名精确判断是否本机来源，避免 localhost.evil.com 这类前缀伪装。"""
    return (urlparse(origin).hostname or "").lower() in LOCAL_HOSTS


@app.after_request
def _cors_localhost(resp):
    origin = request.headers.get("Origin", "")
    if origin and _is_local_origin(origin):
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


@app.after_request
def _cache_policy(resp):
    """按内容类型分配缓存策略。

    - HTML：必须每次回源，否则升级后用户看到的还是旧文案（版本号只描述 js/css）。
    - 静态资源：URL 带 mtime 版本号，可放心长缓存，升级后 URL 变化自动失效。
    - API：一律不落盘，合同正文与授权状态不应停留在浏览器缓存里。
    """
    path = request.path
    if path.startswith("/static/"):
        # Flask 静态响应默认带 no-cache；URL 已含 mtime 版本号，覆盖为长缓存
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif "Cache-Control" in resp.headers:
        return resp
    elif path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    elif resp.mimetype == "text/html":
        resp.headers["Cache-Control"] = "no-cache"
    return resp

VERSION = "1.1.2"


def _consented() -> bool:
    """是否已获得明确的本地部署授权。未询问（None）与拒绝（False）都不算。"""
    return config.load_config().get("deploy_consent") is True


def _json_dict():
    """取 JSON 请求体；不是对象时返回 None，由调用方返回 400 而不是 500。"""
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else None


@app.before_request
def _reject_cross_origin_writes():
    """写操作只接受本机页面发起，挡住其他网页对本机服务的跨站表单提交。

    非浏览器客户端（curl、测试、命令行）通常不带 Origin，保持放行。
    """
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        origin = request.headers.get("Origin", "")
        if origin and not _is_local_origin(origin):
            return jsonify({"error": "仅接受本机页面发起的操作"}), 403
    return None


def _asset_version() -> str:
    """静态资源版本号：取 app.js/app.css 的修改时间，确保升级后浏览器不会继续用旧脚本。"""
    try:
        base = Path(app.static_folder)
        mt = max((base / n).stat().st_mtime for n in ("app.js", "app.css"))
        return str(int(mt))
    except (OSError, TypeError):
        return VERSION
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()
JOB_TTL_MS = 30 * 60 * 1000  # 任务结果在内存中保留 30 分钟，避免长期运行内存膨胀


def _now() -> int:
    return int(time.time() * 1000)


def _purge_jobs() -> None:
    """清理已完成/失败超过 TTL 的任务，避免结果（含合同全文）长期驻留内存。"""
    now = _now()
    with JOBS_LOCK:
        stale = [j for j, jb in JOBS.items()
                 if jb.get("finished_at") and now - jb["finished_at"] > JOB_TTL_MS]
        for j in stale:
            del JOBS[j]


def _run_pipeline(filename: str, data: bytes, use_llm: bool, on_step=None) -> dict:
    if on_step:
        on_step("parse", "解析文档结构")
    doc = parsing.parse(filename, data)
    if on_step:
        on_step("extract", "抽取关键条款")
    llm_res, llm_error = None, ""
    if use_llm:
        st = llm.status()
        _ok, _why = config.model_supported(st["model"])
        if not _ok:
            # 内存不足的模型一旦加载，大部分计算会回退到 CPU，长时间无响应并拖垮整机。
            # 旧配置里已选中的大模型在这里拦住，直接按规则模式出结果。
            llm_error = _why
        elif st["online"] and st["model_ready"]:
            try:
                llm_res = llm.extract_fields(doc["text"])
            except llm.LocalModelError as e:
                llm_error = str(e)
        else:
            llm_error = st.get("error") or "本地模型不可用"
    fields, values = extraction.extract(doc["text"], llm_res)
    # 合同类型二阶段判定：关键词给候选，多候选且模型开启时交给本地模型消歧（只在候选里选）
    candidates = extraction.detect_type_candidates(doc["text"])
    rule_ctype = extraction.detect_type(doc["text"])
    llm_ctype = values.get("contract_type", "") or ""

    # 类型名归一化：LLM 字段抽取可能自创不在候选里的类型名（如 "跨境技术许可与服务协议"），
    # 此时优先用规则速判的候选首选，保证 only_types 规则能正常命中。
    ctype = rule_ctype  # 先取规则速判
    if llm_ctype and llm_ctype in candidates:
        # LLM 给的类型名在候选里 → 采用（比关键词更精准）
        ctype = llm_ctype
    elif llm_ctype and len(candidates) == 1:
        # LLM 自创了名字但关键词只有单候选 → 以单候选为准
        ctype = candidates[0]
    # 多候选且 LLM 自创不在候选里的名字 → 保持 rule_ctype，走下面的消歧
    if len(candidates) > 1 and ctype == rule_ctype and use_llm:
        # ctype 与规则速判结果一致 → extract 没通过 LLM 给出候选内的类型 → 可以安全消歧
        disamb = extraction.llm_disambiguate_type(doc["text"], candidates)
        if disamb and disamb != ctype:
            ctype = disamb
    if on_step:
        on_step("risk", "运行风险规则引擎")
    # 读取用户配置的参数化阈值和行业规则开关
    user_cfg = config.load_config()
    findings, score, level = risks.evaluate(
        doc["text"], values, ctype,
        params=user_cfg.get("rule_params"),
        industry_rules=user_cfg.get("industry_rules"),
    )
    custom_warnings = list(risks.LAST_WARNINGS)  # 自定义规则评估异常的留痕
    cnt = risks.counts(findings)

    # LLM 辅助判别：对符合触发门槛的 finding 调本地模型复核
    llm_verify_stats = {"triggered": 0, "ok": 0, "failed": 0}
    if use_llm:
        llm_status = llm.status()
        if llm_status["online"] and llm_status["model_ready"]:
            if on_step:
                on_step("llm_verify", "AI 复核高争议条款")
            try:
                findings, llm_verify_stats = risks.llm_verify_findings(
                    findings, doc["text"], ctype)
            except Exception as _e:
                # 整轮 LLM 复核失败 → 降级为纯正则结果，不中断审阅
                llm_verify_stats = {"triggered": 0, "ok": 0, "failed": 0}
        # 如果模型不在线，静默跳过（use_llm 但模型没下载好）

    if on_step:
        on_step("kb", "匹配本地法规知识库")
    statutes = KB.search_for_contract(doc["text"], ctype, top_k=8)
    summary = extraction.summary_text(values, findings)
    summary_source = "rule"
    if llm_res and isinstance(llm_res.get("summary"), str) and len(llm_res["summary"]) > 10:
        summary = llm_res["summary"].strip()
        summary_source = "llm"
    if on_step:
        on_step("save", "写入本机审阅记录")

    result = {
        "filename": filename,
        "ext": doc["ext"],
        "chars": doc["chars"],
        "scan_suspect": doc.get("scan_suspect", False),
        "ocr_used": doc.get("ocr_used", False),
        "pages": doc["pages"],
        "size": doc["size"],
        "title_guess": doc["title_guess"],
        "text": doc["text"],
        "warnings": doc["warnings"] + custom_warnings + ([f"本地模型降级为规则抽取：{llm_error}"] if llm_error else []),
        "llm_verify_stats": llm_verify_stats,
        "fields": fields,
        "values": values,
        "contract_type": ctype,
        "contract_name": values.get("contract_name") or doc["title_guess"] or filename,
        "party_a": values.get("party_a", ""),
        "party_b": values.get("party_b", ""),
        "amount_total": values.get("amount_total", ""),
        "findings": findings,
        "score": score,
        "level": level,
        "counts": cnt,
        "statutes": statutes,
        "summary": summary,
        "summary_source": summary_source,
        "llm_used": bool(llm_res),
        "llm_error": llm_error,
        "engine": {
            "rules": risks.total_rules(),
            "kb": KB.total,
            "model": config.load_config()["model"] if llm_res else "规则引擎",
        },
        "created_at": _now(),
    }
    try:
        store.save_review(result)
    except Exception as e:  # noqa: BLE001
        # 落库失败不该让整份审阅（含 OCR/模型耗时）作废，结果照常返回并标注未入库
        result["warnings"].append(f"审阅结果未能写入本机历史：{e.__class__.__name__}")
    return result


def _job_worker(job_id: str, files: list[tuple[str, bytes]], use_llm: bool):
    """后台逐份审阅。"""
    with JOBS_LOCK:
        job = JOBS[job_id]
        job["state"] = "processing"
    results, errors = [], []
    for idx, (name, data) in enumerate(files):
        with JOBS_LOCK:
            job["current"] = idx
            job["progress"][idx]["state"] = "processing"
            job["progress"][idx]["step"] = "解析文档结构"
        try:
            def on_step(code, label, _idx=idx):
                with JOBS_LOCK:
                    job["progress"][_idx]["step"] = label
            res = _run_pipeline(name, data, use_llm, on_step)
            results.append(res)
            with JOBS_LOCK:
                job["progress"][idx]["state"] = "done"
                job["progress"][idx]["step"] = "完成"
                job["progress"][idx]["score"] = res["score"]
                job["progress"][idx]["level"] = res["level"]
        except Exception as e:
            errors.append({"filename": name, "error": str(e)})
            with JOBS_LOCK:
                job["progress"][idx]["state"] = "failed"
                job["progress"][idx]["step"] = str(e)
            app.logger.exception("处理失败: %s", name)
    with JOBS_LOCK:
        job["results"] = results
        job["errors"] = errors
        job["state"] = "done" if results else "failed"
        job["finished_at"] = _now()


@app.route("/")
def index():
    # 命令行启动时预热尚未完成，先返回启动页；预热结束后刷新即为工作台。
    if SERVING and not READY.is_set():
        return render_template("splash.html")
    if SERVING and READY.is_set() and KB.total == 0:
        # 预热已结束但知识库为空（资料损坏、磁盘满、权限异常）：给可读的错误页，
        # 而不是让用户对着空白工作台猜。
        return _kb_failed_page(), 503
    return render_template("index.html", version=VERSION, asset_v=_asset_version())


def _kb_failed_page() -> str:
    """知识库未加载成功时的兜底页面：说明现象、日志位置和下一步动作。"""
    log_path = config.DATA_DIR / "boot.log"
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>知识库加载失败 · Contract LocalScan</title>
<style>
  body{{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
       background:#F6F2EA;color:#2C2A26;
       font-family:-apple-system,"PingFang SC","Helvetica Neue",Arial,sans-serif}}
  .card{{max-width:560px;padding:40px 44px;background:#FFFDF8;border:1px solid #E3DACB;
        border-radius:16px;box-shadow:0 18px 40px rgba(60,45,20,.08)}}
  h1{{margin:0 0 12px;font-size:22px;font-weight:600}}
  p{{margin:10px 0;line-height:1.75;font-size:14px;color:#5B554B}}
  code{{background:#F1EADC;padding:2px 6px;border-radius:5px;font-size:12.5px;
        word-break:break-all}}
  ol{{margin:14px 0 0;padding-left:20px;font-size:14px;line-height:1.9;color:#5B554B}}
</style></head><body>
<div class="card">
  <h1>知识库加载失败</h1>
  <p>应用已启动，但本地法规库没有加载成功，暂时无法审阅合同。</p>
  <ol>
    <li>完全退出程序后重新打开；</li>
    <li>确认磁盘剩余空间充足；</li>
    <li>仍失败时，把日志文件发给售后：<br><code>{log_path}</code></li>
    <li>如曾手动清理过数据目录，请重新安装一次。</li>
  </ol>
  <p style="margin-top:18px;font-size:12.5px;color:#8A8175">
    数据目录：<code>{config.DATA_DIR}</code></p>
</div></body></html>"""


def _model_options(st: dict) -> list[dict]:
    """本应用允许选择的模型：只保留实测通过的推荐清单。

    旧配置里选中的模型若不在清单内，额外保留一项，避免下拉框显示为空白；
    该模型内存不足时仍会置灰，审阅时自动降级为规则引擎。
    """
    cur = st.get("model") or ""
    names = [m["name"] for m in MODEL_CATALOG]
    if cur and cur not in names:
        names.append(cur)
    out = []
    for name in names:
        ok, why = config.model_supported(name)
        out.append({"name": name, "need_gb": config.model_memory_need(name),
                    "rec_gb": config.model_memory_recommend(name),
                    "supported": ok, "reason": why})
    return out


@app.get("/api/status")
def api_status():
    st = llm.status()
    # 把内存门控结果一并给前端：模型已装但本机内存不够时，界面要显示「内存不足」而不是「在线」
    ok, why = config.model_supported(st.get("model") or "")
    st["model_supported"] = ok
    st["model_blocked_reason"] = why
    kb = KB.stats()
    return jsonify({
        "version": VERSION,
        "ollama": st,
        "model_options": _model_options(st),
        "model_catalog": _model_catalog(st),
        "kb": {
            "total": kb["total"],
            "laws": kb["laws"],
            "categories": kb["categories"],
            "jurisdictions": kb["jurisdictions"],
            "languages": kb["languages"],
        },
        "rules": risks.total_rules(),
        "custom": _custom_stats(),
        "history": store.stats(),
        # 只有「用户已授权」且「配置开启」双条件成立才算启用，防止旧配置绕过授权
        "use_llm": bool(config.load_config().get("use_llm")) and _consented(),
        "netguard": netguard.snapshot(),
        "sysinfo": {"memory_gb": config.memory_gb()},
        "privacy": {"uploaded_bytes": 0, "cloud_calls": 0, "storage": "本机 SQLite",
                    "data_dir": _data_dir_display()},
    })


@app.get("/api/netguard")
def api_netguard():
    return jsonify(netguard.snapshot())


@app.post("/api/netguard/lockdown")
def api_netguard_lockdown():
    payload = _json_dict()
    if payload is None:
        return jsonify({"error": "请求体必须是 JSON 对象"}), 400
    on = payload.get("on")
    if on is None:
        on = not netguard.snapshot()["lockdown"]
    return jsonify(netguard.set_lockdown(bool(on)))


@app.post("/api/netguard/check")
def api_netguard_check():
    """外联自检：只报告本进程能看到的事实，不宣称扫描了系统流量。"""
    from urllib.parse import urlsplit

    snap = netguard.snapshot()
    cfg = config.load_config()
    raw = str(cfg.get("ollama_url") or "")
    host = (urlsplit(raw).hostname or "") if raw else ""
    items = []

    items.append({
        "level": "ok" if snap["active"] else "alert",
        "label": "拦截器状态",
        "detail": "运行期拦截已在进程内生效，非本机连接会被记录并阻断。"
        if snap["active"] else "拦截器未安装，外部连接不会被阻断。",
    })

    local_host = host.startswith("127.") or host in {"::1", "localhost"}
    items.append({
        "level": "ok" if local_host else "alert",
        "label": "本地模型地址",
        "detail": f"当前指向 {host or '未配置'}，属于本机回环地址，合同文本不出本机。"
        if local_host else f"当前指向 {host or '未配置'}，不是本机回环地址；请求会被拦截，请改回 127.0.0.1。",
    })

    n = snap["attempts"]
    items.append({
        "level": "warn" if n else "ok",
        "label": "外联尝试记录",
        "detail": f"本次运行共记录 {n} 次指向本机之外的连接尝试，全部已在发出前被阻断。"
        if n else "本次运行尚未出现任何指向本机之外的连接尝试。",
    })

    items.append({
        "level": "ok",
        "label": "锁定模式",
        "detail": f"已锁定（{snap['locked_at']}）：连域名形式的地址也一并拦截。" if snap["lockdown"]
        else "未锁定：仅拦截非本机目标，127.0.0.1 回环正常放行。",
    })

    levels = [i["level"] for i in items]
    verdict = "alert" if "alert" in levels else ("warn" if "warn" in levels else "ok")
    return jsonify({
        "verdict": verdict,
        "items": items,
        "snapshot": snap,
        "checked_at": time.strftime("%H:%M:%S"),
    })


@app.route("/api/ping", methods=["GET", "OPTIONS"])
def api_ping():
    """轻量存活探测。始终返回 200，避免预热期的实例探测被误判为「无实例」；
    是否可进入工作台由 ready 字段告知启动页。"""
    if request.method == "OPTIONS":
        return "", 204
    return jsonify({"app": APP_MARKER, "version": VERSION, "ready": READY.is_set()})


@app.post("/api/review")
def api_review():
    _purge_jobs()
    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "请至少选择一份合同文件"}), 400
    if len(files) > MAX_FILES:
        return jsonify({"error": f"单次最多 {MAX_FILES} 份合同"}), 400
    # 客户端开关只表示意愿；未获部署授权时一律按规则模式执行，避免绕过授权调用本机模型
    use_llm = (request.form.get("use_llm", "false").lower() in {"1", "true", "yes"}
               and _consented())
    payload = []
    for f in files:
        name = f.filename or "未命名"
        data = f.read()
        if not name.lower().endswith(tuple(parsing.SUPPORTED)):
            return jsonify({"error": f"{name}：仅支持 PDF / DOCX / TXT"}), 400
        if len(data) == 0:
            return jsonify({"error": f"{name}：文件为空，请确认文件内容后重新上传"}), 400
        if len(data) > parsing.MAX_BYTES:
            return jsonify({"error": f"{name}：超过 25 MB 限制"}), 400
        payload.append((name, data))

    job_id = uuid.uuid4().hex[:12]
    with JOBS_LOCK:
        JOBS[job_id] = {
            "id": job_id,
            "state": "queued",
            "created_at": _now(),
            "use_llm": use_llm,
            "progress": [{"filename": n, "state": "pending", "step": "等待本地处理"} for n, _ in payload],
            "current": 0,
            "results": [],
            "errors": [],
        }
    try:
        threading.Thread(target=_job_worker,
                         args=(job_id, payload, use_llm), daemon=True).start()
    except Exception as e:  # noqa: BLE001
        with JOBS_LOCK:
            JOBS.pop(job_id, None)
        return jsonify({"error": f"无法启动审阅任务：{e}"}), 500
    return jsonify({"job_id": job_id, "count": len(payload)}), 202


@app.get("/api/jobs/<job_id>")
def api_job(job_id):
    _purge_jobs()
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        # 后台线程会持续往 progress 里加字段，锁内深拷贝一份快照再序列化，
        # 避免序列化过程中字典大小变化触发 RuntimeError
        snapshot = copy.deepcopy(job) if job else None
    if not snapshot:
        return jsonify({"error": "任务不存在或已过期"}), 404
    return jsonify(snapshot)


@app.get("/api/reviews")
def api_reviews():
    return jsonify({"items": store.list_reviews(), "stats": store.stats()})


@app.get("/api/reviews/<rid>")
def api_review_get(rid):
    r = store.get_review(rid)
    if not r:
        return jsonify({"error": "记录不存在"}), 404
    return jsonify(r)


@app.delete("/api/reviews/<rid>")
def api_review_delete(rid):
    ok = store.delete_review(rid)
    return jsonify({"ok": ok})


@app.post("/api/reviews/clear")
def api_review_clear():
    n = store.clear_reviews()
    return jsonify({"ok": True, "deleted": n})


@app.post("/api/uninstall")
def api_uninstall():
    """一键卸载与清理：删除全部用户数据、导出目录，退出应用。

    打包模式下额外安排删除应用本体（.app），该动作在进程退出后由独立脚本执行，
    避免正在运行的可执行文件无法被删除。
    """
    body = _json_dict()
    if body is None:
        return jsonify({"error": "请求体必须是 JSON 对象"}), 400
    if body.get("confirm") != "卸载":
        return jsonify({"error": "请输入「卸载」以确认"}), 400

    deleted: list[str] = []
    kept: list[str] = []
    # 1. 先读配置拿导出目录（数据目录删了就拿不到了）
    try:
        cfg = config.load_config()
    except Exception:
        cfg = {}
    export_dir = _resolve_export_dir(cfg.get("export_dir") or "")

    # 2. 删除数据目录（审阅库、配置、缓存、导出子目录等）
    data_dir = config.DATA_DIR
    if data_dir.exists():
        try:
            shutil.rmtree(data_dir, ignore_errors=True)
            deleted.append(str(data_dir))
        except Exception as e:
            deleted.append(f"{data_dir}（删除失败：{e}）")

    # 3. 删除导出目录（默认桌面的「ContractLocalScan-导出」）
    # 只删本工具托管的目录：用户把导出位置改成「文档」「桌面」时保留，避免误删个人资料。
    if export_dir is not None and export_dir.exists():
        if _export_dir_owned(export_dir):
            try:
                shutil.rmtree(export_dir, ignore_errors=True)
                deleted.append(str(export_dir))
            except Exception as e:
                deleted.append(f"{export_dir}（删除失败：{e}）")
        else:
            kept.append(str(export_dir))

    # 4. 打包模式：安排退出后删除应用本体
    app_bundle: Path | None = None
    if config.FROZEN:
        exe = Path(sys.executable).resolve()
        # sys.executable = .../ContractLocalScan.app/Contents/MacOS/ContractLocalScan
        candidate = exe.parent.parent.parent
        if candidate.name.endswith(".app") and candidate.exists():
            app_bundle = candidate

    # 响应返回后再执行退出与应用本体删除，确保前端能收到结果
    threading.Timer(1.0, _do_uninstall_finalize, args=[app_bundle]).start()

    return jsonify({
        "ok": True,
        "deleted": deleted,
        "kept": kept,
        "app_bundle": str(app_bundle) if app_bundle else None,
        "frozen": config.FROZEN,
    })


def _do_uninstall_finalize(app_bundle: Path | None) -> None:
    """在响应返回后执行：必要时删除应用本体，然后退出进程。"""
    if app_bundle is not None:
        # 写一个临时脚本，等 2 秒后删除应用本体并自删；用 start_new_session 脱离父进程
        import tempfile
        script = Path(tempfile.gettempdir()) / f"cls_uninstall_{int(time.time())}.sh"
        script.write_text(
            "#!/bin/sh\n"
            "sleep 2\n"
            f"rm -rf '{app_bundle}'\n"
            f"rm -f '{script}'\n",
            encoding="utf-8",
        )
        try:
            script.chmod(0o755)
            subprocess.Popen(["/bin/sh", str(script)], start_new_session=True)
        except Exception:
            pass
    # 强制退出，避免 waitress 残留线程阻止进程结束
    os._exit(0)


_EXPORT_MARKER = ".contractlocalscan-export"


def _resolve_export_dir(raw) -> Path | None:
    """规整用户填写的导出目录；指向系统目录、主目录或其上级时返回 None。

    「一键卸载」会整目录删除导出文件夹，所以必须在写入配置这一步就挡住危险路径，
    不能等到删除时才判断。空值回退到默认目录（桌面下的专用文件夹）。
    """
    text = str(raw or "").strip()
    base = text or str(config.DEFAULT_CONFIG.get("export_dir") or "")
    try:
        p = Path(base).expanduser().resolve()
    except (OSError, RuntimeError):
        return None
    if p.parent == p or p.parent == Path("/"):
        # 磁盘根目录，或 /Users、/tmp、/opt、/Volumes、/etc 这类顶层目录
        return None
    home = Path.home().resolve()
    if p in {Path("/"), home, home / "Desktop", home / "Documents",
             home / "Downloads", home / "Library", home / "Pictures"}:
        return None
    try:
        data_dir = config.DATA_DIR.resolve()
        if p == data_dir or data_dir.is_relative_to(p):
            return None  # 不能指到数据目录本身或其上级
    except (OSError, ValueError):
        pass
    return p


def _export_dir_owned(p: Path) -> bool:
    """导出目录能否整目录删除：只认应用数据目录内、带标记文件、空目录三种情况。

    用户把导出目录改成「文档」「桌面」这类已有资料的位置时，卸载绝不能递归删除，
    否则会连带清空与工具无关的文件。
    """
    try:
        data_dir = config.DATA_DIR.resolve()
        if p == data_dir or p.is_relative_to(data_dir):
            return True
    except (OSError, ValueError):
        pass
    if (p / _EXPORT_MARKER).exists():
        return True
    try:
        return not any(p.iterdir())
    except OSError:
        return False


def _safe_name(name: str) -> str:
    keep = "".join(ch for ch in name if ch.isalnum() or ch in "（()）-_ ")
    return keep.strip()[:60] or "合同"


def _save_export_to_disk(data: bytes, filename: str) -> tuple[bool, str, str | None]:
    """将导出文件直接写入用户配置的目录，返回 (ok, message, path)。

    用于 ?save=1 模式：绕过浏览器下载对话框，文件直接落盘到 export_dir。
    """
    cfg = config.load_config()
    export_dir = _resolve_export_dir(cfg.get("export_dir") or "")
    if export_dir is None:
        # 老版本可能把危险路径写进了配置；回退到应用内的导出目录，保证导出仍可用
        export_dir = config.EXPORTS_DIR
    try:
        export_dir.mkdir(parents=True, exist_ok=True)
        marker = export_dir / _EXPORT_MARKER
        if not marker.exists():
            marker.write_text("Contract LocalScan 导出目录标记：卸载时会连同本目录一起清理。\n",
                              encoding="utf-8")
    except OSError as e:
        return False, f"无法创建导出目录：{e}", None
    path = export_dir / filename
    # 若同名文件已存在，追加序号避免覆盖
    if path.exists():
        stem, suffix = path.stem, path.suffix
        n = 1
        while (export_dir / f"{stem}_{n}{suffix}").exists():
            n += 1
        path = export_dir / f"{stem}_{n}{suffix}"
    try:
        path.write_bytes(data)
    except OSError as e:
        return False, f"写入文件失败：{e}", None
    return True, "ok", str(path)


@app.get("/api/reviews/<rid>/pdf")
def api_export_pdf(rid):
    r = store.get_review(rid)
    if not r:
        return jsonify({"error": "记录不存在"}), 404
    data = reporting.build_pdf(r)
    name = _safe_name(r.get("contract_name") or r["filename"]) + "_审阅报告.pdf"
    if request.args.get("save") == "1":
        ok, msg, path = _save_export_to_disk(data, name)
        if not ok:
            return jsonify({"error": msg}), 500
        return jsonify({"ok": True, "path": path, "filename": name})
    return send_file(io.BytesIO(data), mimetype="application/pdf", as_attachment=True, download_name=name)


@app.get("/api/reviews/<rid>/xlsx")
def api_export_xlsx(rid):
    r = store.get_review(rid)
    if not r:
        return jsonify({"error": "记录不存在"}), 404
    data = reporting.build_xlsx(r)
    name = _safe_name(r.get("contract_name") or r["filename"]) + "_字段清单.xlsx"
    if request.args.get("save") == "1":
        ok, msg, path = _save_export_to_disk(data, name)
        if not ok:
            return jsonify({"error": msg}), 500
        return jsonify({"ok": True, "path": path, "filename": name})
    return send_file(io.BytesIO(data),
                     mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                     as_attachment=True, download_name=name)


@app.get("/api/reviews/<rid>/docx")
def api_export_docx(rid):
    r = store.get_review(rid)
    if not r:
        return jsonify({"error": "记录不存在"}), 404
    try:
        data = reporting.build_docx(r)
    except Exception as e:
        app.logger.error("docx export failed: %s", e)
        return jsonify({"error": f"批注版生成失败：{type(e).__name__}"}), 500
    name = _safe_name(r.get("contract_name") or r["filename"]) + "_批注版.docx"
    if request.args.get("save") == "1":
        ok, msg, path = _save_export_to_disk(data, name)
        if not ok:
            return jsonify({"error": msg}), 500
        return jsonify({"ok": True, "path": path, "filename": name})
    return send_file(io.BytesIO(data),
                     mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                     as_attachment=True, download_name=name)


def _data_dir_display() -> str:
    """数据目录的展示形式：家目录缩写为 ~，便于用户在设置页核对。"""
    s = str(config.DATA_DIR)
    home = str(Path.home())
    return s.replace(home, "~") if s.startswith(home) else s


def _custom_law_ids() -> set[str]:
    """当前启用的自定义包在知识库中的 law_id 集合，用于给检索结果打来源标。"""
    try:
        return {law["id"] for _pid, _arts, laws in custom.collect_statutes() for law in laws}
    except Exception:  # noqa: BLE001
        return set()


@app.get("/api/kb/search")
def api_kb_search():
    q = request.args.get("q", "").strip()
    law = request.args.get("law", "").strip()
    cat = request.args.get("cat", "").strip()
    src = request.args.get("src", "").strip()
    try:
        top = min(30, max(1, int(request.args.get("top", 12))))
    except (TypeError, ValueError):
        top = 12
    t0 = time.time()
    law_ids = [law] if law else None
    if cat and not law:
        if cat == "__intl__":
            law_ids = [l["id"] for l in KB.laws if l.get("jurisdiction") == "INTL"]
        else:
            law_ids = [l["id"] for l in KB.laws if l.get("category") == cat]
    custom_ids = _custom_law_ids()

    def _annotate(rows: list[dict]) -> list[dict]:
        for it in rows:
            it["src"] = "custom" if it.get("law_id") in custom_ids else "builtin"
        if src in {"custom", "builtin"}:
            rows = [it for it in rows if it["src"] == src]
        return rows

    # 查询词只是一部法律的全称/简称时，按条文顺序返回整部法律
    if q and not law and not cat:
        browsed = KB.browse_query(q)
        if browsed:
            items, total = browsed
            items = _annotate(items)
            return jsonify({"q": q, "items": items, "count": len(items), "total": total,
                            "browse": KB.law_names[items[0]["law_id"]]["short"] if items else "",
                            "ms": round((time.time() - t0) * 1000)})
    items = _annotate(KB.search(q, law_ids=law_ids, top_k=top))
    return jsonify({"q": q, "items": items, "count": len(items), "ms": round((time.time() - t0) * 1000)})


@app.get("/api/kb/stats")
def api_kb_stats():
    return jsonify(KB.stats())


@app.get("/api/kb/article/<aid>")
def api_kb_article(aid):
    art = KB.by_id.get(aid)
    if not art:
        return jsonify({"error": "条文不存在"}), 404
    return jsonify(art)


# ---------- 自定义规则与法典包 ----------
# 三层判断模型的「自定义」侧入口：导入 → 预览（法条 / 建议规则 / 阈值三区）
# → 确认落库 → 启用 / 停用 / 删除。全部解析在本机完成，文件内容不出本机。
PREVIEW_TTL_MS = 30 * 60 * 1000  # 预览结果（含制度文本）只在内存中保留 30 分钟
_PREVIEWS: dict[str, dict] = {}
_PREVIEWS_LOCK = threading.Lock()
_CUSTOM_EXTS = {".pdf", ".docx", ".txt", ".md", ".json"}
_CUSTOM_JURISDICTIONS = {"ENT", "CN", "LOCAL", "INTL"}
_CUSTOM_KINDS = {"regulation", "policy", "checklist", "mixed"}
_APPLIED_PACKS: set[str] = set()  # 已挂进知识库的包，供增量卸载


def _purge_previews() -> None:
    now = _now()
    with _PREVIEWS_LOCK:
        stale = [k for k, v in _PREVIEWS.items() if now - v["created_at"] > PREVIEW_TTL_MS]
        for k in stale:
            del _PREVIEWS[k]


def _custom_stats() -> dict:
    try:
        packs = custom.list_packs()
    except Exception:  # noqa: BLE001
        return {"packs": 0, "enabled": 0, "rules": 0, "statutes": 0}
    return {
        "packs": len(packs),
        "enabled": sum(1 for p in packs if p.get("enabled")),
        "rules": sum(int(p.get("rules", 0)) for p in packs),
        "statutes": sum(int(p.get("statutes", 0)) for p in packs),
    }


def _apply_custom() -> dict:
    """把启用包挂到知识库与规则引擎，并按来源重算阈值。幂等，可反复调用。

    知识库按包增量增删（墓碑式，不重建主索引）；规则集与阈值整体重算，
    与现状一致时 recompute_params 不落盘。
    """
    out = {"kb_added": 0, "kb_removed": 0, "rules": 0}
    global _APPLIED_PACKS
    try:
        wanted = {pid for pid, _arts, _laws in custom.collect_statutes()}
        for pid in _APPLIED_PACKS - wanted:
            KB.remove_custom(pid)
            _APPLIED_PACKS.discard(pid)
            out["kb_removed"] += 1
        for pid, arts, laws in custom.collect_statutes():
            if pid in _APPLIED_PACKS:
                continue
            KB.add_custom(pid, arts, laws)
            _APPLIED_PACKS.add(pid)
            out["kb_added"] += 1
        rules = custom.collect_rules()
        risks.register_custom(rules)
        out["rules"] = len(rules)
        custom.recompute_params()
    except Exception as e:  # noqa: BLE001
        # 某个包损坏不该拖垮整个应用：记录并维持现有可用状态
        app.logger.exception("自定义包应用失败")
        out["error"] = e.__class__.__name__
    return out


def _statutes_from_articles(articles: list[dict], pack_id: str, name: str,
                            jurisdiction: str) -> list[dict]:
    """把切条结果转成知识库条文结构（与 validate_statute 的输出同构）。"""
    law_id = f"{jurisdiction.lower()}-{pack_id}"
    return [{
        "id": f"{law_id}-{i}", "law_id": law_id, "law": name[:custom.MAX_TITLE_CHARS],
        "label": art["label"][:30], "article_no": art["article_no"], "text": art["text"],
        "chapter": "", "jurisdiction": jurisdiction, "source_pack": pack_id,
    } for i, art in enumerate(articles, 1)]


def _value_pattern_for(param_key: str) -> str:
    """阈值规则在合同文本中提取数值的模式：单位必须与参数口径一致。"""
    if param_key.endswith(("_pct", "_perday")):
        return r"(?P<v>\d+(?:\.\d+)?)\s*[%％]"
    if param_key.endswith("_months"):
        return r"(?P<v>\d+(?:\.\d+)?)\s*(?:个月|月|年)"
    if param_key.endswith("_years"):
        return r"(?P<v>\d+(?:\.\d+)?)\s*年"
    return r"(?P<v>\d+(?:\.\d+)?)"


def _rules_from_llm(text: str, pack_id: str, start_seq: int,
                    errors: list[str]) -> tuple[list[dict], dict]:
    """本地模型抽取（宽松 schema）→ 转正式结构 → 复用 validate_rule 同一套校验。

    模型只负责理解语义，关键词转义为字面量、阈值口径映射都在这里完成，
    不让模型直接写正则。返回 (规则草稿, 阈值覆盖)。
    """
    rules: list[dict] = []
    overrides: dict = {}
    try:
        raw_rules = llm.extract_rules(text)
    except llm.LocalModelError as e:
        errors.append(f"模型辅助识别规则未成功：{e}")
        return rules, overrides
    seq = start_seq
    for raw in raw_rules[:50]:
        kind = raw.get("kind")
        keywords = [k.strip() for k in (raw.get("keywords") or [])
                    if isinstance(k, str) and 1 < len(k.strip()) <= 12][:3]
        formal: dict = {
            "title": raw.get("title"), "severity": raw.get("severity", "medium"),
            "analysis": raw.get("summary", ""),
            "advice": "按导入制度执行；与内置结论分歧时请由专业人士复核。",
        }
        related = raw.get("related")
        if isinstance(related, str) and re.fullmatch(r"[A-Z]{1,3}\d{2}", related):
            formal["related"] = [related]
        if kind == "forbid" and keywords:
            formal["mode"] = "match"
            formal["patterns"] = [re.escape(k) for k in keywords[:2]]
        elif kind == "require" and keywords:
            formal["mode"] = "absence"
            formal["markers"] = [re.escape(k) for k in keywords[:2]]
        elif kind == "threshold":
            key = str(raw.get("param_key") or "")
            try:
                value = float(raw.get("value"))
            except (TypeError, ValueError):
                errors.append(f"「{raw.get('title') or '未命名'}」阈值缺少数值，已跳过")
                continue
            if key not in custom.RULE_PARAM_SPEC or raw.get("op") not in custom.OPS:
                errors.append(f"「{raw.get('title') or '未命名'}」的阈值无法对应内置口径，已跳过")
                continue
            # 带上主题词上下文，避免「任意百分比」误命中（如付款比例）
            vp = _value_pattern_for(key)
            if keywords:
                vp = re.escape(keywords[0]) + r"\D{0,10}" + vp
            formal["mode"] = "threshold"
            formal["threshold"] = {"param_key": key, "op": raw["op"],
                                   "value_pattern": vp, "value": value}
            lo, hi, cast = custom.RULE_PARAM_SPEC[key]
            if lo <= value <= hi:
                overrides[key] = cast(value)
        else:
            continue  # 纯叙述段不是可执行检查点，跳过（仍作为法条进知识库）
        seq += 1
        rule = custom.validate_rule(formal, pack_id, seq, errors)
        if rule:
            rule["draft"] = True
            rule["llm_derived"] = True
            rules.append(rule)
    return rules, overrides


def _preview_from_text(text: str, pack_id: str, name: str, jurisdiction: str,
                       use_llm_rules: bool) -> tuple[list[dict], list[dict], dict, list[str]]:
    """文档类导入：一切内容先切条进法条层，再派生建议规则与阈值接管项。"""
    errors: list[str] = []
    articles = custom.split_articles(text)  # 条数超限时抛 CustomError
    statutes = _statutes_from_articles(articles, pack_id, name, jurisdiction)
    article_ids = {s["article_no"]: s["id"] for s in statutes}
    rules, overrides = custom.derive_rules(articles, pack_id, name, article_ids)
    if use_llm_rules:
        llm_rules, llm_overrides = _rules_from_llm(text, pack_id, len(rules), errors)
        rules += llm_rules
        overrides.update(llm_overrides)
    return statutes, rules, overrides, errors


def _preview_from_json(obj: dict, pack_id: str, name: str,
                       jurisdiction: str) -> tuple[list[dict], list[dict], dict, list[str]]:
    """JSON 清单导入：逐条校验，合法的留下、不合法的逐条报错。"""
    errors: list[str] = []
    statutes_raw = obj.get("statutes") or []
    rules_raw = obj.get("rules") or []
    if not isinstance(statutes_raw, list) or not isinstance(rules_raw, list):
        raise custom.CustomError("JSON 模板中的 statutes / rules 必须是数组")
    if len(statutes_raw) + len(rules_raw) > custom.MAX_ITEMS:
        raise custom.CustomError(f"单包条目上限 {custom.MAX_ITEMS}，请拆分后分批导入")
    statutes, rules, overrides = [], [], {}
    for i, raw in enumerate(statutes_raw, 1):
        s = custom.validate_statute(raw, pack_id, i, jurisdiction, name, errors)
        if s:
            statutes.append(s)
    for i, raw in enumerate(rules_raw, 1):
        r = custom.validate_rule(raw, pack_id, i, errors)
        if r:
            rules.append(r)  # JSON 清单是显式编写的规则，默认全部勾选
            th = r.get("threshold") or {}
            key = th.get("param_key", "")
            if key in custom.RULE_PARAM_SPEC and "value" in th:
                lo, hi, cast = custom.RULE_PARAM_SPEC[key]
                try:
                    v = cast(float(th["value"]))
                except (TypeError, ValueError):
                    continue
                if lo <= float(v) <= hi:
                    overrides[key] = v
    if not statutes and not rules:
        raise custom.CustomError("没有可导入的有效条文或规则。", errors)
    return statutes, rules, overrides, errors


@app.post("/api/custom/packs")
def api_custom_import():
    """第一步：解析并生成预览，不落库。确认走 confirm 接口。"""
    _purge_previews()
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "请选择要导入的文件"}), 400
    ext = Path(f.filename).suffix.lower()
    if ext not in _CUSTOM_EXTS:
        return jsonify({"error": "仅支持 PDF / Word / TXT / Markdown / JSON 格式"}), 400
    data = f.read()
    if not data:
        return jsonify({"error": "文件为空，请确认内容后重新上传"}), 400
    if len(data) > custom.MAX_FILE_BYTES:
        return jsonify({"error": "超过 8 MB 限制，请拆分后分批导入"}), 400
    name = ((request.form.get("name") or "").strip()
            or Path(f.filename).stem)[:custom.MAX_TITLE_CHARS]
    jurisdiction = (request.form.get("jurisdiction") or "ENT").strip().upper()
    if jurisdiction not in _CUSTOM_JURISDICTIONS:
        jurisdiction = "ENT"
    kind = (request.form.get("kind") or "mixed").strip()
    if kind not in _CUSTOM_KINDS:
        kind = "mixed"
    pack_id = custom.new_pack_id()
    doc_warnings: list[str] = []
    try:
        if ext == ".json":
            try:
                obj = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                return jsonify({"error": "JSON 解析失败，请使用 UTF-8 编码并检查格式"}), 400
            if not isinstance(obj, dict):
                return jsonify({"error": "JSON 模板须为对象，含 statutes / rules 数组"}), 400
            statutes, rules, overrides, errors = _preview_from_json(
                obj, pack_id, name, jurisdiction)
        else:
            if ext in (".txt", ".md"):
                text = data.decode("utf-8", errors="ignore")
            else:
                doc = parsing.parse(f.filename, data)
                text = doc["text"]
                doc_warnings = [w for w in (doc.get("warnings") or []) if isinstance(w, str)]
            if len(text.strip()) < 10:
                return jsonify({"error": "未能从文件中提取到有效文本"}), 400
            # 模型辅助识别规则需授权；未授权时仅用规范句识别，功能照常可用
            use_llm_rules = (request.form.get("llm_rules", "").lower() in {"1", "true", "yes"}
                             and _consented())
            statutes, rules, overrides, errors = _preview_from_text(
                text, pack_id, name, jurisdiction, use_llm_rules)
            errors = doc_warnings + errors
    except custom.CustomError as e:
        return jsonify({"error": str(e), "errors": e.errors}), 400
    preview_id = uuid.uuid4().hex[:12]
    with _PREVIEWS_LOCK:
        _PREVIEWS[preview_id] = {
            "created_at": _now(), "pack_id": pack_id, "name": name,
            "kind": kind, "jurisdiction": jurisdiction, "source_file": f.filename,
            "statutes": statutes, "rules": rules, "overrides": overrides,
        }
    return jsonify({
        "preview_id": preview_id, "name": name, "kind": kind, "jurisdiction": jurisdiction,
        "statutes": {"total": len(statutes),
                     "sample": [{"label": s["label"], "text": s["text"][:200]}
                                for s in statutes[:30]]},
        "rules": [{"id": r["id"], "title": r["title"], "severity": r["severity"],
                   "mode": r["mode"], "analysis": r.get("analysis", ""),
                   "related": r.get("related", []),
                   "llm_derived": bool(r.get("llm_derived"))} for r in rules],
        "thresholds": custom.pack_threshold_diff(overrides),
        "errors": errors[:20],
    })


@app.post("/api/custom/packs/confirm/<preview_id>")
def api_custom_confirm(preview_id):
    """第二步：按预览页的勾选落库并立即生效（法条入库、规则并列、阈值接管）。"""
    _purge_previews()
    with _PREVIEWS_LOCK:
        preview = _PREVIEWS.pop(preview_id, None)
    if not preview:
        return jsonify({"error": "预览不存在或已过期（30 分钟），请重新导入"}), 404
    body = _json_dict() or {}
    rules = preview["rules"]
    sel_rules = body.get("rules")
    if isinstance(sel_rules, list):
        wanted = {str(x) for x in sel_rules}
        rules = [r for r in rules if r["id"] in wanted]
    overrides = preview["overrides"]
    sel_params = body.get("params")
    if isinstance(sel_params, list):
        keep = {str(x) for x in sel_params}
        overrides = {k: v for k, v in overrides.items() if k in keep}
    for r in rules:
        r.pop("draft", None)  # 确认后即生效，不再是草稿
    name = (str(body.get("name") or "").strip()
            or preview["name"])[:custom.MAX_TITLE_CHARS]
    pack = {
        "id": preview["pack_id"], "name": name,
        "kind": preview["kind"], "jurisdiction": preview["jurisdiction"],
        "enabled": True, "imported_at": custom.now_ts(),
        "source_file": preview["source_file"],
        "statutes": preview["statutes"], "rules": rules,
        "threshold_overrides": overrides,
    }
    diff = custom.pack_threshold_diff(overrides)  # 须在落库前算，否则「当前值」已被覆盖
    try:
        custom.put_pack(pack)
    except Exception as e:  # noqa: BLE001
        return jsonify({"error": f"保存失败：{e.__class__.__name__}"}), 500
    applied = _apply_custom()
    cfg = config.load_config()
    return jsonify({
        "ok": True,
        "pack": {k: pack[k] for k in ("id", "name", "kind", "jurisdiction", "enabled")},
        "counts": {"statutes": len(pack["statutes"]), "rules": len(rules)},
        "thresholds": diff, "applied": applied,
        "rule_params": cfg.get("rule_params"),
        "threshold_overrides": cfg.get("threshold_overrides"),
    })


@app.get("/api/custom/packs")
def api_custom_list():
    cfg = config.load_config()
    return jsonify({
        "packs": custom.list_packs(),
        "stats": _custom_stats(),
        "rule_params": cfg.get("rule_params"),
        "threshold_overrides": cfg.get("threshold_overrides"),
    })


@app.get("/api/custom/packs/<pid>")
def api_custom_get(pid):
    pack = custom.get_pack(pid)
    if not pack:
        return jsonify({"error": "规则包不存在"}), 404
    return jsonify(pack)


@app.patch("/api/custom/packs/<pid>")
def api_custom_patch(pid):
    body = _json_dict()
    if body is None or not isinstance(body.get("enabled"), bool):
        return jsonify({"error": "请求必须包含布尔字段 enabled"}), 400
    pack = custom.set_enabled(pid, body["enabled"])
    if not pack:
        return jsonify({"error": "规则包不存在"}), 404
    applied = _apply_custom()  # 停用即回退：规则退出评估、阈值按来源重算
    cfg = config.load_config()
    return jsonify({"ok": True, "enabled": pack["enabled"], "applied": applied,
                    "rule_params": cfg.get("rule_params"),
                    "threshold_overrides": cfg.get("threshold_overrides")})


@app.delete("/api/custom/packs/<pid>")
def api_custom_delete(pid):
    pack = custom.remove_pack(pid)
    if not pack:
        return jsonify({"error": "规则包不存在"}), 404
    applied = _apply_custom()
    cfg = config.load_config()
    return jsonify({"ok": True, "applied": applied,
                    "rule_params": cfg.get("rule_params"),
                    "threshold_overrides": cfg.get("threshold_overrides")})


@app.get("/api/config")
def api_config():
    return jsonify(config.load_config())


_doc_cache: dict[str, str] = {}


def _read_doc(name: str) -> str:
    if name not in _doc_cache:
        path = config.RESOURCE_DIR / name
        _doc_cache[name] = path.read_text(encoding="utf-8") if path.exists() else ""
    return _doc_cache[name]


@app.get("/api/manual")
def api_manual():
    """随附 PDF《安装与排障》：应用打不开时也能从分发目录直接打开。"""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    for p in (base / "安装与排障.pdf",
              base.parent / "Resources" / "安装与排障.pdf",
              Path(__file__).resolve().parent / "安装与排障.pdf"):
        if p.exists():
            return send_file(p, mimetype="application/pdf")
    return jsonify({"error": "本地未找到《安装与排障》PDF：源码运行版不随附该文件"}), 404


@app.get("/api/notices")
def api_notices():
    """开源依赖与法规数据来源声明。"""
    return jsonify({"content": _read_doc("THIRD_PARTY_NOTICES.md")})


@app.post("/api/config")
def api_config_set():
    patch = _json_dict()
    if patch is None:
        return jsonify({"error": "请求体必须是 JSON 对象"}), 400
    if patch.get("ollama_url"):
        host = (urlparse(patch["ollama_url"]).hostname or "").lower()
        if host not in LOCAL_HOSTS:
            return jsonify({"error": "为保障数据不出本机，仅允许 127.0.0.1 / localhost 的本地模型地址"}), 400
    if "rule_params" in patch:
        raw = patch["rule_params"]
        if not isinstance(raw, dict):
            return jsonify({"error": "rule_params 需要是对象"}), 400
        clean = {}
        for key, value in raw.items():
            spec = _RULE_PARAM_SPEC.get(key)
            if spec is None:
                continue
            lo, hi, cast = spec
            try:
                num = cast(float(value))
            except (TypeError, ValueError):
                return jsonify({"error": f"{key} 需要填写数字"}), 400
            if not (lo <= num <= hi):
                return jsonify({"error": f"{key} 需在 {lo}–{hi} 之间"}), 400
            clean[key] = num
        patch["rule_params"] = clean
    if "export_dir" in patch:
        resolved = _resolve_export_dir(patch.get("export_dir"))
        if resolved is None:
            return jsonify({
                "error": "导出目录不能是系统目录、用户主目录（桌面/文稿/下载）或其上级目录，"
                         "请选择专用文件夹，例如 桌面/ContractLocalScan-导出",
            }), 400
        patch["export_dir"] = str(resolved)
    if patch.get("model"):
        ok, why = config.model_supported(patch["model"])
        if not ok:
            return jsonify({"error": why}), 400
    cfg = config.save_config(patch)
    return jsonify({"ok": True, "config": cfg, "status": llm.status()})

# 阈值允许范围：下限贴近常识下限，上限留出「内部更严格标准」的空间。
# 默认值本身多为法定红线，改动只用于企业内部更严的口径。
_RULE_PARAM_SPEC = {
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


# ---------- Ollama 环境自检与一键修复 ----------
# 离线工具没有云端后台可代为排障，测试连接失败时由工具自身完成
# 三层诊断（安装 → 服务 → 模型）并给出可执行的修复动作。

REPAIR = {"state": "idle", "message": "", "percent": None, "error": "", "model": "",
          "downloaded": 0, "expected": 0, "stage": ""}
REPAIR_LOCK = threading.Lock()

# 经八模型同题实测筛选后的可装清单，与「规则引擎与本地模型」说明页的结论一致。
# 只提供清单内的模型，避免用户在本机拉取体积过大或效果更差的模型。
MODEL_CATALOG = [
    {"name": "qwen2.5:3b", "title": "Qwen2.5 3B", "size_gb": 1.9,
     "note": "适合 8 GB 及以下内存，单份合同十余秒完成"},
    {"name": "gemma3:4b", "title": "Gemma 3 4B", "size_gb": 3.4,
     "note": "推荐 16 GB 内存；8 GB 机器也能运行，单份约一分钟以上"},
]

# 打包成 .app 后由访达双击启动时，进程 PATH 只有 /usr/bin:/bin:/usr/sbin:/sbin，
# shutil.which("ollama") 找不到 Homebrew 装的命令，界面会误报「未安装」。
# 这里按常见安装位置逐个探测，仍失败再回退到 which。
_OLLAMA_PATHS = (
    "/opt/homebrew/bin/ollama",
    "/usr/local/bin/ollama",
    "/opt/local/bin/ollama",
    "/Applications/Ollama.app/Contents/Resources/ollama",
    "~/Applications/Ollama.app/Contents/Resources/ollama",
)


def _find_ollama() -> str:
    """返回本机 ollama 可执行文件路径；找不到返回空字符串。"""
    for raw in _OLLAMA_PATHS:
        p = Path(raw).expanduser()
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
    found = shutil.which("ollama")
    if found:
        return found
    if os.name == "nt":
        for env in ("LOCALAPPDATA", "ProgramFiles"):
            base = os.environ.get(env)
            if not base:
                continue
            p = Path(base) / "Ollama" / "ollama.exe"
            if p.is_file():
                return str(p)
    return ""


def _ollama_env(binary: str) -> dict:
    """子进程环境：把 ollama 所在目录补进 PATH，避免其内部再调用时找不到自己。"""
    env = dict(os.environ)
    folder = str(Path(binary).parent)
    paths = env.get("PATH", "").split(os.pathsep)
    if folder not in paths:
        env["PATH"] = os.pathsep.join([folder] + paths)
    return env


def _start_ollama(binary: str) -> bool:
    """后台拉起 Ollama 服务，返回是否成功发起了启动。

    macOS 优先唤起 Ollama.app（图形版会自己拉起服务，用户也能看到托盘图标）；
    其余情况直接跑无界面的 ollama serve。
    """
    try:
        if sys.platform == "darwin" and Path("/Applications/Ollama.app").exists():
            subprocess.Popen(["open", "-a", "Ollama"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        if binary:
            subprocess.Popen([binary, "serve"], env=_ollama_env(binary),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
            return True
    except OSError:
        pass
    return False


def _norm_model(name: str) -> str:
    """ollama 列表省略 :latest 后缀，比较前统一补齐。"""
    return name if ":" in name else f"{name}:latest"


def _model_catalog(st: dict) -> list[dict]:
    """推荐模型清单 + 本机安装状态，供设置页直接渲染。"""
    installed = {_norm_model(m) for m in (st.get("models") or [])}
    out = []
    for item in MODEL_CATALOG:
        name = item["name"]
        ok, why = config.model_supported(name)
        out.append({
            **item,
            "need_gb": config.model_memory_need(name),
            "rec_gb": config.model_memory_recommend(name),
            "supported": ok,
            "reason": why,
            # 按完整名称比对：本机装着 qwen2.5:7b 时不能算作 qwen2.5:3b 已安装
            "installed": _norm_model(name) in installed,
        })
    return out


def _ollama_tags(url: str) -> list[dict]:
    """读取本机已安装模型及体积（只访问回环地址）。"""
    try:
        with urllib.request.urlopen(f"{url.rstrip('/')}/api/tags", timeout=4) as r:
            data = json.loads(r.read())
        return [{"name": m.get("name", ""), "size": int(m.get("size") or 0)}
                for m in (data.get("models") or []) if m.get("name")]
    except (OSError, ValueError, TypeError):
        return []


def _prunable_models() -> list[dict]:
    """清单之外的本机模型。这些模型经实测体积大、效果差，安装包里也不应保留。"""
    allowed = {_norm_model(m["name"]) for m in MODEL_CATALOG}
    return [m for m in _ollama_tags(config.load_config()["ollama_url"])
            if _norm_model(m["name"]) not in allowed]


@app.get("/api/ollama/diagnose")
def api_ollama_diagnose():
    cfg = config.load_config()
    url = cfg["ollama_url"].rstrip("/")
    model = cfg["model"]
    installed = bool(_find_ollama())
    st = llm.status(url, model)
    running = st["online"]
    model_ready = st["model_ready"]
    steps = []
    if not installed:
        steps.append("安装 Ollama：终端执行 brew install ollama，或前往 ollama.com 下载安装包")
    if installed and not running:
        steps.append("启动服务：终端执行 ollama serve，或点击下方「一键启动服务」")
    if running and not model_ready:
        steps.append(f"拉取模型：ollama pull {model}，或点击下方「一键拉取模型」")
    if installed and running and model_ready:
        steps.append("环境就绪，无需修复。")
    return jsonify({
        "installed": installed, "running": running,
        "model_ready": model_ready, "model": model, "url": url,
        "steps": steps,
        "catalog": _model_catalog(st),
    })


def _ollama_blobs_dir() -> Path:
    """ollama 把模型分片写入 blobs 目录，下载中的分片带 -partial 后缀。"""
    models = os.environ.get("OLLAMA_MODELS")
    base = Path(models).expanduser() if models else Path.home() / ".ollama" / "models"
    return base / "blobs"


def _dir_bytes(path: Path, skip_partial: bool = False) -> int:
    total = 0
    try:
        for p in path.iterdir():
            if skip_partial and "-partial" in p.name:
                continue
            try:
                if p.is_file():
                    total += p.stat().st_size
            except OSError:
                pass
    except OSError:
        pass
    return total


def _human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


# ollama 的流式事件里 status 形如 pulling manifest / pulling <digest> /
# verifying sha256 digest / writing manifest，这里统一换成中文阶段名。
_REPAIR_STAGES = (
    ("pulling manifest", "读取模型清单"),
    ("verifying", "校验文件完整性"),
    ("writing manifest", "写入模型清单"),
    ("removing", "清理旧分片"),
)


def _stage_label(status: str) -> str:
    for token, text in _REPAIR_STAGES:
        if token in status:
            return text
    return "下载模型中" if status.startswith("pulling") else "处理中"


def _pull_stream(url: str, model: str, label: str) -> tuple[bool, str]:
    """流式拉取一个模型，边下边回传进度。返回 (是否成功, 错误信息)。

    必须用 stream=true：非流式请求要等整个模型下载完才返回，期间连接上不
    传输任何字节。3 GB 的模型按常见网速要十几分钟，客户端读超时（此前设
    600 秒）会把正在正常下载的任务直接掐断，表现为「进度停在某个百分比后
    报 timed out」。流式请求持续有事件，30 秒收不到任何数据才算真断流。
    """
    payload = json.dumps({"name": model, "stream": True}).encode()
    req = urllib.request.Request(f"{url.rstrip('/')}/api/pull", data=payload,
                                 headers={"Content-Type": "application/json"})
    per_blob: dict[str, list[int]] = {}  # digest -> [total, completed]
    if DEPLOY_CANCEL.is_set():
        return False, "cancelled"
    with urllib.request.urlopen(req, timeout=30) as resp:
        for raw in resp:
            # 撤回授权后不再继续消耗磁盘与网络，下一个事件处退出
            if DEPLOY_CANCEL.is_set():
                return False, "cancelled"
            line = raw.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("error"):
                return False, str(ev["error"])
            digest = ev.get("digest") or ""
            total = int(ev.get("total") or 0)
            done = int(ev.get("completed") or 0)
            if digest and total:
                slot = per_blob.setdefault(digest, [total, 0])
                slot[0] = max(slot[0], total)
                slot[1] = max(slot[1], done)
            status = str(ev.get("status") or "")
            need = sum(s[0] for s in per_blob.values())
            got = sum(s[1] for s in per_blob.values())
            pct = min(99.0, round(got / need * 100, 1)) if need else None
            with REPAIR_LOCK:
                REPAIR.update({
                    "stage": _stage_label(status),
                    "downloaded": got,
                    "expected": need or REPAIR.get("expected", 0),
                    "percent": pct if pct is not None else REPAIR.get("percent"),
                    "updated_at": time.time(),
                    "message": f"{label} · 已下载 {_human_bytes(got)}"
                               + (f" / 共 {_human_bytes(need)}" if need else ""),
                })
            if status == "success":
                break
    return True, ""


def _pull_models(models: list[str]) -> None:
    """按顺序下载模型，全部完成后把 REPAIR 置为 done。"""
    url = config.load_config()["ollama_url"]
    total = len(models)
    for idx, model in enumerate(models, 1):
        label = model if total == 1 else f"{model}（第 {idx}/{total} 个）"
        expected = next((int(m["size_gb"] * 1000 ** 3) for m in MODEL_CATALOG
                         if m["name"] == model), 0)
        with REPAIR_LOCK:
            REPAIR.update({"model": model, "percent": None, "downloaded": 0,
                           "expected": expected, "stage": "", "error": "",
                           "updated_at": time.time(),
                           "message": f"正在下载 {label}"})
        try:
            ok, why = _pull_stream(url, model, label)
        except Exception as e:  # noqa: BLE001
            ok, why = False, str(e)
        if not ok:
            cancelled = why == "cancelled"
            with REPAIR_LOCK:
                REPAIR.update({
                    "state": "idle" if cancelled else "error",
                    "error": "" if cancelled else why,
                    "message": "已撤回本地部署授权，下载已停止" if cancelled
                               else REPAIR.get("message", ""),
                })
            return
    with REPAIR_LOCK:
        REPAIR.update({"state": "done", "percent": 100.0, "message": "完成", "stage": ""})


def _start_pull(models: list[str]) -> tuple[bool, str]:
    """启动下载线程。已有任务在跑时直接复用，返回 (是否受理, 错误信息)。"""
    if not models:
        return True, ""
    with REPAIR_LOCK:
        if REPAIR["state"] == "running":
            return True, ""
        REPAIR.update({"state": "running", "message": "正在准备下载…", "percent": None,
                       "error": "", "model": models[0], "downloaded": 0,
                       "expected": 0, "stage": ""})
    threading.Thread(target=_pull_worker, args=(list(models),), daemon=True).start()
    return True, ""


def _pull_worker(models: list[str]) -> None:
    """下载线程入口：任何异常都要落到 REPAIR.error，不能只让线程静默退出。"""
    try:
        _pull_models(models)
    except Exception as e:  # noqa: BLE001
        with REPAIR_LOCK:
            REPAIR.update({"state": "error",
                           "error": f"下载线程异常：{e.__class__.__name__}",
                           "message": "模型下载失败，可重试"})


def _begin_pull(model: str) -> tuple[bool, str]:
    """启动清单内单个模型的下载，返回 (是否开始, 错误信息)。"""
    allowed = {m["name"] for m in MODEL_CATALOG}
    if model not in allowed:
        return False, "仅支持安装推荐清单中的模型"
    ok, why = config.model_supported(model)
    if not ok:
        return False, why
    if not _find_ollama():
        return False, "未检测到 ollama 命令，无法下载模型"
    return _start_pull([model])


def _missing_catalog_models(url: str) -> list[str]:
    """清单里尚未安装、且本机内存跑得动的模型。"""
    installed = {_norm_model(m["name"]) for m in _ollama_tags(url)}
    out = []
    for item in MODEL_CATALOG:
        if _norm_model(item["name"]) in installed:
            continue
        ok, _why = config.model_supported(item["name"])
        if ok:  # 内存不够的模型不下载，避免白占几 GB 磁盘
            out.append(item["name"])
    return out


@app.post("/api/ollama/repair")
def api_ollama_repair():
    body = _json_dict()
    if body is None:
        return jsonify({"error": "请求体必须是 JSON 对象"}), 400
    action = body.get("action")
    # 启动服务与下载模型都属于本地部署动作，必须先获得授权
    if action in {"start", "pull"} and not _consented():
        return jsonify({"state": "no_consent", "need_consent": True,
                        "error": "尚未获得本地部署授权"}), 403
    if action == "start":
        binary = _find_ollama()
        if not binary:
            return jsonify({"error": "未检测到 ollama 命令，请先安装 Ollama"}), 400
        if not _start_ollama(binary):
            return jsonify({"error": "启动 Ollama 失败，请手动打开 Ollama 应用"}), 500
        return jsonify({"ok": True})
    if action == "pull":
        model = str(body.get("model") or config.load_config()["model"]).strip()
        ok, why = _begin_pull(model)
        if not ok:
            return jsonify({"error": why}), 400
        return jsonify({"ok": True})
    return jsonify({"error": "未知操作"}), 400


@app.get("/api/ollama/repair/status")
def api_ollama_repair_status():
    with REPAIR_LOCK:
        return jsonify(dict(REPAIR))


# ---------- 首启全自动部署 ----------
# 用户解压后双击打开就该能用：定位 Ollama → 启动服务 → 拉取默认模型。
# 下载期间规则模式照常可用，进度条只作为后台状态展示，不阻塞审阅。
AUTOPILOT = {"state": "idle", "message": "", "model": ""}
AUTOPILOT_LOCK = threading.Lock()
# 撤回授权时置位：正在跑的启动/下载线程在每个事件处检查并尽快退出
DEPLOY_CANCEL = threading.Event()


def _autopilot_set(state: str, message: str, model: str = "") -> None:
    with AUTOPILOT_LOCK:
        AUTOPILOT.update({"state": state, "message": message,
                          "model": model or AUTOPILOT.get("model", ""),
                          "updated_at": time.time()})


def _wait_online(url: str, model: str, seconds: int) -> dict:
    """轮询 Ollama 直到在线或超时，返回最后一次状态。"""
    deadline = time.time() + seconds
    st = llm.status(url, model)
    while not st["online"] and time.time() < deadline:
        time.sleep(2)
        st = llm.status(url, model)
    return st


def _autopilot_worker() -> None:
    """部署线程入口：异常必须落到 error 状态，否则前端会永远轮询"下载中"。"""
    try:
        _autopilot_run()
    except Exception as e:  # noqa: BLE001
        _autopilot_set("error", f"自动部署异常：{e.__class__.__name__}")


def _autopilot_run() -> None:
    if DEPLOY_CANCEL.is_set():
        _autopilot_set("idle", "尚未获得本地部署授权")
        return
    cfg = config.load_config()
    url, model = cfg["ollama_url"], cfg["model"]
    binary = _find_ollama()
    if not binary:
        _autopilot_set("no_ollama", "未检测到 Ollama，请先安装后再启动自动部署", model)
        return
    st = llm.status(url, model)
    if not st["online"]:
        _autopilot_set("starting", "正在启动本地模型服务…", model)
        _start_ollama(binary)
        st = _wait_online(url, model, 40)
        if not st["online"]:
            _autopilot_set("error", "本地模型服务未能自动启动，可点击「自检与修复」手动排查", model)
            return
    # 两款推荐模型一次装齐，用户装完在设置里挑一个当默认即可
    missing = _missing_catalog_models(url)
    if not missing:
        _autopilot_set("ready", "本机可用的推荐模型已就绪，可在设置中切换默认模型", model)
        return
    # 用户可能在启动服务/等待期间撤回授权，下载前再确认一次
    if DEPLOY_CANCEL.is_set():
        _autopilot_set("idle", "已撤回本地部署授权，未开始下载")
        return
    _autopilot_set("pulling", "正在下载 " + "、".join(missing) + "，下载期间可用规则模式审阅", model)
    ok, why = _start_pull(missing)
    if not ok:
        _autopilot_set("error", why, model)


def _autopilot_snapshot() -> dict:
    """当前部署状态。下载阶段把 repair 的进度一并带上，前端只需轮询这一个接口。"""
    with AUTOPILOT_LOCK:
        snap = dict(AUTOPILOT)
    # 启动/下载阶段长时间无更新说明后台线程已失联，降级为 error，避免前端无限等
    if snap["state"] in {"starting", "pulling"}:
        last = snap.get("updated_at") or 0
        if snap["state"] == "pulling":
            with REPAIR_LOCK:
                last = max(last, REPAIR.get("updated_at") or 0)
        if last and time.time() - last > 180:
            _autopilot_set("error", "部署状态长时间无更新，请重试")
            with AUTOPILOT_LOCK:
                return dict(AUTOPILOT)
    if snap["state"] != "pulling":
        return snap
    with REPAIR_LOCK:
        rep = dict(REPAIR)
    if rep["state"] == "done":
        _autopilot_set("ready", "本机可用的推荐模型已就绪，可在设置中切换默认模型")
        with AUTOPILOT_LOCK:
            return dict(AUTOPILOT)
    if rep["state"] == "error":
        _autopilot_set("error", f"模型下载失败：{rep.get('error') or '未知错误'}")
        with AUTOPILOT_LOCK:
            return dict(AUTOPILOT)
    for key in ("percent", "stage", "downloaded", "expected"):
        snap[key] = rep.get(key)
    snap["message"] = rep.get("message") or snap["message"]
    return snap


@app.get("/api/consent")
def api_consent_get():
    """首次启动的本地部署授权状态。None 表示尚未询问用户。"""
    cfg = config.load_config()
    return jsonify({
        "deploy_consent": cfg.get("deploy_consent"),
        "consent_at": cfg.get("consent_at") or "",
        "memory_gb": config.memory_gb(),
    })


@app.post("/api/consent")
def api_consent_set():
    """记录用户是否同意本地部署。同意后才允许启动 Ollama 与下载模型。
    撤回授权同时关闭本地模型增强，审阅立刻切回规则引擎，状态栏才有可见变化。"""
    body = _json_dict()
    # 必须显式给出布尔 agree：空 body / 表单提交不能再把"已授权"静默改成撤回
    if body is None or not isinstance(body.get("agree"), bool):
        return jsonify({"error": "请求必须包含布尔字段 agree"}), 400
    agree = body["agree"]
    if agree:
        DEPLOY_CANCEL.clear()
    else:
        # 撤回后立即停掉正在进行的部署：置位取消标记，并把状态归零，前端不再显示"下载中"
        DEPLOY_CANCEL.set()
        _autopilot_set("idle", "已撤回本地部署授权，审阅将使用规则模式")
        with REPAIR_LOCK:
            if REPAIR["state"] == "running":
                REPAIR.update({"state": "idle", "message": "", "percent": None, "error": ""})
    cfg = config.save_config({
        "deploy_consent": agree,
        "consent_at": time.strftime("%Y-%m-%d %H:%M") if agree else "",
        "use_llm": agree,
    })
    return jsonify({"deploy_consent": cfg["deploy_consent"], "consent_at": cfg["consent_at"],
                    "use_llm": cfg.get("use_llm")})


@app.get("/api/ollama/autopilot")
def api_ollama_autopilot_status():
    return jsonify(_autopilot_snapshot())


@app.post("/api/ollama/autopilot")
def api_ollama_autopilot():
    """首启部署入口。幂等：已在部署中直接返回当前状态，不会重复拉起线程。"""
    # 未获授权不启动：部署会读取本机内存并下载数 GB 模型，必须先经用户确认
    if config.load_config().get("deploy_consent") is not True:
        return jsonify({"state": "no_consent", "need_consent": True,
                        "error": "尚未获得本地部署授权"}), 403
    with AUTOPILOT_LOCK:
        busy = AUTOPILOT["state"] in ("starting", "pulling")
        if not busy:
            AUTOPILOT.update({"state": "starting", "message": "正在检查本地模型环境…",
                              "model": config.load_config()["model"]})
    if not busy:
        threading.Thread(target=_autopilot_worker, daemon=True).start()
    return jsonify(_autopilot_snapshot())


@app.get("/api/ollama/prune")
def api_ollama_prune_preview():
    """列出推荐清单之外的本机模型，供设置页显示可释放的空间。"""
    items = _prunable_models()
    return jsonify({"items": items, "count": len(items),
                    "total_bytes": sum(i["size"] for i in items)})


@app.post("/api/ollama/prune")
def api_ollama_prune():
    """删除推荐清单之外的本机模型权重，回收磁盘空间。

    可在 body.models 中指定只删哪几个；不传则清理全部清单外模型。
    """
    # 删除本机模型权重会改动用户磁盘，未获授权时不允许
    if not _consented():
        return jsonify({"state": "no_consent", "need_consent": True,
                        "error": "尚未获得本地部署授权"}), 403
    binary = _find_ollama()
    if not binary:
        return jsonify({"error": "未检测到 ollama 命令，无法清理"}), 400
    body = _json_dict()
    if body is None:
        return jsonify({"error": "请求体必须是 JSON 对象"}), 400
    items = _prunable_models()
    wanted = body.get("models")
    if isinstance(wanted, list):
        wanted = {str(m) for m in wanted}
        items = [m for m in items if m["name"] in wanted]
    removed, failed, freed = [], [], 0
    for m in items:
        try:
            r = subprocess.run([binary, "rm", m["name"]], env=_ollama_env(binary),
                               capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.SubprocessError) as e:
            failed.append({"name": m["name"], "error": str(e)})
            continue
        if r.returncode == 0:
            removed.append(m["name"])
            freed += m["size"]
        else:
            failed.append({"name": m["name"],
                           "error": (r.stderr or r.stdout or "").strip()[:200]})
    # 删掉的正好是当前默认模型时，回落到清单第一项，避免下次启动指向不存在的模型
    cfg = config.load_config()
    if cfg["model"] in removed:
        config.save_config({"model": MODEL_CATALOG[0]["name"]})
    return jsonify({"ok": not failed, "removed": removed, "freed": freed,
                    "failed": failed, "model": config.load_config()["model"]})


@app.post("/api/reveal")
def api_reveal():
    """在系统文件管理器中打开指定路径（macOS 访达 / Windows 资源管理器 / Linux 文件管理器）。"""
    body = _json_dict()
    if body is None:
        return jsonify({"error": "请求体必须是 JSON 对象"}), 400
    path = body.get("path", "")
    if not path:
        return jsonify({"error": "缺少 path"}), 400
    p = Path(path)
    if not p.exists():
        return jsonify({"error": "文件不存在"}), 404
    try:
        if sys.platform == "darwin":
            import subprocess
            subprocess.Popen(["open", "-R", str(p)])
        elif os.name == "nt":
            os.startfile(str(p.parent))  # type: ignore[attr-defined]
        else:
            import subprocess
            subprocess.Popen(["xdg-open", str(p.parent)])
    except Exception as e:
        return jsonify({"error": f"无法打开文件管理器：{e}"}), 500
    return jsonify({"ok": True})


@app.get("/api/samples")
def api_samples():
    items = []
    for p in sorted(config.SAMPLES_DIR.glob("*")):
        if p.suffix.lower() in parsing.SUPPORTED:
            items.append({
                "name": p.name,
                "size": p.stat().st_size,
                "type": p.suffix.lstrip(".").lower(),
                "description": {
                    "软件开发服务合同.txt": "高风险示例：30% 违约金、360 天账期、单方解除",
                    "劳动合同.docx": "高风险示例：8 个月试用期、竞业限制无补偿、押金",
                    "采购框架合同.pdf": "中风险示例：金额待定、不可抗力过宽、争议解决缺失",
                }.get(p.name, "本地示例合同"),
            })
    return jsonify({"items": items})


@app.get("/api/samples/<path:name>")
def api_sample_file(name):
    p = (config.SAMPLES_DIR / name).resolve()
    if not p.is_relative_to(config.SAMPLES_DIR.resolve()) or not p.exists():
        return jsonify({"error": "示例不存在"}), 404
    return send_file(p, as_attachment=True, download_name=p.name)


@app.errorhandler(413)
def too_large(_):
    return jsonify({"error": "上传体积过大：单次最多 8 份合同、总大小不超过 200 MB"}), 413


@app.errorhandler(500)
def server_error(e):
    app.logger.error("server error: %s", e)
    return jsonify({"error": "本地服务异常，请查看终端日志"}), 500


@app.errorhandler(404)
def not_found(e):
    # /api/ 下的未知路径统一返回 JSON，避免前端拿到 Flask 的 HTML 错误页无法解析
    if request.path.startswith("/api/"):
        return jsonify({"error": "接口或资源不存在"}), 404
    return e


def _boot_log(msg: str) -> None:
    """打包模式下 stdout 不可见，将关键启动信息写入数据目录便于排障。"""
    try:
        with open(config.DATA_DIR / "boot.log", "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().isoformat(timespec='seconds')}] {msg}\n")
    except OSError:
        pass


def _warmup_async() -> None:
    """知识库预热。放到后台线程，让服务先开始监听，启动页可立即打开。"""
    try:
        warmup()
        applied = _apply_custom()  # 恢复上次启用的自定义包：法条入库、规则并列、阈值重算
        print("知识库：", KB.total, "条法条；规则：", risks.total_rules(), "条",
              f"（含自定义 {applied.get('rules', 0)} 条）" if applied.get("rules") else "")
        st = llm.status()
        print("本地模型：", "在线" if st["online"] else "离线", st["model"], st.get("error", ""))
    except Exception:
        app.logger.exception("知识库预热失败")
        # 打包模式没有控制台，异常必须落到 boot.log，否则用户只能看到白屏
        _boot_log("warmup failed:\n" + traceback.format_exc())
    finally:
        READY.set()


def _serve(port: int) -> None:
    """在当前线程启动 HTTP 服务；绑定失败会抛异常并结束线程。"""
    try:
        from waitress import serve
        serve(app, host="127.0.0.1", port=port, threads=8)
    except ImportError:
        app.run(host="127.0.0.1", port=port, debug=False, threaded=True)


if __name__ == "__main__":
    SERVING = True
    print("Contract LocalScan", VERSION)
    # 预热与监听同时开始：不再等知识库加载完才占用端口
    threading.Thread(target=_warmup_async, daemon=True).start()

    port = None
    # 先让出预启动页占用的端口，再按原顺序尝试绑定，保证浏览器地址不变
    _stop_preflight()
    _order = [p for p in ([_preflight_port] if _preflight_port else [])]
    _order += [p for p in _candidate_ports() if p not in _order]
    for cand in _order:
        if not _port_available(cand):
            continue
        th = threading.Thread(target=_serve, args=(cand,), daemon=True)
        th.start()
        if _wait_ping(cand, alive=th):
            port = cand
            break

    if port is None:
        _boot_log("startup failed: no available port")
        print("启动失败：没有可用的本机端口。请关闭占用端口的程序后重试。")
        sys.exit(1)

    url = f"http://127.0.0.1:{port}"
    if config.FROZEN:
        _boot_log(f"version={VERSION} port={port}")
        # 预启动页已经打开过浏览器时不再重复打开，避免多出一个标签页
        if not _BROWSER_OPENED:
            webbrowser.open(url)
    if port != PORT_PREFERRED:
        print(f"端口 {PORT_PREFERRED} 不可用，已改用 {port}")
    print("打开", url)

    # 主线程只负责保持进程存活；服务运行在后台线程
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass

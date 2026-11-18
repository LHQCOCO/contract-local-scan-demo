#!/usr/bin/env python3
"""从私有源仓库生成可公开的展示版，并同步到本仓库工作区。

一条命令重跑：
    python scripts/sync_public.py                 # 生成 + 自检 + 同步到当前仓库工作区
    python scripts/sync_public.py --commit "发布 v1.1.2 展示版" --push
    python scripts/sync_public.py --source ../contract-local-scan   # 用本地已有克隆，省一次下载

它做的事，按顺序：
  1. 准备私有源（默认浅克隆到 .sync-cache/private-src，之后每次只 fetch）
  2. 按 STRIP_* 规则复制到临时目录：剥掉授权校验、渠道/打包配置、密钥与运行期数据
  3. 把 scripts/showcase/files/ 下的展示版专属文件覆盖进去（CI 配置等）
  4. 把 37MB 的 data/statutes_extra.json 按法律切成 4 个分片（每片 < 15MB）
  5. 套用 scripts/showcase/patches/*.patch（去掉授权接口与前端授权 UI）
  6. 自检：无 license 模块、无密钥、无 .env、无 >20MB 单文件
  7. 同步到仓库工作区（README、.gitignore、scripts/ 下的本脚本等展示版文件不会被覆盖）

为什么用 patch 而不是在脚本里跑正则替换：patch 带上下文，私有源码改动导致补丁失配时
脚本会直接失败，而不是悄悄漏掉一段授权代码。失败时重建补丁即可，工作区不会被改动。
"""
from __future__ import annotations

import argparse
import fnmatch
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

PRIVATE_URL = "git@github.com:LHQCOCO/contract-local-scan.git"
PRIVATE_BRANCH = "main"

ROOT = Path(__file__).resolve().parent.parent          # 展示仓根目录
SHOWCASE = ROOT / "scripts" / "showcase"
CACHE = ROOT / ".sync-cache" / "private-src"
SIZE_LIMIT = 20 * 1024 * 1024                          # 单文件 20MB 上限

# 目录级剥离（相对源仓库根目录）
STRIP_DIRS = {
    ".git", "__pycache__", ".venv", ".venv-build", ".pyi-cache", "build", "dist",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".idea", ".vscode",
    "packaging",                     # 渠道配置、部署脚本与密钥占位
    "data/exports", "data/custom",   # 运行期产物
    "data/uploads", "data/tmp",
    "data/sources/repo",             # LawRefBook 快照（97MB 第三方内容，可重新下载）
    "data/sources/laws",             # 抓取缓存（28MB，scripts/fetch_statutes.py 可重建）
}

# 文件级剥离
STRIP_FILES = {
    "cls/license.py",                # 授权校验
    "LICENSE.txt",                   # 商业授权协议全文
    "ContractLocalScan.spec",        # 打包配置
    "data/statutes_extra.json",      # 37MB 单文件，运行时改读分片
    "data/config.json", "data/license.json", "data/instance.lock", "data/history.db",
    "tests/test_license.py",
    ".github/workflows/build.yml",   # 旧打包流水线，展示版改用 ci.yml
    "docs/营销文案与合规自查.md",     # 定价与渠道材料
    "docs/UNIDROIT授权请求.md",       # 对外授权往来函件
}

# 文件级剥离（通配）
STRIP_GLOBS = [
    ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "*.crt", "id_rsa*",
    "*.pyc", "*.pyo", "*.c", "*.so", "*.dylib", "*.log", "*.db-journal",
    "data/history.db-*", ".DS_Store",
]

# 展示版自己维护、同步时不允许被私有源覆盖或删除的路径
PROTECTED = {
    ".git", ".sync-cache", "README.md", ".gitignore",
    "scripts/sync_public.py", "scripts/split_statutes.py", "scripts/showcase",
}


def log(msg: str) -> None:
    print(msg, flush=True)


def run(cmd: list[str], cwd: Path | None = None) -> str:
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"命令失败：{' '.join(cmd)}\n{proc.stdout}\n{proc.stderr}")
    return proc.stdout.strip()


def prepare_source(local: str | None) -> Path:
    if local:
        src = Path(local).expanduser().resolve()
        if not (src / "app.py").exists():
            raise SystemExit(f"{src} 看起来不是 Contract LocalScan 源码目录")
        log(f"源：本地目录 {src}")
        return src
    if (CACHE / ".git").exists():
        log(f"源：更新缓存克隆 {CACHE}")
        run(["git", "-C", str(CACHE), "fetch", "--depth", "1", "origin", PRIVATE_BRANCH])
        run(["git", "-C", str(CACHE), "reset", "--hard", "FETCH_HEAD"])
    else:
        log(f"源：浅克隆 {PRIVATE_URL} → {CACHE}")
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", "--depth", "1", "--branch", PRIVATE_BRANCH, PRIVATE_URL, str(CACHE)])
    return CACHE


def _skipped(rel: str, name: str) -> bool:
    if rel in STRIP_FILES:
        return True
    parts = rel.split("/")
    for i in range(1, len(parts) + 1):
        if "/".join(parts[:i]) in STRIP_DIRS:
            return True
    return any(fnmatch.fnmatch(name, pat) for pat in STRIP_GLOBS)


def copy_tree(src: Path, dst: Path) -> tuple[int, int]:
    """按剥离规则复制；返回 (文件数, 字节数)。"""
    files = size = 0
    for path in sorted(src.rglob("*")):
        rel = path.relative_to(src).as_posix()
        if path.is_dir():
            continue
        if _skipped(rel, path.name):
            continue
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        files += 1
        size += path.stat().st_size
    return files, size


def overlay_showcase_files(build: Path) -> None:
    """覆盖展示版专属文件（CI 配置等）。"""
    base = SHOWCASE / "files"
    if not base.exists():
        return
    for path in sorted(base.rglob("*")):
        if path.is_dir():
            continue
        target = build / path.relative_to(base)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        log(f"  覆盖 {target.relative_to(build)}")


def split_statutes(src: Path, build: Path) -> None:
    """从私有源的 37MB 单文件切出分片，直接写进 build（源文件本身已被剥离）。"""
    spec = importlib.util.spec_from_file_location(
        "split_statutes", ROOT / "scripts" / "split_statutes.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    source = src / "data" / "statutes_extra.json"
    if not source.exists():
        log("  法库分片：源文件不存在，跳过（仓库将只有核心法库）")
        return
    payload = json.loads(source.read_text(encoding="utf-8"))
    paths = mod.split(payload, build / "data")
    for p in paths:
        log(f"  分片 {p.name}  {p.stat().st_size / 1024 / 1024:.2f} MB")


def apply_patches(build: Path) -> None:
    patches = sorted((SHOWCASE / "patches").glob("*.patch"))
    if not patches:
        raise SystemExit("scripts/showcase/patches/ 为空：没有补丁就无法剥离授权代码")
    # git apply 需要工作树，build 目录临时初始化为仓库
    if not (build / ".git").exists():
        run(["git", "init", "-q"], cwd=build)
    for patch in patches:
        try:
            run(["git", "apply", "--whitespace=nowarn", str(patch)], cwd=build)
        except RuntimeError as e:
            raise SystemExit(
                f"补丁 {patch.name} 无法应用，私有源码对应位置可能已改动。\n"
                f"请更新 scripts/showcase/patches/ 后重跑；工作区不会被修改。\n{e}"
            ) from e
        log(f"  应用 {patch.name}")
    shutil.rmtree(build / ".git", ignore_errors=True)


def prune_large(build: Path) -> list[str]:
    removed = []
    for path in sorted(build.rglob("*")):
        if path.is_file() and path.stat().st_size > SIZE_LIMIT:
            removed.append(f"{path.relative_to(build).as_posix()} "
                           f"({path.stat().st_size / 1024 / 1024:.1f} MB)")
            path.unlink()
    return removed


SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)(api[_-]?key|secret|password|token)\s*[:=]\s*['\"][A-Za-z0-9_\-]{12,}['\"]"),
]
LICENSE_CODE_PATTERNS = [
    re.compile(r"from cls import .*\blicense\b"),
    re.compile(r"\bcls\.license\b"),
    re.compile(r"/api/licensing"),
    re.compile(r"lic\.state\("),
]
LICENSE_UI_PATTERNS = [
    re.compile(r"授权码|兑换码|激活码|机器码"),
]
TEXT_SUFFIXES = {".py", ".js", ".html", ".css", ".sh", ".md", ".yml", ".yaml",
                 ".json", ".txt", ".toml", ".cfg", ".ini"}


def _scan(build: Path, patterns: list[re.Pattern], suffixes=TEXT_SUFFIXES) -> list[str]:
    hits = []
    for path in build.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in suffixes:
            continue
        if path.relative_to(build).as_posix().startswith("data/"):
            continue  # 法规原文属于公开文本，正文里出现「授权」是正常语义
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if any(p.search(line) for p in patterns):
                hits.append(f"{path.relative_to(build)}:{i}")
                break
    return hits


def verify(build: Path) -> list[tuple[str, bool, str]]:
    results: list[tuple[str, bool, str]] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, detail))

    add("无 cls/license.py", not (build / "cls" / "license.py").exists())
    add("无 packaging/", not (build / "packaging").exists())
    add("无 LICENSE.txt（商业授权协议）", not (build / "LICENSE.txt").exists())

    env_files = [p.relative_to(build).as_posix() for p in build.rglob(".env*")
                 if p.is_file()]
    add("无 .env", not env_files, ", ".join(env_files))

    key_files = [p.relative_to(build).as_posix() for p in build.rglob("*")
                 if p.is_file() and p.suffix.lower() in {".pem", ".key", ".p12", ".pfx"}]
    add("无密钥/证书文件", not key_files, ", ".join(key_files))

    big = [(p.relative_to(build).as_posix(), p.stat().st_size) for p in build.rglob("*")
           if p.is_file() and p.stat().st_size > SIZE_LIMIT]
    add("无 >20MB 单文件", not big,
        ", ".join(f"{n} {s / 1024 / 1024:.1f}MB" for n, s in big))

    secret_hits = _scan(build, SECRET_PATTERNS)
    add("无密钥样式字符串", not secret_hits, ", ".join(secret_hits[:5]))

    code_hits = _scan(build, LICENSE_CODE_PATTERNS)
    add("无授权校验代码/接口", not code_hits, ", ".join(code_hits[:5]))

    ui_hits = _scan(build, LICENSE_UI_PATTERNS)
    add("无授权码/兑换码文案", not ui_hits, ", ".join(ui_hits[:5]))

    add("无 data/statutes_extra.json",
        not (build / "data" / "statutes_extra.json").exists())
    shards = sorted((build / "data").glob("statutes_extra_*.json"))
    add("法库分片齐全", len(shards) >= 2, f"{len(shards)} 个分片")
    add("入口与测试齐全",
        (build / "app.py").exists() and (build / "tests").is_dir() and (build / "samples").is_dir())
    return results


def sync_to_repo(build: Path, repo: Path) -> tuple[int, int, int]:
    """把 build 树同步到 repo；返回 (新增/更新文件数, 删除文件数, 总字节数)。"""
    wanted = {p.relative_to(build).as_posix() for p in build.rglob("*") if p.is_file()}
    deleted = 0
    for path in sorted(repo.rglob("*"), reverse=True):
        rel = path.relative_to(repo).as_posix()
        if path.is_dir():
            continue
        if any(rel == p or rel.startswith(p + "/") for p in PROTECTED):
            continue
        if rel not in wanted:
            path.unlink()
            deleted += 1

    written = size = 0
    for rel in sorted(wanted):
        src = build / rel
        dst = repo / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        written += 1
        size += src.stat().st_size

    # 清掉同步后残留的空目录（保留受保护目录）
    for path in sorted(repo.rglob("*"), reverse=True):
        if not path.is_dir() or path.name == ".git":
            continue
        rel = path.relative_to(repo).as_posix()
        if any(rel == p or rel.startswith(p + "/") or p.startswith(rel + "/") for p in PROTECTED):
            continue
        if not any(path.iterdir()):
            path.rmdir()
    return written, deleted, size


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="从私有源生成并同步展示仓")
    ap.add_argument("--source", help="本地私有源码目录（默认浅克隆到 .sync-cache/）")
    ap.add_argument("--repo", default=str(ROOT), help="展示仓工作区（默认脚本所在仓库）")
    ap.add_argument("--dry-run", action="store_true", help="只生成与自检，不写入工作区")
    ap.add_argument("--commit", metavar="MSG", help="同步后提交，MSG 为提交信息")
    ap.add_argument("--push", action="store_true", help="提交后推送到 origin/main")
    args = ap.parse_args(argv)

    repo = Path(args.repo).expanduser().resolve()
    src = prepare_source(args.source)
    build = Path(tempfile.mkdtemp(prefix="cls-public-"))
    try:
        log("复制并剥离私有源码 …")
        files, size = copy_tree(src, build)
        log(f"  {files} 个文件，{size / 1024 / 1024:.1f} MB")

        log("叠加展示版文件 …")
        overlay_showcase_files(build)
        log("切分完整法库 …")
        split_statutes(src, build)
        log("应用剥离补丁 …")
        apply_patches(build)

        pruned = prune_large(build)
        for item in pruned:
            log(f"  剔除超大文件 {item}")

        total = sum(p.stat().st_size for p in build.rglob("*") if p.is_file())
        count = sum(1 for p in build.rglob("*") if p.is_file())
        log(f"构建完成：{count} 个文件，{total / 1024 / 1024:.1f} MB")

        log("自检 …")
        results = verify(build)
        for name, ok, detail in results:
            mark = "PASS" if ok else "FAIL"
            log(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""))
        failed = [r for r in results if not r[1]]
        if failed:
            log("自检未通过，已停止；工作区未改动。")
            return 1

        if args.dry_run:
            log(f"dry-run：构建结果在 {build}（不写入工作区）")
            return 0

        written, deleted, size = sync_to_repo(build, repo)
        log(f"已同步到 {repo}：写入 {written} 个文件，删除 {deleted} 个，{size / 1024 / 1024:.1f} MB")
    finally:
        shutil.rmtree(build, ignore_errors=True)

    if args.commit:
        run(["git", "add", "-A"], cwd=repo)
        run(["git", "commit", "-m", args.commit], cwd=repo)
        log(f"已提交：{args.commit}")
    if args.push:
        if not args.commit:
            raise SystemExit("--push 需要同时给 --commit")
        run(["git", "push", "origin", "HEAD:main"], cwd=repo)
        log("已推送 origin/main")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

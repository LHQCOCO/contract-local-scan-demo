"""首启部署与模型清单测试：定位 Ollama、自动部署状态机、清理清单外的模型。"""
from __future__ import annotations

import pytest

import app as app_module


@pytest.fixture
def client():
    return app_module.app.test_client()


@pytest.fixture
def fresh_autopilot(monkeypatch):
    """每个用例从干净的部署状态开始，避免用例间互相污染。"""
    monkeypatch.setattr(app_module, "AUTOPILOT",
                        {"state": "idle", "message": "", "model": ""})
    app_module.DEPLOY_CANCEL.clear()


def test_find_ollama_uses_known_install_path(monkeypatch, tmp_path):
    exe = tmp_path / "ollama"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    monkeypatch.setattr(app_module, "_OLLAMA_PATHS", (str(exe),))
    monkeypatch.setattr(app_module.shutil, "which", lambda _n: None)
    assert app_module._find_ollama() == str(exe)


def test_find_ollama_falls_back_to_which(monkeypatch):
    monkeypatch.setattr(app_module, "_OLLAMA_PATHS", ())
    monkeypatch.setattr(app_module.shutil, "which", lambda _n: "/opt/homebrew/bin/ollama")
    assert app_module._find_ollama() == "/opt/homebrew/bin/ollama"


def test_find_ollama_returns_empty_when_absent(monkeypatch):
    monkeypatch.setattr(app_module, "_OLLAMA_PATHS", ())
    monkeypatch.setattr(app_module.shutil, "which", lambda _n: None)
    assert app_module._find_ollama() == ""


def test_model_catalog_requires_exact_name():
    # 本机装着 7b 不等于配置里的 3b 可用
    st = {"models": ["qwen2.5:7b", "qwen2.5:7b-instruct"]}
    catalog = {m["name"]: m for m in app_module._model_catalog(st)}
    assert catalog["qwen2.5:3b"]["installed"] is False
    assert catalog["gemma3:4b"]["installed"] is False

    st = {"models": ["qwen2.5:3b", "gemma3:4b"]}
    catalog = {m["name"]: m for m in app_module._model_catalog(st)}
    assert catalog["qwen2.5:3b"]["installed"] is True
    assert catalog["gemma3:4b"]["installed"] is True


def test_prunable_models_excludes_catalog(monkeypatch):
    monkeypatch.setattr(app_module, "_ollama_tags", lambda _u: [
        {"name": "qwen2.5:3b", "size": 10},
        {"name": "gemma3:4b", "size": 20},
        {"name": "qwen2.5:7b", "size": 30},
        {"name": "qwen2.5:7b-instruct", "size": 40},
    ])
    names = [m["name"] for m in app_module._prunable_models()]
    assert names == ["qwen2.5:7b", "qwen2.5:7b-instruct"]


def test_prune_preview_endpoint(monkeypatch, client):
    monkeypatch.setattr(app_module, "_ollama_tags", lambda _u: [
        {"name": "qwen2.5:3b", "size": 1},
        {"name": "qwen2.5:7b", "size": 9 * 10 ** 9},
    ])
    d = client.get("/api/ollama/prune").get_json()
    assert d["count"] == 1
    assert d["total_bytes"] == 9 * 10 ** 9


def test_prune_removes_only_out_of_catalog_and_resets_default(monkeypatch, client):
    removed_calls = []

    class _Done:
        returncode = 0
        stdout = ""
        stderr = ""

    store = {"cfg": {**app_module.config.DEFAULT_CONFIG, "model": "qwen2.5:7b",
                     "deploy_consent": True}}
    monkeypatch.setattr(app_module.config, "load_config", lambda: dict(store["cfg"]))
    monkeypatch.setattr(app_module.config, "save_config",
                        lambda patch: (store["cfg"].update(patch), dict(store["cfg"]))[1])
    monkeypatch.setattr(app_module, "_find_ollama", lambda: "/fake/ollama")
    monkeypatch.setattr(app_module, "_prunable_models",
                        lambda: [{"name": "qwen2.5:7b", "size": 9 * 10 ** 9}])
    monkeypatch.setattr(app_module.subprocess, "run",
                        lambda cmd, **kw: (removed_calls.append(cmd), _Done())[1])

    d = client.post("/api/ollama/prune", json={}).get_json()
    assert removed_calls == [["/fake/ollama", "rm", "qwen2.5:7b"]]
    assert d["removed"] == ["qwen2.5:7b"]
    assert d["freed"] == 9 * 10 ** 9
    # 删掉的正好是当前默认模型时，回落为清单第一项
    assert store["cfg"]["model"] == app_module.MODEL_CATALOG[0]["name"]
    assert d["model"] == app_module.MODEL_CATALOG[0]["name"]


def test_prune_can_target_specific_models(monkeypatch, client):
    """指定 models 时只删指定的那几个，其余清单外模型保留。"""
    calls = []

    class _Done:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(app_module.config, "load_config",
                        lambda: {"deploy_consent": True, "model": "qwen2.5:3b"})
    monkeypatch.setattr(app_module, "_find_ollama", lambda: "/fake/ollama")
    monkeypatch.setattr(app_module, "_prunable_models", lambda: [
        {"name": "qwen3:4b", "size": 2},
        {"name": "qwen2.5:7b", "size": 3},
    ])
    monkeypatch.setattr(app_module.subprocess, "run",
                        lambda cmd, **kw: (calls.append(cmd), _Done())[1])

    d = client.post("/api/ollama/prune", json={"models": ["qwen2.5:7b", "qwen2.5:7b"]}).get_json()
    assert calls == [["/fake/ollama", "rm", "qwen2.5:7b"]]
    assert d["removed"] == ["qwen2.5:7b"]


def test_prune_without_ollama_returns_400(monkeypatch, client):
    monkeypatch.setattr(app_module.config, "load_config",
                        lambda: {"deploy_consent": True})
    monkeypatch.setattr(app_module, "_find_ollama", lambda: "")
    r = client.post("/api/ollama/prune", json={})
    assert r.status_code == 400


def test_prune_requires_consent(monkeypatch, client):
    monkeypatch.setattr(app_module.config, "load_config",
                        lambda: {"deploy_consent": None})
    r = client.post("/api/ollama/prune", json={})
    assert r.status_code == 403
    assert r.get_json()["need_consent"] is True


def test_repair_pull_rejects_model_outside_catalog(monkeypatch, client):
    monkeypatch.setattr(app_module.config, "load_config",
                        lambda: {"deploy_consent": True})
    r = client.post("/api/ollama/repair", json={"action": "pull", "model": "qwen2.5:7b"})
    assert r.status_code == 400
    assert "推荐清单" in r.get_json()["error"]


def test_repair_pull_requires_consent(monkeypatch, client):
    monkeypatch.setattr(app_module.config, "load_config",
                        lambda: {"deploy_consent": None})
    r = client.post("/api/ollama/repair", json={"action": "pull", "model": "qwen2.5:3b"})
    assert r.status_code == 403
    assert r.get_json()["state"] == "no_consent"


def test_autopilot_reports_missing_ollama(monkeypatch, fresh_autopilot):
    monkeypatch.setattr(app_module, "_find_ollama", lambda: "")
    app_module._autopilot_worker()
    assert app_module.AUTOPILOT["state"] == "no_ollama"


def test_autopilot_ready_when_model_installed(monkeypatch, fresh_autopilot):
    monkeypatch.setattr(app_module, "_find_ollama", lambda: "/fake/ollama")
    monkeypatch.setattr(app_module.llm, "status",
                        lambda *a, **k: {"online": True, "model_ready": True,
                                         "models": ["qwen2.5:3b"], "error": ""})
    # 两款推荐模型都在本机，无需再下载
    monkeypatch.setattr(app_module, "_ollama_tags", lambda _u: [
        {"name": m["name"], "size": 1} for m in app_module.MODEL_CATALOG
    ])
    app_module._autopilot_worker()
    assert app_module.AUTOPILOT["state"] == "ready"


def test_autopilot_starts_service_then_pulls_both_models(monkeypatch, fresh_autopilot):
    started = []
    monkeypatch.setattr(app_module, "_find_ollama", lambda: "/fake/ollama")
    # 第一次离线，启动后变为在线但模型缺失
    states = iter([
        {"online": False, "model_ready": False, "models": [], "error": ""},
        {"online": True, "model_ready": False, "models": [], "error": ""},
    ])
    monkeypatch.setattr(app_module.llm, "status",
                        lambda *a, **k: next(states, {"online": True, "model_ready": False,
                                                      "models": [], "error": ""}))
    monkeypatch.setattr(app_module, "_start_ollama", lambda b: started.append(b) or True)
    monkeypatch.setattr(app_module, "_wait_online",
                        lambda *a, **k: {"online": True, "model_ready": False,
                                         "models": [], "error": ""})
    monkeypatch.setattr(app_module, "_ollama_tags", lambda _u: [])
    monkeypatch.setattr(app_module.config, "model_supported", lambda _m: (True, ""))
    pulled = []
    monkeypatch.setattr(app_module, "_start_pull", lambda m: (pulled.append(list(m)), (True, ""))[1])

    app_module._autopilot_worker()
    assert started == ["/fake/ollama"]
    # 首启一次装齐两款，用户之后自己选默认
    assert pulled == [[m["name"] for m in app_module.MODEL_CATALOG]]
    assert app_module.AUTOPILOT["state"] == "pulling"


def test_missing_catalog_models_skips_installed_and_unsupported(monkeypatch):
    monkeypatch.setattr(app_module, "_ollama_tags", lambda _u: [
        {"name": "qwen2.5:3b", "size": 1},
    ])
    # gemma3:4b 因内存不足跳过，避免白占磁盘
    monkeypatch.setattr(app_module.config, "model_supported",
                        lambda m: (m != "gemma3:4b", "内存不足"))
    assert app_module._missing_catalog_models("http://127.0.0.1:11434") == []


def test_missing_catalog_models_returns_uninstalled(monkeypatch):
    monkeypatch.setattr(app_module, "_ollama_tags", lambda _u: [])
    monkeypatch.setattr(app_module.config, "model_supported", lambda _m: (True, ""))
    assert app_module._missing_catalog_models("http://127.0.0.1:11434") == [
        m["name"] for m in app_module.MODEL_CATALOG
    ]


def test_stage_label_maps_ollama_status():
    assert app_module._stage_label("pulling manifest") == "读取模型清单"
    assert app_module._stage_label("verifying sha256 digest") == "校验文件完整性"
    assert app_module._stage_label("writing manifest") == "写入模型清单"
    assert app_module._stage_label("removing any unused layers") == "清理旧分片"
    assert app_module._stage_label("pulling f06e91e") == "下载模型中"
    assert app_module._stage_label("") == "处理中"


def test_autopilot_snapshot_finishes_when_pull_done(monkeypatch, fresh_autopilot):
    monkeypatch.setattr(app_module, "AUTOPILOT",
                        {"state": "pulling", "message": "下载中", "model": "qwen2.5:3b"})
    monkeypatch.setattr(app_module, "REPAIR",
                        {"state": "done", "percent": 100.0, "message": "完成", "error": "",
                         "model": "qwen2.5:3b", "downloaded": 0, "expected": 0, "stage": ""})
    snap = app_module._autopilot_snapshot()
    assert snap["state"] == "ready"


def test_autopilot_snapshot_passes_progress_through(monkeypatch, fresh_autopilot):
    monkeypatch.setattr(app_module, "AUTOPILOT",
                        {"state": "pulling", "message": "下载中", "model": "qwen2.5:3b"})
    monkeypatch.setattr(app_module, "REPAIR",
                        {"state": "running", "percent": 42.0, "message": "已下载 0.8 GB / 约 1.9 GB",
                         "error": "", "model": "qwen2.5:3b", "downloaded": 0,
                         "expected": 0, "stage": "读取模型清单"})
    snap = app_module._autopilot_snapshot()
    assert snap["state"] == "pulling"
    assert snap["percent"] == 42.0
    assert snap["stage"] == "读取模型清单"


def test_autopilot_post_is_idempotent(monkeypatch, fresh_autopilot, client):
    spawned = []

    class _Thread:
        def __init__(self, target=None, daemon=None):
            spawned.append(target)

        def start(self):
            pass

    monkeypatch.setattr(app_module.threading, "Thread", _Thread)
    # 部署入口要求先获得用户授权
    monkeypatch.setattr(app_module.config, "load_config",
                        lambda: {"deploy_consent": True, "model": "qwen2.5:3b"})
    first = client.post("/api/ollama/autopilot", json={}).get_json()
    assert first["state"] == "starting"
    assert len(spawned) == 1
    # 部署中再次请求不会重复拉起线程
    app_module.AUTOPILOT["state"] = "starting"
    client.post("/api/ollama/autopilot", json={})
    assert len(spawned) == 1


def test_autopilot_requires_consent(monkeypatch, fresh_autopilot, client):
    """未授权时不得启动部署：既不拉起线程，也不触碰本机模型。"""
    spawned = []

    class _Thread:
        def __init__(self, target=None, daemon=None):
            spawned.append(target)

        def start(self):
            pass

    monkeypatch.setattr(app_module.threading, "Thread", _Thread)
    monkeypatch.setattr(app_module.config, "load_config",
                        lambda: {"deploy_consent": None})
    r = client.post("/api/ollama/autopilot", json={})
    assert r.status_code == 403
    body = r.get_json()
    assert body["need_consent"] is True
    assert body["state"] == "no_consent"
    assert spawned == []
    assert app_module.AUTOPILOT["state"] == "idle"


def test_consent_get_reports_pending(monkeypatch, client):
    monkeypatch.setattr(app_module.config, "load_config",
                        lambda: {"deploy_consent": None, "consent_at": ""})
    body = client.get("/api/consent").get_json()
    assert body["deploy_consent"] is None
    assert body["consent_at"] == ""
    assert isinstance(body["memory_gb"], float)


def test_consent_post_records_decision(monkeypatch, client):
    saved = {}

    def fake_save(patch):
        saved.update(patch)
        return {"deploy_consent": patch["deploy_consent"],
                "consent_at": patch["consent_at"],
                "use_llm": patch.get("use_llm")}

    monkeypatch.setattr(app_module.config, "save_config", fake_save)
    body = client.post("/api/consent", json={"agree": True}).get_json()
    assert saved["deploy_consent"] is True
    assert saved["consent_at"]  # 记录授权时间
    assert saved["use_llm"] is True  # 同意即启用本地模型
    assert body["deploy_consent"] is True

    saved.clear()
    body = client.post("/api/consent", json={"agree": False}).get_json()
    assert saved["deploy_consent"] is False
    assert saved["consent_at"] == ""
    assert saved["use_llm"] is False  # 撤回后审阅切回规则引擎
    assert body["deploy_consent"] is False

"""模型内存门槛测试：内存不足的模型不允许选择，也不允许在审阅时加载。"""
from __future__ import annotations

import app as app_module
from cls import config


def test_model_memory_need_by_param_count():
    assert config.model_memory_need("qwen2.5:3b") == 8
    assert config.model_memory_need("qwen3:4b") == 8
    # 实测结论：gemma3:4b 在 8 GB 机器上仍可运行，门槛保持 8 GB
    assert config.model_memory_need("gemma3:4b") == 8
    assert config.model_memory_need("gemma3") == 8
    assert config.model_memory_need("qwen2.5:7b-instruct") == 16
    assert config.model_memory_need("llama3.1:8b") == 16
    assert config.model_memory_need("qwen2.5:14b") == 24
    assert config.model_memory_need("qwen2.5:70b") == 64
    assert config.model_memory_need("未知名模型") == 0


def test_model_memory_recommend_is_label_only():
    # 推荐档位只用于界面标注，不影响可选性
    assert config.model_memory_recommend("gemma3:4b") == 16
    assert config.model_memory_recommend("gemma3") == 16
    assert config.model_memory_recommend("qwen2.5:3b") == 8
    # 未登记推荐档位的模型回退到最低门槛
    assert config.model_memory_recommend("qwen2.5:7b-instruct") == 16


def test_model_supported_on_8gb_machine(monkeypatch):
    monkeypatch.setattr(config, "memory_gb", lambda: 8.0)
    ok, why = config.model_supported("qwen2.5:3b")
    assert ok and why == ""
    ok, why = config.model_supported("qwen2.5:7b-instruct")
    assert not ok
    assert "16 GB" in why and "8 GB" in why
    # gemma3:4b 推荐 16 GB，但 8 GB 机器上仍可选（只是更慢）
    ok, _why = config.model_supported("gemma3:4b")
    assert ok
    assert config.model_memory_recommend("gemma3:4b") == 16


def test_model_supported_on_16gb_machine(monkeypatch):
    monkeypatch.setattr(config, "memory_gb", lambda: 16.0)
    ok, _ = config.model_supported("qwen2.5:7b-instruct")
    assert ok
    # 16 GB 机器上 8 GB 档与 16 GB 档都可选
    assert config.model_supported("qwen2.5:3b")[0]
    assert config.model_supported("gemma3:4b")[0]


def test_model_supported_unknown_memory_does_not_block(monkeypatch):
    # 取不到内存信息时按宽松处理，避免把用户挡在门外
    monkeypatch.setattr(config, "memory_gb", lambda: 0.0)
    ok, _ = config.model_supported("qwen2.5:7b-instruct")
    assert ok


def test_config_api_rejects_oversized_model(monkeypatch):
    monkeypatch.setattr(config, "memory_gb", lambda: 8.0)
    resp = app_module.app.test_client().post("/api/config", json={"model": "qwen2.5:7b-instruct"})
    assert resp.status_code == 400
    assert "运行内存" in resp.get_json()["error"]


def test_config_api_accepts_small_model(monkeypatch):
    monkeypatch.setattr(config, "memory_gb", lambda: 8.0)
    monkeypatch.setattr(config, "save_config", lambda patch: {**config.DEFAULT_CONFIG, **patch})
    resp = app_module.app.test_client().post("/api/config", json={"model": "qwen2.5:3b"})
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True


def test_status_exposes_memory_and_model_options(monkeypatch):
    monkeypatch.setattr(config, "memory_gb", lambda: 8.0)
    monkeypatch.setattr(app_module.llm, "status", lambda *a, **k: {
        "url": "http://127.0.0.1:11434", "model": "qwen2.5:3b", "local_endpoint": True,
        "online": True, "models": ["qwen2.5:3b", "qwen2.5:7b-instruct"],
        "model_ready": True, "error": "",
    })
    body = app_module.app.test_client().get("/api/status").get_json()
    assert body["sysinfo"]["memory_gb"] == 8.0
    # 下拉框只提供推荐清单，本机装着 7b 也不会出现在可选列表里
    opts = {m["name"]: m for m in body["model_options"]}
    assert set(opts) == {"qwen2.5:3b", "gemma3:4b"}
    assert opts["qwen2.5:3b"]["supported"] is True
    # gemma3:4b 在 8 GB 机器上仍可选，但推荐档位标注为 16 GB
    assert opts["gemma3:4b"]["supported"] is True
    assert opts["gemma3:4b"]["need_gb"] == 8
    assert opts["gemma3:4b"]["rec_gb"] == 16
    assert body["ollama"]["model_supported"] is True


def test_model_options_keep_legacy_selection_disabled(monkeypatch):
    # 旧配置里选中的模型不在清单内时保留一项，避免下拉框空白；内存不足时置灰
    monkeypatch.setattr(config, "memory_gb", lambda: 8.0)
    monkeypatch.setattr(app_module.llm, "status", lambda *a, **k: {
        "url": "http://127.0.0.1:11434", "model": "qwen2.5:7b-instruct", "local_endpoint": True,
        "online": True, "models": ["qwen2.5:7b-instruct"], "model_ready": True, "error": "",
    })
    opts = {m["name"]: m for m in app_module._model_options(app_module.llm.status())}
    assert opts["qwen2.5:7b-instruct"]["supported"] is False
    assert opts["qwen2.5:7b-instruct"]["need_gb"] == 16

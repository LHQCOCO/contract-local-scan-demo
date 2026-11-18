"""/api/review 路由校验测试：文件数量限制。"""
from __future__ import annotations

import io

import app as app_module


def _client():
    return app_module.app.test_client()


def _file(name: str = "c.txt", content: bytes = b"test"):
    return (io.BytesIO(content), name)


def test_review_rejects_empty_files():
    resp = _client().post("/api/review", data={}, content_type="multipart/form-data")
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "请至少选择一份合同文件"


def test_review_rejects_more_than_max_files(monkeypatch):
    # 让后台 worker 不真正跑，避免测试产生副作用
    monkeypatch.setattr(app_module, "_job_worker", lambda *a, **k: None)
    files = {"files": [_file(f"c{i}.txt") for i in range(app_module.MAX_FILES + 1)]}
    resp = _client().post("/api/review", data=files, content_type="multipart/form-data")
    assert resp.status_code == 400
    assert resp.get_json()["error"] == f"单次最多 {app_module.MAX_FILES} 份合同"


def test_review_accepts_up_to_max_files(monkeypatch):
    monkeypatch.setattr(app_module, "_job_worker", lambda *a, **k: None)
    files = {"files": [_file(f"c{i}.txt") for i in range(app_module.MAX_FILES)]}
    resp = _client().post("/api/review", data=files, content_type="multipart/form-data")
    assert resp.status_code == 202
    body = resp.get_json()
    assert "job_id" in body
    assert body["count"] == app_module.MAX_FILES


def test_review_rejects_zero_byte_files(monkeypatch):
    # 0 字节文件应在上传阶段直接拒绝，而非进入后台任务后才报"文件为空"
    monkeypatch.setattr(app_module, "_job_worker", lambda *a, **k: None)
    files = {"files": [_file("empty.txt", content=b"")]}
    resp = _client().post("/api/review", data=files, content_type="multipart/form-data")
    assert resp.status_code == 400
    assert "文件为空" in resp.get_json()["error"]


def test_review_ignores_llm_flag_without_consent(monkeypatch):
    # 未授权时即使前端传 use_llm=true，也只能按规则模式执行
    monkeypatch.setattr(app_module, "_job_worker", lambda *a, **k: None)
    monkeypatch.setattr(app_module.config, "load_config", lambda: {"deploy_consent": None})
    files = {"files": [_file()], "use_llm": "true"}
    resp = _client().post("/api/review", data=files, content_type="multipart/form-data")
    assert resp.status_code == 202
    job_id = resp.get_json()["job_id"]
    assert app_module.JOBS[job_id]["use_llm"] is False
    app_module.JOBS.pop(job_id, None)


def test_review_allows_llm_flag_after_consent(monkeypatch):
    monkeypatch.setattr(app_module, "_job_worker", lambda *a, **k: None)
    monkeypatch.setattr(app_module.config, "load_config",
                        lambda: {"deploy_consent": True, "use_llm": True})
    files = {"files": [_file()], "use_llm": "true"}
    resp = _client().post("/api/review", data=files, content_type="multipart/form-data")
    assert resp.status_code == 202
    job_id = resp.get_json()["job_id"]
    assert app_module.JOBS[job_id]["use_llm"] is True
    app_module.JOBS.pop(job_id, None)

"""运行期外联自检。

把「合同不上云」从一句文案变成可验证的运行时事实：在进程内拦截 socket
连接，任何指向本机回环之外的目标都会被记录并阻断。侧栏「外部访问」状态
即由这里的计数驱动，而非写死的 0。

拦截是「先拦后记」：列表里的每一条，都是已经被挡回本机、没有发出去的
尝试，因此它是零外联的证据，而不是泄漏的证据。

覆盖范围：本进程（Flask 服务与规则引擎、解析、报告）内的所有 socket 连接。
Ollama 等独立进程的流量不在本进程内，本模块观察不到。
"""
from __future__ import annotations

import os
import socket
import threading
import time
import traceback
from collections import deque

_LOCK = threading.Lock()
_APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_EVENTS: deque = deque(maxlen=80)
_STATE = {
    "attempts": 0,
    "blocked": 0,
    "last_target": "",
    "installed": False,
    "lockdown": False,
    "locked_at": "",
}

_real_connect = socket.socket.connect
_LOOPBACK_NAMES = {"127.0.0.1", "::1", "localhost", "0.0.0.0", ""}


def _is_loopback_host(host: str) -> bool:
    """严格判定：只有字面量回环地址算本机，域名形式一律按外部处理。"""
    return host.startswith("127.") or host in {"::1", "::1:0"}


def _is_local(address, strict: bool = False) -> bool:
    # AF_UNIX 等本地套接字地址为字符串，一律放行
    if not isinstance(address, tuple) or not address:
        return True
    host = str(address[0])
    if strict:
        return _is_loopback_host(host)
    return host in _LOOPBACK_NAMES or host.startswith("127.")


def _origin() -> str:
    """回溯调用来源，优先取本应用内的帧，便于定位是哪段代码在尝试外联。"""
    activity = traceback.extract_stack()
    fallback = ""
    for frame in reversed(activity):
        path = frame.filename or ""
        if path == __file__ or "netguard.py" in path:
            continue
        candidate = f"{frame.name}:{frame.lineno}"
        if path.startswith(_APP_DIR):
            return f"{os.path.relpath(path, _APP_DIR)} · {candidate}"
        if not fallback:
            fallback = f"{os.path.basename(path)} · {candidate}"
    return fallback or "未知来源"


def _connect(self, address):  # noqa: ANN001
    strict = _STATE["lockdown"]
    if _is_local(address, strict):
        return _real_connect(self, address)
    host, port = str(address[0]), address[1] if len(address) > 1 else ""
    with _LOCK:
        _STATE["attempts"] += 1
        _STATE["blocked"] += 1
        _STATE["last_target"] = f"{host}:{port}"
        _EVENTS.append({
            "time": time.strftime("%H:%M:%S"),
            "target": f"{host}:{port}",
            "origin": _origin(),
            "strict": strict,
        })
    raise OSError(101, "Contract LocalScan 已拦截外部网络连接：合同数据不出本机")


def install() -> None:
    if _STATE["installed"]:
        return
    socket.socket.connect = _connect
    _STATE["installed"] = True


def set_lockdown(on: bool) -> dict:
    with _LOCK:
        _STATE["lockdown"] = bool(on)
        _STATE["locked_at"] = time.strftime("%Y-%m-%d %H:%M:%S") if on else ""
    return snapshot()


def clear_events() -> dict:
    with _LOCK:
        _EVENTS.clear()
        _STATE["attempts"] = 0
        _STATE["blocked"] = 0
        _STATE["last_target"] = ""
    return snapshot()


def snapshot(limit: int = 20) -> dict:
    with _LOCK:
        events = list(_EVENTS)[-limit:]
        return {
            "active": _STATE["installed"],
            "attempts": _STATE["attempts"],
            "blocked": _STATE["blocked"],
            "last_target": _STATE["last_target"],
            "lockdown": _STATE["lockdown"],
            "locked_at": _STATE["locked_at"],
            "events": list(reversed(events)),
        }

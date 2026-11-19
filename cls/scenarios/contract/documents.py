"""进程内文档暂存：只保存正文与摘要标识，不落盘、不进 trace。"""
from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass, field


@dataclass
class Document:
    doc_id: str
    text: str
    name_sha8: str
    meta: dict = field(default_factory=dict)
    state: dict = field(default_factory=dict)


class DocStore:
    def __init__(self) -> None:
        self._docs: dict[str, Document] = {}
        self._lock = threading.RLock()

    def add(self, text: str, filename: str = "", meta: dict | None = None) -> str:
        doc_id = hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:8]
        with self._lock:
            if doc_id not in self._docs:
                self._docs[doc_id] = Document(
                    doc_id=doc_id,
                    text=text or "",
                    name_sha8=hashlib.sha256((filename or "").encode("utf-8")).hexdigest()[:8],
                    meta=dict(meta or {}),
                )
        return doc_id

    def get(self, doc_id: str) -> Document | None:
        with self._lock:
            return self._docs.get(doc_id)

    def clear(self) -> None:
        with self._lock:
            self._docs.clear()


DOCS = DocStore()

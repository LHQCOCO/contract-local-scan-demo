"""本地审阅历史（SQLite），数据只写入本机 data/history.db。"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path

from .config import DB_PATH


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""CREATE TABLE IF NOT EXISTS reviews (
        id TEXT PRIMARY KEY,
        created_at INTEGER NOT NULL,
        filename TEXT NOT NULL,
        ext TEXT,
        score INTEGER,
        level TEXT,
        high INTEGER,
        medium INTEGER,
        low INTEGER,
        contract_type TEXT,
        payload TEXT NOT NULL
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS jobs (
        id TEXT PRIMARY KEY,
        created_at INTEGER,
        state TEXT,
        payload TEXT
    )""")
    return conn


def save_review(result: dict) -> str:
    rid = uuid.uuid4().hex[:12]
    result["id"] = rid
    result["created_at"] = int(time.time() * 1000)
    counts = result.get("counts", {})
    with _conn() as c:
        c.execute(
            "INSERT INTO reviews (id, created_at, filename, ext, score, level, high, medium, low, contract_type, payload)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (rid, result["created_at"], result["filename"], result.get("ext", ""),
             result.get("score", 0), result.get("level", "low"), counts.get("high", 0),
             counts.get("medium", 0), counts.get("low", 0), result.get("contract_type", ""),
             json.dumps(result, ensure_ascii=False)),
        )
    return rid


def get_review(rid: str) -> dict | None:
    with _conn() as c:
        row = c.execute("SELECT payload FROM reviews WHERE id=?", (rid,)).fetchone()
    return json.loads(row[0]) if row else None


def list_reviews(limit: int = 100) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT id, created_at, filename, ext, score, level, high, medium, low, contract_type"
            " FROM reviews ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    keys = ["id", "created_at", "filename", "ext", "score", "level", "high", "medium", "low", "contract_type"]
    return [dict(zip(keys, r)) for r in rows]


def delete_review(rid: str) -> bool:
    with _conn() as c:
        cur = c.execute("DELETE FROM reviews WHERE id=?", (rid,))
    return cur.rowcount > 0


def clear_reviews() -> int:
    with _conn() as c:
        cur = c.execute("DELETE FROM reviews")
    return cur.rowcount


def stats() -> dict:
    with _conn() as c:
        total = c.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]
        avg = c.execute("SELECT AVG(score) FROM reviews").fetchone()[0] or 0
        high = c.execute("SELECT COUNT(*) FROM reviews WHERE level='high'").fetchone()[0]
    return {"total": total, "avg_score": round(avg), "high_risk": high}

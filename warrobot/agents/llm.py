"""Local LLM helper: Ollama only for text tasks, cached, guarded.

Hard-won facts on this machine (4 vCPU, 8 GB RAM, no GPU):
    qwen2.5:1.5b  ~15 s / 60 tokens, weak Persian
    gemma2:2b     ~47 s / 80 tokens, worse
So Persian reasoning stays with the architect; the local model is used only for
short ENGLISH summaries/classifications, and never during market hours (it would
compete with Chrome for RAM).

    from agents.llm import ask
    txt = ask("Summarize these log lines in one English sentence: ...")
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import urllib.request
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
STATE = BASE / "state"
CACHE_DB = STATE / "agents_cache.sqlite"
OLLAMA = "http://127.0.0.1:11434"
MODEL = "qwen2.5:1.5b-instruct"
TIMEOUT = 240
MARKET_QUIET = ((8, 30), (13, 0))     # no local model while the market is open


def _market_hours() -> bool:
    try:
        import sys
        sys.path.insert(0, str(BASE))
        import market_cal
        now = market_cal.now_tehran()
        if not market_cal.market_day():
            return False
        h0, m0 = MARKET_QUIET[0]
        h1, m1 = MARKET_QUIET[1]
        return (now.hour, now.minute) >= (h0, m0) and (now.hour, now.minute) <= (h1, m1)
    except Exception:
        return False


def _cache() -> sqlite3.Connection:
    STATE.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(CACHE_DB)
    db.execute("CREATE TABLE IF NOT EXISTS answers (k TEXT PRIMARY KEY, v TEXT, at TEXT)")
    return db


def _key(prompt: str) -> str:
    return hashlib.sha256(f"{MODEL}|{prompt}".encode("utf-8")).hexdigest()


def ask(prompt: str, ttl_days: int = 7, allow_market: bool = False,
        timeout: int = TIMEOUT) -> str | None:
    """Cached, guarded single-shot generation. None when unavailable."""
    if _market_hours() and not allow_market:
        return None
    key = _key(prompt)
    db = _cache()
    row = db.execute("SELECT v, at FROM answers WHERE k=?", (key,)).fetchone()
    if row:
        try:
            age = (datetime.now() - datetime.fromisoformat(row[1])).days
            if age <= ttl_days:
                return row[0]
        except Exception:
            pass
    body = json.dumps({"model": MODEL, "prompt": prompt, "stream": False,
                       "options": {"num_predict": 220, "temperature": 0.2}}).encode()
    try:
        req = urllib.request.Request(f"{OLLAMA}/api/generate", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
        text = (data.get("response") or "").strip()
    except Exception:
        return None
    if text:
        db.execute("INSERT OR REPLACE INTO answers VALUES (?,?,?)",
                   (key, text, datetime.now().isoformat(timespec="seconds")))
        db.commit()
    return text or None


def healthy() -> bool:
    try:
        with urllib.request.urlopen(f"{OLLAMA}/api/tags", timeout=5) as r:
            return r.status == 200
    except Exception:
        return False

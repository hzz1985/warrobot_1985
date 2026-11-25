"""Self-calibrating clock for the bot.

The host clock was 30 minutes behind real UTC, which made the bot miss the
09:00-09:05 pre-open window. A fixed `system.clock_offset_sec` fixes that day,
but it silently breaks again if the OS clock is corrected or drifts further.

This module measures the offset against public HTTP servers and keeps the config
value in sync, so `now_tehran()` always shows real Tehran time.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Optional

CONFIG = Path(__file__).resolve().parents[2] / "config" / "config.json"
# broker + TSETMC first: they are reachable from Iran and are the clocks that matter
# for the 08:45:01 order; the global ones are fallbacks.
SOURCES = ("https://api.meibours.ir", "https://online.meibours.ir", "https://cdn.tsetmc.com",
           "https://www.google.com", "https://www.cloudflare.com", "https://www.bing.com")


def measure_offset() -> Optional[float]:
    """Seconds to add to the OS clock so it matches real UTC (None if unmeasurable).

    Median of up to three independent sources, so one wrong/cached Date header
    cannot shift the bot clock."""
    vals = []
    for url in SOURCES:
        v = _measure_one(url)
        if v is not None:
            vals.append(v)
        if len(vals) >= 3:
            break
    if not vals:
        return None
    vals.sort()
    return float(vals[len(vals) // 2])


def _measure_one(url: str) -> Optional[float]:
    for _ in (0,):
        try:
            before = datetime.now(timezone.utc)
            try:
                with urllib.request.urlopen(urllib.request.Request(url, method="HEAD"), timeout=8) as r:
                    header = r.headers.get("Date")
            except urllib.error.HTTPError as he:      # a 404/405 still carries a Date
                header = he.headers.get("Date") if he.headers else None
            after = datetime.now(timezone.utc)
            if not header:
                continue
            real = parsedate_to_datetime(header)
            # the request took (after-before); use the midpoint to reduce latency bias
            mid = before + (after - before) / 2
            offset = (real - mid).total_seconds()
            if abs(offset) > 12 * 3600:      # a timezone mix-up, not a clock error
                return None
            return float(round(offset))
        except Exception:
            return None
    return None


def current_offset() -> float:
    try:
        return float(json.loads(CONFIG.read_text(encoding="utf-8"))
                     .get("system", {}).get("clock_offset_sec", 0) or 0)
    except Exception:
        return 0.0


def apply_offset(offset: float) -> bool:
    """Persist the measured offset into config.json (now_tehran reads it live)."""
    try:
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
        cfg.setdefault("system", {})["clock_offset_sec"] = float(offset)
        CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False


def calibrate(min_change_sec: float = 30.0, force: bool = False) -> dict:
    """Measure and (only if it matters) update the stored offset."""
    measured = measure_offset()
    stored = current_offset()
    result = {"stored": stored, "measured": measured, "changed": False}
    if measured is None:
        return result
    if force or abs(measured - stored) >= min_change_sec:
        result["changed"] = apply_offset(measured)
    return result

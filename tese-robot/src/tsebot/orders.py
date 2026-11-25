# -*- coding: utf-8 -*-
"""TSE Bot v2 — order intents and idempotency.

The v1 duplicate-buy bug in one sentence: it decided "not submitted" from a UI
modal, so it re-sent a live order every 20-45 minutes. Here:

  * an intent is created BEFORE touching the broker and persisted;
  * at most ONE live intent per (day, side, symbol);
  * a submitted-but-unverified intent is never re-submitted; it is reconciled
    against the broker's order list first;
  * every state change is an appended event, so the history is auditable.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .calendar_ir import now_tehran

TERMINAL = {"rejected", "cancelled", "expired", "failed"}
LIVE = {"intent", "submitted", "unverified", "verified", "partial"}


@dataclass
class OrderIntent:
    ref: str
    day: str                  # Gregorian date string, Tehran
    symbol: str
    side: str                 # buy | sell
    qty: int
    price: float
    reason: str = ""
    status: str = "intent"
    created_at: str = field(default_factory=lambda: now_tehran().isoformat(timespec="seconds"))
    updated_at: str = ""
    broker_order_no: str = ""
    filled_qty: int = 0
    attempts: int = 0
    note: str = ""
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL

    @property
    def is_live(self) -> bool:
        return self.status in LIVE


class OrderStore:
    def __init__(self, path: Path | str, lock: Optional[threading.Lock] = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = lock or threading.Lock()
        self.intents: Dict[str, OrderIntent] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            self._fold(ev)

    def _fold(self, ev: Dict[str, Any]) -> None:
        kind = ev.get("kind")
        ref = ev.get("ref")
        if not ref:
            return
        if kind == "intent":
            d = ev.get("intent") or {}
            self.intents[ref] = OrderIntent(**{k: v for k, v in d.items()
                                              if k in OrderIntent.__dataclass_fields__})
        elif ref in self.intents:
            it = self.intents[ref]
            if kind == "status":
                it.status = ev.get("status", it.status)
                it.updated_at = ev.get("ts", it.updated_at)
                for k in ("broker_order_no", "filled_qty", "attempts", "note"):
                    if k in ev:
                        setattr(it, k, ev[k])
            elif kind == "note":
                it.note = ev.get("text", it.note)

    def _append(self, ev: Dict[str, Any]) -> None:
        with self._lock:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
                fh.flush()
                os.fsync(fh.fileno())

    # ------------------------------------------------------------------ api
    @staticmethod
    def make_ref(day: str, side: str, symbol: str, seq: int = 1) -> str:
        base = f"{day}:{side}:{symbol}"
        return base if seq <= 1 else f"{base}#{seq}"

    def create(self, day: str, symbol: str, side: str, qty: int, price: float,
               reason: str = "", extra: Optional[Dict[str, Any]] = None) -> OrderIntent:
        """Create the next attempt for (day, side, symbol).

        Attempts are numbered because a rejected order may legitimately be
        retried within the exchange's per-symbol daily order limit. A *live*
        (submitted/unverified/verified) attempt, however, must never be followed
        by another one; that check lives in the engine.
        """
        prior = self.attempts(day, symbol, side)
        seq = len(prior) + 1
        ref = self.make_ref(day, side, symbol, seq)
        if ref in self.intents:
            raise ValueError(f"intent already exists for {ref}")
        it = OrderIntent(ref=ref, day=day, symbol=symbol, side=side, qty=int(qty),
                         price=float(price), reason=reason,
                         updated_at=now_tehran().isoformat(timespec="seconds"),
                         extra=extra or {})
        self.intents[ref] = it
        self._append({"kind": "intent", "ts": it.created_at, "ref": ref, "intent": it.to_dict()})
        return it

    def set_status(self, ref: str, status: str, **fields: Any) -> OrderIntent:
        it = self.intents[ref]
        it.status = status
        it.updated_at = now_tehran().isoformat(timespec="seconds")
        for k, v in fields.items():
            if hasattr(it, k):
                setattr(it, k, v)
        self._append({"kind": "status", "ts": it.updated_at, "ref": ref,
                      "status": status, **{k: v for k, v in fields.items() if hasattr(it, k)}})
        return it

    def bump_attempt(self, ref: str) -> int:
        it = self.intents[ref]
        return int(self.set_status(ref, "submitted", attempts=it.attempts + 1).attempts)

    def get(self, ref: str) -> Optional[OrderIntent]:
        return self.intents.get(ref)

    def for_day(self, day: str) -> List[OrderIntent]:
        return [i for i in self.intents.values() if i.day == day]

    def live_intents(self, day: Optional[str] = None) -> List[OrderIntent]:
        return [i for i in self.intents.values()
                if i.is_live and (day is None or i.day == day)]

    def live_for(self, day: str, symbol: str, side: str) -> Optional[OrderIntent]:
        for i in reversed(self.attempts(day, symbol, side)):
            if i.is_live:
                return i
        return None

    def attempts(self, day: str, symbol: str, side: Optional[str] = None) -> List[OrderIntent]:
        rows = [i for i in self.for_day(day)
                if i.symbol == symbol and (side is None or i.side == side)]
        rows.sort(key=lambda x: x.created_at)
        return rows

    def attempts_count(self, day: str, symbol: str, side: Optional[str] = None) -> int:
        """Attempts that reached the broker; rejected/cancelled tickets do not count."""
        dead = {"rejected", "cancelled", "failed"}
        n = 0
        for i in self.for_day(day):
            if i.symbol != symbol:
                continue
            if side is not None and i.side != side:
                continue
            if str(getattr(i, "status", "") or "") in dead:
                continue
            n += 1
        return n

    def any_today(self, day: str, symbol: str, side: Optional[str] = None) -> bool:
        """True only for tickets that actually reached the broker.

        A failed submit (symbol not found, panel error) used to block the symbol for
        the whole day; such tickets are now ignored so the bot can retry.
        """
        dead = {"rejected", "cancelled", "failed"}
        for i in self.for_day(day):
            if i.symbol == symbol and (side is None or i.side == side):
                if str(getattr(i, "status", "") or "") in dead:
                    continue
                return True
        return False

    def counts(self, day: str, side: str) -> int:
        return sum(1 for i in self.for_day(day)
                   if i.side == side and i.status not in TERMINAL)


@dataclass
class BrokerOrder:
    order_no: str
    day: str
    symbol: str
    side: str
    qty: int
    price: float
    filled: int
    status: str


def match_intent(intent: OrderIntent, orders: List[BrokerOrder],
                 used_order_nos: Optional[set] = None) -> Optional[BrokerOrder]:
    """Find the broker row that corresponds to an intent.

    Matching is intentionally conservative: same side, same symbol, price within
    1%. Never matches a different side, and never reuses an order number that
    already belongs to another intent (which is how one fill could otherwise be
    attributed twice).
    """
    used = used_order_nos or set()
    best = None
    for o in orders:
        if o.order_no in used:
            continue
        if o.side != intent.side or o.symbol != intent.symbol:
            continue
        if intent.price and o.price and abs(o.price - intent.price) / max(intent.price, 1) > 0.01:
            continue
        best = o
    return best

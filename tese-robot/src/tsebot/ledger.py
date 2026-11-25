# -*- coding: utf-8 -*-
"""TSE Bot v2 — append-only ledger = the single source of accounting truth.

Why this exists
---------------
v1 kept three independent stores (trade_journal/*.json, orders_log.json,
active_positions.json) and each disagreed with the broker by 2-3x. Here every
economic fact is one immutable event with a unique ``dedupe`` key, so:

  * re-importing the same broker fill twice changes nothing (idempotent);
  * positions, average cost and realised PnL are a pure function of the log;
  * any disagreement with the broker is detectable and reportable, not silent.

Event kinds
-----------
fill     a real trade: side buy/sell, qty, price, optional fee, order_no
cash     deposit / withdrawal / correction of cash balance (amount signed)
adjust   manual correction of a position quantity (audited, reason required)
note     free-form annotation, no economic effect
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .calendar_ir import now_tehran


def _iso() -> str:
    return now_tehran().isoformat(timespec="seconds")


@dataclass
class Position:
    symbol: str
    qty: int = 0
    cost: float = 0.0            # remaining cost basis, rial
    fees: float = 0.0
    realized: float = 0.0
    invested: float = 0.0        # lifetime cash put in (buys incl. fees)
    buys: int = 0
    sells: int = 0
    first_ts: Optional[str] = None
    last_ts: Optional[str] = None
    last_price: Optional[float] = None

    @property
    def avg_cost(self) -> float:
        return self.cost / self.qty if self.qty else 0.0

    def unrealized(self, price: Optional[float]) -> float:
        if not self.qty or price is None:
            return 0.0
        return (price * self.qty) - self.cost

    def unrealized_pct(self, price: Optional[float]) -> float:
        if not self.qty or not self.cost or price is None:
            return 0.0
        return (price * self.qty - self.cost) / self.cost * 100.0

    def total_pnl(self, price: Optional[float]) -> float:
        """Cumulative profit/loss since the first purchase: realised + open."""
        return self.realized + self.unrealized(price)

    def total_pct(self, price: Optional[float]) -> float:
        if not self.invested or price is None:
            return 0.0
        return self.total_pnl(price) / self.invested * 100.0

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "symbol": self.symbol,
            "qty": self.qty,
            "avg_cost": round(self.avg_cost, 2),
            "cost": round(self.cost, 2),
            "realized": round(self.realized, 2),
            "invested": round(self.invested, 2),
            "fees": round(self.fees, 2),
            "buys": self.buys,
            "sells": self.sells,
            "first_ts": self.first_ts,
            "last_ts": self.last_ts,
        }
        if self.last_price is not None:
            d["last_price"] = self.last_price
            d["unrealized"] = round(self.unrealized(self.last_price), 2)
            d["unrealized_pct"] = round(self.unrealized_pct(self.last_price), 3)
            d["total_pnl"] = round(self.total_pnl(self.last_price), 2)
            d["total_pct"] = round(self.total_pct(self.last_price), 3)
        return d


class LedgerError(RuntimeError):
    pass


class Ledger:
    """Append-only JSONL ledger with in-process dedupe and a rebuildable state."""

    def __init__(self, path: Path | str, lock: Optional[threading.Lock] = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = lock or threading.Lock()
        self._events: List[Dict[str, Any]] = []
        self._dedupe: set[str] = set()
        self.positions: Dict[str, Position] = {}
        self.cash: float = 0.0
        self._load()

    # ------------------------------------------------------------------ io
    def _load(self) -> None:
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as fh:
            for ln, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    # A torn last line must not destroy history: keep going,
                    # the bad line is preserved on disk for inspection.
                    continue
                self._apply(ev, persist=False)
        self._recompute()

    def _recompute(self) -> None:
        self.positions = {}
        self.cash = 0.0
        for ev in self._events:
            self._fold(ev)

    def _apply(self, ev: Dict[str, Any], persist: bool = True) -> bool:
        key = ev.get("dedupe")
        if key:
            if key in self._dedupe:
                return False
            self._dedupe.add(key)
        self._events.append(ev)
        self._fold(ev)
        if persist:
            with self._lock:
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
                    fh.flush()
                    os.fsync(fh.fileno())
        return True

    # --------------------------------------------------------------- folding
    def _fold(self, ev: Dict[str, Any]) -> None:
        kind = ev.get("kind")
        if kind == "fill":
            self._fold_fill(ev)
        elif kind == "cash":
            self.cash += float(ev.get("amount", 0) or 0)
        elif kind == "adjust":
            sym = str(ev.get("symbol", ""))
            pos = self.positions.setdefault(sym, Position(symbol=sym))
            delta = int(ev.get("qty_delta", 0) or 0)
            pos.qty += delta
            pos.last_ts = ev.get("ts") or pos.last_ts
            if delta > 0:
                added = float(ev.get("cost_delta", 0) or 0)
                pos.cost += added
                pos.invested += added
            else:
                pos.cost = max(0.0, pos.cost - float(ev.get("cost_delta", 0) or 0))
            if pos.qty <= 0:
                pos.qty = 0
                pos.cost = 0.0

    def _fold_fill(self, ev: Dict[str, Any]) -> None:
        sym = str(ev.get("symbol", "")).strip()
        if not sym:
            raise LedgerError("fill without symbol")
        side = str(ev.get("side", "")).lower()
        qty = int(ev.get("qty", 0) or 0)
        price = float(ev.get("price", 0) or 0)
        fee = float(ev.get("fee", 0) or 0)
        if qty <= 0 or price <= 0:
            raise LedgerError(f"bad fill {ev}")
        pos = self.positions.setdefault(sym, Position(symbol=sym))
        ts = ev.get("ts") or _iso()
        if side == "buy":
            pos.qty += qty
            pos.cost += qty * price + fee
            pos.invested += qty * price + fee
            pos.fees += fee
            pos.buys += 1
            self.cash -= qty * price + fee
        elif side == "sell":
            sell_qty = min(qty, pos.qty)
            if sell_qty < qty:
                # Selling more than we hold: record what we can, flag the rest.
                ev["_oversold"] = qty - sell_qty
            avg = pos.avg_cost
            removed = avg * sell_qty
            proceeds = sell_qty * price - fee
            pos.qty -= sell_qty
            pos.cost = max(0.0, pos.cost - removed)
            pos.realized += proceeds - removed
            pos.fees += fee
            pos.sells += 1
            self.cash += proceeds
            if pos.qty == 0:
                pos.cost = 0.0
        else:
            raise LedgerError(f"unknown side {side!r}")
        pos.first_ts = pos.first_ts or ts
        pos.last_ts = ts

    # ---------------------------------------------------------------- public
    def record_fill(self, symbol: str, side: str, qty: int, price: float,
                    fee: float = 0.0, order_no: str = "", source: str = "broker",
                    ts: Optional[str] = None, extra: Optional[Dict[str, Any]] = None) -> bool:
        """Idempotent on (order_no, side). Returns True if newly recorded."""
        sym = symbol.strip()
        dedupe = f"fill:{order_no}:{side}" if order_no else f"fill:{sym}:{ts}:{side}:{qty}:{price}"
        ev = {
            "v": 1,
            "id": uuid.uuid4().hex[:12],
            "ts": ts or _iso(),
            "kind": "fill",
            "side": side.lower(),
            "symbol": sym,
            "qty": int(qty),
            "price": float(price),
            "fee": float(fee),
            "order_no": str(order_no or ""),
            "source": source,
            "dedupe": dedupe,
        }
        if extra:
            ev["extra"] = extra
        return self._apply(ev)

    def record_cash(self, amount: float, reason: str, ts: Optional[str] = None) -> None:
        ev = {"v": 1, "id": uuid.uuid4().hex[:12], "ts": ts or _iso(),
              "kind": "cash", "amount": float(amount), "reason": reason,
              "dedupe": f"cash:{ts}:{amount}:{reason}:{uuid.uuid4().hex[:6]}"}
        self._apply(ev)

    def record_adjust(self, symbol: str, qty_delta: int, cost_delta: float = 0.0,
                      reason: str = "manual") -> None:
        ev = {"v": 1, "id": uuid.uuid4().hex[:12], "ts": _iso(), "kind": "adjust",
              "symbol": symbol, "qty_delta": int(qty_delta),
              "cost_delta": float(cost_delta), "reason": reason,
              "dedupe": f"adjust:{symbol}:{qty_delta}:{reason}:{uuid.uuid4().hex[:6]}"}
        self._apply(ev)

    def note(self, text: str, **kw: Any) -> None:
        ev = {"v": 1, "id": uuid.uuid4().hex[:12], "ts": _iso(), "kind": "note",
              "text": text, **kw, "dedupe": f"note:{uuid.uuid4().hex[:8]}"}
        self._apply(ev)

    def mark_price(self, symbol: str, price: float) -> None:
        pos = self.positions.get(symbol)
        if pos is not None:
            pos.last_price = float(price)

    def open_positions(self) -> List[Position]:
        return [p for p in self.positions.values() if p.qty > 0]

    def get(self, symbol: str) -> Optional[Position]:
        return self.positions.get(symbol)

    def realized_total(self) -> float:
        return sum(p.realized for p in self.positions.values())

    def exposure(self, prices: Optional[Dict[str, float]] = None) -> float:
        total = 0.0
        for p in self.open_positions():
            px = (prices or {}).get(p.symbol, p.last_price or p.avg_cost)
            total += p.qty * float(px)
        return total

    def events(self) -> List[Dict[str, Any]]:
        return list(self._events)

    def snapshot(self, prices: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
        prices = prices or {}
        pos = []
        for p in sorted(self.open_positions(), key=lambda x: -(x.qty * (x.last_price or x.avg_cost))):
            px = prices.get(p.symbol, p.last_price)
            if px is not None:
                p.last_price = float(px)
            pos.append(p.to_dict())
        exposure = self.exposure(prices)
        unreal = sum(p.unrealized(prices.get(p.symbol, p.last_price)) for p in self.open_positions())
        return {
            "at": _iso(),
            "positions": pos,
            "cash_ledger": round(self.cash, 2),
            "exposure": round(exposure, 2),
            "unrealized": round(unreal, 2),
            "realized_total": round(self.realized_total(), 2),
            "equity": round(exposure + self.cash, 2) if self.cash else None,
        }

    # --------------------------------------------------------- reconciliation
    def diff_vs_broker(self, broker_positions: Dict[str, int]) -> Dict[str, Any]:
        """Compare ledger quantities with the broker's holdings.

        broker_positions: symbol -> qty from the broker panel.
        Returns only mismatches plus symbols present on one side only.
        """
        led = {p.symbol: p.qty for p in self.open_positions()}
        syms = set(led) | set(broker_positions)
        diffs = {}
        for s in sorted(syms):
            a, b = led.get(s, 0), int(broker_positions.get(s, 0) or 0)
            if a != b:
                diffs[s] = {"ledger": a, "broker": b, "delta": b - a}
        return diffs


def load(path: Path | str) -> Ledger:
    return Ledger(path)


def dedupe_closed_positions(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """v1 stored the same closed trade up to 3 times; this is the fix."""
    seen, out = set(), []
    for r in rows:
        key = (r.get("symbol"), r.get("exit_at"), r.get("count"), r.get("exit_price"))
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out

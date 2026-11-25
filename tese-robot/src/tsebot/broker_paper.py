# -*- coding: utf-8 -*-
"""TSE Bot v2 — paper broker.

Used by `--dry-run` and by the test-suite. It reads a market snapshot JSON:

    {"buying_power": 582381070,
     "symbols": {"وایرا": {"last": 3710, "ceiling": 3895, "floor": 3520,
                            "best_bid": 3700, "best_ask": 3710,
                            "min_qty": 1, "max_qty": 900000}},
     "positions": {"وایرا": 900000}}

and fills orders immediately at the requested price (never above ceiling /
below floor), updating an internal book. Orders are numbered `PAPER-...`.
This makes the whole decision path executable and auditable today, with zero
market risk, which is exactly what the live path needs before being trusted.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from .broker_base import Account, BrokerError, SymbolInfo
from .orders import BrokerOrder


class PaperBroker:
    def __init__(self, snapshot_path: Path | str, fee_buy_pct: float = 0.6,
                 fee_sell_pct: float = 0.6):
        self.snapshot_path = Path(snapshot_path)
        self.fee_buy = fee_buy_pct / 100.0
        self.fee_sell = fee_sell_pct / 100.0
        self.buying_power = 0.0
        self._info: Dict[str, SymbolInfo] = {}
        self._positions: Dict[str, int] = {}
        self._orders: List[BrokerOrder] = []
        self._seq = 0
        self.connected = False

    # ------------------------------------------------------------- lifecycle
    def connect(self) -> None:
        if not self.snapshot_path.exists():
            raise BrokerError(f"paper snapshot not found: {self.snapshot_path}")
        data = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
        self.buying_power = float(data.get("buying_power", 0))
        self._positions = {k: int(v) for k, v in (data.get("positions") or {}).items()}
        self._avg = {k: float(v) for k, v in (data.get("cost_basis") or {}).items()}
        self._info = {}
        for sym, d in (data.get("symbols") or {}).items():
            self._info[sym] = SymbolInfo(
                symbol=sym,
                isin=d.get("isin", ""),
                last=d.get("last"),
                best_bid=d.get("best_bid"),
                best_ask=d.get("best_ask"),
                ceiling=d.get("ceiling"),
                floor=d.get("floor"),
                min_qty=int(d.get("min_qty", 1) or 1),
                max_qty=int(d.get("max_qty", 0) or 0),
                market_type=d.get("market_type", "paper"),
                state=d.get("state", "open"),
                prev_close=d.get("prev_close"),
            )
        self.connected = True

    def is_logged_in(self) -> bool:
        return self.connected

    def close(self) -> None:
        self.connected = False

    # ----------------------------------------------------------------- reads
    def account(self) -> Account:
        return Account(buying_power=self.buying_power, positions=dict(self._positions),
                       avg_prices=dict(getattr(self, "_avg", {})))

    def symbol_info(self, symbol: str) -> SymbolInfo:
        info = self._info.get(symbol)
        if info is None:
            raise BrokerError(f"unknown symbol in paper snapshot: {symbol}")
        return info

    def orderlist(self) -> List[BrokerOrder]:
        return list(self._orders)

    # ---------------------------------------------------------------- writes
    def submit(self, side: str, symbol: str, qty: int, price: float) -> Dict[str, object]:
        if not self.connected:
            raise BrokerError("not connected")
        info = self.symbol_info(symbol)
        qty = int(qty)
        price = float(price)
        if qty <= 0 or price <= 0:
            return {"ok": False, "error": "bad_qty_or_price"}
        if info.ceiling and price > info.ceiling:
            return {"ok": False, "error": "above_ceiling", "ceiling": info.ceiling}
        if info.floor and price < info.floor:
            return {"ok": False, "error": "below_floor", "floor": info.floor}
        if info.max_qty and qty > info.max_qty:
            qty = info.max_qty
        if qty < info.min_qty:
            return {"ok": False, "error": "below_min_qty", "min_qty": info.min_qty}

        self._seq += 1
        order_no = f"PAPER-{self._seq:08d}"
        if side == "buy":
            need = qty * price * (1 + self.fee_buy)
            if need > self.buying_power:
                # partial fill with what we can afford
                affordable = int(self.buying_power / (price * (1 + self.fee_buy)))
                if affordable < info.min_qty:
                    return {"ok": False, "error": "insufficient_cash",
                            "need": need, "have": self.buying_power}
                qty = affordable
                need = qty * price * (1 + self.fee_buy)
            self.buying_power -= need
            self._positions[symbol] = self._positions.get(symbol, 0) + qty
            filled = qty
        else:
            held = self._positions.get(symbol, 0)
            if held <= 0:
                return {"ok": False, "error": "no_position"}
            qty = min(qty, held)
            self._positions[symbol] = held - qty
            self.buying_power += qty * price * (1 - self.fee_sell)
            filled = qty

        self._orders.append(BrokerOrder(
            order_no=order_no, day="", symbol=symbol, side=side,
            qty=qty, price=price, filled=filled,
            status="سفارش بصورت کامل انجام شد",
        ))
        return {"ok": True, "order_no": order_no, "filled": filled, "qty": qty, "price": price}


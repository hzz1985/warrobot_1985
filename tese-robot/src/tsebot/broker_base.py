# -*- coding: utf-8 -*-
"""TSE Bot v2 — broker interface.

Two implementations:
  * PaperBroker  — deterministic simulator used by dry-run and tests
  * SeleniumBroker — drives the real broker web UI

Everything above this layer talks only to this interface, so the trading logic
is testable without a browser and swappable without rewriting it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Protocol

from .orders import BrokerOrder


@dataclass
class SymbolInfo:
    symbol: str
    isin: str = ""
    last: Optional[float] = None
    best_bid: Optional[float] = None
    best_ask: Optional[float] = None
    ceiling: Optional[float] = None
    floor: Optional[float] = None
    min_qty: int = 1
    max_qty: int = 0
    market_type: str = ""
    state: str = ""
    prev_close: Optional[float] = None


@dataclass
class Account:
    buying_power: float = 0.0
    positions: Dict[str, int] = field(default_factory=dict)
    avg_prices: Dict[str, float] = field(default_factory=dict)
    breakevens: Dict[str, float] = field(default_factory=dict)
    raw: Dict[str, object] = field(default_factory=dict)


class BrokerError(RuntimeError):
    pass


class Broker(Protocol):
    def connect(self) -> None: ...
    def is_logged_in(self) -> bool: ...
    def account(self) -> Account: ...
    def symbol_info(self, symbol: str) -> SymbolInfo: ...
    def submit(self, side: str, symbol: str, qty: int, price: float) -> Dict[str, object]: ...
    def orderlist(self) -> List[BrokerOrder]: ...
    def close(self) -> None: ...


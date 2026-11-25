# -*- coding: utf-8 -*-
"""TSE Bot v2 — position sizing and risk gates.

Every gate returns a reason string so a rejected buy is explainable in the log,
instead of the v1 behaviour where a bad size was simply sent to the broker.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from .broker_base import SymbolInfo


@dataclass
class BuyPlan:
    ok: bool
    reason: str = ""
    qty: int = 0
    value_rial: float = 0.0
    price: float = 0.0
    target_value: float = 0.0


def is_base_market(info: SymbolInfo) -> bool:
    return "FaraBourseBase" in str(info.market_type or "")


def round_lot(qty: int) -> int:
    return max(1, int(qty))


def choose_price(info: SymbolInfo, mode: str, offset_pct: float = 0.0) -> Optional[float]:
    """Pick a limit price. Ceiling is available but NOT the default anymore."""
    cand: Optional[float]
    if mode == "ceiling":
        cand = info.ceiling
    elif mode == "best_bid":
        cand = info.best_bid or info.last
    elif mode == "last":
        cand = info.last
    else:
        cand = info.last
    if not cand:
        return None
    p = cand * (1 + offset_pct / 100.0)
    if info.ceiling:
        p = min(p, info.ceiling)
    if info.floor:
        p = max(p, info.floor)
    return float(int(round(p)))


def plan_buy(
    info: SymbolInfo,
    *,
    equity: float,
    cash: float,
    exposure: float,
    open_positions: int,
    slots: int,
    max_positions: int,
    max_value_per_position_pct: float,
    max_total_exposure_pct: float,
    min_cash_buffer_pct: float,
    deploy_fraction: float,
    min_order_rial: float,
    min_order_rial_base: float,
    price_mode: str,
    price_offset_pct: float,
) -> BuyPlan:
    if slots <= 0:
        return BuyPlan(False, "no_free_slot")
    if equity <= 0:
        return BuyPlan(False, "no_equity")

    price = choose_price(info, price_mode, price_offset_pct)
    if not price:
        return BuyPlan(False, "no_price")

    floor_value = min_order_rial_base if is_base_market(info) else min_order_rial
    cap_by_position = equity * max_value_per_position_pct / 100.0
    cap_by_exposure = max(0.0, equity * max_total_exposure_pct / 100.0 - exposure)
    cash_after_buffer = max(0.0, cash - equity * min_cash_buffer_pct / 100.0)
    # EQUAL-MONEY RULE: every slot is funded with the same target value
    # (equity / max_positions), regardless of how many slots are currently free.
    # Without this, filling one free slot after a sale would size the new position
    # off "remaining cash / remaining slots" and the three positions drift apart
    # exactly like the previous robot's book.
    slot_value = equity / max(1, int(max_positions)) if max_positions else equity

    target = min(cap_by_position, cap_by_exposure, cash_after_buffer, slot_value)
    if target < floor_value:
        return BuyPlan(False, "budget_below_minimum",
                       price=price, target_value=target)

    qty = int(target / price)
    if info.max_qty:
        qty = min(qty, int(info.max_qty))
    qty = max(qty, int(info.min_qty or 1))
    qty = round_lot(qty)
    value = qty * price
    if value < floor_value:
        return BuyPlan(False, "below_min_order_value", qty=qty, value_rial=value,
                       price=price, target_value=target)
    if value > cash_after_buffer:
        affordable = int(cash_after_buffer / price)
        if affordable < int(info.min_qty or 1):
            return BuyPlan(False, "insufficient_cash", price=price)
        qty, value = affordable, affordable * price
    return BuyPlan(True, "ok", qty=qty, value_rial=value, price=price, target_value=target)


@dataclass
class ExitDecision:
    symbol: str
    qty: int
    reason: str
    pnl_pct: float
    price_hint: Optional[float] = None
    blocked: str = ""


def decide_exit(
    *,
    symbol: str,
    qty: int,
    avg_cost: float,
    last_price: Optional[float],
    days_held: int,
    peak_pnl_pct: float,
    info: SymbolInfo,
    target_pct: float,
    stop_pct: float,
    max_hold_days: int,
    trailing_stop_pct: Optional[float],
    trailing_arm_pct: float,
    exit_on_queue_break: bool,
    was_locked_up: bool,
    riding_queue: bool = False,
    total_pnl_pct: Optional[float] = None,
) -> Optional[ExitDecision]:
    """Return an exit decision or None to hold.

    P&L is evaluated on BOTH bases, and whichever is worse/better triggers:
      * `pnl`            — price now vs average cost (unrealised)
      * `total_pnl_pct`  — everything since the first purchase (realised + open)
    The operator's rule is explicit: hit -3% on either basis -> sell now, because
    in TSE a sell-queue turns a small loss into a large one. Same for +12%.
    """
    if last_price is None or not avg_cost or qty <= 0:
        return None
    pnl = (last_price - avg_cost) / avg_cost * 100.0
    total = float(total_pnl_pct) if total_pnl_pct is not None else pnl
    worst = min(pnl, total)
    best = max(pnl, total)

    # Cannot sell into a sell-queue: price at floor and no bid.
    if info.floor and last_price <= info.floor * 1.001 and not info.best_bid:
        if worst <= -stop_pct:
            return ExitDecision(symbol, qty, "stop_blocked_floor", worst, blocked="sell_queue")
        return None

    price_hint = info.best_bid or last_price

    if worst <= -stop_pct:
        return ExitDecision(symbol, qty, "stop", worst, price_hint)

    # Target wins over riding: selling into a buy-queue is always possible, and the
    # operator wants a hard +12% gross (~+10% net of the 2% round-trip cost).
    if best >= target_pct:
        return ExitDecision(symbol, qty, "target", best, price_hint)

    locked_now = bool(info.ceiling and last_price >= info.ceiling * 0.999 and not info.best_ask)
    if exit_on_queue_break and (riding_queue or was_locked_up):
        if not locked_now:
            return ExitDecision(symbol, qty, "queue_break", pnl, price_hint)
        return None  # ride the queue
    if trailing_stop_pct and peak_pnl_pct >= trailing_arm_pct:
        if pnl <= peak_pnl_pct - trailing_stop_pct:
            return ExitDecision(symbol, qty, "trailing", pnl, price_hint)
    if max_hold_days and days_held >= max_hold_days:
        return ExitDecision(symbol, qty, "timeout", pnl, price_hint)
    return None



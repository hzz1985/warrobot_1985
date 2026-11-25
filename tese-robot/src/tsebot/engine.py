# -*- coding: utf-8 -*-
"""TSE Bot v2 — trading engine.

This is the only place that mutates state. It composes:

    broker  (read quotes / place orders / read order list)
    ledger  (accounting truth)
    orders  (idempotency)
    risk    (sizing + exit decisions)

and exposes cycle-level operations that a scheduler can call safely:

    sync()          import broker fills into the ledger, reconcile
    refresh_marks() update last prices / lock flags
    sell_cycle()    evaluate and execute exits
    buy_cycle()     evaluate and execute entries
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .broker_base import SymbolInfo
from .calendar_ir import TradingCalendar, from_jalali, jalali_str, now_tehran, to_jalali
from .config import Config
from .ledger import Ledger
from .orders import BrokerOrder, OrderIntent, OrderStore
from .risk import ExitDecision, decide_exit, plan_buy
from .state import JsonState


@dataclass
class Pick:
    symbol: str
    score: float
    for_date: str = ""
    source: str = ""

    def key(self) -> str:
        return self.symbol


def _parse_ts(value: str) -> Optional[datetime]:
    """Accept ISO or Jalali 'YYYY/MM/DD' / 'YYYY-MM-DD' order dates."""
    if not value:
        return None
    v = str(value).strip()
    try:
        return datetime.fromisoformat(v)
    except Exception:
        pass
    head = v[:10].replace("/", "-")
    try:
        y = int(head[:4])
    except Exception:
        return None
    if y >= 1300:  # Jalali
        try:
            g = from_jalali(head)
            return datetime(g.year, g.month, g.day)
        except Exception:
            return None
    try:
        return datetime.strptime(head, "%Y-%m-%d")
    except Exception:
        return None


class Engine:
    def __init__(self, cfg: Config, broker, clock=None, now_fn=None,
                 ledger: Optional[Ledger] = None, orders: Optional[OrderStore] = None,
                 state: Optional[JsonState] = None, logger=None):
        self.cfg = cfg
        self.broker = broker
        self.cal = TradingCalendar(holidays_path=cfg.path("state_dir") / "holidays.json")
        self.ledger = ledger or Ledger(cfg.path("state_dir") / "ledger.jsonl")
        self.orders = orders or OrderStore(cfg.path("state_dir") / "orders.jsonl")
        self.state = state or JsonState(cfg.path("state_dir") / "engine_state.json")
        self.log = logger or (lambda msg: None)
        self._now_fn = now_fn or now_tehran
        self._quote_cache: Dict[str, Any] = {}
        self._quote_at: Dict[str, float] = {}

    # ------------------------------------------------------------------ time
    def now(self) -> datetime:
        return self._now_fn()

    def today(self) -> str:
        return self.now().date().isoformat()

    def session(self) -> str:
        return self.cal.session(self.cfg.section("market"), self.now())

    def can_trade(self, allow_pre_open: bool = False) -> Tuple[bool, str]:
        return self.cal.is_open_for_orders(self.cfg.section("market"), self.now(),
                                           allow_pre_open=allow_pre_open)

    # ------------------------------------------------------------- broker io
    def connect(self) -> None:
        if getattr(self.broker, "connected", False):
            return
        self.broker.connect()
        if not self.broker.is_logged_in():
            raise RuntimeError("broker session not logged in")

    _SNAP: Dict[str, Any] = {"at": 0.0, "df": None}

    def _market_snapshot(self):
        """Full TSE snapshot (stocks + order_book), cached 30s."""
        now = time.time()
        if now - float(self._SNAP.get("at") or 0) < 30 and self._SNAP.get("df") is not None:
            return self._SNAP["df"]
        try:
            import algotik_tse as att
            snap = att.get_market_snapshot()
        except Exception:
            snap = None
        self._SNAP["at"] = now
        self._SNAP["df"] = snap
        return snap

    def market_quote(self, symbol: str):
        """Quote from the exchange snapshot: last, ceiling and floor.

        The order-entry panel (tab 4) is the only place that exposes
        `symbolInformation`; on the portfolio screen (tab 1) it does not exist.
        Prices must therefore be readable without touching the broker DOM.
        """
        snap = self._market_snapshot()
        df = (snap or {}).get("stocks") if isinstance(snap, dict) else None
        if df is None or len(df) == 0:
            return None
        def norm(s: str) -> str:
            return (str(s).replace("ي", "ی").replace("ك", "ک")
                    .replace("\u200c", "").replace(" ", "").strip())
        want = norm(symbol)
        try:
            match = df[df["Symbol"].map(norm) == want]
        except Exception:
            return None
        if len(match) == 0:
            return None
        row = match.iloc[0]
        def f(key):
            try:
                v = float(row.get(key))
                return v if v > 0 else None
            except Exception:
                return None
        return SymbolInfo(
            symbol=symbol,
            isin=str(row.get("ISIN") or ""),
            # closing price is the official, stable value; Last can flip during
            # the closing auction and made phone and desktop disagree
            last=f("Close") or f("Last"),
            ceiling=f("MaxAllowed"), floor=f("MinAllowed"),
            prev_close=f("Yesterday"),
            max_qty=0, min_qty=1, market_type="snapshot",
        )

    def quote(self, symbol: str, max_age: float = 0.0):
        """Price for one symbol: broker DOM when logged in, exchange snapshot otherwise.

        The snapshot path needs no broker session at all, so a dropped login must
        never stop the bot from computing stop-losses.
        """
        if max_age and symbol in self._quote_cache:
            if time.time() - self._quote_at.get(symbol, 0) <= max_age:
                return self._quote_cache[symbol]
        try:
            logged_in = bool(self.broker.is_logged_in())
        except Exception:
            logged_in = False
        info = None
        if not logged_in:
            # Never touch the page while the login form is showing: reloading it
            # regenerates the captcha and can cancel a fresh session.
            info = self.market_quote(symbol)
            if info is not None:
                self._quote_cache[symbol] = info
                self._quote_at[symbol] = time.time()
            return info
        # The symbol search on the portfolio screen re-initialises the SPA and is
        # what keeps killing the broker session. On page-1 prices therefore come
        # from the exchange snapshot only; the DOM is touched solely when an order
        # is actually placed.
        try:
            cur = str(self.broker.driver.current_url)
        except Exception:
            cur = ""
        if "page-4" not in cur:
            info = self.market_quote(symbol)
            if info is not None:
                self._quote_cache[symbol] = info
                self._quote_at[symbol] = time.time()
            return info
        search = getattr(self.broker, "search", None)
        for attempt in (1, 2):
            try:
                if callable(search):
                    item = search(symbol)
                    if item:
                        sel = getattr(self.broker, "select", None)
                        if callable(sel):
                            sel(item)
                        time.sleep(float(self.cfg.get("broker.page_settle_sec", 3.0)))
                info = self.broker.symbol_info(symbol)
                if info is not None:
                    break
            except Exception as exc:
                self.log(f"quote({symbol}) attempt {attempt} failed: {exc}")
                info = None
            if attempt == 1:
                # No navigation here on purpose. Every attempt to move the SPA
                # (link click or driver.get) reloads the page and the broker drops
                # the session. The operator keeps the tab on the order-entry page
                # and the bot only reads from it.
                self.log(f"quote({symbol}): order scope missing "
                         f"(leave the broker tab on the order-entry page)")
                break
        if info is None:
            # The panel has no order scope (e.g. portfolio screen): fall back to
            # the exchange snapshot so P&L and stop-loss still work.
            info = self.market_quote(symbol)
        if info is not None:
            self._quote_cache[symbol] = info
            self._quote_at[symbol] = time.time()
        return info

    # ------------------------------------------------------------ accounting
    def _order_ts(self, o: BrokerOrder) -> str:
        d = _parse_ts(str(o.day))
        if d is None:
            return self.now().isoformat(timespec="seconds")
        return d.replace(hour=12, minute=0).isoformat(timespec="seconds")

    def sync(self, import_fills: bool = True) -> Dict[str, Any]:
        """Import broker fills (idempotent) and reconcile quantities."""
        out: Dict[str, Any] = {"imported": 0, "skipped": 0, "orders": 0, "diffs": {}}
        try:
            rows = self.broker.orderlist()
        except Exception as exc:
            out["error"] = f"orderlist: {exc}"
            return out
        out["orders"] = len(rows)
        if import_fills:
            for o in rows:
                if not o.filled or o.filled <= 0:
                    continue
                if "انجام" not in str(o.status):
                    continue
                fee_pct = float(self.cfg.get("fees.buy_pct", 0.6)) if o.side == "buy" \
                    else float(self.cfg.get("fees.sell_pct", 0.6))
                fee = o.filled * o.price * fee_pct / 100.0
                new = self.ledger.record_fill(
                    symbol=o.symbol, side=o.side, qty=int(o.filled), price=float(o.price),
                    fee=fee, order_no=o.order_no, source="broker",
                    ts=self._order_ts(o),
                    extra={"broker_status": o.status},
                )
                if new:
                    out["imported"] += 1
                else:
                    out["skipped"] += 1
        try:
            acct = self.broker.account()
            out["diffs"] = self.ledger.diff_vs_broker(acct.positions)
            out["cash_broker"] = acct.buying_power
            if getattr(acct, "breakevens", None):
                cb = dict(self.state.get("cost_basis") or {})
                cb.update(acct.breakevens)
                self.state.set("cost_basis", cb)
            # The order list only shows the last page of tickets, so a position
            # opened weeks ago may never be imported. The broker's own holding
            # table is therefore authoritative: align the ledger to it, keeping
            # the broker's average cost when a quantity has to be added.
            for sym, qty in (acct.positions or {}).items():
                pos = self.ledger.get(sym)
                cur = pos.qty if pos else 0
                if int(cur) == int(qty):
                    continue
                delta = int(qty) - int(cur)
                avg = float((acct.avg_prices or {}).get(sym) or 0)
                if delta > 0 and avg <= 0:
                    info = self.quote(sym, max_age=3.0)
                    avg = float((info.last if info else 0) or 0)
                if delta > 0:
                    self.ledger.record_adjust(sym, delta, cost_delta=delta * avg,
                                              reason="reconcile_broker")
                else:
                    cost_out = (pos.avg_cost if pos else 0) * abs(delta)
                    self.ledger.record_adjust(sym, delta, cost_delta=cost_out,
                                              reason="reconcile_broker")
                out.setdefault("seeded", []).append(sym)
        except Exception as exc:
            out["account_error"] = str(exc)
        if out["imported"] or out["diffs"]:
            self.state.set("last_sync", {"at": self.now().isoformat(timespec="seconds"), **out})
        if out["diffs"]:
            try:
                from .alerts import alert_reconcile
                alert_reconcile(out["diffs"])
            except Exception:
                pass
        return out

    def account_snapshot(self) -> Dict[str, Any]:
        snap = self.ledger.snapshot()
        try:
            acct = self.broker.account()
            snap["cash_broker"] = acct.buying_power
            snap["equity"] = float(acct.buying_power) + float(snap.get("exposure") or 0)
        except Exception:
            snap["cash_broker"] = None
        snap["mode"] = self.cfg.mode
        return snap

    # ----------------------------------------------------------------- marks
    def refresh_marks(self, symbols: Optional[List[str]] = None) -> Dict[str, Any]:
        syms = symbols or [p.symbol for p in self.ledger.open_positions()]
        marks: Dict[str, Any] = {}
        for sym in syms:
            info = self.quote(sym, max_age=5.0)
            if info is None:
                continue
            px = info.last or info.best_bid or info.best_ask
            if px:
                self.ledger.mark_price(sym, float(px))
                self._remember_price(sym, float(px))
            locked_up = bool(info.ceiling and px and px >= info.ceiling * 0.999
                             and not info.best_ask)
            prev = (self.state.get("lock_flags") or {}).get(sym, {})
            self._update_peak(sym, px)
            marks[sym] = {"price": px, "locked_up": locked_up,
                          "was_locked_up": bool(prev.get("locked_up"))}
            flags = dict(self.state.get("lock_flags") or {})
            flags[sym] = {"locked_up": locked_up, "at": self.now().isoformat(timespec="seconds")}
            self.state.set("lock_flags", flags)
            if locked_up:
                ever = dict(self.state.get("ever_locked") or {})
                ever[sym] = True
                self.state.set("ever_locked", ever)
        return marks

    def _remember_price(self, symbol: str, price: float) -> None:
        """Persist the latest price so the dashboard can show a live P&L even when
        it reads the ledger through a separate process."""
        marks = dict(self.state.get("marks") or {})
        marks[symbol] = {"price": float(price),
                         "at": self.now().isoformat(timespec="seconds")}
        self.state.set("marks", marks)

    def _update_peak(self, symbol: str, price: Optional[float]) -> None:
        pos = self.ledger.get(symbol)
        if pos is None or not price or not pos.avg_cost:
            return
        pnl = (price - pos.avg_cost) / pos.avg_cost * 100.0
        peaks = dict(self.state.get("peak_pnl") or {})
        cur = float(peaks.get(symbol, 0) or 0)
        if pnl > cur:
            peaks[symbol] = round(pnl, 4)
            self.state.set("peak_pnl", peaks)

    # ------------------------------------------------------------------ sell
    def managed_symbols(self) -> set:
        return set(self.state.get("managed") or [])

    def _is_managed(self, symbol: str) -> bool:
        if bool(self.cfg.get("risk.manage_existing_positions", False)):
            return True
        return symbol in self.managed_symbols()

    def _mark_managed(self, symbol: str) -> None:
        cur = list(self.state.get("managed") or [])
        if symbol not in cur:
            cur.append(symbol)
            self.state.set("managed", cur)

    def excluded_symbols(self) -> set:
        """Symbols that are accounted for but never traded or counted as slots.

        Used for broker-side artifacts (e.g. a phantom holding created by a
        broker bug) so they cannot block the 3-slot cycle or distort sizing.
        """
        return set(self.cfg.get("portfolio.exclude_symbols") or [])

    def scan_exits(self) -> List[ExitDecision]:
        s = self.cfg.section("sell")
        peaks = self.state.get("peak_pnl") or {}
        flags = self.state.get("lock_flags") or {}
        out: List[ExitDecision] = []
        excluded = self.excluded_symbols()
        for pos in self.ledger.open_positions():
            if pos.symbol in excluded:
                continue
            if pos.symbol in set(s.get("blocked_symbols") or []):
                continue
            if not self._is_managed(pos.symbol):
                # Legacy holdings are accounted for but not traded until the
                # operator explicitly adopts them (run.py adopt --symbols ...).
                continue
            info = self.quote(pos.symbol, max_age=10.0)
            if info is None:
                continue
            px = info.last or info.best_bid or info.best_ask
            if not px:
                continue
            # sanity: during the pre-open auction the panel can expose a placeholder
            # price (saw px ~0.15 vs avg 21,556 -> "stop -99.99%"). Never act on a
            # price that is less than half the cost basis.
            try:
                if pos.avg_cost and float(px) < float(pos.avg_cost) * 0.5:
                    self.log(f"exit skip {pos.symbol}: implausible price {px} vs avg {pos.avg_cost:.0f}")
                    continue
            except Exception:
                pass
            self.ledger.mark_price(pos.symbol, float(px))
            self._remember_price(pos.symbol, float(px))
            self._update_peak(pos.symbol, px)
            first = _parse_ts(pos.first_ts or "")
            days_held = self.cal.trading_days_between(first.date(), self.now().date()) if first else 0
            # NET basis: the broker charges its fee on the buy and again on the
            # sell. "قیمت سربه‌سر" is the price at which a sale nets exactly zero,
            # so measuring against it already removes both fees. Falling back to
            # our own average cost (buy fee only) keeps the bot working before the
            # first sync of the day.
            basis_map = dict(self.state.get("cost_basis") or {})
            try:
                basis_map.update(json.loads((self.cfg.path("state_dir") / "cost_basis.json").read_text(encoding="utf-8")))
            except Exception:
                pass
            basis = float(basis_map.get(pos.symbol) or pos.avg_cost)
            invested_net = basis * pos.qty
            total_net = ((pos.realized + (float(px) - basis) * pos.qty) / invested_net * 100.0
                         if invested_net else 0.0)
            d = decide_exit(
                symbol=pos.symbol, qty=pos.qty, avg_cost=basis, last_price=float(px),
                days_held=days_held,
                peak_pnl_pct=float(peaks.get(pos.symbol, 0) or 0),
                info=info,
                # both bases, both NET of the broker's fees
                total_pnl_pct=total_net,
                target_pct=float(s.get("target_pct", 10.0)),
                stop_pct=float(s.get("stop_pct", 3.0)),
                max_hold_days=int(s.get("max_hold_days", 10)),
                trailing_stop_pct=s.get("trailing_stop_pct"),
                trailing_arm_pct=float(s.get("trailing_arm_pct", 5.0)),
                exit_on_queue_break=bool(s.get("exit_on_queue_break", True)),
                was_locked_up=bool((flags.get(pos.symbol) or {}).get("locked_up")),
                riding_queue=bool((self.state.get("ever_locked") or {}).get(pos.symbol, False)),
            )
            if d:
                out.append(d)
                continue
            # Burst As Exit: tape-based exit that fires before the -3% stop can.
            try:
                from .burst_exit import should_exit as _burst_exit
                riding = bool((self.state.get("ever_locked") or {}).get(pos.symbol, False))
                b = _burst_exit(self.cfg, pos.symbol, total_net, riding)
                if b:
                    out.append(ExitDecision(pos.symbol, pos.qty, b["reason"],
                                            (float(px) - pos.avg_cost) / pos.avg_cost * 100.0,
                                            info.best_bid or float(px)))
                    self.log(f"burst exit {pos.symbol}: {b.get('reason')} "
                             f"flk={b.get('flk')} flc={b.get('flc')} "
                             f"dist_peak={b.get('distance_from_peak_pct')}")
            except Exception as exc:
                self.log(f"burst_exit check failed for {pos.symbol}: {exc}")
        return out

    def _can_attempt(self, day: str, symbol: str, side: str) -> Tuple[bool, str]:
        """Order-attempt policy.

        Allows a retry after a rejection, but never after a *live* order, and
        never beyond the exchange/broker per-symbol daily order limit.
        """
        prior = self.orders.attempts(day, symbol, side)
        if any(i.status == "verified" for i in prior):
            return False, "already_done"
        live = [i for i in prior if i.status in ("submitted", "unverified")]
        if live:
            cooldown = float(self.cfg.get("sell.retry_cooldown_sec", 180))
            last = _parse_ts(live[-1].updated_at or live[-1].created_at)
            if last is not None:
                age = (now_tehran().replace(tzinfo=None) - last.replace(tzinfo=None)).total_seconds()
                if age < cooldown:
                    return False, "cooldown"
            return True, "retry_live"
        max_att = int(self.cfg.get("sell.max_attempts_per_day", 3))
        if len(prior) >= max_att:
            return False, "max_attempts"
        per_sym = int(self.cfg.get("risk.max_orders_per_symbol_per_day", 3))
        if self.orders.attempts_count(day, symbol) >= per_sym:
            return False, "symbol_order_limit"
        total_cap = int(self.cfg.get("risk.max_daily_orders_total", 12))
        if sum(1 for i in self.orders.for_day(day)) >= total_cap:
            return False, "daily_order_limit"
        return True, "ok"

    def sell_cycle(self, dry: Optional[bool] = None) -> Dict[str, Any]:
        dry = (self.cfg.mode != "live") if dry is None else dry
        try:
            self.connect()
        except Exception as exc:
            return {"session": "unknown", "allowed": False, "decisions": [],
                    "executed": [], "skipped": [], "reason": f"connect_failed:{exc}"}
        allowed, session = self.cal.is_open_for_sell(
            self.cfg.section("market"), self.now(),
            window=self.cfg.get("sell.allow_sell_only_window"))
        if not allowed and session == "pre_open" and self.cfg.get("sell.allow_pre_open_sell", True):
            # Pre-open auction: orders entered here take part in the opening match,
            # which is the only way to exit before a sell-queue can form.
            pw = self.cfg.get("sell.pre_open_window") or ["08:30", "09:00"]
            from .calendar_ir import _t
            t = self.now().time()
            if _t(pw[0]) <= t <= _t(pw[1]):
                allowed, session = True, "pre_open_auction"
        out: Dict[str, Any] = {"session": session, "allowed": allowed,
                               "decisions": [], "executed": [], "skipped": []}
        if not allowed:
            out["reason"] = f"market_not_sellable:{session}"
            return out
        decisions = self.scan_exits()
        out["decisions"] = [d.__dict__ for d in decisions]
        for d in decisions:
            if d.blocked:
                out["skipped"].append({"symbol": d.symbol, "reason": d.blocked})
                continue
            res = self.execute_exit(d, dry=dry)
            (out["executed"] if res.get("ok") else out["skipped"]).append(res)
        return out

    def _submit_order(self, side: str, symbol: str, qty: int, price: float) -> Dict[str, Any]:
        """Send an order without ever loading a page manually.

        SELL → portfolio screen (tab 1): row -> side switch -> inputs -> send.
        BUY  → order-entry screen (tab 4): Angular order scope API, which is the
               path the previous robot used and the operator finds easier.
        The tab is switched by clicking the in-app link (safe); `driver.get()` and
        the root link are never used.
        """
        drv = getattr(self.broker, "driver", None)
        url = str(getattr(drv, "current_url", "") or "")
        goto = getattr(self.broker, "goto_page", None)
        expected_isin = ""
        # 0) CDP path: works even when chromedriver cannot drive the browser
        #    (Chrome 155 vs driver 154 on 2026-10-24) and rounds the price to the
        #    symbol's tick so «قیمت باید مضربی از ۱۰ باشد» cannot happen.
        try:
            from .cdp_trade import submit_via_cdp
            cdp_res = submit_via_cdp(side, symbol, int(qty), float(price))
            self.log(f"cdp submit {side} {symbol}: {str(cdp_res.get('reason') or cdp_res.get('message') or 'ok')[:90]}")
            if cdp_res.get("ok"):
                return cdp_res
        except Exception as exc:
            self.log(f"cdp submit failed: {str(exc)[:80]}")
        try:
            q = self.market_quote(symbol)
            expected_isin = str(getattr(q, "isin", "") or "") if q else ""
        except Exception:
            expected_isin = ""
        # Page-1 row click is today's proven path (no navigation, the session
        # survives). The order-entry search on page-4 re-initialises the SPA and
        # once built a ticket on the wrong ISIN, so it is only the fallback.
        # operator instruction (2026-10-21): tab 2 is the order screen for BOTH
        # sides; tab 1 is only for reading the portfolio
        tab2 = getattr(self.broker, "submit_tab2", None)
        if callable(tab2):
            try:
                r2 = tab2(side, symbol, qty, price, expected_isin)
                if r2.get("placed") or r2.get("message") or not r2.get("reason"):
                    return r2
                self.log(f"tab2 submit failed ({r2.get('reason')}); trying portfolio UI")
            except Exception as exc:
                self.log(f"tab2 submit error ({exc}); trying portfolio UI")
        ui0 = getattr(self.broker, "submit_via_ui", None)
        if callable(ui0):
            try:
                return ui0(side, symbol, qty, price)
            except Exception as exc:
                self.log(f"portfolio UI submit failed ({exc}); trying guarded path")
        place = getattr(self.broker, "place_order", None)
        if callable(place):
            try:
                return place(side, symbol, qty, price, expected_isin)
            except Exception as exc:
                self.log(f"guarded submit failed ({exc}); falling back")
        if str(side).lower() == "buy":
            # No page switching: in this panel ANY navigation invalidates the
            # session. The portfolio screen has a buy side switch, so both sides
            # are placed from the same page.
            ui = getattr(self.broker, "submit_via_ui", None)
            if callable(ui):
                try:
                    return ui("buy", symbol, qty, price)
                except Exception as exc:
                    self.log(f"buy via portfolio UI failed ({exc})")
            return self.broker.submit("buy", symbol, qty, price)
        else:
            if callable(goto):
                try:
                    goto(1)
                except Exception:
                    pass
            ui = getattr(self.broker, "submit_via_ui", None)
            if callable(ui):
                try:
                    return ui(side, symbol, qty, price)
                except Exception as exc:
                    self.log(f"ui submit failed ({exc}); falling back to scope API")
        return self.broker.submit(side, symbol, qty, price)

    def execute_exit(self, d: ExitDecision, dry: bool = True) -> Dict[str, Any]:
        day = self.today()
        sym = d.symbol
        ok, why = self._can_attempt(day, sym, "sell")
        if not ok:
            return {"ok": False, "symbol": sym, "reason": why}

        s = self.cfg.section("sell")
        mode = str(s.get("price_mode", "best_bid"))
        info = self.quote(sym)
        price = None
        if info is not None:
            base = {"best_bid": info.best_bid, "last": info.last, "floor_minus": info.floor}.get(mode)
            price = base or info.last or info.best_bid
        # A stop/queue-break exit must actually fill: in the Tehran market a
        # limit price above the opening print simply never trades and the position
        # ends up in a sell-queue. For those exits the limit is set at the daily
        # floor, which means "accept any price the exchange allows today".
        aggressive = str(s.get("stop_price_mode", "floor")) == "floor" and (
            "stop" in str(d.reason) or str(d.reason).startswith("queue"))
        if aggressive and info is not None and info.floor:
            price = float(info.floor)
        else:
            price = d.price_hint or price
            if not price:
                return {"ok": False, "symbol": sym, "reason": "no_price"}
            price = float(price) * (1 + float(s.get("price_offset_pct", 0.0)) / 100.0)
        if info and info.floor:
            price = max(price, float(info.floor))
        if info and info.ceiling:
            price = min(price, float(info.ceiling))
        # At the moment of selling, aim at the HIGHEST DEMAND (best bid) capped by the
        # ceiling: a standing buy queue at the ceiling fills at once, and if the queue
        # sits a tick lower we still take the best price available right now.
        best_demand = None
        try:
            best_demand = float(getattr(info, "best_bid", None) or 0) or None
        except Exception:
            best_demand = None
        if best_demand is None:
            try:
                cells = self.broker.row_cells(sym) if hasattr(self.broker, "row_cells") else {}
                raw = (cells or {}).get("ح بهترین تقاضا") or ""
                digits = str(raw).translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩",
                                                          "01234567890123456789"))
                best_demand = float(digits.replace(",", "").strip() or 0) or None
            except Exception:
                best_demand = None
        if not aggressive and info is not None and info.ceiling:
            cap = float(info.ceiling)
            if best_demand and 0 < best_demand <= cap:
                price = best_demand
            elif info.last and float(info.last) >= cap * 0.999:
                price = cap
        # Exchange rule (learned the hard way today): a sell order may be at most
        # half of the down-range away from the last trade, otherwise it is rejected
        # with "حداکثر ۵۰٪ دامنه نوسان". Sell floor = (last + floor) / 2.
        if info and info.last and info.floor:
            lo = (float(info.last) + float(info.floor)) / 2.0
            price = max(price, lo)
        price = float(int(round(price)))

        # TSE/broker cap the quantity per order ("MaxQuantityOrder", e.g. 1000
        # shares while we hold 3000). The position must therefore be sold in
        # several orders; the cap differs per symbol, so it is read from the
        # symbol's own snapshot instead of being hard-coded.
        # shares already committed to an OPEN sell order must not be sold twice:
        # the panel rejects the duplicate with "موجودی سهام کافی نیست" and the bot
        # would otherwise retry it every cycle (seen on وایرا, 2026-10-21).
        try:
            cells = self.broker.row_cells(sym) if hasattr(self.broker, "row_cells") else {}
            raw_open = str((cells or {}).get("سفارشات باز") or "")
            digits = raw_open.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
            parts = [p.strip().replace(",", "") for p in digits.split("-")]
            open_buy = float(parts[0]) if parts and parts[0] else 0.0
            open_sell = float(parts[1]) if len(parts) > 1 and parts[1] else 0.0
        except Exception:
            open_buy, open_sell = 0.0, 0.0
        if open_sell > 0:
            available = float(d.qty) - open_sell
            if available <= 0:
                return {"ok": False, "symbol": sym, "reason": "already_in_open_order"}
            d.qty = int(available)
        # per-order cap from the panel («آستانه حجم») and the live best bid
        panel_max, panel_bid = 0, 0.0
        try:
            if hasattr(self.broker, "bind_symbol"):
                self.broker.bind_symbol(sym)          # per-symbol «آستانه حجم»
            if hasattr(self.broker, "order_limits"):
                _lo, panel_max = self.broker.order_limits()
            if hasattr(self.broker, "best_bid_ask"):
                panel_bid, _ask = self.broker.best_bid_ask()
        except Exception:
            panel_max, panel_bid = 0, 0.0
        if panel_bid and not aggressive:
            price = float(panel_bid)
        max_q = int(getattr(info, "max_qty", 0) or 0) if info is not None else 0
        if max_q <= 0 and panel_max > 0:
            max_q = int(panel_max)
        min_q = int(getattr(info, "min_qty", 1) or 1) if info is not None else 1
        chunks: List[int] = []
        left = int(d.qty)
        while left > 0:
            n = min(left, max_q) if max_q > 0 else left
            if n < min_q:
                break  # remainder below the exchange minimum: not orderable
            chunks.append(n)
            left -= n
            if len(chunks) >= 10:
                break

        results: List[Dict[str, Any]] = []
        for idx, n in enumerate(chunks, 1):
            try:
                intent = self.orders.create(day, sym, "sell", n, price,
                                            reason=d.reason,
                                            extra={"pnl_pct": round(d.pnl_pct, 3),
                                                   "max_qty": max_q, "chunk": idx})
            except ValueError as exc:
                results.append({"ok": False, "qty": n, "reason": f"intent_exists:{exc}"})
                break
            if dry:
                self.orders.set_status(intent.ref, "cancelled", note="dry_run")
                results.append({"ok": True, "dry_run": True, "qty": n, "price": price,
                                "ref": intent.ref})
                continue
            res = self._submit_order("sell", sym, n, price)
            try:
                from .alerts import alert_order
                ok_submit = not (res.get("placed") is False or res.get("sent") is False
                                 or res.get("ok") is False)
                alert_order(sym, "sell", n, price,
                            "ثبت شد" if ok_submit else "رد شد",
                            str(res.get("message") or res.get("reason") or "")[:200],
                            {"دلیل": d.reason, "سود/زیان": f"{d.pnl_pct:+.2f}%"})
            except Exception:
                pass
            # A submission that never reached the panel must NOT count as live: the
            # attempt policy would then block retries for the whole cooldown window
            # (exactly what happened to the وایرا sale this morning).
            if (res.get("placed") is False or res.get("sent") is False
                    or res.get("ok") is False
                    or (res.get("reason") and not res.get("sent") and not res.get("placed"))):
                self.orders.set_status(intent.ref, "rejected", attempts=1,
                                       note=json.dumps(res, ensure_ascii=False)[:400])
                results.append({"ok": False, "qty": n, "price": price, "ref": intent.ref,
                                "submit": res, "reason": "not_placed"})
                break
            self.orders.set_status(intent.ref, "unverified", attempts=1,
                                   note=json.dumps(res, ensure_ascii=False)[:400])
            verified = self._verify_intent(intent, timeout=float(s.get("verify_timeout_sec", 45)))
            results.append({"ok": bool(verified.get("ok")), "qty": n, "price": price,
                            "ref": intent.ref, "verify": verified, "submit": res})
            if not verified.get("ok"):
                # Never continue after an unconfirmed order: that is how the old
                # bot ended up with duplicate submissions.
                break
        return {"ok": all(r.get("ok") for r in results) if results else False,
                "dry_run": dry, "symbol": sym, "qty": d.qty, "price": price,
                "reason": d.reason, "max_qty": max_q, "chunks": chunks,
                "orders": results}

    # ------------------------------------------------------------------- buy
    def load_picks(self) -> List[Pick]:
        sig = self.cfg.section("signals")
        src = str(sig.get("source", "rules_v2"))
        path = Path(str(sig.get("external_picks_path") or ""))
        if src == "external_json" and path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            for_date = str(data.get("for_date_g") or "")
            if for_date and for_date != self.today():
                raise ValueError(f"picks are for {for_date}, today is {self.today()}")
            min_score = float(self.cfg.get("buy.min_score", 0.6))
            picks = [Pick(symbol=str(p.get("symbol")).strip(), score=float(p.get("score", 0) or 0),
                          for_date=for_date, source=str(data.get("source") or "external"))
                     for p in (data.get("picks") or []) if p.get("symbol")]
            return [p for p in picks if p.score >= min_score]
        # rules_v2 is produced by signals.generate_picks()
        if src == "ride_locked":
            # TSE_Robot_V2 result: buy stocks that closed locked at the ceiling on
            # the last session and are likely to keep the lock for >= 2 sessions.
            from . import ride_locked
            r = self.cfg.section("ride")
            # prefer the nightly scan (written after the data download); fall back to
            # recomputing from the research event table when it is missing
            try:
                from pathlib import Path as _P
                nightly = _P(str(self.cfg.path("state_dir"))) / "candidates_next.csv"
                if nightly.exists():
                    import pandas as _pd
                    nd = _pd.read_csv(nightly)
                    if len(nd):
                        return [ride_locked.RidePick(symbol=str(row["symbol"]),
                                                     score=float(row["prob"]),
                                                     for_date=self.today())
                                for _, row in nd.iterrows()]
            except Exception as exc:
                self.log(f"nightly candidates unreadable ({exc}); recomputing")
            picks = ride_locked.candidates(
                str(r.get("events_path")),
                str(r.get("model_path")),
                float(r.get("min_prob", 0.7)),
                int(r.get("top_n", 3)),
                int(r.get("lookback", 3)),
            )
            for p in picks:
                p.for_date = self.today()
            return picks
        if src == "queue_v3":
            from .signals_v3 import generate_picks as gen_v3
            picks = gen_v3(self.cfg)
            today = self.today()
            stale = [p for p in picks if p.for_date and p.for_date != today]
            if stale:
                raise ValueError(
                    f"queue_v3 picks are for {stale[0].for_date}, today is {today} "
                    f"(data is stale -> refusing to trade)")
            return picks
        from .signals import generate_picks
        return generate_picks(self.cfg)

    def buy_cycle(self, dry: Optional[bool] = None) -> Dict[str, Any]:
        dry = (self.cfg.mode != "live") if dry is None else dry
        p = self.cfg.section("portfolio")
        b = self.cfg.section("buy")
        day = self.today()
        out: Dict[str, Any] = {"day": day, "dry_run": dry, "candidates": [],
                               "executed": [], "skipped": []}
        try:
            self.connect()
        except Exception as exc:
            out["reason"] = f"connect_failed:{exc}"
            return out

        allowed, session = self.cal.is_open_for_orders(
            self.cfg.section("market"), self.now(), allow_pre_open=True)
        out["session"] = session
        if not allowed:
            out["reason"] = f"market_not_buyable:{session}"
            return out

        max_buys = int(self.cfg.get("risk.max_daily_buys", 3))
        if self.orders.counts(day, "buy") >= max_buys:
            out["reason"] = "max_daily_buys_reached"
            return out

        try:
            picks = self.load_picks()
        except Exception as exc:
            out["reason"] = f"picks_error:{exc}"
            return out
        out["picks"] = [p_.__dict__ for p_ in picks]

        # Cash only exists in the order-entry header (page 4); the portfolio screen
        # does not print it. Move there first (safe page switch), then read.
        cash = 0.0
        try:
            bp = self.broker.buying_power() if hasattr(self.broker, "buying_power") else None
            if not bp:
                goto = getattr(self.broker, "goto_page", None)
                if callable(goto):
                    goto(4)
                bp = self.broker.buying_power() if hasattr(self.broker, "buying_power") else None
            cash = float(bp or 0)
        except Exception as exc:
            self.log(f"buy: buying power unreadable ({exc})")
        # The first live orders must not empty the account: legacy holdings are
        # unmanaged and their cash is not part of the new strategy budget.
        cash_budget = cash * float(self.cfg.get("buy.max_cash_fraction_per_buy", 1.0))
        # Slot accounting uses every open position EXCEPT symbols excluded in
        # config (a broker-side phantom position must not consume a slot or
        # inflate the equal-money target).
        excluded = self.excluded_symbols()
        live_positions = [p_ for p_ in self.ledger.open_positions()
                          if p_.symbol not in excluded]
        open_pos = {p_.symbol for p_ in live_positions}
        exposure = 0.0
        for p_ in live_positions:
            q = self.market_quote(p_.symbol)
            px = (q.last if q and q.last else (p_.last_price or p_.avg_cost))
            exposure += float(px) * p_.qty
        equity = cash + exposure
        max_pos = int(p.get("max_positions", 3))
        slots = max(0, max_pos - len(open_pos))
        out.update({"cash": cash, "exposure": exposure, "equity": equity,
                    "open_positions": sorted(open_pos),
                    "excluded": sorted(excluded), "slots": slots})
        if slots <= 0:
            out["reason"] = "no_free_slot"
            return out

        already = set(open_pos)
        for pick in picks:
            if len(out["executed"]) >= slots:
                break
            sym = pick.symbol
            if sym in already:
                out["skipped"].append({"symbol": sym, "reason": "already_held"})
                continue
            if self.orders.any_today(day, sym, "buy"):
                out["skipped"].append({"symbol": sym, "reason": "already_ordered_today"})
                continue
            # the broker announces a delayed start (e.g. «معاملات از ساعت ۱۰»); do not
            # burn attempts before that hour
            try:
                notice_hour = int(self.broker.trading_start_hour()) if hasattr(self.broker, "trading_start_hour") else 0
            except Exception:
                notice_hour = 0
            if notice_hour and self.now().hour < notice_hour:
                out["skipped"].append({"symbol": sym,
                                       "reason": f"trading_starts_at_{notice_hour:02d}:00"})
                continue
            info = self.quote(sym)
            if info is None:
                out["skipped"].append({"symbol": sym, "reason": "no_quote"})
                continue
            # Order-book confirmation (queue size at the ceiling, no supply at the
            # ceiling, bid/ask imbalance). This is the filter that turns a ranked
            # candidate into an actual queue entry.
            try:
                from . import preopen
                metrics = preopen.queue_metrics(self._market_snapshot(), sym)
                ok_book, why_book, metrics = preopen.confirm(self.cfg, metrics)
                out.setdefault("books", {})[sym] = metrics
                if not ok_book:
                    out["skipped"].append({"symbol": sym, "reason": f"preopen:{why_book}"})
                    continue
                if metrics.get("ceiling"):
                    info.ceiling = float(metrics["ceiling"])
                if metrics.get("floor"):
                    info.floor = float(metrics["floor"])
            except Exception as exc:
                self.log(f"preopen filter failed for {sym}: {exc}")
            plan = plan_buy(
                info, equity=equity, cash=cash_budget, exposure=exposure,
                open_positions=len(open_pos), slots=slots, max_positions=max_pos,
                max_value_per_position_pct=float(p.get("max_value_per_position_pct", 40)),
                max_total_exposure_pct=float(p.get("max_total_exposure_pct", 90)),
                min_cash_buffer_pct=float(p.get("min_cash_buffer_pct", 5)),
                deploy_fraction=float(p.get("deploy_fraction", 1.0)),
                min_order_rial=float(b.get("min_order_rial", 5_000_000)),
                min_order_rial_base=float(b.get("min_order_rial_base_market", 50_000_000)),
                price_mode=str(b.get("entry_price_mode", "best_bid")),
                price_offset_pct=float(b.get("price_offset_pct", 0.0)),
            )
            if not plan.ok:
                out["skipped"].append({"symbol": sym, "reason": plan.reason})
                continue
            res = self.execute_buy(sym, plan.qty, plan.price, pick.score, dry=dry)
            if res.get("ok"):
                out["executed"].append(res)
                already.add(sym)
                cash -= plan.value_rial
                exposure += plan.value_rial
                slots = max(0, slots - 1)
                out["slots"] = slots
            else:
                out["skipped"].append(res)
        return out

    def execute_buy(self, symbol: str, qty: int, price: float, score: float,
                    dry: bool = True) -> Dict[str, Any]:
        day = self.today()
        ok, why = self._can_attempt(day, symbol, "buy")
        if not ok:
            return {"ok": False, "symbol": symbol, "reason": why}
        # Mirror of the sell rule: a buy order may be at most half of the up-range
        # above the last trade, otherwise the exchange rejects it.
        try:
            info = self.market_quote(symbol)
            if info is not None and info.last and info.ceiling:
                hi = (float(info.last) + float(info.ceiling)) / 2.0
                price = min(float(price), hi)
        except Exception:
            pass
        # LIVE cash check: the blocked money from an unfilled ticket is released the
        # next session, so the affordable size must be recomputed at every attempt.
        try:
            power = float(self.broker.buying_power() or 0) if hasattr(self.broker, "buying_power") else 0.0
            self._last_power_text = f"{power:,.0f} ریال"
        except Exception:
            power = 0.0
        if power > 0:
            fee_mult = 1.0 + float(self.cfg.get("fees.buy_pct", 1.5)) / 100.0
            affordable = int(power / max(float(price) * fee_mult, 1.0))
            if qty > affordable:
                self.log(f"buy {symbol}: qty {qty:,} capped to {affordable:,} "
                         f"(power {power:,.0f} rial @ {price:,.0f})")
                qty = affordable
            else:
                self.log(f"buy {symbol}: qty {qty:,} within power {power:,.0f} rial @ {price:,.0f}")
            if qty <= 0:
                return {"ok": False, "symbol": symbol, "reason": "insufficient_cash",
                        "power_rial": power, "price": price}

        # Split into tickets the panel accepts («آستانه حجم»), same rule as the sell
        # path: N-1 tickets of the maximum size plus one remainder ticket.
        max_q, min_q = 0, 1
        try:
            if hasattr(self.broker, "bind_symbol"):
                self.broker.bind_symbol(symbol)       # per-symbol «آستانه حجم»
            if hasattr(self.broker, "order_limits"):
                min_q, max_q = self.broker.order_limits()
        except Exception:
            max_q, min_q = 0, 1
        chunks: List[int] = []
        left = int(qty)
        while left > 0:
            n = min(left, max_q) if max_q > 0 else left
            if n < max(1, int(min_q)):
                break
            chunks.append(n)
            left -= n
            if len(chunks) >= 20:
                break
        if not chunks:
            return {"ok": False, "symbol": symbol, "reason": "qty_below_minimum"}

        results: List[Dict[str, Any]] = []
        for n in chunks:
            intent = self.orders.create(day, symbol, "buy", n, price,
                                        reason=f"score={score:.4f}",
                                        extra={"score": score})
            if dry:
                self.orders.set_status(intent.ref, "cancelled", note="dry_run")
                results.append({"ok": True, "dry_run": True, "qty": n, "price": price,
                                "value_rial": n * price})
                continue
            res = self._submit_order("buy", symbol, n, price)
            try:
                from .alerts import alert_order
                ok_submit = not (res.get("placed") is False or res.get("sent") is False
                                 or res.get("ok") is False)
                alert_order(symbol, "buy", n, price,
                            "ثبت شد" if ok_submit else "رد شد",
                            str(res.get("message") or res.get("reason") or "")[:200],
                            {"تیکت": f"{n:,} از {qty:,}", "قدرت خرید": self._last_power_text})
            except Exception:
                pass
            if (res.get("placed") is False or res.get("sent") is False
                    or res.get("ok") is False
                    or (res.get("reason") and not res.get("sent") and not res.get("placed"))):
                self.orders.set_status(intent.ref, "rejected", attempts=1,
                                       note=json.dumps(res, ensure_ascii=False)[:400])
                results.append({"ok": False, "qty": n, "price": price,
                                "reason": "not_placed", "submit": res})
                break
            self.orders.set_status(intent.ref, "unverified", attempts=1,
                                   note=json.dumps(res, ensure_ascii=False)[:400])
            verified = self._verify_intent(intent, timeout=float(
                self.cfg.get("buy.verify_timeout_sec", 45)))
            results.append({"ok": bool(verified.get("ok")), "qty": n, "price": price,
                            "ref": intent.ref, "verify": verified, "submit": res})
            if not verified.get("ok"):
                break
        filled = sum(r["qty"] for r in results if r.get("ok"))
        if filled and not dry:
            self._mark_managed(symbol)
        return {"ok": all(r.get("ok") for r in results), "symbol": symbol, "qty": qty,
                "filled": filled, "price": price, "chunks": chunks,
                "value_rial": qty * price, "orders": results, "score": score}

    # ---------------------------------------------------------- verification
    def _verify_intent(self, intent: OrderIntent, timeout: float = 20.0) -> Dict[str, Any]:
        """Confirm the fill from the PORTFOLIO ROW (no navigation, session survives).

        "فروش امروز" / "خرید امروز" carry the executed quantity and "میانگین فروش" /
        "میانگین خرید" the average price, so a filled order can be recorded in the
        ledger without ever opening the order list (which drops the session).
        """
        def num(txt: str) -> float:
            t = str(txt or "").translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
            t = t.replace(",", "").replace("٬", "").strip()
            try:
                return float(t)
            except Exception:
                return 0.0

        deadline = time.time() + max(5.0, timeout)
        while time.time() < deadline:
            try:
                cells = self.broker.row_cells(intent.symbol) if hasattr(self.broker, "row_cells") else {}
            except Exception:
                cells = {}
            if cells:
                done = num(cells.get("فروش امروز") if intent.side == "sell" else cells.get("خرید امروز"))
                if done >= intent.qty:
                    px = num(cells.get("میانگین فروش") if intent.side == "sell" else cells.get("میانگین خرید"))
                    if px <= 0:
                        px = float(intent.price)
                    fee_pct = float(self.cfg.get("fees.sell_pct", 0.5)) if intent.side == "sell" \
                        else float(self.cfg.get("fees.buy_pct", 1.5))
                    filled = int(min(done, intent.qty))
                    self.ledger.record_fill(symbol=intent.symbol, side=intent.side, qty=filled,
                                            price=px, fee=filled * px * fee_pct / 100.0,
                                            order_no=f"row-{intent.ref}", source="broker", ts=None)
                    self.orders.set_status(intent.ref, "verified", filled_qty=filled)
                    return {"ok": True, "filled": filled, "price": px}
            time.sleep(4)
        self.orders.set_status(intent.ref, "submitted", note="sent_not_confirmed")
        return {"ok": True, "reason": "submitted_unconfirmed"}
    # ------------------------------------------------------------- reporting
    def daily_report(self) -> Dict[str, Any]:
        snap = self.account_snapshot()
        day = self.today()
        intents = self.orders.for_day(day)
        return {
            "day": day,
            "mode": self.cfg.mode,
            "session": self.session(),
            "account": snap,
            "orders_today": [i.to_dict() for i in intents],
            "last_sync": self.state.get("last_sync"),
        }





















# -*- coding: utf-8 -*-
"""TSE Bot v2 — configuration.

Single source of truth for every tunable. JSON (no extra dependency).
All money values are in RIAL (integer). Percentages are percent (10.0 == 10%).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[2]  # ...\Tse
CONFIG_PATH = ROOT / "config" / "config.json"


def _deep_update(base: Dict[str, Any], extra: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_update(out[k], v)
        else:
            out[k] = v
    return out


DEFAULTS: Dict[str, Any] = {
    "schema": 2,
    "mode": "dry_run",            # dry_run | live   (live requires explicit --i-understand-risk)
    "timezone": "Asia/Tehran",

    "paths": {
        "state_dir": "state",
        "log_dir": "logs",
        "report_dir": "reports",
        "market_data_dir": r"C:\TSE_Strategy\data\all_symbols_daily",
        "picks_dir": "data",
    },

    "market": {
        "open": "09:00",
        "close_auction_start": "12:00",
        "close": "12:30",
        "pre_open_start": "08:30",
        # TSE daily price limit. Base market (فرابورس) uses 10 for some symbols;
        # symbol-level override is applied when known, otherwise this default.
        "daily_limit_pct": 5.0,
        "limit_pct_overrides": {},
        "tick_rial": 1,
    },

    "fees": {
        # Round-trip ~1.2% split evenly; override with broker statement figures.
        "buy_pct": 0.6,
        "sell_pct": 0.6,
        "min_fee_rial": 0,
    },

    "portfolio": {
        "max_positions": 3,
        "max_value_per_position_pct": 40.0,   # of equity
        "max_total_exposure_pct": 90.0,
        "min_cash_buffer_pct": 5.0,
        "deploy_fraction": 1.0,
    },

    "buy": {
        "max_picks_per_day": 3,
        "min_score": 0.60,               # enforced for EVERY pick source
        "entry_price_mode": "best_bid",  # best_bid | last | ceiling
        "price_offset_pct": 0.0,         # + means more aggressive
        "min_order_rial": 5_000_000,
        "min_order_rial_base_market": 50_000_000,
        "attempts_tehran": ["08:45", "09:00", "09:20", "09:45", "10:15", "11:00", "11:40"],
        "deadline_tehran": "12:05",
        "verify_timeout_sec": 45,
    },

    "sell": {
        "target_pct": 10.0,
        "stop_pct": 3.0,
        "max_hold_days": 10,
        "trailing_stop_pct": None,       # e.g. 4.0 -> arms after +5%
        "trailing_arm_pct": 5.0,
        "exit_on_queue_break": True,     # sell when High != Low (queue broken)
        "price_mode": "best_bid",        # best_bid | last | floor_minus
        "price_offset_pct": -0.3,        # negative = a bit below bid to ensure fill
        "check_interval_sec": 120,
        "max_attempts_per_day": 4,
        "retry_cooldown_sec": 600,
        "verify_timeout_sec": 45,
        "allow_sell_only_window": ["09:00", "12:28"],
        "blocked_symbols": [],
    },

    "schedule": {
        "preflight": "08:20",
        "nightly_refresh": "20:30",
        "picks": "21:10",
        "close_report": "13:10",
        "heartbeat_sec": 20,
    },

    "signals": {
        "source": "rules_v2",        # rules_v2 | external_json
        "external_picks_path": "",   # e.g. C:\TSE_Strategy\data\today_picks.json
        "top_n": 3,
        "weights": {
            "low_intraday_volume": 18.0,
            "no_recent_down": 14.0,
            "near_ceiling": 12.0,
            "low_reopen": 10.0,
            "rsi_mid": 10.0,
            "volume_contraction": 14.0,
            "range_compression": 10.0,
            "dist_from_52w_high": 12.0,
        },
    },

    "risk": {
        "max_daily_buys": 3,
        "max_orders_per_symbol_per_day": 1,
        "kill_switch_file": "state/STOP",
        "max_unrealized_loss_pct": -12.0,   # alert (not auto-liquidate)
        "alert_email": False,
    },

    "broker": {
        "kind": "selenium",          # selenium | paper
        "debugger_address": "127.0.0.1:9222",
        "chromedriver": r"C:\TSE_Strategy\chromedriver.exe",
        "trade_url": "https://online.meibours.ir/Home/Index/page-4",
        "orderlist_rows": 200,
        "page_settle_sec": 3.0,
    },

    "backtest": {
        "target_pct": 10.0,
        "stop_pct": 3.0,
        "max_hold_days": 10,
        "slippage_pct": 0.3,
        "fill_model": "open_if_not_locked",   # honest default
        "start": "1404-06-01",
        "end": "",
        "top_n": 3,
        "min_score": 0.60,
    },
}


@dataclass
class Config:
    raw: Dict[str, Any] = field(default_factory=dict)

    def get(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self.raw
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def path(self, key: str) -> Path:
        p = Path(self.get(f"paths.{key}", key))
        return p if p.is_absolute() else (ROOT / p)

    @property
    def mode(self) -> str:
        return str(self.get("mode", "dry_run"))

    @property
    def is_live(self) -> bool:
        return self.mode == "live"

    def section(self, name: str) -> Dict[str, Any]:
        return dict(self.get(name, {}) or {})


def load(path: Path | str | None = None) -> Config:
    p = Path(path) if path else CONFIG_PATH
    raw = dict(DEFAULTS)
    if p.exists():
        user = json.loads(p.read_text(encoding="utf-8"))
        raw = _deep_update(raw, user)
    cfg = Config(raw)
    for key in ("state_dir", "log_dir", "report_dir", "picks_dir"):
        cfg.path(key).mkdir(parents=True, exist_ok=True)
    return cfg


def save_template(path: Path | str | None = None) -> Path:
    p = Path(path) if path else CONFIG_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        p.write_text(json.dumps(DEFAULTS, ensure_ascii=False, indent=2), encoding="utf-8")
    return p

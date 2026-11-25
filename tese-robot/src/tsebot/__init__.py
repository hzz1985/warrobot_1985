# -*- coding: utf-8 -*-
"""TSE Bot v2 — corrected Tehran Stock Exchange trading robot.

Modules
-------
config          every tunable, JSON-backed
calendar_ir     Jalali calendar + market session model
ledger          append-only accounting truth (the fix for v1's broken books)
orders          order intents + idempotency (the fix for duplicate orders)
risk            sizing and exit decisions
engine          the only place that mutates state
scheduler       one clock, idempotent daily tasks
signals         transparent rank-only pick generator
backtest        honest backtest with a realistic fill model
broker_paper    deterministic simulator (dry-run / tests)
broker_selenium real broker UI adapter
"""
__version__ = "2.0.0"

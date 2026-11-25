# -*- coding: utf-8 -*-
"""Email alerts (Gmail SMTP with an app password).

Used for events a human must act on:
  * broker session logged out before/at the open  -> reminder to log in
  * order submitted but not verified                -> manual check
  * ledger vs broker mismatch                       -> reconciliation
  * kill switch / fatal task error

Settings live in `config/email.json`; the SECRET does not:
    {"enabled": true, "host": "smtp.gmail.com", "port": 465,
     "user": "...@gmail.com", "app_password": "",
     "to": ["..."], "min_interval_sec": 600}
The Gmail app password is read from the environment variable
WARROBOT_SMTP_PASSWORD (set it for the pm2 processes); the file value is only a
fallback for old installs and must stay "" in git.
"""
from __future__ import annotations

import json
import os
import smtplib
import ssl
import time
from email.message import EmailMessage
from pathlib import Path
from typing import Dict, List, Optional

from .config import ROOT
from .calendar_ir import now_tehran

_LAST_SENT: Dict[str, float] = {}


def _load_cfg(path: Optional[Path] = None) -> Dict:
    p = Path(path) if path else (ROOT / "config" / "email.json")
    if not p.exists():
        return {"enabled": False}
    try:
        cfg = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"enabled": False}
    env_pw = os.environ.get("WARROBOT_SMTP_PASSWORD", "").strip()
    if env_pw:
        cfg["app_password"] = env_pw
    return cfg


def _clean(s: str) -> str:
    return str(s or "").replace("\xa0", "").replace("\u200c", "").replace(" ", "").strip()


def send(subject: str, body: str, key: Optional[str] = None,
         force: bool = False, cfg_path: Optional[Path] = None) -> bool:
    """Send an email. `key` throttles repeats (same key within min_interval_sec)."""
    cfg = _load_cfg(cfg_path)
    if not cfg.get("enabled"):
        return False
    min_interval = float(cfg.get("min_interval_sec", 600))
    if key and not force:
        last = _LAST_SENT.get(key, 0.0)
        if time.time() - last < min_interval:
            return False
    to = cfg.get("to") or []
    if isinstance(to, str):
        to = [to]
    to = [t for t in to if t]
    if not to:
        return False

    msg = EmailMessage()
    msg["From"] = cfg.get("user", "")
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    msg.set_content(body, charset="utf-8")
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(cfg.get("host", "smtp.gmail.com"),
                              int(cfg.get("port", 465)), context=ctx, timeout=25) as srv:
            srv.login(_clean(cfg.get("user", "")), _clean(cfg.get("app_password", "")))
            srv.send_message(msg)
        _LAST_SENT[key or subject] = time.time()
        return True
    except Exception as exc:
        print(f"[{now_tehran():%H:%M:%S}] email failed: {type(exc).__name__}: {exc}", flush=True)
        return False


def alert_login_required(session_url: str = "") -> bool:
    body = (
        "ربات TeseRobot به حساب کارگزاری وارد نشده است.\n\n"
        f"زمان: {now_tehran():%Y-%m-%d %H:%M:%S} (تهران)\n"
        f"آدرس فعلی تب: {session_url or 'unknown'}\n\n"
        "تا زمانی که ورود انجام نشود، هیچ سفارشی ثبت نمی‌شود.\n"
        "لطفاً Chrome ربات را باز کنید و وارد سامانه شوید.\n"
    )
    return send("TeseRobot: ورود به کارگزاری لازم است", body, key="login_required")


def alert_order_unverified(symbol: str, side: str, qty: int, price: float,
                           detail: str = "") -> bool:
    body = (
        f"سفارش ثبت شد ولی در لیست سفارشات کارگزار پیدا نشد.\n\n"
        f"نماد: {symbol}\nسمت: {side}\nتعداد: {qty:,}\nقیمت: {price:,.0f} ریال\n"
        f"زمان: {now_tehran():%Y-%m-%d %H:%M:%S}\n\n"
        "ربات عمداً دوباره ارسال نمی‌کند تا سفارش تکراری ایجاد نشود.\n"
        "لطفاً «گزارش سفارشات» را دستی بررسی کنید.\n"
        f"{detail}\n"
    )
    return send(f"TeseRobot: سفارش تأییدنشده {symbol}", body,
                key=f"unverified:{symbol}:{now_tehran():%Y%m%d}", force=True)


def alert_reconcile(diffs: Dict) -> bool:
    body = "مغایرت بین دفتر حسابداری و کارگزار:\n\n" + json.dumps(diffs, ensure_ascii=False, indent=2)
    return send("TeseRobot: مغایرت حساب", body, key="reconcile")


def alert_text(subject: str, text: str, key: Optional[str] = None) -> bool:
    return send(subject, text, key=key)


def send_test() -> bool:
    return send("TeseRobot: تست ایمیل",
                f"اتصال ایمیل درست کار می‌کند.\nزمان: {now_tehran():%Y-%m-%d %H:%M:%S}",
                key="test", force=True)


def alert_nightly_summary(rows, session_label: str = "", stats: dict | None = None) -> bool:
    """Email after the nightly data refresh: download status + next-session candidates."""
    stats = stats or {}
    lines = []
    lines.append("دانلود دادهٔ شبانه با موفقیت و به‌طور کامل انجام شد.")
    lines.append("")
    lines.append(f"تاریخ اجرا: {now_tehran().strftime('%Y-%m-%d %H:%M')} (تهران)")
    if stats.get("last_date"):
        lines.append(f"آخرین جلسهٔ موجود در داده: {stats['last_date']}")
    if stats.get("symbols"):
        lines.append(f"نمادهای به‌روزشده: {stats['symbols']}")
    if session_label:
        lines.append(f"جلسهٔ هدف خرید: {session_label}")
    lines.append("")
    if rows:
        lines.append("کاندیداهای خرید جلسهٔ بعد:")
        lines.append("")
        lines.append(f"{'#':<3}{'نماد':<10}{'احتمال':>8}{'فاصله تا سقف':>14}{'قیمت بسته':>12}{'سقف بعد':>12}")
        for i, r in enumerate(rows, 1):
            lines.append(f"{i:<3}{r.get('symbol',''):<10}{float(r.get('prob') or 0):>8.3f}"
                         f"{float(r.get('dist_pct') or 0):>13.2f}%"
                         f"{float(r.get('close') or 0):>12,.0f}"
                         f"{float(r.get('next_ceiling') or 0):>12,.0f}")
        lines.append("")
        lines.append("سفارش‌گذاری خودکار: ۰۸:۴۵ (سرخطی) و ۰۹:۰۰ تا ۰۹:۰۵ هر ۳۰ ثانیه.")
    else:
        lines.append("هیچ کاندیدای واجد شرایطی برای جلسهٔ بعد پیدا نشد.")
    body = "\n".join(lines)
    return send(f"[TeseRobot] دانلود شبانه کامل شد — {session_label or 'کاندیدهای فردا'}",
                body, key=f"nightly:{session_label}", force=True)


def alert_order(symbol: str, side: str, qty: int, price: float, status: str,
                detail: str = "", extra: dict | None = None) -> bool:
    """Email for EVERY order ticket the bot sends (placed or not)."""
    side_fa = "خرید" if side == "buy" else "فروش"
    extra = extra or {}
    lines = [
        f"سفارش {side_fa}: {symbol}",
        f"زمان: {now_tehran().strftime('%Y-%m-%d %H:%M:%S')} (تهران)",
        "",
        f"نماد       : {symbol}",
        f"نوع        : {side_fa}",
        f"تعداد      : {qty:,}",
        f"قیمت       : {price:,.0f} ریال",
        f"ارزش       : {qty * price:,.0f} ریال",
        f"وضعیت      : {status}",
    ]
    if detail:
        lines.append(f"پیام پنل   : {detail}")
    for k, v in extra.items():
        lines.append(f"{k:10s} : {v}")
    return send(f"[TeseRobot] {side_fa} {symbol} — {status}",
                "\n".join(lines), key=f"order:{symbol}:{side}:{qty}:{price}", force=True)

"""Morning dress rehearsal — proves the 08:45:01 path works, without sending orders.

Runs the exact chain the bot uses in the morning and reports PASS/FAIL per step:

  1  pw_reset            free the Chrome profile
  2  broker login        check2.js -> AccountBalance
  3  prices/balance      nightly_prices.py
  4  candidates          today's list is non-empty and priced
  5  order form           flow.js with FLOW_DRY=1: bind symbol, type qty+price,
                          read the fields back, verify them — no click, no order
  6  sell form            same for a sell ticket
  7  open orders          read them back (must not crash)
  8  plan dry-run         order_candidates --dry

Exit code 0 only when every step passes. Writes state/rehearsal.json and (when
run by the scheduler at 08:36) emails the result so any breakage shows up BEFORE
the pre-open window, not after.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "src"))
sys.path.insert(0, str(BASE.parent / "Tse2" / "orderflow"))
sys.path.insert(0, str(BASE.parent / "TeseRobot" / "src"))
sys.path.insert(0, str(BASE.parent / "TeseRobot"))

PW = BASE / "pw"
NODE = r"C:\Program Files\nodejs\node.exe"
PY = sys.executable
OUT = BASE / "state" / "rehearsal.json"


def run(cmd: list[str], timeout: int = 900, cwd: Path | None = None,
        env: dict | None = None) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, cwd=str(cwd or BASE), capture_output=True, text=True,
                           timeout=timeout, encoding="utf-8", errors="replace",
                           env=env)
        return p.returncode, ((p.stdout or "") + (p.stderr or ""))
    except Exception as exc:
        return 1, str(exc)


def last_json(text: str) -> dict:
    for line in reversed((text or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except Exception:
                continue
    return {}


def main() -> int:
    rows: list[dict] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        rows.append({"step": name, "ok": bool(ok), "detail": detail[:300]})
        print(("PASS  " if ok else "FAIL  ") + name + (f"  [{detail[:120]}]" if detail else ""))

    # 1 free the profile
    rc, out = run([PY, str(BASE / "pw_reset.py")])
    add("۱ پاکسازی پروفایل", rc == 0, last_json(out) or out[-120:])

    # 2 broker login + balance
    rc, out = run([NODE, str(PW / "balance.js")], cwd=PW)
    bal = last_json(out)
    add("۲ ورود کارگزار و موجودی", rc == 0 and bool(bal.get("account_balance")),
        f"balance={bal.get('account_balance')}")

    # 3 fresh prices snapshot
    rc, out = run([PY, str(BASE / "nightly_prices.py")])
    snap = last_json(out)
    add("۳ اسنپ‌شات قیمت و موجودی", rc == 0 and bool((snap.get("balance") or {}).get("account_balance")),
        f"symbols={snap.get('symbols')}")

    # 4 candidates
    try:
        cand_file = BASE / "state" / "candidates.csv"
        n_cand = max(0, len(cand_file.read_text(encoding="utf-8-sig").splitlines()) - 1)
    except Exception:
        n_cand = 0
    add("۴ لیست کاندیداها", n_cand > 0, f"{n_cand} candidates")

    # 5 buy ticket (dry): bind + fill + verify, no click
    symbol, isin, price, qty = "", "", "0", "0"
    try:
        import order_candidates as oc
        plan = oc.main(dry=True) if False else None
    except Exception:
        plan = None
    try:
        rc, out = run([PY, str(BASE / "order_candidates.py"), "--dry"])
        d = last_json(out)
        orders = d.get("orders") or []
        if orders:
            o = orders[0]
            symbol, isin = str(o.get("symbol") or ""), str(o.get("isin") or "")
            price, qty = str(int(o.get("price") or 0)), str(int(o.get("qty") or 0))
    except Exception:
        pass
    if not (symbol and int(qty or 0) > 0):
        # fallback candidate: prove the form path even if today's plan is empty
        try:
            import csv as _csv
            with (BASE / "state" / "candidates.csv").open(encoding="utf-8-sig") as fh:
                first = next(iter(_csv.DictReader(fh)), None)
            if first:
                symbol = str(first.get("symbol") or "")
                isin = str(first.get("isin") or "")
                price = str(int(float(first.get("close") or 0)))
                qty = "100"
        except Exception:
            pass
    if symbol and int(qty or 0) > 0:
        env = dict(os.environ, FLOW_DRY="1")
        rc, out = run([NODE, str(PW / "flow.js"), symbol, isin, qty, price],
                      cwd=PW, env=env, timeout=900)
        d = last_json(out)
        ok = (rc == 0 and d.get("price_ok")
              and str(d.get("intended_price")) == price
              and ("dry-run" in str(d.get("message") or "")))
        add("۵ فرم خرید (بدون ارسال)", ok,
            f"{symbol} qty={qty} price={price} field_ok={d.get('price_ok')}")
        buy_ref = {"symbol": symbol, "isin": isin, "qty": qty, "price": price}
    else:
        add("۵ فرم خرید (بدون ارسال)", False, "no planned buy order to rehearse")
        buy_ref = {}

    # 6 sell ticket (dry) on the first holding
    sell_ref = {}
    try:
        import json as _j
        pnl = _j.loads((BASE / "state" / "pnl.json").read_text(encoding="utf-8"))
        pos = [p for p in pnl.get("positions", []) if p.get("symbol") != "افران"]
        if pos:
            p0 = pos[0]
            sym = str(p0["symbol"])
            try:
                import tsetmc as api
                isin = str((api.resolve(sym) or {}).get("isin") or "")
            except Exception:
                isin = ""
            px = int(float(p0.get("price") or 0))
            env = dict(os.environ, FLOW_DRY="1")
            rc, out = run([NODE, str(PW / "flow.js"), sym, isin, "100", str(px)],
                          cwd=PW, env=env, timeout=900)
            d = last_json(out)
            ok = (rc == 0 and d.get("price_ok")
                  and "dry-run" in str(d.get("message") or ""))
            add("۶ فرم فروش (بدون ارسال)", ok, f"{sym} price={px} field_ok={d.get('price_ok')}")
            sell_ref = {"symbol": sym, "price": px}
        else:
            add("۶ فرم فروش (بدون ارسال)", True, "no holding to rehearse")
    except Exception as exc:
        add("۶ فرم فروش (بدون ارسال)", False, str(exc)[:120])

    # 7 open orders readable
    rc, out = run([NODE, str(PW / "open_orders.js")], cwd=PW)
    add("۷ خواندن سفارش‌های باز", rc == 0 and ('"orders"' in out), out.strip()[:120])

    # 8 plan dry-run
    rc, out = run([PY, str(BASE / "order_candidates.py"), "--dry"])
    d = last_json(out)
    add("۸ برنامهٔ خرید (dry)", rc == 0, f"free={d.get('free_balance')} target={d.get('target_per_symbol')}")

    failed = [r for r in rows if not r["ok"]]
    payload = {"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
               "passed": len(rows) - len(failed), "total": len(rows),
               "failed": [r["step"] for r in failed],
               "buy_reference": buy_ref, "sell_reference": sell_ref,
               "steps": rows}
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    if os.environ.get("REHEARSAL_EMAIL") == "1":
        try:
            import tsebot.alerts as alerts
            icon = "✅" if not failed else "⚠️"
            body = "\n".join(("✔ " if r["ok"] else "✘ ") + r["step"] + " " + r["detail"]
                             for r in rows)
            alerts.send(f"{icon} [WarRobot] تمرین پیش از بازار — {payload['passed']}/{payload['total']}",
                        body)
        except Exception:
            pass

    print(json.dumps({"passed": payload["passed"], "total": payload["total"],
                      "failed": payload["failed"]}, ensure_ascii=False))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())

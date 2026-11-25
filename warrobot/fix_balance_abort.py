"""Fix the silent "free balance = 0" abort that blocked orders again (1405/08/25).

Why it kept happening:
  order_candidates._free_balance() ran one Playwright login (check2.js). Under
  lock contention with the 3-minute pnl/exit jobs it sometimes returned nothing,
  the function turned that into 0, and the job exited with
  "free balance too low (0)" — no order, no visible error.

Fix:
  1) retry the live read twice
  2) fall back to the balance saved by the morning/nightly snapshot
     (state/prices.json -> balance.account_balance)
  3) if both fail, append a loud record to orders_log and keep going to the
     email path (preflight already warns), never a silent zero
"""
from pathlib import Path

P = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot\order_candidates.py")
s = P.read_text(encoding="utf-8")

start = s.find("def _free_balance() -> float:")
end = s.find("def main(", start)
if start == -1 or end == -1:
    raise SystemExit("pattern not found")

new = '''def _snapshot_balance() -> float:
    """Balance written by the morning/nightly snapshot (state/prices.json)."""
    try:
        import json as _json
        from pathlib import Path as _P
        d = _json.loads((_P(__file__).resolve().parent / "state" / "prices.json"
                         ).read_text(encoding="utf-8"))
        return float((d.get("balance") or {}).get("account_balance") or 0)
    except Exception:
        return 0.0


def _free_balance() -> float:
    """AccountBalance: live broker read (2 tries), then the snapshot fallback."""
    import time as _time
    for attempt in range(2):
        try:
            p = subprocess.run([NODE, str(PW / "check2.js")], cwd=str(PW),
                               capture_output=True, text=True, timeout=420,
                               encoding="utf-8", errors="replace")
            for line in reversed((p.stdout or "").splitlines()):
                line = line.strip()
                if not line.startswith("{"):
                    continue
                d = json.loads(line)
                acct = json.loads(d.get("acct") or "{}").get("Data") or {}
                bal = float(acct.get("AccountBalance") or acct.get("RealBalance") or 0)
                if bal > 0:
                    return bal
                break
        except Exception:
            pass
        if attempt == 0:
            _time.sleep(8)
    snap = _snapshot_balance()
    if snap > 0:
        try:
            with LOG.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                     "symbol": None, "source": "snapshot_balance",
                                     "free_balance": snap, "submitted": False}) + "\\n")
        except Exception:
            pass
    return snap


'''

P.write_text(s[:start] + new + s[end:], encoding="utf-8")
print("free-balance reader now retries and falls back to the snapshot")

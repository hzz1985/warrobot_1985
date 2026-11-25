"""List the broker's open orders, robustly (orderid / side / qty / price)."""
import json
import re
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")
sys.path.insert(0, str(BASE / "src"))
sys.path.insert(0, str(BASE))

import broker_pw  # noqa: E402


def raw_orders() -> list[dict]:
    """Pull GetOpenOrder straight from the page and keep the whole objects."""
    import subprocess
    p = subprocess.run([r"C:\Program Files\nodejs\node.exe",
                        str(BASE / "pw" / "open_orders.js")],
                       cwd=str(BASE / "pw"), capture_output=True, text=True,
                       timeout=600, encoding="utf-8", errors="replace")
    for line in reversed((p.stdout or "").splitlines()):
        if line.strip().startswith("{"):
            try:
                return json.loads(line).get("orders") or []
            except Exception:
                continue
    return []


def main() -> int:
    orders = raw_orders()
    print(json.dumps({"count": len(orders), "orders": orders}, ensure_ascii=False)[:3000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

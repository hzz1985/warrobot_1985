"""Patch webapp/app.py: per-symbol P&L from invested->proceeds, Jalali dates, toman axis."""
from pathlib import Path

P = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\TeseRobot\webapp\app.py")
src = P.read_text(encoding="utf-8")


def replace_func(src: str, name: str, new: str) -> str:
    marker = f"def {name}("
    i = src.index(marker)
    j = src.index("\ndef ", i + 1)
    return src[:i] + new.strip("\n") + "\n\n" + src[j + 1:]


NEW_SERIES = r'''
def to_jalali(ts: str) -> str:
    """'2026-10-13 12:00' -> '۱۴۰۵/۰۷/۲۱' (Gregorian dates in the ledger are UTC+3:30)."""
    try:
        import jdatetime
        d = str(ts)[:10]
        y, m, dd = (int(x) for x in d.split("-"))
        j = jdatetime.date.fromgregorian(year=y, month=m, day=dd)
        return f"{j.year:04d}/{j.month:02d}/{j.day:02d}"
    except Exception:
        return str(ts)[:10]


def per_symbol_series(limit: int = 3) -> List[Dict[str, Any]]:
    """One row per SOLD SYMBOL: invested rial -> proceeds rial -> realised rial P&L.

    P&L for a symbol is literally (money received) - (money paid) for the quantity
    that was sold, so a 20M purchase sold for 22M shows +2M on the curve.
    """
    led = _ledger()
    lots: Dict[str, List[Dict[str, float]]] = {}
    rows: Dict[str, Dict[str, Any]] = {}
    for ev in sorted(led.events(), key=lambda e: str(e.get("ts") or "")):
        if ev.get("kind") != "fill" or not ev.get("side"):
            continue
        sym = ev.get("symbol")
        q = float(ev.get("qty") or 0)
        px = float(ev.get("price") or 0)
        fee = float(ev.get("fee") or 0)
        if ev.get("side") == "buy":
            lots.setdefault(sym, []).append({"q": q, "unit": px + (fee / q if q else 0.0)})
        elif ev.get("side") == "sell" and lots.get(sym):
            remaining = q
            invested = 0.0
            while remaining > 0 and lots[sym]:
                lot = lots[sym][0]
                take = min(remaining, lot["q"])
                invested += take * lot["unit"]
                lot["q"] -= take
                remaining -= take
                if lot["q"] <= 1e-9:
                    lots[sym].pop(0)
            sold = q - remaining
            if sold <= 0:
                continue
            proceeds = sold * px - fee
            r = rows.setdefault(sym, {"symbol": sym, "qty": 0.0, "invested": 0.0,
                                      "proceeds": 0.0, "ts": "", "avg": 0.0, "price": 0.0})
            r["qty"] += sold
            r["invested"] += invested
            r["proceeds"] += proceeds
            r["ts"] = str(ev.get("ts") or "")[:16].replace("T", " ")
            r["avg"] = r["invested"] / r["qty"] if r["qty"] else 0.0
            r["price"] = px
    out = []
    for r in rows.values():
        if r["qty"] <= 0:
            continue
        pnl = r["proceeds"] - r["invested"]
        out.append({"symbol": r["symbol"], "ts": r["ts"], "ts_fa": to_jalali(r["ts"]),
                    "qty": int(r["qty"]), "invested": round(r["invested"], 0),
                    "proceeds": round(r["proceeds"], 0),
                    "avg": round(r["invested"] / r["qty"], 1),
                    "price": round(r["proceeds"] / r["qty"], 1),
                    "pnl_pct": round((r["proceeds"] / r["invested"] - 1) * 100, 2) if r["invested"] else 0.0,
                    "pnl_rial": round(pnl, 0)})
    out.sort(key=lambda r: r["ts"])
    return out[-limit:]
'''

NEW_CHART = r'''
def _toman(v: float) -> str:
    """Rial -> compact toman label (1 toman = 10 rial)."""
    t = v / 10.0
    a = abs(t)
    if a >= 1e9:
        return f"{t/1e9:,.2f}B"
    if a >= 1e6:
        return f"{t/1e6:,.2f}M"
    if a >= 1e3:
        return f"{t/1e3:,.0f}K"
    return f"{t:,.0f}"


def _step_for(limit: float, n: int = 4) -> float:
    raw = max(limit, 1.0) / n
    mag = 10 ** np.floor(np.log10(raw)) if raw > 0 else 1.0
    for m in (1, 2, 2.5, 5, 10):
        if m * mag >= raw:
            return m * mag
    return 10 * mag


def svg_chart(points: List[Dict[str, Any]], width: int = 920, height: int = 330) -> str:
    """Standard P&L chart in TOMAN: zero in the middle, profit above, loss below.

    The curve is the running sum of (proceeds - invested) of each closed symbol.
    """
    if not points:
        return ("<div class='empty'>هنوز فروشی ثبت نشده است؛ نمودار پس از اولین فروش رسم می‌شود.</div>")
    pad_l, pad_r, pad_t, pad_b = 92, 22, 30, 76
    cum = [0.0]
    for p in points:
        cum.append(cum[-1] + float(p.get("pnl_rial") or 0.0))
    peak = max(abs(v) for v in cum) or 1.0
    step = _step_for(peak, 4)
    limit = step * 4
    n = len(cum)
    w = width - pad_l - pad_r
    h = height - pad_t - pad_b

    def x(i: int) -> float:
        return pad_l + (w * i / max(1, n - 1))

    def y(v: float) -> float:
        return pad_t + h * (1 - (v + limit) / (2 * limit))

    parts = [f"<svg viewBox='0 0 {width} {height}' class='chart' role='img'>"]
    v = -limit
    while v <= limit + 1e-6:
        yy = y(v)
        cls = "zero" if abs(v) < 1e-9 else "grid"
        parts.append(f"<line x1='{pad_l}' y1='{yy:.1f}' x2='{width-pad_r}' y2='{yy:.1f}' class='{cls}'/>")
        parts.append(f"<text x='{pad_l-8}' y='{yy+4:.1f}' class='axis' text-anchor='end'>{_toman(v)}</text>")
        v += step
    parts.append(f"<text x='{width-pad_r}' y='{y(limit*0.72):.1f}' class='zone' text-anchor='end'>سود</text>")
    parts.append(f"<text x='{width-pad_r}' y='{y(-limit*0.72):.1f}' class='zone' text-anchor='end'>ضرر</text>")
    pts = " ".join(f"{x(i):.1f},{y(val):.1f}" for i, val in enumerate(cum))
    parts.append(f"<polyline points='{pts}' class='line'/>")
    for i, p in enumerate(points):
        val = cum[i + 1]
        cx, cy = x(i + 1), y(val)
        color = "#1faa4b" if val >= 0 else "#e53935"
        parts.append(f"<circle cx='{cx:.1f}' cy='{cy:.1f}' r='5' fill='{color}'/>")
        above = val >= 0
        ly = cy - 10 if above else cy + 18
        parts.append(f"<text x='{cx:.1f}' y='{ly:.1f}' class='point-label' text-anchor='middle'>{p['symbol']}</text>")
        vy = ly - 13 if above else ly + 13
        parts.append(f"<text x='{cx:.1f}' y='{vy:.1f}' class='point-value' text-anchor='middle'>{_toman(val)}</text>")
        parts.append(
            f"<title>{p['symbol']} | خرید {_toman(p['invested'])} · فروش {_toman(p['proceeds'])} تومان "
            f"| سود {_toman(float(p.get('pnl_rial') or 0))} تومان | جمع {_toman(val)} تومان "
            f"| تاریخ {p.get('ts_fa', p['ts'][:10])}</title>")
    parts.append(f"<text x='{x(0):.1f}' y='{height-40}' class='point-label' text-anchor='middle'>شروع</text>")
    for i, p in enumerate(points, start=1):
        parts.append(f"<text x='{x(i):.1f}' y='{height-40}' class='point-label' text-anchor='middle'>{p['symbol']}</text>")
        parts.append(f"<text x='{x(i):.1f}' y='{height-24}' class='axis' text-anchor='middle'>{p.get('ts_fa', p['ts'][:10])}</text>")
    total = cum[-1]
    parts.append(f"<text x='{width-pad_r}' y='{pad_t-10}' class='axis' text-anchor='end'>جمع: {_toman(total)} تومان</text>")
    parts.append(f"<text x='{pad_l}' y='{height-8}' class='axis' text-anchor='start'>محور افقی: تاریخ فروش (شمسی) · محور عمودی: تغییر دارایی به تومان · خط صفر وسط</text>")
    parts.append("</svg>")
    return "".join(parts)


'''

src = replace_func(src, "per_symbol_series", NEW_SERIES)
src = replace_func(src, "svg_chart", NEW_CHART)
P.write_text(src, encoding="utf-8")
print("patched:", "to_jalali" in src, "_toman" in src)

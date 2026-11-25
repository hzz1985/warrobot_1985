"""Replace the chart with an EQUITY (buying power + locked money) curve.

equity(t) = cash(t) + cost of open positions
cash(t)   = C0 - sum(buy costs) + sum(sell proceeds)   [replayed in time order]

C0 (initial capital) comes from config: webapp.initial_capital_rial, default 4e9.
"""
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
    try:
        import jdatetime
        d = str(ts)[:10]
        y, m, dd = (int(x) for x in d.split("-"))
        j = jdatetime.date.fromgregorian(year=y, month=m, day=dd)
        return f"{j.year:04d}/{j.month:02d}/{j.day:02d}"
    except Exception:
        return str(ts)[:10]


def initial_capital() -> float:
    try:
        v = float(CFG.get("webapp.initial_capital_rial", 4_000_000_000))
        return v if v > 0 else 4_000_000_000.0
    except Exception:
        return 4_000_000_000.0


def equity_series() -> List[Dict[str, Any]]:
    """Buying power and locked capital after every closed symbol.

    cash   = C0 - sum(buy) + sum(sell proceeds)
    locked = cost of the positions still open at that moment
    equity = cash + locked
    """
    led = _ledger()
    cash = initial_capital()
    pos: Dict[str, Dict[str, float]] = {}
    out: List[Dict[str, Any]] = []
    for ev in sorted(led.events(), key=lambda e: str(e.get("ts") or "")):
        if ev.get("kind") != "fill" or not ev.get("side"):
            continue
        sym = ev.get("symbol")
        q = float(ev.get("qty") or 0)
        px = float(ev.get("price") or 0)
        fee = float(ev.get("fee") or 0)
        if ev.get("side") == "buy":
            cash -= q * px + fee
            p = pos.setdefault(sym, {"q": 0.0, "cost": 0.0})
            p["q"] += q
            p["cost"] += q * px + fee
        elif ev.get("side") == "sell":
            p = pos.get(sym)
            if not p or p["q"] <= 0:
                continue
            sold = min(q, p["q"])
            unit = p["cost"] / p["q"]
            cost_part = unit * sold
            proceeds = sold * px - fee * (sold / q if q else 1.0)
            cash += proceeds
            p["q"] -= sold
            p["cost"] -= cost_part
            if p["q"] <= 1e-9:
                pos.pop(sym, None)
            locked = sum(v["cost"] for v in pos.values())
            out.append({
                "symbol": sym, "ts": str(ev.get("ts") or "")[:16].replace("T", " "),
                "ts_fa": to_jalali(ev.get("ts") or ""),
                "qty": int(sold),
                "invested": round(cost_part, 0),
                "proceeds": round(proceeds, 0),
                "pnl_rial": round(proceeds - cost_part, 0),
                "cash": round(cash, 0), "locked": round(locked, 0),
                "value": round(cash + locked, 0),
            })
    return out
'''

NEW_CHART = r'''
def _toman(v: float) -> str:
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
    """Equity curve in TOMAN: cash + locked capital after each closed symbol."""
    if not points:
        return ("<div class='empty'>هنوز فروشی ثبت نشده است؛ نمودار پس از اولین فروش رسم می‌شود.</div>")
    pad_l, pad_r, pad_t, pad_b = 96, 22, 34, 78
    vals = [float(p["value"]) for p in points]
    c0 = initial_capital()
    lo = min(min(vals), c0)
    hi = max(max(vals), c0)
    span = max(hi - lo, 1.0)
    step = _step_for(span / 2 * 2, 4)
    lo = np.floor((lo - span * 0.05) / step) * step
    hi = np.ceil((hi + span * 0.05) / step) * step
    if hi - lo < step * 2:
        hi = lo + step * 2
    n = len(vals) + 1
    w = width - pad_l - pad_r
    h = height - pad_t - pad_b

    def x(i: int) -> float:
        return pad_l + (w * i / max(1, n - 1))

    def y(v: float) -> float:
        return pad_t + h * (1 - (v - lo) / (hi - lo))

    parts = [f"<svg viewBox='0 0 {width} {height}' class='chart' role='img'>"]
    v = lo
    while v <= hi + 1e-6:
        yy = y(v)
        parts.append(f"<line x1='{pad_l}' y1='{yy:.1f}' x2='{width-pad_r}' y2='{yy:.1f}' class='grid'/>")
        parts.append(f"<text x='{pad_l-8}' y='{yy+4:.1f}' class='axis' text-anchor='end'>{_toman(v)}</text>")
        v += step
    # starting equity
    pts = [f"{x(0):.1f},{y(c0):.1f}"] + [f"{x(i+1):.1f},{y(v2):.1f}" for i, v2 in enumerate(vals)]
    parts.append(f"<polyline points='{' '.join(pts)}' class='line'/>")
    parts.append(f"<circle cx='{x(0):.1f}' cy='{y(c0):.1f}' r='5' fill='#8b98a5'/>")
    for i, p in enumerate(points):
        val = float(p["value"])
        cx, cy = x(i + 1), y(val)
        color = "#1faa4b" if val >= c0 else "#e53935"
        parts.append(f"<circle cx='{cx:.1f}' cy='{cy:.1f}' r='5' fill='{color}'/>")
        parts.append(f"<text x='{cx:.1f}' y='{cy-12:.1f}' class='point-label' text-anchor='middle'>{p['symbol']}</text>")
        parts.append(f"<text x='{cx:.1f}' y='{cy+18:.1f}' class='point-value' text-anchor='middle'>{_toman(val)}</text>")
        parts.append(
            f"<title>{p['symbol']} | خرید {_toman(p['invested'])} · فروش {_toman(p['proceeds'])} تومان "
            f"| سود {_toman(p['pnl_rial'])} تومان | قدرت خرید پس از فروش {_toman(p['cash'])} "
            f"| پول درگیر {_toman(p['locked'])} | جمع دارایی {_toman(val)} تومان "
            f"| {p.get('ts_fa','')}</title>")
    parts.append(f"<text x='{x(0):.1f}' y='{height-42}' class='point-label' text-anchor='middle'>سرمایه اولیه</text>")
    parts.append(f"<text x='{x(0):.1f}' y='{height-26}' class='axis' text-anchor='middle'>{_toman(c0)}</text>")
    for i, p in enumerate(points, start=1):
        parts.append(f"<text x='{x(i):.1f}' y='{height-42}' class='point-label' text-anchor='middle'>{p['symbol']}</text>")
        parts.append(f"<text x='{x(i):.1f}' y='{height-26}' class='axis' text-anchor='middle'>{p.get('ts_fa','')}</text>")
    last = points[-1]
    delta = float(last["value"]) - c0
    parts.append(f"<text x='{width-pad_r}' y='{pad_t-12}' class='axis' text-anchor='end'>"
                 f"دارایی فعلی: {_toman(float(last['value']))} تومان · تغییر: {_toman(delta)}</text>")
    parts.append(f"<text x='{pad_l}' y='{height-8}' class='axis' text-anchor='start'>"
                 f"دارایی = قدرت خرید + پول درگیر · محور افقی: تاریخ فروش (شمسی) · تومان</text>")
    parts.append("</svg>")
    return "".join(parts)


'''

src = replace_func(src, "equity_series", NEW_SERIES) if "def equity_series(" in src else src
src = replace_func(src, "per_symbol_series", NEW_SERIES)
src = replace_func(src, "svg_chart", NEW_CHART)
src = src.replace("chart=svg_chart(per_symbol_series(3))", "chart=svg_chart(equity_series())")
P.write_text(src, encoding="utf-8")
print("ok:", "equity_series" in src, "svg_chart(equity_series())" in src)

"""Rewrite the portfolio chart: readable labels, symbol names, nothing clipped.

Old problems: labels printed twice (above the points and again on the axis), so
they overlapped; long Persian names ran outside the box; and the history points
were labelled with dates only.

New design:
  * fixed padding, everything clamped inside the viewBox
  * one label per point (symbol + value), alternating above/below
  * axis shows the date for at most 8 points, spaced out
  * legend with the running total
"""
from pathlib import Path

APP = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\TeseRobot\webapp\app.py")
s = APP.read_text(encoding="utf-8")

start = s.find("def svg_chart(")
end = s.find("\ndef ", start + 10)
if start == -1 or end == -1:
    raise SystemExit("svg_chart not found")

new_fn = '''def svg_chart(points: List[Dict[str, Any]], width: int = 1000, height: int = 400) -> str:
    """Cumulative portfolio P&L with readable, non-overlapping labels."""
    if not points:
        return "<div class='empty'>هنوز داده‌ای برای رسم منحنی نیست.</div>"

    PAD_L, PAD_R, PAD_T, PAD_B = 118, 34, 46, 96
    w = width - PAD_L - PAD_R
    h = height - PAD_T - PAD_B

    cum = [0.0]
    for p in points:
        cum.append(cum[-1] + float(p.get("pnl_rial") or 0))
    peak = max(abs(v) for v in cum) or 1.0
    step = _step_for(peak, 4)
    limit = step * 4

    def x(i: int) -> float:
        return PAD_L + (w * i / max(1, len(cum) - 1))

    def y(v: float) -> float:
        return PAD_T + h * (1 - (v + limit) / (2 * limit))

    def clamp_x(v: float) -> float:
        return min(max(v, PAD_L + 26), width - PAD_R - 26)

    def clamp_y(v: float) -> float:
        return min(max(v, PAD_T + 14), height - PAD_B - 4)

    out = [f"<svg viewBox='0 0 {width} {height}' class='chart' role='img' "
           f"preserveAspectRatio='xMidYMid meet'>"]

    # grid + money labels
    v = -limit
    while v <= limit + 1e-6:
        yy = y(v)
        cls = "zero" if abs(v) < 1e-9 else "grid"
        out.append(f"<line x1='{PAD_L}' y1='{yy:.1f}' x2='{width-PAD_R}' y2='{yy:.1f}' class='{cls}'/>")
        out.append(f"<text x='{PAD_L-10}' y='{yy+4:.1f}' class='axis' text-anchor='end'>{_toman(v)}</text>")
        v += step

    # profit / loss zones
    out.append(f"<text x='{width-PAD_R}' y='{y(limit*0.78):.1f}' class='zone' text-anchor='end'>سود</text>")
    out.append(f"<text x='{width-PAD_R}' y='{y(-limit*0.78):.1f}' class='zone' text-anchor='end'>ضرر</text>")

    # the curve
    pts = " ".join(f"{x(i):.1f},{y(val):.1f}" for i, val in enumerate(cum))
    out.append(f"<polyline points='{pts}' class='line'/>")

    # points: one label each, alternating sides, clamped inside the box
    labelled = []
    for i, p in enumerate(points):
        val = cum[i + 1]
        cx, cy = x(i + 1), y(val)
        colour = "#14804a" if val >= 0 else "#c62828"
        out.append(f"<circle cx='{cx:.1f}' cy='{cy:.1f}' r='5.5' fill='{colour}'/>")
        above = (i % 2 == 0) if val >= 0 else (i % 2 != 0)
        name = str(p.get("symbol") or "").strip()
        ly = clamp_y(cy - 14 if above else cy + 20)
        lx = clamp_x(cx)
        out.append(f"<text x='{lx:.1f}' y='{ly:.1f}' class='point-label' text-anchor='middle'>{name}</text>")
        out.append(f"<text x='{lx:.1f}' y='{ly + (14 if above else -14):.1f}' class='point-value' "
                   f"text-anchor='middle'>{_toman(float(p.get('pnl_rial') or 0))}</text>")
        out.append(f"<title>{name} | خرید {_toman(p.get('invested'))} · فروش {_toman(p.get('proceeds'))} "
                   f"| سود {_toman(float(p.get('pnl_rial') or 0))} | جمع {_toman(val)} | {p.get('ts_fa', '')}</title>")
        labelled.append((i, cx, str(p.get("ts_fa") or "")))

    # x axis: dates, thinned out so they never collide
    every = max(1, (len(labelled) + 7) // 8)
    for i, cx, date in labelled:
        if i % every:
            continue
        out.append(f"<text x='{clamp_x(cx):.1f}' y='{height-PAD_B+20:.0f}' class='axis' "
                   f"text-anchor='middle'>{date}</text>")

    # legend
    total = cum[-1]
    out.append(f"<text x='{PAD_L}' y='{PAD_T-16:.0f}' class='legend-main'>جمع سود/زیان: {_toman(total)} تومان</text>")
    out.append(f"<text x='{PAD_L}' y='{height-8:.0f}' class='axis'>محور افقی: تاریخ · محور عمودی: سود/زیان تجمعی (تومان) · خط‌چین: صفر</text>")
    out.append("</svg>")
    return "".join(out)
'''

APP.write_text(s[:start] + new_fn + s[end:], encoding="utf-8")
print("svg_chart rewritten")

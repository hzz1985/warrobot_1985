"""Chart polish: no duplicated axis labels, readable value offsets, short caption."""
from pathlib import Path

APP = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\TeseRobot\webapp\app.py")
s = APP.read_text(encoding="utf-8")

# 1) value labels further from the dot so they never cover it
s = s.replace("        ly = clamp_y(cy - 14 if above else cy + 20)",
              "        ly = clamp_y(cy - 24 if above else cy + 30)")
s = s.replace("        out.append(f\"<text x='{lx:.1f}' y='{ly + (14 if above else -14):.1f}' class='point-value' \"",
              "        out.append(f\"<text x='{lx:.1f}' y='{ly + (15 if above else -15):.1f}' class='point-value' \"")
# 2) axis labels: skip when they would just repeat the point label
s = s.replace('''    # x axis: dates, thinned out so they never collide
    every = max(1, (len(labelled) + 7) // 8)
    for i, cx, date in labelled:
        if i % every:
            continue
        out.append(f"<text x='{clamp_x(cx):.1f}' y='{height-PAD_B+20:.0f}' class='axis' "
                   f"text-anchor='middle'>{date}</text>")''',
'''    # x axis: only when the caption is not already the point name
    names = {str(p.get("symbol") or "") for p in points}
    dates = {str(p.get("ts_fa") or "") for p in points}
    show_axis = not dates.issubset(names)          # true only for a real time series
    every = max(1, (len(labelled) + 7) // 8)
    for i, cx, date in labelled:
        if not show_axis or i % every:
            continue
        out.append(f"<text x='{clamp_x(cx):.1f}' y='{height-PAD_B+20:.0f}' class='axis' "
                   f"text-anchor='middle'>{date}</text>")''')
# 3) short caption anchored at the right edge
s = s.replace('''    out.append(f"<text x='{PAD_L}' y='{height-8:.0f}' class='axis'>محور افقی: تاریخ · محور عمودی: سود/زیان تجمعی (تومان) · خط‌چین: صفر</text>")''',
'''    out.append(f"<text x='{width-PAD_R}' y='{height-10:.0f}' class='axis' text-anchor='end'>"
               f"محور عمودی: سود/زیان تجمعی (تومان) · خط‌چین: صفر</text>")''')
APP.write_text(s, encoding="utf-8")
print("chart spacing/axis/caption fixed")

# 4) realised points: label with the symbol (consistent with the rest)
W = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\TeseRobot\webapp\warrobot_data.py")
w = W.read_text(encoding="utf-8")
w = w.replace('''                pts.append({"symbol": d.get("symbol", ""),
                            "ts_fa": str(d.get("at", ""))[:10],''',
'''                pts.append({"symbol": d.get("symbol", ""),
                            "ts_fa": d.get("symbol", ""),''')
W.write_text(w, encoding="utf-8")
print("realised points labelled by symbol")

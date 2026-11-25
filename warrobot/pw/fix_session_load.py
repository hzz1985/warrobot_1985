from pathlib import Path
import re

for f in ("broker_client.js", "advanced_portfolio_values.js", "open_orders.js", "balance.js"):
    p = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot\pw") / f
    s = p.read_text(encoding="utf-8")
    # drop a misplaced load line inside the launch options
    s = re.sub(r"(launchPersistentContext\([^\n]*\{\n)\s*await SESSION\.load\(ctx\);\n", r"\1", s)
    # ensure exactly one load right after the launch call closes
    if "await SESSION.load(ctx);" not in s or s.count("await SESSION.load(ctx);") != 1:
        m = re.search(r"(const ctx = await chromium\.launchPersistentContext\([^;]*?\);\n)", s, re.S)
        if m:
            s = s[: m.end()] + "  await SESSION.load(ctx);\n" + s[m.end():]
    p.write_text(s, encoding="utf-8")
    print(f, "load ok:", s.count("await SESSION.load(ctx);"))

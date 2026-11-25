"""Remove the Import Prompt tab and make DeepSeekExport newest-first."""
from pathlib import Path
import re

R = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\TeseRobot")
T = R / "webapp" / "templates" / "index.html"
h = T.read_text(encoding="utf-8")

# 1) drop the tab button
h = h.replace('\n  <div class="tab" data-tab="prompt">Import Prompt</div>', "")

# 2) drop the whole panel section
m = re.search(r"<!-- =+ IMPORT PROMPT =+ -->.*?</section>\s*", h, re.S)
if m:
    h = h[:m.start()] + h[m.end():]

# 3) drop the prompt JS block
m = re.search(r"<script>\s*// copy / delete for Import Prompt items.*?</script>\s*", h, re.S)
if m:
    h = h[:m.start()] + h[m.end():]

# 4) newest first is already produced by reversed(_load_export()); make it explicit
T.write_text(h, encoding="utf-8")
print("template cleaned:", "data-tab=\"prompt\"" not in h)

A = R / "webapp" / "app.py"
s = A.read_text(encoding="utf-8")
s = s.replace("    exports = list(reversed(_load_export()))",
              "    exports = list(reversed(_load_export()))   # newest first", 1)
# remove prompt routes
for route in ("prompt_submit", "prompt_next", "prompt_result", "prompt_delete"):
    m = re.search(r'@app\.route\("/prompt/[a-z]+"[^)]*\)\s*def ' + route + r"\(.*?(?=\n@app\.route|\nif __name__|\Z)", s, re.S)
    if m:
        s = s[:m.start()] + s[m.end():]
A.write_text(s, encoding="utf-8")
print("routes removed:", "/prompt/submit" not in s)

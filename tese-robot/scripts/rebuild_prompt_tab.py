"""Rebuild the Import Prompt tab as a boxed list: red line + Copy/Delete per command."""
from pathlib import Path

R = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\TeseRobot")

# ---------------------------------------------------------------- backend
A = R / "webapp" / "app.py"
s = A.read_text(encoding="utf-8")
if '@app.route("/prompt/delete"' not in s:
    route = '''
@app.route("/prompt/delete", methods=["POST"])
def prompt_delete():
    data = request.get_json(force=True, silent=True) or {}
    pid = str(data.get("id") or "")
    rows = [r for r in _prompt_rows(2000) if r.get("id") != pid]
    PROMPT_FILE.write_text("".join(json.dumps(r, ensure_ascii=False) + "\\n" for r in rows),
                           encoding="utf-8")
    return jsonify({"ok": True, "count": len(rows)})

'''
    idx = s.index('@app.route("/prompt/submit"')
    s = s[:idx] + route + s[idx:]
    A.write_text(s, encoding="utf-8")
    print("delete route added")

# ---------------------------------------------------------------- template
T = R / "webapp" / "templates" / "index.html"
h = T.read_text(encoding="utf-8")

start_marker = "<!-- ================= IMPORT PROMPT ================= -->"
end_marker = "<!-- ================= DEEPSEEK EXPORT ================= -->"
i = h.index(start_marker)
j = h.index(end_marker)

NEW = '''<!-- ================= IMPORT PROMPT ================= -->
<section class="panel" id="prompt">
  <h2>Import Prompt — ارسال دستور از همین صفحه</h2>
  <p class="note">متن دستور را بنویس: <b>ثبت در صف</b> آن را برای ایجنت ذخیره می‌کند و
     <b>اجرا کن</b> فقط دستورهای مجاز را همان‌جا اجرا می‌کند
     (<code>!status</code> · <code>!scan</code> · <code>!report</code> · <code>!restart-web</code>).</p>

  <div class="prompt-form">
    <input id="prompt-title" type="text" placeholder="عنوان (اختیاری)">
    <textarea id="prompt-text" rows="3" placeholder="متن دستور…"></textarea>
    <div class="prompt-actions">
      <button id="prompt-queue" type="button">ثبت در صف</button>
      <button id="prompt-run" type="button">اجرا کن</button>
      <span id="prompt-status" class="prompt-status"></span>
    </div>
  </div>

  {% if prompts %}
  <div class="prompt-list">
    {% for p in prompts %}
    <article class="prompt-item" data-id="{{ p.id }}">
      <div class="export-sep"></div>
      <div class="export-tools">
        <button class="btn-copy" type="button">📋 Copy</button>
        <button class="btn-del" type="button">🗑 Delete</button>
        <span class="export-meta">{{ p.ts }} · {{ p.title }} · {{ p.mode }} · {{ p.status }}</span>
      </div>
      <pre class="export-body prompt-body" dir="auto">{{ p.text }}</pre>
      {% if p.result %}
      <details class="prompt-result">
        <summary>نتیجهٔ اجرا</summary>
        <pre dir="auto">{{ p.result[:2000] }}</pre>
      </details>
      {% endif %}
    </article>
    {% endfor %}
  </div>
  {% else %}
  <div class="empty">هنوز دستوری ثبت نشده است.</div>
  {% endif %}
</section>

'''

h = h[:i] + NEW + h[j:]

css = '''
  /* ---------------- Import Prompt items ---------------- */
  .prompt-item { margin-bottom: 6px; }
  .prompt-body { background:#121922; }
  .prompt-result { margin-top:8px; }
  .prompt-result summary { cursor:pointer; color:var(--muted); font-size:13px; padding:6px 0; }
  .prompt-result pre { background:#0d1319; border:1px solid var(--line); border-radius:10px;
                       padding:12px; white-space:pre-wrap; max-height:40vh; overflow:auto;
                       font-family:Consolas,monospace; font-size:12.5px; direction:ltr; }
'''
h = h.replace("  /* ---------------- Import Prompt ---------------- */",
              "  /* ---------------- Import Prompt ---------------- */" + css, 1)

js = '''
<script>
  // copy / delete for Import Prompt items
  document.querySelectorAll('.prompt-item').forEach(function (item) {
    var body = item.querySelector('.prompt-body');
    var copy = item.querySelector('.btn-copy');
    var del = item.querySelector('.btn-del');
    if (copy) copy.addEventListener('click', function () {
      var text = body ? body.textContent : '';
      var flash = function () { copy.textContent = '✔ کپی شد'; setTimeout(function(){ copy.textContent = '📋 Copy'; }, 1600); };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(flash).catch(fallback);
      } else { fallback(); }
      function fallback() {
        var ta = document.createElement('textarea'); ta.value = text; document.body.appendChild(ta);
        ta.select(); try { document.execCommand('copy'); } catch (e) {} document.body.removeChild(ta); flash();
      }
    });
    if (del) del.addEventListener('click', function () {
      if (!confirm('این دستور حذف شود؟')) return;
      fetch('/prompt/delete', {method:'POST', headers:{'Content-Type':'application/json'},
                               body: JSON.stringify({id: item.dataset.id})})
        .then(function(r){ return r.json(); })
        .then(function(){ item.style.transition='opacity .25s ease'; item.style.opacity='0';
                          setTimeout(function(){ item.remove(); }, 250); });
    });
  });
</script>
'''
h = h.replace("</body>", js + "\n</body>", 1)
T.write_text(h, encoding="utf-8")
print("template rebuilt")

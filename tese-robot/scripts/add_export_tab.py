"""Add a «DeepSeekExport» tab: every assistant response archived with copy/delete."""
from pathlib import Path

R = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\TeseRobot")

# ---------------------------------------------------------------- backend
A = R / "webapp" / "app.py"
s = A.read_text(encoding="utf-8")

if "deepseek_export" not in s:
    helper = '''
EXPORT_FILE = Path(__file__).resolve().parents[1] / "state" / "deepseek_export.json"


def _load_export() -> List[Dict[str, Any]]:
    try:
        return json.loads(EXPORT_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_export(items: List[Dict[str, Any]]) -> None:
    EXPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    EXPORT_FILE.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")

'''
    anchor = "def to_jalali(ts: str) -> str:"
    s = s.replace(anchor, helper + "\n" + anchor, 1)

    # pass entries to the template
    s = s.replace('''    picks = current_picks()''',
                  '''    picks = current_picks()
    exports = list(reversed(_load_export()))''', 1)
    s = s.replace('picks=picks, positions=positions,',
                  'picks=picks, exports=exports, positions=positions,', 1)

    # routes
    routes = '''
@app.route("/export/add", methods=["POST"])
def export_add():
    data = request.get_json(force=True, silent=True) or {}
    content = str(data.get("content") or "")
    if not content.strip():
        return jsonify({"ok": False, "error": "empty"}), 400
    items = _load_export()
    items.append({"id": uuid.uuid4().hex[:12],
                  "ts": now_tehran().strftime("%Y-%m-%d %H:%M:%S"),
                  "title": str(data.get("title") or "پاسخ"),
                  "content": content})
    _save_export(items)
    return jsonify({"ok": True, "id": items[-1]["id"]})


@app.route("/export/delete", methods=["POST"])
def export_delete():
    data = request.get_json(force=True, silent=True) or {}
    target = str(data.get("id") or "")
    items = [i for i in _load_export() if i.get("id") != target]
    _save_export(items)
    return jsonify({"ok": True, "count": len(items)})


'''
    if '@app.route("/export/add"' not in s:
        idx = s.index('@app.route("/")') if '@app.route("/")' in s else s.index("def index():")
        s = s[:idx] + routes + s[idx:]

    if "import uuid" not in s:
        s = s.replace("import json", "import json\nimport uuid", 1)

    A.write_text(s, encoding="utf-8")
    print("backend added")

# ---------------------------------------------------------------- template
T = R / "webapp" / "templates" / "index.html"
h = T.read_text(encoding="utf-8")

if 'data-tab="export"' not in h:
    h = h.replace('''  <div class="tab" data-tab="sell">کاندیدای فروش</div>''',
                  '''  <div class="tab" data-tab="sell">کاندیدای فروش</div>
  <div class="tab" data-tab="export">DeepSeekExport</div>''', 1)

    panel = '''
<!-- ================= DEEPSEEK EXPORT ================= -->
<section class="panel" id="export">
  <h2>DeepSeekExport — آرشیو پاسخ‌ها</h2>
  <p class="note">هر پاسخ با یک خط قرمز افقی از پاسخ قبلی جدا شده است. با دکمهٔ «کپی» متن کامل
     پاسخ در کلیپ‌بورد قرار می‌گیرد و با «حذف» همان ردیف پاک می‌شود.</p>
  {% if exports %}
  <div class="export-list">
    {% for e in exports %}
    <article class="export-item" data-id="{{ e.id }}">
      <div class="export-sep"></div>
      <div class="export-tools">
        <button class="btn-copy" type="button">📋 Copy</button>
        <button class="btn-del" type="button">🗑 Delete</button>
        <span class="export-meta">{{ e.ts }} · {{ e.title }}</span>
      </div>
      <pre class="export-body" dir="auto">{{ e.content }}</pre>
    </article>
    {% endfor %}
  </div>
  {% else %}
  <div class="empty">هنوز پاسخی آرشیو نشده است.</div>
  {% endif %}
</section>
'''
    h = h.replace("</section>\n\n<script>", "</section>\n" + panel + "\n<script>", 1)

    css = '''
  /* ---------------- DeepSeekExport ---------------- */
  .export-item { margin-bottom: 6px; }
  .export-sep { height: 0; border-top: 2px solid #e53935; margin: 18px 0 10px; }
  .export-tools { display:flex; gap:10px; align-items:center; margin-bottom:8px; }
  .export-tools button { background:#1b2733; color:#e6edf3; border:1px solid var(--line);
                         border-radius:8px; padding:7px 14px; font-size:13px; cursor:pointer;
                         transition:background .2s ease, transform .15s ease; }
  .export-tools button:hover { background:#233142; transform:translateY(-1px); }
  .export-tools .btn-del:hover { background:#3a1515; border-color:#5a2020; }
  .export-meta { color:var(--muted); font-size:12px; margin-inline-start:auto; }
  .export-body { background:var(--card); border:1px solid var(--line); border-radius:12px;
                 padding:14px 16px; white-space:pre-wrap; overflow-x:auto;
                 font-family:Tahoma,"Segoe UI",monospace; font-size:13.5px; line-height:1.9;
                 max-height:65vh; overflow-y:auto; }
'''
    h = h.replace("  /* ---------------- mobile ---------------- */", css + "\n  /* ---------------- mobile ---------------- */", 1)

    js = '''
<script>
  // copy / delete for DeepSeekExport entries
  document.querySelectorAll('.export-item').forEach(function (item) {
    var body = item.querySelector('.export-body');
    var copy = item.querySelector('.btn-copy');
    var del = item.querySelector('.btn-del');
    if (copy) copy.addEventListener('click', function () {
      var text = body ? body.textContent : '';
      var done = function () { copy.textContent = '✔ کپی شد'; setTimeout(function(){ copy.textContent = '📋 Copy'; }, 1600); };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done).catch(function () { fallback(text); done(); });
      } else { fallback(text); done(); }
      function fallback(t) {
        var ta = document.createElement('textarea'); ta.value = t; document.body.appendChild(ta);
        ta.select(); try { document.execCommand('copy'); } catch (e) {} document.body.removeChild(ta);
      }
    });
    if (del) del.addEventListener('click', function () {
      if (!confirm('این پاسخ حذف شود؟')) return;
      fetch('/export/delete', {method:'POST', headers:{'Content-Type':'application/json'},
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
    print("template added")

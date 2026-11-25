"""Add an «Import Prompt» tab: queue commands for the agent and run safe actions.

Two paths:
  * ثبت در صف   → stored in state/prompt_inbox.jsonl; the agent polls /prompt/next
  * اجرا کن     → only whitelisted actions (status / scan / report / restart),
                  and only from localhost
"""
from pathlib import Path

R = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\TeseRobot")

A = R / "webapp" / "app.py"
s = A.read_text(encoding="utf-8")

HELPER = '''
PROMPT_FILE = Path(__file__).resolve().parents[1] / "state" / "prompt_inbox.jsonl"

SAFE_ACTIONS = {
    "status":  ["python", "scripts/readiness_check.py"],
    "scan":    ["python", "-c",
                "import sys; sys.path.insert(0,'src'); from tsebot import config, ride_locked; "
                "cfg=config.load(); ride_locked.write_candidates(cfg); print('scan done')"],
    "report":  ["python", "scripts/daily_report.py"],
    "restart-web": ["pm2", "restart", "tse-web"],
}


def _prompt_rows(limit: int = 50) -> List[Dict[str, Any]]:
    if not PROMPT_FILE.exists():
        return []
    rows = []
    for line in PROMPT_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows[-limit:]


def _prompt_append(rec: Dict[str, Any]) -> None:
    PROMPT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with PROMPT_FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\\n")

'''

if "prompt_inbox" not in s:
    anchor = "EXPORT_FILE = Path(__file__).resolve().parents[1] / \"state\" / \"deepseek_export.json\""
    s = s.replace(anchor, HELPER + anchor, 1)

    # index context
    s = s.replace("    exports = list(reversed(_load_export()))",
                  "    exports = list(reversed(_load_export()))\n    prompts = list(reversed(_prompt_rows(30)))", 1)
    s = s.replace("picks=picks, exports=exports, positions=positions,",
                  "picks=picks, exports=exports, prompts=prompts, positions=positions,", 1)

    routes = '''
@app.route("/prompt/submit", methods=["POST"])
def prompt_submit():
    data = request.get_json(force=True, silent=True) or {}
    text = str(data.get("text") or "").strip()
    if not text:
        return jsonify({"ok": False, "error": "empty"}), 400
    mode = str(data.get("mode") or "queue")
    rec = {"id": uuid.uuid4().hex[:12],
           "ts": now_tehran().strftime("%Y-%m-%d %H:%M:%S"),
           "title": str(data.get("title") or "دستور"),
           "text": text, "mode": mode, "status": "pending", "result": ""}
    if mode == "run":
        action = text.strip().lstrip("!")
        if not request.remote_addr or request.remote_addr not in ("127.0.0.1", "::1"):
            rec["status"] = "rejected"; rec["result"] = "only localhost may execute"
        elif action not in SAFE_ACTIONS:
            rec["status"] = "rejected"; rec["result"] = "action not allowed"
        else:
            try:
                proc = subprocess.run(SAFE_ACTIONS[action], cwd=str(Path(__file__).resolve().parents[1]),
                                      capture_output=True, text=True, timeout=180)
                rec["status"] = "done"
                rec["result"] = ((proc.stdout or "") + (proc.stderr or ""))[-4000:]
            except Exception as exc:
                rec["status"] = "error"
                rec["result"] = str(exc)[:400]
    _prompt_append(rec)
    return jsonify({"ok": True, "id": rec["id"], "status": rec["status"], "result": rec["result"]})


@app.route("/prompt/next", methods=["GET"])
def prompt_next():
    for rec in _prompt_rows(200):
        if rec.get("status") == "pending":
            return jsonify({"ok": True, "prompt": rec})
    return jsonify({"ok": True, "prompt": None})


@app.route("/prompt/result", methods=["POST"])
def prompt_result():
    data = request.get_json(force=True, silent=True) or {}
    pid = str(data.get("id") or "")
    rows = _prompt_rows(2000)
    for rec in rows:
        if rec.get("id") == pid:
            rec["status"] = str(data.get("status") or "done")
            rec["result"] = str(data.get("result") or "")[:4000]
    PROMPT_FILE.write_text("".join(json.dumps(r, ensure_ascii=False) + "\\n" for r in rows), encoding="utf-8")
    return jsonify({"ok": True})


'''
    if '@app.route("/prompt/submit"' not in s:
        idx = s.index('@app.route("/export/add"')
        s = s[:idx] + routes + s[idx:]

    if "import subprocess" not in s:
        s = s.replace("import json", "import json\nimport subprocess", 1)

    A.write_text(s, encoding="utf-8")
    print("backend added")

# ---------------------------------------------------------------- template
T = R / "webapp" / "templates" / "index.html"
h = T.read_text(encoding="utf-8")

if 'data-tab="prompt"' not in h:
    h = h.replace('''  <div class="tab" data-tab="export">DeepSeekExport</div>''',
                  '''  <div class="tab" data-tab="export">DeepSeekExport</div>
  <div class="tab" data-tab="prompt">Import Prompt</div>''', 1)

    panel = '''
<!-- ================= IMPORT PROMPT ================= -->
<section class="panel" id="prompt">
  <h2>Import Prompt — ارسال دستور از همین صفحه</h2>
  <p class="note">متن دستور را بنویس و یکی از دو دکمه را بزن:
     <b>ثبت در صف</b> دستور را برای ایجنت ذخیره می‌کند،
     <b>اجرا کن</b> فقط دستورهای مجاز را همان‌جا اجرا می‌کند
     (<code>!status</code> · <code>!scan</code> · <code>!report</code> · <code>!restart-web</code>).</p>

  <div class="prompt-form">
    <input id="prompt-title" type="text" placeholder="عنوان (اختیاری)">
    <textarea id="prompt-text" rows="4" placeholder="متن دستور…"></textarea>
    <div class="prompt-actions">
      <button id="prompt-queue" type="button">ثبت در صف</button>
      <button id="prompt-run" type="button">اجرا کن</button>
      <span id="prompt-status" class="prompt-status"></span>
    </div>
  </div>

  {% if prompts %}
  <h2>آخرین دستورها</h2>
  <div class="table-wrap">
  <table class="responsive">
    <tr class="head"><th>زمان</th><th>عنوان</th><th>حالت</th><th>وضعیت</th><th>نتیجه</th></tr>
    {% for p in prompts %}
    <tr>
      <td data-label="زمان">{{ p.ts }}</td>
      <td data-label="عنوان">{{ p.title }}</td>
      <td data-label="حالت">{{ p.mode }}</td>
      <td data-label="وضعیت">{{ p.status }}</td>
      <td data-label="نتیجه"><span class="mono">{{ (p.result or '')[:120] }}</span></td>
    </tr>
    {% endfor %}
  </table>
  </div>
  {% endif %}
</section>
'''
    h = h.replace("<!-- ================= DEEPSEEK EXPORT ================= -->",
                  panel + "\n<!-- ================= DEEPSEEK EXPORT ================= -->", 1)

    css = '''
  /* ---------------- Import Prompt ---------------- */
  .prompt-form { background:var(--card); border:1px solid var(--line); border-radius:12px;
                 padding:14px; display:flex; flex-direction:column; gap:10px; }
  .prompt-form input, .prompt-form textarea {
      background:#121922; color:var(--txt); border:1px solid var(--line); border-radius:10px;
      padding:10px 12px; font-family:inherit; font-size:14px; width:100%; }
  .prompt-form textarea { resize:vertical; min-height:90px; line-height:1.9; }
  .prompt-actions { display:flex; gap:10px; align-items:center; }
  .prompt-actions button { background:#1f6feb; color:#fff; border:none; border-radius:10px;
                           padding:10px 18px; font-size:14px; cursor:pointer; }
  .prompt-actions button#prompt-queue { background:#1b2733; color:var(--txt);
                                        border:1px solid var(--line); }
  .prompt-actions button:hover { transform:translateY(-1px); }
  .prompt-status { color:var(--muted); font-size:13px; }
  .mono { font-family:monospace; direction:ltr; display:inline-block; max-width:420px;
          overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
'''
    h = h.replace("  /* ---------------- DeepSeekExport ---------------- */",
                  css + "\n  /* ---------------- DeepSeekExport ---------------- */", 1)

    js = '''
<script>
  (function () {
    var text = document.getElementById('prompt-text');
    var title = document.getElementById('prompt-title');
    var status = document.getElementById('prompt-status');
    if (!text) return;
    function send(mode) {
      var body = {text: text.value.trim(), title: title.value.trim() || 'دستور', mode: mode};
      if (!body.text) { status.textContent = 'متن خالی است'; return; }
      status.textContent = 'در حال ارسال…';
      fetch('/prompt/submit', {method:'POST', headers:{'Content-Type':'application/json'},
                               body: JSON.stringify(body)})
        .then(function(r){ return r.json(); })
        .then(function(d){
          status.textContent = d.ok ? ('✔ ' + d.status) : ('خطا: ' + (d.error||''));
          if (d.ok && mode === 'run') {
            var out = document.createElement('pre');
            out.className = 'export-body';
            out.textContent = (d.result || '(بدون خروجی)').slice(0, 4000);
            status.after(out);
          }
          if (d.ok && mode === 'queue') { text.value = ''; title.value = ''; setTimeout(function(){ location.reload(); }, 900); }
        })
        .catch(function(e){ status.textContent = 'خطا: ' + e; });
    }
    document.getElementById('prompt-queue').addEventListener('click', function(){ send('queue'); });
    document.getElementById('prompt-run').addEventListener('click', function(){ send('run'); });
  })();
</script>
'''
    h = h.replace("</body>", js + "\n</body>", 1)
    T.write_text(h, encoding="utf-8")
    print("template added")

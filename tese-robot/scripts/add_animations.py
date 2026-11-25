"""Add a self-contained animation layer to the web app (no CDN needed)."""
from pathlib import Path

P = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\TeseRobot\webapp\templates\index.html")
s = P.read_text(encoding="utf-8")

CSS = r'''
  /* ================= animations (self-contained, no CDN) ================= */
  @keyframes fadeUp   { from { opacity:0; transform:translateY(14px); } to { opacity:1; transform:none; } }
  @keyframes popIn    { 0% { opacity:0; transform:scale(.4); } 70% { transform:scale(1.15); } 100% { opacity:1; transform:scale(1); } }
  @keyframes drawLine { to { stroke-dashoffset:0; } }
  @keyframes pulseLive{ 0%,100% { box-shadow:0 0 0 0 rgba(255,138,128,.55); } 50% { box-shadow:0 0 0 7px rgba(255,138,128,0); } }
  @keyframes shimmer  { 0% { background-position:-200% 0; } 100% { background-position:200% 0; } }
  @keyframes glow     { 0%,100% { box-shadow:0 0 0 0 rgba(31,111,235,.0); } 50% { box-shadow:0 0 18px -2px rgba(31,111,235,.45); } }
  @keyframes slideBar { from { transform:scaleX(0); } to { transform:scaleX(1); } }

  header h1 {
    background:linear-gradient(90deg,#e6edf3 20%,#7ee2a8 40%,#1f6feb 55%,#e6edf3 75%);
    background-size:200% auto; -webkit-background-clip:text; background-clip:text;
    color:transparent; animation:shimmer 7s linear infinite;
  }
  .badge.live { animation:pulseLive 2.4s ease-in-out infinite; }

  .tab { transition:background .25s ease, color .25s ease, transform .18s ease; position:relative; overflow:hidden; }
  .tab:hover { color:var(--txt); background:#16202b; }
  .tab.active::after {
    content:""; position:absolute; inset-inline:12px; bottom:0; height:3px; border-radius:3px;
    background:linear-gradient(90deg,#1f6feb,#7ee2a8); transform-origin:center;
    animation:slideBar .45s cubic-bezier(.2,.8,.2,1) both;
  }

  .panel.active { animation:fadeUp .45s ease both; }

  .kpi { transition:transform .22s ease, box-shadow .22s ease, border-color .22s ease;
         animation:fadeUp .5s ease both; }
  .kpi:hover { transform:translateY(-3px); border-color:#31414f;
               box-shadow:0 10px 24px -14px rgba(0,0,0,.9); }
  .kpi:nth-child(1){animation-delay:.02s} .kpi:nth-child(2){animation-delay:.07s}
  .kpi:nth-child(3){animation-delay:.12s} .kpi:nth-child(4){animation-delay:.17s}
  .kpi:nth-child(5){animation-delay:.22s} .kpi:nth-child(6){animation-delay:.27s}
  .kpi .val { transition:color .3s ease; }
  .kpi:hover .val { color:#bfe6ff; }

  table tbody tr, table tr:not(.head) {
    animation:fadeUp .42s ease both;
  }
  table tr:nth-child(2){animation-delay:.03s}  table tr:nth-child(3){animation-delay:.08s}
  table tr:nth-child(4){animation-delay:.13s}  table tr:nth-child(5){animation-delay:.18s}
  table tr:nth-child(6){animation-delay:.23s}  table tr:nth-child(7){animation-delay:.28s}
  table tr:not(.head):hover td { background:#1b2530; }
  td { transition:background .2s ease; }

  .chart { animation:fadeUp .55s ease both; }
  svg .line {
    stroke-dasharray:4000; stroke-dashoffset:4000;
    animation:drawLine 1.9s cubic-bezier(.2,.75,.25,1) .15s forwards;
    filter:drop-shadow(0 3px 10px rgba(31,170,75,.25));
  }
  svg circle { transform-box:fill-box; transform-origin:center;
               animation:popIn .5s cubic-bezier(.2,.9,.25,1.2) both; }
  svg circle:nth-of-type(1){animation-delay:.55s} svg circle:nth-of-type(2){animation-delay:.75s}
  svg circle:nth-of-type(3){animation-delay:.95s} svg circle:nth-of-type(4){animation-delay:1.15s}
  svg circle:nth-of-type(5){animation-delay:1.35s} svg circle:nth-of-type(6){animation-delay:1.55s}
  svg circle:nth-of-type(7){animation-delay:1.75s} svg circle:nth-of-type(8){animation-delay:1.95s}
  svg .point-label, svg .point-value { animation:fadeUp .45s ease both; animation-delay:1.2s; }

  .empty { animation:fadeUp .5s ease both; }

  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after { animation:none !important; transition:none !important; }
  }
'''

JS = r'''
<script>
  // ---- count-up for KPI numbers (respects reduced motion) ----
  (function () {
    if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    function animate(el) {
      var raw = (el.textContent || "").trim();
      var m = raw.match(/^([+\-]?)([\d,]+(?:\.\d+)?)(.*)$/);
      if (!m) return;
      var sign = m[1], num = parseFloat(m[2].replace(/,/g, "")), tail = m[3];
      if (!isFinite(num) || num === 0) return;
      var dec = (m[2].split(".")[1] || "").length;
      var start = performance.now(), dur = 900;
      function frame(now) {
        var t = Math.min(1, (now - start) / dur);
        var eased = 1 - Math.pow(1 - t, 3);
        var v = num * eased;
        el.textContent = sign + v.toLocaleString('en-US', { minimumFractionDigits: dec, maximumFractionDigits: dec }) + tail;
        if (t < 1) requestAnimationFrame(frame);
        else el.textContent = raw;
      }
      requestAnimationFrame(frame);
    }
    document.querySelectorAll('.kpi .val').forEach(animate);
  })();
</script>
'''

if "animations (self-contained" not in s:
    s = s.replace("  /* ---------------- mobile ---------------- */", CSS + "\n  /* ---------------- mobile ---------------- */", 1)
if "count-up for KPI numbers" not in s:
    s = s.replace("</body>", JS + "\n</body>", 1)

P.write_text(s, encoding="utf-8")
print("animations added:", "animations (self-contained" in s, "| js:", "count-up for KPI numbers" in s)

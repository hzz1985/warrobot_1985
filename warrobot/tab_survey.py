"""Survey every tab of the broker panel and record its capabilities.

For each page (1..5 + Default2) the script clicks the broker's own pager link,
waits for the SPA to render, and dumps:
  * URL and page title
  * visible inputs (id, placeholder, size)
  * visible buttons / links with short text
  * table headers and the first data row
  * whether a symbol search box exists

Output: tab_survey.md + tab_survey.json
"""
import json
import time
from pathlib import Path

import sys

sys.path.insert(0, r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\TeseRobot\src")
from tsebot.cdp import CDP

OUT_MD = Path(__file__).resolve().parent / "tab_survey.md"
OUT_JSON = Path(__file__).resolve().parent / "tab_survey.json"

PAGES = [
    ("1", "/"),
    ("2", "/Home/Default2"),
    ("3", "/Home/Index/page-3"),
    ("4", "/Home/Index/page-4"),
    ("5", "/Home/Index/page-5"),
]

DUMP = """
(function(){
  var out = {};
  out.url = location.pathname;
  out.inputs = [];
  document.querySelectorAll('input,select,textarea').forEach(function(e){
    var r = e.getBoundingClientRect();
    if (r.width > 40 && e.offsetParent !== null)
      out.inputs.push({id: e.id || '', ph: e.placeholder || '', w: Math.round(r.width)});
  });
  out.buttons = [];
  document.querySelectorAll('button,a.ng-binding,a[onclick],div[onclick]').forEach(function(e){
    var t = (e.innerText || '').trim().replace(/\\s+/g, ' ');
    if (t && t.length <= 24 && e.offsetParent !== null)
      out.buttons.push(t);
  });
  out.buttons = out.buttons.slice(0, 18);
  out.th = [];
  document.querySelectorAll('table tr th').forEach(function(e){
    var t = (e.innerText || '').trim().replace(/\\s+/g, ' ');
    if (t) out.th.push(t);
  });
  out.th = Array.from(new Set(out.th)).slice(0, 14);
  out.searchBoxes = [];
  document.querySelectorAll('input[id*=search], input[class*=search], input[id*=Symbol]').forEach(function(e){
    out.searchBoxes.push({id: e.id || '', visible: e.offsetParent !== null});
  });
  out.text = (document.body.innerText || '').replace(/\\s+/g, ' ').slice(0, 400);
  return JSON.stringify(out);
})()
"""


def main() -> int:
    c = CDP()
    result = []
    for label, href in PAGES:
        try:
            c.eval("""(function(){var a=[].slice.call(document.querySelectorAll('a')).filter(
              function(x){return (x.getAttribute('href')||'')==='%s';});
              if(a.length){['mousedown','mouseup','click'].forEach(function(ev){
              a[0].dispatchEvent(new MouseEvent(ev,{bubbles:true,cancelable:true,view:window}));});}
              return a.length;})()""" % href)
            time.sleep(6)
            raw = c.eval(DUMP)
            data = json.loads(raw)
            data["tab"] = label
            result.append(data)
            print(f"tab {label}: ok · inputs={len(data['inputs'])} · buttons={len(data['buttons'])}")
        except Exception as exc:
            print(f"tab {label}: error {str(exc)[:60]}")
    c.close()

    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    md = ["# پیمایش تب‌های پنل کارگزار", ""]
    for d in result:
        md += [f"## تب {d['tab']} — `{d['url']}`", "",
               "**ورودی‌های فعال:** " + ", ".join(
                   f"`{i['id'] or i['ph']}` ({i['w']}px)" for i in d["inputs"]) or "—", "",
               "**دکمه‌ها/لینک‌ها:** " + " · ".join(d["buttons"]) if d["buttons"] else "—", "",
               "**ستون‌های جدول:** " + " · ".join(d["th"]) if d["th"] else "—", "",
               "**کادر جستجوی نماد:** " + (json.dumps(d["searchBoxes"], ensure_ascii=False)
                                          if d["searchBoxes"] else "یافت نشد"), "",
               "```", d["text"][:300], "```", ""]
    OUT_MD.write_text("\n".join(md), encoding="utf-8")
    print("saved:", OUT_MD.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

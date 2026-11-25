"""Allow the collapsed search box to be used (it stays 0x0 but accepts input)."""
from pathlib import Path

P = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\TeseRobot\src\tsebot\broker_selenium.py")
s = P.read_text(encoding="utf-8")

old = """        var box = null;
        document.querySelectorAll('input').forEach(function(e){
          var ph=(e.placeholder||'');
          var r=e.getBoundingClientRect();
          if (!box && (ph.indexOf('جستجوی')>=0) && r.width>0) box=e;
        });
        if (!box) return 'no_box';"""

new = """        var box = document.querySelector('#txt_search');
        if (!box) {
          document.querySelectorAll('input').forEach(function(e){
            var ph = (e.placeholder||'');
            if (!box && ph.indexOf('جستجوی') >= 0) box = e;
          });
        }
        if (!box) return 'no_box';"""

if old in s:
    s = s.replace(old, new)
    P.write_text(s, encoding="utf-8")
    print("patched")
else:
    print("pattern not found; dumping block:")
    i = s.index("var box")
    print(s[i:i+400])

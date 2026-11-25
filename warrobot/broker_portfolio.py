"""Read the broker's own portfolio table (qty, average buy, last price).

The panel is authoritative: the ledger's cost basis can drift (manual buys, extra
fees, partial fills), so the web app marks positions to the broker's numbers when
a session is available.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BASE = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop")
sys.path.insert(0, str(BASE / "TeseRobot" / "src"))


def rows() -> list[dict]:
    try:
        from tsebot.cdp import CDP
    except Exception:
        return []
    try:
        c = CDP()
    except Exception:
        return []
    try:
        raw = c.eval("""
        (function(){
          var out=[];
          var tbl=null;
          document.querySelectorAll('table').forEach(function(t){
            var h=(t.innerText||'').slice(0,200);
            if(h.indexOf('میانگین خرید')>=0 && h.indexOf('آخرین قیمت')>=0) tbl=t;
          });
          if(!tbl) return JSON.stringify([]);
          tbl.querySelectorAll('tr').forEach(function(tr){
            var tds=tr.querySelectorAll('td');
            if(tds.length<6) return;
            var c=[].map.call(tds,function(td){return (td.innerText||'').trim();});
            var sym=c[0].replace(/\\s+/g,'');
            if(!sym || sym.length>14) return;
            out.push(c.slice(0,12));
          });
          return JSON.stringify(out);
        })()
        """)
        data = json.loads(raw or "[]")
    except Exception:
        data = []
    finally:
        try:
            c.close()
        except Exception:
            pass

    def num(x: str):
        try:
            return float(str(x).replace(",", "").replace("٪", "").strip())
        except Exception:
            return None

    parsed = []
    for r in data:
        if len(r) < 8:
            continue
        sym = r[0]
        qty = num(r[1])
        avg = num(r[6]) if len(r) > 6 else None
        last = num(r[8]) if len(r) > 8 else None
        parsed.append({"symbol": sym, "qty": qty, "avg": avg, "last": last,
                       "raw": r})
    return parsed


if __name__ == "__main__":
    print(json.dumps(rows(), ensure_ascii=False, indent=1)[:2000])


_CACHE = {"ts": 0.0, "data": {}}


def last_prices(ttl: float = 5.0) -> dict:
    """Live {symbol: price} from the panel, cached for `ttl` seconds."""
    import time as _t
    now = _t.time()
    if now - _CACHE["ts"] < ttl and _CACHE["data"]:
        return _CACHE["data"]
    data = _last_prices_raw()
    if data:
        _CACHE["ts"], _CACHE["data"] = now, data
    return data or _CACHE["data"]


def _last_prices_raw() -> dict:
    """{symbol: LAST traded price} from the portfolio grid (not the closing price)."""
    try:
        from tsebot.cdp import CDP
        import time as _t
        c = CDP()
        ok = c.eval("""(function(){var t=(document.body.innerText||'');return t.indexOf('خرید امروز')>=0;})()""")
        if not ok:
            c.eval("""(function(){
            var a=[].slice.call(document.querySelectorAll('a[href]')).filter(function(x){
              return (x.getAttribute('href')||'')==='/Home/Default2';});
            if(a.length){['mousedown','mouseup','click'].forEach(function(e){
              a[0].dispatchEvent(new MouseEvent(e,{bubbles:true,cancelable:true,view:window}));});}
            return 1;})()""")
            _t.sleep(6)
        txt = c.eval("""(function(){
        var t=(document.body.innerText||'').replace(/\\s+/g,' ');
        var i=t.indexOf('خرید امروز');
        return i>=0? t.slice(i, i+6000) : '';})()""")
        c.close()
    except Exception:
        return {}
    import re
    out = {}
    # row: TSE <sym> <qty> <value> <vol> 0 0 0 - 0 0 0 <LAST> % <chg> <CLOSE> % <chg2> ...
    # explicit: "<LAST> % <chg> <CLOSE> % <chg2>"
    for m in re.finditer(r"TSE\s+(\S+)\s+([\d,]+)\s+([\d,]+)\s+[\d.]+\s*[MB]?\s+[\d\s\-]+?([\d,]{4,})\s*%\s*[\d.\-]+\s+([\d,]{4,})\s*%", txt):
        sym = m.group(1)
        last = m.group(4)
        try:
            out[sym] = float(last.replace(",", ""))
        except Exception:
            pass
    return out


def last_prices(ttl: float = 5.0) -> dict:
    """Live {symbol: price} from the panel, cached for `ttl` seconds."""
    import time as _t
    now = _t.time()
    if now - _CACHE["ts"] < ttl and _CACHE["data"]:
        return _CACHE["data"]
    data = _last_prices_raw()
    if data:
        _CACHE["ts"], _CACHE["data"] = now, data
    return data or _CACHE["data"]


def _last_prices_raw() -> dict:
    """{symbol: last traded price} parsed from the broker portfolio grid."""
    try:
        from tsebot.cdp import CDP
        import time as _t
        c = CDP()
        # make sure the portfolio grid is on screen
        ok = c.eval("""(function(){var t=(document.body.innerText||'');return t.indexOf('خرید امروز')>=0;})()""")
        if not ok:
            c.eval("""(function(){
            var a=[].slice.call(document.querySelectorAll('a[href]')).filter(function(x){
              return (x.getAttribute('href')||'')==='/Home/Default2';});
            if(a.length){['mousedown','mouseup','click'].forEach(function(e){
              a[0].dispatchEvent(new MouseEvent(e,{bubbles:true,cancelable:true,view:window}));});}
            return 1;})()""")
            _t.sleep(6)
        txt = c.eval("""(function(){
        var t=(document.body.innerText||'').replace(/\\s+/g,' ');
        var i=t.indexOf('خرید امروز');
        return i>=0? t.slice(i, i+4000) : '';})()""")
        c.close()
    except Exception:
        return {}
    import re
    out = {}
    for m in re.finditer(r"TSE\s+(\S+)\s+([\d,]+)\s+([\d,]+)\s+([\d.]+\s*[MB]?)\s+(.*?)(?=TSE\s|$)", txt):
        sym = m.group(1)
        tail = m.group(5)
        nums = re.findall(r"([\d,]{3,})\s*%", tail)
        if nums:
            try:
                out[sym] = float(nums[0].replace(",", ""))
            except Exception:
                pass
    return out

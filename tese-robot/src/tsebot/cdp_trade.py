"""CDP-based order submission + price-tick handling.

Why: on 2026-10-24 Chrome auto-updated to 155 while chromedriver stayed 154, so
every Selenium call crashed; the matching driver download is blocked by the proxy.
The panel also rejected orders with «قیمت باید مضربی از ۱۰ باشد» — the click looked
fine but nothing was registered. Both are handled here:
  * tick detection from the five best quotes (gcd of price differences)
  * real keyboard input + real mouse click through CDP
  * reading the panel's red/green message and retrying once after a tick error
"""
from __future__ import annotations

import json
import math
import time
from typing import Optional, Tuple

from .cdp import CDP

# ----------------------------------------------------------------- tick helpers
def _gcd_list(values):
    g = 0
    for v in values:
        g = math.gcd(g, int(round(v)))
    return g


def detect_tick(prices) -> int:
    """Smallest price step implied by a list of quoted prices (1, 5, 10, ...)."""
    vals = sorted({int(round(p)) for p in prices if p and int(round(p)) > 0})
    if len(vals) < 2:
        return 10 if vals and vals[0] >= 1000 else 1
    diffs = [b - a for a, b in zip(vals, vals[1:]) if b > a]
    g = _gcd_list(vals) if len(vals) >= 2 else 0
    gd = _gcd_list(diffs) if diffs else 0
    tick = gd or g or 1
    if tick not in (1, 2, 5, 10, 20, 25, 50, 100, 500, 1000):
        tick = 10 if vals[-1] >= 1000 else 1
    return max(1, int(tick))


def round_to_tick(price: float, tick: int, side: str = "buy") -> int:
    """Round to a valid tick; buys round down, sells round up (never worse)."""
    if tick <= 1:
        return int(round(price))
    if side == "buy":
        return int(math.floor(price / tick) * tick)
    return int(math.ceil(price / tick) * tick)


# ----------------------------------------------------------------- panel helpers
_JS_RECT = """
var e=document.querySelector(%s);
if(!e) return null;
var r=e.getBoundingClientRect();
return JSON.stringify({x:r.x+r.width/2, y:r.y+r.height/2, w:r.width});
"""

_JS_TEXT = "return (document.body.innerText||'').replace(/\\s+/g,' ');"


class CdpTrader:
    def __init__(self, port: int = 9333):
        self.c = CDP(port=port)

    # -- low level ---------------------------------------------------------
    def ev(self, js: str):
        return self.c.eval("(function(){" + js + "})()")

    def rect(self, sel: str):
        raw = self.c.eval("(function(){" + (_JS_RECT % json.dumps(sel)) + "})()")
        return json.loads(raw) if raw else None

    def click_at(self, p) -> None:
        for kind in ("mousePressed", "mouseReleased"):
            self.c.call("Input.dispatchMouseEvent",
                        {"type": kind, "x": p["x"], "y": p["y"],
                         "button": "left", "clickCount": 1})

    def set_field(self, sel: str, text: str) -> str:
        r = self.rect(sel)
        if not r or r.get("w", 0) < 10:
            return ""
        self.click_at(r)
        time.sleep(0.25)
        self.ev("var e=document.querySelector(%s); e.focus(); e.select(); return true;" % json.dumps(sel))
        time.sleep(0.15)
        self.c.call("Input.insertText", {"text": text})
        time.sleep(0.35)
        return str(self.ev("return document.querySelector(%s).value;" % json.dumps(sel)) or "")

    def bind(self, symbol: str) -> bool:
        cur = str(self.ev("var e=document.querySelector('#fulltextContainer'); "
                          "return e?(e.innerText||'').trim():'';") or "")
        if cur.startswith(symbol[:2]):
            return True
        ok = self.ev("""
        var w=arguments[0], el=null;
        document.querySelectorAll('li,a,tr,div,span').forEach(function(x){
          if(el) return; var t=(x.innerText||'').trim();
          if(t===w && x.offsetParent!==null) el=x;});
        if(!el) return false;
        var t=el.closest('li')||el.closest('tr')||el;
        ['mousedown','mouseup','click'].forEach(function(ev){
          t.dispatchEvent(new MouseEvent(ev,{bubbles:true,cancelable:true,view:window}));});
        return true;
        """)
        time.sleep(3)
        cur = str(self.ev("var e=document.querySelector('#fulltextContainer'); "
                          "return e?(e.innerText||'').trim():'';") or "")
        return cur.startswith(symbol[:2])

    def quote_prices(self) -> list:
        raw = self.ev("""
        var t=null, out=[];
        document.querySelectorAll('table').forEach(function(x){
          var h=(x.innerText||'').replace(/\\s+/g,' ');
          if(!t && h.indexOf('تعداد')>=0 && h.indexOf('حجم')>=0 && h.indexOf('قیمت')>=0) t=x;});
        if(!t) return '[]';
        t.querySelectorAll('tr').forEach(function(tr){
          var c=[].slice.call(tr.querySelectorAll('td')).map(function(x){
            return (x.innerText||'').trim().replace(/,/g,'');});
          if(c.length>=4 && /^[\\d.]+$/.test(c[2])) out.push([c[2], c[3]]);
        });
        return JSON.stringify(out);
        """)
        try:
            rows = json.loads(raw or "[]")
        except Exception:
            rows = []
        vals = []
        for bid, ask in rows:
            for v in (bid, ask):
                try:
                    if float(v) > 0:
                        vals.append(float(v))
                except Exception:
                    continue
        return vals

    def tick(self) -> int:
        return detect_tick(self.quote_prices())

    def message(self) -> str:
        txt = str(self.ev(_JS_TEXT) or "")
        for kw in ("موفقیت", "مضربی", "کافی", "دامنه", "خطا", "عدم پذیرش"):
            i = txt.rfind(kw)
            if i >= 0:
                start = max(0, txt.rfind("سفارش", 0, i))
                return txt[start:i + 90].strip()
        return ""

    # -- order -------------------------------------------------------------
    def submit(self, side: str, symbol: str, qty: int, price: float) -> dict:
        res = {"path": "cdp", "symbol": symbol, "side": side, "qty": int(qty)}
        if not self.bind(symbol):
            res.update({"placed": False, "reason": "symbol_not_bound"})
            return res
        tick = self.tick() or 1
        price = round_to_tick(float(price), tick, side)
        res["tick"] = tick
        res["price"] = price

        side_sel = ".orderside65" if side == "buy" else ".orderside86"
        self.ev("var e=document.querySelector(%s); if(e) e.click(); return true;" % json.dumps(side_sel))
        time.sleep(0.8)
        got_qty = self.set_field("#send_order_txtCount", str(int(qty)))
        got_px = self.set_field("#send_order_txtPrice", str(int(price)))
        res["inputs"] = {"qty": got_qty, "price": got_px}

        btn = self.rect("#send_order_btnSendOrder")
        if not btn:
            res.update({"placed": False, "reason": "no_send_button"})
            return res
        self.click_at(btn)
        time.sleep(4)
        msg = self.message()
        res["message"] = msg

        if "مضرب" in msg:
            # the panel told us the real tick; honour it once
            price = round_to_tick(float(price), 10, side)
            self.set_field("#send_order_txtPrice", str(int(price)))
            self.click_at(self.rect("#send_order_btnSendOrder"))
            time.sleep(4)
            msg = self.message()
            res["price"] = price
            res["message"] = msg

        res["ok"] = "موفقیت" in msg
        res["placed"] = res["ok"]
        if not res["ok"]:
            res["reason"] = msg or "no_confirmation"
        return res


def submit_via_cdp(side: str, symbol: str, qty: int, price: float) -> dict:
    try:
        t = CdpTrader()
        try:
            return t.submit(side, symbol, qty, price)
        finally:
            t.c.close()
    except Exception as exc:
        return {"path": "cdp", "placed": False, "reason": f"cdp_error:{str(exc)[:80]}"}

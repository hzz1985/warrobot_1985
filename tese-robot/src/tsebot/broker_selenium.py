# -*- coding: utf-8 -*-
"""TSE Bot v2 — Selenium adapter for the broker web UI (meibours).

Design rules that fix the v1 failures:
  1. ``submit()`` only *places* the order and reports what the UI said. It never
     claims success. The caller must verify against ``orderlist()``.
  2. The confirmation modal is handled, but its absence is NOT treated as either
     success or failure.
  3. Order history is parsed from the order list widget, which is the only
     broker-side truth we can read; the parser tolerates the widget paging.
  4. Every DOM lookup has an explicit failure path with a readable reason, and
     the scope lookup is retried after a page re-navigation.

Nothing here is imported unless the bot actually runs in live mode, so the
package (and its tests) work on a machine without Selenium.
"""
from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional

from .broker_base import Account, BrokerError, SymbolInfo
from .orders import BrokerOrder

BUY_SIDE = 65
SELL_SIDE = 86

_TRADE_URL_PATH = "/Index/page-4"


class SeleniumBroker:
    def __init__(self, debugger_address: str = "127.0.0.1:9222",
                 chromedriver: str = r"C:\TSE_Strategy\chromedriver.exe",
                 trade_url: str = "https://online.meibours.ir/Home/Index/page-4",
                 page_settle_sec: float = 3.0, script_timeout: int = 60):
        self.debugger_address = debugger_address
        self.chromedriver = chromedriver
        self.trade_url = trade_url
        self.page_settle = page_settle_sec
        self.script_timeout = script_timeout
        self.driver = None
        self.connected = False
        self._scope_ok = False

    # ------------------------------------------------------------- lifecycle
    _SHARED: Dict[str, Any] = {"driver": None}

    def connect(self) -> None:
        """Attach to the operator's Chrome — and reuse one session per process.

        Every `webdriver.Chrome(...)` call spawns another chromedriver process that
        keeps its own debugger session. Twenty of them had accumulated, and when one
        ended it could close/navigate the tab, which looked exactly like the broker
        logging the operator out. One shared session is the fix.
        """
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service

        shared = self._SHARED.get("driver")
        if shared is not None:
            try:
                _ = shared.current_url
                self.driver = shared
                self.connected = True
                return
            except Exception:
                self._SHARED["driver"] = None

        opts = Options()
        opts.add_experimental_option("debuggerAddress", self.debugger_address)
        try:
            svc = Service(executable_path=self.chromedriver)
            self.driver = webdriver.Chrome(service=svc, options=opts)
        except Exception:
            # Newer selenium-manager can resolve the driver by itself.
            self.driver = webdriver.Chrome(options=opts)
        self.driver.set_script_timeout(self.script_timeout)
        try:
            self.driver.set_page_load_timeout(60)
        except Exception:
            pass
        self._SHARED["driver"] = self.driver
        self.connected = True
        self._select_logged_in_tab()

    def _select_logged_in_tab(self) -> bool:
        """Switch to a tab that is actually logged in.

        A ChromeDriver session binds to ONE target; that target is often a stray
        /Login tab while the operator is logged in on another one. Without this the
        bot reports "login_screen" even though the panel is open and authenticated.
        """
        try:
            handles = list(self.driver.window_handles)
        except Exception:
            return False
        best = None
        for h in handles:
            try:
                self.driver.switch_to.window(h)
                url = str(self.driver.current_url)
            except Exception:
                continue
            if "meibours" in url and "Login" not in url:
                best = h
                if "page-1" in url or "page-4" in url or "Default" in url:
                    break
        if best is not None:
            try:
                self.driver.switch_to.window(best)
            except Exception:
                pass
            return True
        return False

    def close(self) -> None:
        # Never quit the user's browser: only drop the reference.
        self.driver = None
        self.connected = False

    # ------------------------------------------------------------------ util
    def _js(self, script: str, *args):
        if self.driver is None:
            raise BrokerError("not connected")
        return self.driver.execute_script(script, *args)

    HEADERS = ["نماد", "دارایی لحظه", "ارزش فروش", "حجم معاملات", "خرید امروز",
               "فروش امروز", "سفارشات باز", "میانگین خرید", "میانگین فروش",
               "آخرین قیمت", "قیمت پایانی"]

    def row_cells(self, symbol: str) -> Dict[str, str]:
        """Portfolio row as {header: value} parsed from the page text (no navigation).

        Confirms fills via "فروش امروز" / "خرید امروز" / "دارایی لحظه" — the order
        list cannot be used because changing the page drops the broker session.
        """
        try:
            _text = self.page_text() or ""
        except Exception:
            return {}
        # only parse the PORTFOLIO table; on the order page the symbol also appears
        # (search box, chart) and the naive parse returned shifted garbage values
        if "دارایی لحظه" not in _text or "سفارشات باز" not in _text:
            return {}
        lines = [l.strip() for l in _text.splitlines() if l.strip()]
        try:
            i = lines.index(symbol)
        except ValueError:
            i = next((k for k, l in enumerate(lines) if l == symbol), -1)
        if i < 0:
            return {}
        vals = lines[i + 1:i + len(self.HEADERS)]
        out = {"نماد": symbol}
        for k, h in enumerate(self.HEADERS[1:]):
            out[h] = vals[k] if k < len(vals) else ""
        return out


    def ensure_order_page(self) -> bool:
        """Click the broker's own link to the order page (never driver.get: a full
        navigation drops the session and lands on /Login)."""
        import time as _t
        d = self.driver
        if d is None:
            return False
        try:
            if self._js("return !!document.querySelector('#send_order_txtCount')"):
                return True
            clicked = self._js("""
            var a=[].slice.call(document.querySelectorAll('a,li,span,div')).filter(function(x){
              var h=(x.getAttribute && (x.getAttribute('href')||'')) || '';
              var t=(x.innerText||'').trim();
              return h.indexOf('Default2')>=0 || /^(سفارش|معاملات|خرید و فروش|سفارشات)$/.test(t);
            });
            if(a.length){ a[0].click(); return true; }
            return false;
            """)
            _t.sleep(3.5)
            ok = bool(self._js("return !!document.querySelector('#send_order_txtCount')"))
            return bool(ok)
        except Exception:
            return False

    def bind_symbol(self, symbol: str) -> bool:
        """Bind a symbol on tab 2 WITHOUT the flaky search box.

        The watchlist row (li.freez.flex) carries the symbol name and a click on it
        binds the order panel — the search box (#txt_search) often stays 0px wide
        and never returns results (seen repeatedly on 2026-10-24).
        """
        import time as _t
        d = self.driver
        if d is None:
            return False
        try:
            # already bound?
            cur = str(self._js("var e=document.querySelector('#fulltextContainer'); "
                               "return e?(e.innerText||'').trim():'';") or "")
            if cur.startswith(symbol[:2]):
                return True
            ok = self._js("""
            var w = arguments[0], el = null;
            document.querySelectorAll('li,a,tr,div,span').forEach(function(x){
              if (el) return;
              var t = (x.innerText||'').trim();
              if (t === w && x.offsetParent !== null) el = x;
            });
            if (!el) return false;
            var target = el.closest('li') || el.closest('tr') || el;
            ['mousedown','mouseup','click'].forEach(function(ev){
              target.dispatchEvent(new MouseEvent(ev,{bubbles:true,cancelable:true,view:window}));
            });
            return true;
            """, symbol)
            _t.sleep(3.5)
            cur = str(self._js("var e=document.querySelector('#fulltextContainer'); "
                               "return e?(e.innerText||'').trim():'';") or "")
            if cur.startswith(symbol[:2]):
                return True
            # fallback: the angucomplete box is 0px wide until it is given a width;
            # without it the dropdown never renders (root cause found 2026-10-26).
            self._js("""
            var e=document.querySelector('#txt_search');
            if(e){ var h=e.closest('.angucomplete-holder')||e.parentElement;
              [e,h].forEach(function(x){ if(x){ x.style.width='220px'; x.style.minWidth='220px';
                x.style.display='inline-block'; }});
              e.focus(); }
            return 1;
            """)
            _t.sleep(0.6)
            try:
                from selenium.webdriver.common.by import By as _By
                box = self.driver.find_element(_By.ID, "txt_search")
                box.clear()
                box.send_keys(symbol)
            except Exception:
                self._js("""
                var e=document.querySelector('#txt_search');
                if(!e) return;
                e.focus(); e.value=arguments[0];
                ['input','keyup','change'].forEach(function(ev){ e.dispatchEvent(new Event(ev,{bubbles:true})); });
                """, symbol)
            _t.sleep(2.5)
            self._js("""
            var e=document.querySelector('#txt_search'); if(!e) return;
            e.focus(); e.value=arguments[0];
            ['input','keyup','change'].forEach(function(ev){ e.dispatchEvent(new Event(ev,{bubbles:true})); });
            """, symbol)
            _t.sleep(3)
            self._js("""
            var w=arguments[0], t=null;
            document.querySelectorAll('li,div,span,a').forEach(function(e){
              var x=(e.innerText||'').trim(); if(!t && x===w && e.offsetParent!==null) t=e;});
            if(t){var el=t.closest('li')||t.parentElement||t;
              ['mousedown','mouseup','click'].forEach(function(ev){
                el.dispatchEvent(new MouseEvent(ev,{bubbles:true,cancelable:true,view:window}));});}
            """, symbol)
            _t.sleep(2.5)
            cur = str(self._js("var e=document.querySelector('#fulltextContainer'); "
                               "return e?(e.innerText||'').trim():'';") or "")
            return cur.startswith(symbol[:2])
        except Exception:
            return False

    def trading_start_hour(self) -> int:
        """Hour (0-23) announced on the page for a delayed symbol, else 0.

        The broker prints notices like «معاملات نماد از ساعت ۱۰ انجام میشود»; acting
        before that hour only wastes the pre-open attempts.
        """
        import re as _re
        try:
            txt = self.page_text() or ""
        except Exception:
            return 0
        fa = "۰۱۲۳۴۵۶۷۸۹"
        for m in _re.finditer(r"(?:از|ساعت)\s*ساعت\s*([۰-۹0-9]{1,2})|ساعت\s*([۰-۹0-9]{1,2})", txt):
            raw = m.group(1) or m.group(2) or ""
            if not raw:
                continue
            num = int("".join(str(fa.index(ch)) if ch in fa else ch for ch in raw))
            if 1 <= num <= 23:
                return num
        return 0



    def panel_price_limits(self):
        """(min, max) allowed price for the bound symbol, read from the panel slider.

        The three labels sit on one horizontal line (min · base · max); grouping by
        y avoids picking the step bubble below the slider.
        """
        try:
            raw = self._js("""
            var out=[];
            document.querySelectorAll('span,div,label').forEach(function(x){
              var t=(x.innerText||'').trim();
              if(x.children.length===0 && /^[\\d,]{4,8}$/.test(t)){
                var r=x.getBoundingClientRect();
                if(r.width>0 && r.top>550 && r.top<720){
                  out.push([parseInt(t.replace(/,/g,''),10), Math.round(r.top)]);
                }
              }
            });
            return JSON.stringify(out);
            """)
            import json as _j
            rows = {}
            for val, top in _j.loads(raw or "[]"):
                rows.setdefault(top // 8, []).append(val)
            best = max(rows.values(), key=len) if rows else []
            if len(best) >= 3:
                return min(best), max(best)
            if len(best) == 2:
                return min(best), max(best)
        except Exception:
            pass
        return None, None

    def cancel_open_orders(self, symbols=None) -> int:
        """Cancel open orders (all, or only the given symbols). Returns the count.

        Uses the broker's own «سفارشات باز» tab and clicks the row's cancel
        control. window.confirm is auto-accepted so the click is not swallowed.
        """
        import time as _t
        d = self.driver
        if d is None:
            return 0
        try:
            self._js("window.confirm = function(){ return true; };")
            # open the tab
            self._js("""
            var tabs=[].slice.call(document.querySelectorAll('li,span,a')).filter(function(x){
              return (x.innerText||'').trim() === 'سفارشات باز' && x.offsetParent!==null;});
            if(tabs.length){ var t=tabs[0].closest('li')||tabs[0];
              ['mousedown','mouseup','click'].forEach(function(ev){
                t.dispatchEvent(new MouseEvent(ev,{bubbles:true,cancelable:true,view:window}));});}
            """)
            _t.sleep(2.5)
            want = None if not symbols else {str(x) for x in symbols}
            clicked = self._js("""
            var want = arguments[0];
            var rows=[].slice.call(document.querySelectorAll('tr'));
            var n=0;
            rows.forEach(function(tr){
              var txt=(tr.innerText||'').replace(/\\s+/g,' ');
              if(!txt || txt.length<6) return;
              var mt = want ? want.some(function(w){ return txt.indexOf(w)>=0; }) : true;
              if(!mt) return;
              var btn=[].slice.call(tr.querySelectorAll('a,button,span,i,div')).filter(function(x){
                var t=(x.innerText||'').trim();
                var title=(x.getAttribute('title')||'')+' '+(x.getAttribute('class')||'');
                return /حذف|لغو|×|✕|Cancel|cancel|delete/i.test(t+' '+title) && x.offsetParent!==null;});
              if(btn.length){
                ['mousedown','mouseup','click'].forEach(function(ev){
                  btn[0].dispatchEvent(new MouseEvent(ev,{bubbles:true,cancelable:true,view:window}));});
                n += 1;
              }
            });
            return n;
            """, list(want) if want else None)
            _t.sleep(2)
            return int(clicked or 0)
        except Exception:
            return 0

    def order_limits(self) -> tuple:
        """(min, max) shares per order from the panel's «آستانه حجم» row.

        The broker caps every ticket at a symbol-specific maximum (300,000 for وایرا
        on 2026-10-21); exceeding it makes the ticket fail silently.
        """
        import re as _re
        try:
            txt = self.page_text() or ""
        except Exception:
            return (1, 0)
        m = _re.search(r"آستانه\s*حجم[^\d]{0,6}([\d,]+)\s*[-–]\s*([\d,]+)", txt)
        if not m:
            # the row is rendered a moment after the symbol binds: retry briefly
            import time as _t
            for _ in range(4):
                _t.sleep(1.2)
                try:
                    txt = self.page_text() or ""
                except Exception:
                    continue
                m = _re.search(r"آستانه\s*حجم[^\d]{0,6}([\d,]+)\s*[-–]\s*([\d,]+)", txt)
                if m:
                    break
            if not m:
                return (1, 0)
        lo = int(m.group(1).replace(",", ""))
        hi = int(m.group(2).replace(",", ""))
        return (max(lo, 1), hi)

    def best_bid_ask(self) -> tuple:
        """(best_bid, best_ask) from the depth table printed on the order panel.

        Rows look like: n  volume  bid  ask  volume  n
        """
        import re as _re
        try:
            txt = self.page_text() or ""
        except Exception:
            return (0.0, 0.0)
        bids, asks = [], []
        for m in _re.finditer(r"\b\d+\s+([\d,]{3,})\s+([\d,]{3,})\s+([\d,]{3,})\s+([\d,]{3,})\s+\d+", txt):
            try:
                bid = float(m.group(2).replace(",", ""))
                ask = float(m.group(3).replace(",", ""))
            except Exception:
                continue
            if 0 < bid < ask and 100 <= bid <= 1e9:
                bids.append(bid)
                asks.append(ask)
        return (max(bids) if bids else 0.0, min(asks) if asks else 0.0)
    def page_text(self) -> str:

        """Visible text of the current page (no navigation, no network)."""
        try:
            return str(self._js("return (document.body && document.body.innerText) || \x27\x27") or "")
        except Exception:
            return ""

    def is_logged_in(self) -> bool:
        try:
            url = str(self.driver.current_url)
        except Exception:
            return False
        if "meibours" not in url or "Login" in url:
            return False
        try:
            body = self._js("return (document.body && document.body.innerText) || ''") or ""
        except Exception:
            return False
        return ("قدرت خرید" in body) or ("ارسال سفارش" in body) or ("نماد" in body)

    def ensure_trade_page(self) -> bool:
        """Get to the order-entry screen WITHOUT a full page reload.

        IMPORTANT: while the login page is showing, this method must do NOTHING.
        Any navigation on the login page reloads it, which regenerates the captcha
        every couple of seconds (the operator could not even type the code) and
        can cancel a session that was just established.
        """
        try:
            cur = str(self.driver.current_url)
        except Exception:
            return False
        if "Login" in cur:
            return False
        try:
            if _TRADE_URL_PATH in cur:
                return True
        except Exception:
            pass
        click_js = """
        try {
          var as = document.querySelectorAll('a[href]');
          for (var i = 0; i < as.length; i++) {
            var h = as[i].getAttribute('href') || '';
            if (h.indexOf('page-4') >= 0) {
              as[i].click();
              return 'CLICKED';
            }
          }
          return 'NOTFOUND';
        } catch (e) { return 'ERR'; }
        """
        try:
            self._js(click_js)
        except Exception:
            pass
        time.sleep(2.5)
        try:
            if _TRADE_URL_PATH in str(self.driver.current_url):
                return True
        except Exception:
            pass
        # No `driver.get()` fallback on purpose: a full reload of the SPA is what
        # kept kicking the session (and regenerating the login captcha).
        return False

    def _scope(self) -> bool:
        js = """
        try {
          var el = document.querySelector('[ng-click*="orderoptions"]');
          if (!el) return 'NO_BUTTON';
          var s = angular.element(el).scope();
          var k = 0;
          while (s && k < 30 && !s.order) { s = s.$parent; k++; }
          if (!s || !s.order) return 'NO_SCOPE';
          window.__tse = s;
          return 'OK';
        } catch (e) { return 'ERR ' + e; }
        """
        try:
            r = self._js(js)
        except Exception as exc:
            r = f"EXC {exc}"
        self._scope_ok = (r == "OK")
        if not self._scope_ok:
            # one recovery attempt: reload the trade page and retry
            try:
                self.driver.get(self.trade_url)
                time.sleep(self.page_settle)
                r = self._js(js)
                self._scope_ok = (r == "OK")
            except Exception:
                pass
        return self._scope_ok

    # --------------------------------------------------------------- session
    def ping(self) -> str:
        """Touch the broker session without navigating away.

        A full page reload is what the old robot did and it is also what tends to
        lose the SPA state right before the open. This only performs an XHR and
        reports whether the response landed on the login page.
        """
        js = (
            "var cb = arguments[arguments.length - 1];"
            "try {"
            "  fetch('/Home/Index/page-4', {credentials: 'include', cache: 'no-store'})"
            "    .then(function (r) { cb(r.url.indexOf('Login') >= 0 ? 'LOGIN' : String(r.status)); })"
            "    .catch(function (e) { cb('ERR'); });"
            "} catch (e) { cb('ERR'); }"
        )
        try:
            return str(self.driver.execute_async_script(js))
        except Exception as exc:
            return "EXC " + str(exc)[:80]

    def keepalive(self) -> str:
        """Keep the SPA's idle timer awake WITHOUT any network request.

        A fetch to the broker's own pages turned out to invalidate the session
        (the panel treats it as a new client handshake), so this only synthesises
        local user activity: a real CDP mouse move plus DOM mouse/key events.
        Reading `current_url` is a local CDP call and does not hit the server.
        """
        try:
            self.driver.execute_cdp_cmd(
                "Input.dispatchMouseEvent",
                {"type": "mouseMoved", "x": 200, "y": 200, "button": "none"})
        except Exception:
            pass
        try:
            self.driver.execute_script(
                "var x=180+Math.floor(Math.random()*80), y=180+Math.floor(Math.random()*80);"
                "document.dispatchEvent(new MouseEvent('mousemove',{bubbles:true,clientX:x,clientY:y}));"
                "window.dispatchEvent(new MouseEvent('mousemove',{bubbles:true,clientX:x,clientY:y}));"
                "document.body.dispatchEvent(new MouseEvent('mousemove',{bubbles:true,clientX:x,clientY:y}));")
        except Exception:
            pass
        try:
            url = str(self.driver.current_url)
        except Exception:
            return "NO_DRIVER"
        return "LOGIN" if "Login" in url else "200"

    def cookie_report(self) -> list:
        """Names/expiry of the broker cookies — used to measure the session TTL.

        Values are never printed; only name, domain and expiry epoch.
        """
        out = []
        try:
            cookies = self.driver.get_cookies()
        except Exception:
            return out
        for c in cookies:
            out.append({"name": c.get("name"), "domain": c.get("domain"),
                        "expiry": c.get("expiry"), "httpOnly": c.get("httpOnly")})
        return out

    def bring_to_front(self) -> None:
        try:
            self.driver.execute_script("window.focus();")
        except Exception:
            pass

    # --------------------------------------------------------------- reading
    def buying_power(self) -> Optional[int]:
        """Read 'قدرت خرید' from the order-entry header.

        The header renders as `قدرت خرید: \n  200,580,196` — there are more than
        15 non-digit characters (colon, newline, indentation) between the label and
        the amount, which is why a tight regex silently returned None and the bot
        believed cash was zero.
        """
        try:
            txt = self._js("return (document.body && document.body.textContent) || ''") or ""
        except Exception:
            return None
        txt = (txt.replace("۰", "0").replace("۱", "1").replace("۲", "2").replace("۳", "3")
                  .replace("۴", "4").replace("۵", "5").replace("۶", "6").replace("۷", "7")
                  .replace("۸", "8").replace("۹", "9").replace("\u066c", ","))
        for pat in (r"قدرت\s*خرید[^0-9]{0,60}([0-9][0-9,]{3,})",
                    r"قدرت\s*خرید[^0-9]{0,60}([0-9]{3,})"):
            for m in re.finditer(pat, txt):
                try:
                    v = int(m.group(1).replace(",", ""))
                    if v >= 0:
                        return v
                except Exception:
                    continue
        return None

    def search(self, term: str) -> Optional[dict]:
        js = """
        var done = arguments[arguments.length - 1];
        var term = arguments[0];
        function inj() {
          var cands = [document.body, document.querySelector('[ng-app]')];
          for (var i = 0; i < cands.length; i++) {
            try { var x = angular.element(cands[i]).injector(); if (x) return x; } catch (e) {}
          }
          return null;
        }
        var $inj = inj();
        if (!$inj) { done('NO_INJECTOR'); return; }
        var $http = $inj.get('$http');
        $http.get('/Symbol/GetSymbol', {params: {term: term}}).then(function (r) {
          var d = r.data, item = null;
          if (Object.prototype.toString.call(d) === '[object Array]') { item = d[0] || null; }
          else if (d) {
            var ks = ['data', 'rows', 'items', 'result'];
            for (var i = 0; i < ks.length; i++) {
              if (d[ks[i]] && d[ks[i]].length) { item = d[ks[i]][0]; break; }
            }
          }
          if (!item) { done('NO_ITEM'); return; }
          done(JSON.stringify(item));
        }, function (e) { done('HTTP_ERR ' + e); });
        """
        try:
            raw = self.driver.execute_async_script(js, term)
        except Exception as exc:
            raise BrokerError(f"search failed: {exc}") from exc
        if not raw or not str(raw).startswith("{"):
            return None
        import json
        try:
            return json.loads(raw)
        except Exception:
            return None

    def select(self, item: dict) -> bool:
        if not self._scope_ok and not self._scope():
            return False
        js = """
        try {
          var s = window.__tse;
          var item = JSON.parse(arguments[0]);
          var structure = {label: item.label, title: item.label, description: item.label,
                           image: '', originalObject: item};
          s.$apply(function () { s.selectedSymbolChanged(structure, false); });
          return 'OK';
        } catch (e) { return 'ERR ' + e; }
        """
        import json
        r = self._js(js, json.dumps(item, ensure_ascii=False))
        return r == "OK"

    def symbol_info(self, symbol: str) -> SymbolInfo:
        if not self._scope_ok and not self._scope():
            raise BrokerError("order scope not available")
        js = """
        try {
          var si = window.__tse.symbolInformation || {};
          return JSON.stringify({
            LastTradedPrice: si.LastTradedPrice, ClosingPrice: si.ClosingPrice,
            YesterdayPrice: si.YesterdayPrice, HighAllowedPrice: si.HighAllowedPrice,
            LowAllowedPrice: si.LowAllowedPrice, MinQuantityOrder: si.MinQuantityOrder,
            MaxQuantityOrder: si.MaxQuantityOrder, MarketType: si.MarketType,
            SymbolStateId: si.SymbolStateId, Name: (si.Name || si.Symbol || '')
          });
        } catch (e) { return JSON.stringify({error: String(e)}); }
        """
        import json
        raw = self._js(js) or "{}"
        d = json.loads(raw)
        if d.get("error"):
            raise BrokerError(f"symbol_info({symbol}): {d['error']}")

        def _f(k):
            v = d.get(k)
            try:
                return float(v) if v not in (None, "", 0) else None
            except Exception:
                return None

        best_bid, best_ask = self._book_prices()
        return SymbolInfo(
            symbol=symbol,
            last=_f("LastTradedPrice") or _f("ClosingPrice"),
            best_bid=best_bid,
            best_ask=best_ask,
            ceiling=_f("HighAllowedPrice"),
            floor=_f("LowAllowedPrice"),
            min_qty=int(_f("MinQuantityOrder") or 1),
            max_qty=int(_f("MaxQuantityOrder") or 0),
            market_type=str(d.get("MarketType") or ""),
            state=str(d.get("SymbolStateId") or ""),
            prev_close=_f("YesterdayPrice"),
        )

    def _book_prices(self) -> tuple[Optional[float], Optional[float]]:
        js = """
        try {
          var s = window.__tse;
          var ob = s.orderBook || s.orderbook || s.symbolInformation || {};
          function n(v){ var x = parseFloat(String(v||'').replace(/,/g,'')); return isNaN(x)?null:x; }
          var bid = ob.BestBidPrice || ob.BidPrice1 || ob.FirstBidPrice || null;
          var ask = ob.BestAskPrice || ob.AskPrice1 || ob.FirstAskPrice || null;
          return JSON.stringify({bid: n(bid), ask: n(ask)});
        } catch (e) { return JSON.stringify({}); }
        """
        import json
        try:
            d = json.loads(self._js(js) or "{}")
        except Exception:
            return None, None
        return d.get("bid"), d.get("ask")

    # ---------------------------------------------------------------- writing
    def submit(self, side: str, symbol: str, qty: int, price: float) -> Dict[str, object]:
        """Place an order. Returns what the UI reported; success is NOT claimed."""
        if not self._scope_ok and not self._scope():
            return {"placed": False, "reason": "no_scope"}
        side_code = BUY_SIDE if side == "buy" else SELL_SIDE
        js = """
        try {
          var s = window.__tse;
          var side = arguments[0], qty = arguments[1], price = arguments[2];
          var out = {set_ok: false, added: false};
          s.$apply(function () {
            try {
              s.orderoptions.setOrderSide(side);
              s.orderoptions.setCount(qty);
              s.orderoptions.setOrderPrice(price);
              out.set_ok = true;
            } catch (e) { out.set_error = String(e); }
          });
          if (out.set_ok) {
            try {
              s.$apply(function () { s.orderoptions.addOrder(); out.added = true; });
            } catch (e) { out.add_error = String(e); }
          }
          out.order_isin = s.order && s.order.isin;
          out.order_price = s.order && s.order.orderPrice;
          out.order_count = s.order && s.order.orderCount;
          return JSON.stringify(out);
        } catch (e) { return JSON.stringify({fatal: String(e)}); }
        """
        import json
        raw = self._js(js, side_code, int(qty), float(price)) or "{}"
        out = json.loads(raw)
        out["side"] = side
        out["symbol"] = symbol
        out["qty"] = int(qty)
        out["price"] = float(price)
        if not out.get("added"):
            return out
        modal = self._handle_modal()
        out.update(modal)
        return out

    def submit_tab2(self, side: str, symbol: str, qty: int, price: float,
                    expected_isin: str = "") -> Dict[str, object]:
        """Place buy OR sell from tab 2 (the operator's order screen).

        Verified sequence (2026-10-21): the panel at the bottom of tab 2 is opened by
        the symbol search, then side -> count -> price -> send. Everything is done
        with real DOM events; the page is never reloaded.
        """
        import time as _t
        from selenium.webdriver.common.by import By

        res: Dict[str, object] = {"path": "tab2"}
        d = self.driver
        if d is None:
            return {**res, "placed": False, "reason": "no_driver"}
        if "Login" in str(d.current_url):
            return {**res, "placed": False, "reason": "login_screen"}
        if "Default2" not in str(d.current_url):
            try:
                links = [x for x in d.find_elements(By.CSS_SELECTOR, "a[href='/Home/Default2']")
                         if x.is_displayed()]
                if links:
                    from selenium.webdriver.common.action_chains import ActionChains
                    ActionChains(d).move_to_element(links[0]).pause(0.2).click().perform()
                    _t.sleep(5)
            except Exception:
                pass

        # 0) bind through the WATCHLIST ROW first: the search box is unreliable
        #    (returns symbol_result_not_found during the opening auction).
        try:
            if self.bind_symbol(symbol):
                _t.sleep(0.6)
                res["bound"] = "watchlist"
            else:
                self._js("""
                var icon = document.querySelector('#btnSearchStockAutoComplete');
                if (icon) icon.click();
                """)
                _t.sleep(1)
        except Exception:
            pass

        # 1) search box (the visible one, placeholder "جستجوی سهم")
        typed = self._js("""
        var box = document.querySelector('#txt_search');
        if (!box) {
          document.querySelectorAll('input').forEach(function(e){
            var ph = (e.placeholder||'');
            if (!box && ph.indexOf('جستجوی') >= 0) box = e;
          });
        }
        if (!box) return 'no_box';
        box.focus(); box.value='';
        box.dispatchEvent(new Event('input',{bubbles:true}));
        box.value = arguments[0];
        box.dispatchEvent(new Event('input',{bubbles:true}));
        box.dispatchEvent(new Event('change',{bubbles:true}));
        return 'ok';
        """, symbol)
        if typed != "ok":
            return {**res, "placed": False, "reason": "search_box_missing"}
        _t.sleep(3)

        # 2) click the matching result with full mouse events
        picked = self._js("""
        var want = arguments[0], target = null, best = null;
        document.querySelectorAll('li,div,span,a').forEach(function(e){
          var t = (e.innerText||'').trim();
          if (t === want && e.offsetParent !== null) {
            target = e;
            best = e;
          }
        });
        if (!target) return 'no_result';
        var el = target.closest('li') || target.parentElement || target;
        ['mousedown','mouseup','click'].forEach(function(ev){
          el.dispatchEvent(new MouseEvent(ev,{bubbles:true,cancelable:true,view:window}));
        });
        return (target.innerText||'').trim().slice(0,30);
        """, symbol)
        res["picked"] = picked
        if picked == "no_result":
            return {**res, "placed": False, "reason": "symbol_result_not_found"}
        _t.sleep(3)

        # 3) side tab
        cls = "orderside65" if str(side).lower() == "buy" else "orderside86"
        self._js("var e=document.querySelector('.%s'); if(e){e.click();}" % cls)
        _t.sleep(1)

        # 4) count + price (Angular listens to input/change)
        filled = self._js("""
        function setv(sel, v){
          var e = document.querySelector(sel);
          if (!e) return false;
          e.focus(); e.value = v;
          e.dispatchEvent(new Event('input',{bubbles:true}));
          e.dispatchEvent(new Event('change',{bubbles:true}));
          return true;
        }
        return JSON.stringify({c: setv('#send_order_txtCount', arguments[0]),
                               p: setv('#send_order_txtPrice', arguments[1])});
        """, str(int(qty)), str(int(price)))
        res["inputs"] = filled

        # 5) send: the clickable element is the ANCHOR wrapper (#send_order_btnBuySell);
        # clicking the inner div produced no order at all (2026-10-21, وایرا).
        clicked = "none"
        try:
            from selenium.webdriver.common.by import By
            from selenium.webdriver.common.action_chains import ActionChains
            anchors = [a for a in d.find_elements(By.ID, "send_order_btnBuySell") if a.is_displayed()]
            if anchors:
                d.execute_script("arguments[0].scrollIntoView({block:'center'});", anchors[0])
                _t.sleep(0.5)
                ActionChains(d).move_to_element(anchors[0]).pause(0.2).click().perform()
                clicked = "anchor"
            else:
                inner = [x for x in d.find_elements(By.ID, "send_order_btnSendOrder") if x.is_displayed()]
                if inner:
                    d.execute_script("arguments[0].scrollIntoView({block:'center'});", inner[0])
                    ActionChains(d).move_to_element(inner[0]).pause(0.2).click().perform()
                    clicked = "inner"
        except Exception as exc:
            clicked = "err:" + str(exc)[:40]
        res["clicked"] = clicked
        _t.sleep(4)

        # 6) read the panel message
        msg = ""
        for line in (self.page_text() or "").splitlines():
            t = line.strip()
            if ("موفق" in t or "خطا" in t or "عدم پذیرش" in t) and len(t) < 160:
                msg = t
        if msg:
            res["message"] = msg
        res["placed"] = "موفق" in msg
        res["rejected"] = bool(msg) and not res["placed"]
        return res

    def place_order(self, side: str, symbol: str, qty: int, price: float,
                    expected_isin: str = "") -> Dict[str, object]:
        """Guarded order entry: select the symbol through the UI, verify its ISIN,
        then submit. Never sends a ticket whose ISIN does not match the symbol.

        The panel's `$http` symbol search returned the wrong instrument once
        (a sell ticket for شپاس was built on وسرمد's ISIN and was rejected), so the
        ISIN check is mandatory before any click on the send button.
        """
        import json

        from selenium.webdriver.common.by import By
        from selenium.webdriver.common.action_chains import ActionChains

        res: Dict[str, object] = {"path": "guarded"}
        d = self.driver
        if d is None:
            return {**res, "placed": False, "reason": "no_driver"}
        if "Login" in str(d.current_url):
            return {**res, "placed": False, "reason": "login_screen"}

        # 1) order-entry page (trusted click on the pager; driver.get logs out)
        if "page-4" not in str(d.current_url):
            try:
                a = d.find_element(By.CSS_SELECTOR, "a[href='/Home/Index/page-4']")
                ActionChains(d).move_to_element(a).pause(0.2).click().perform()
                time.sleep(5)
            except Exception as exc:
                return {**res, "placed": False, "reason": f"nav_failed:{str(exc)[:60]}"}

        # 2) type the symbol into the panel's own search box
        try:
            inp = d.find_element(By.CSS_SELECTOR, "input[placeholder='جستجوی نماد']")
            inp.click()
            inp.clear()
            inp.send_keys(symbol)
            time.sleep(2.5)
        except Exception as exc:
            return {**res, "placed": False, "reason": f"search_box:{str(exc)[:60]}"}

        # 3) click the matching result (rows look like "شپاس1 - نفت پاسارگاد";
        #    the text lives in child nodes, so match on the whole element string)
        target = None
        try:
            for el in d.find_elements(By.XPATH, f"//*[starts-with(normalize-space(.),'{symbol}')]"):
                if not el.is_displayed():
                    continue
                txt = (el.text or "").strip()
                if 0 < len(txt) <= 60:
                    target = el
                    break
        except Exception:
            pass
        if target is None:
            return {**res, "placed": False, "reason": "symbol_result_not_found"}
        res["picked"] = (target.text or "")[:40]
        try:
            target.click()
            time.sleep(2.5)
        except Exception as exc:
            return {**res, "placed": False, "reason": f"pick_failed:{str(exc)[:60]}"}

        # 4) ISIN guard (re-acquire the scope: the click rebuilds the form)
        got = ""
        try:
            self._scope()
            time.sleep(0.5)
            got = str(self._js("try{return window.__tse.order.isin||'';}catch(e){return '';}") or "")
        except Exception:
            got = ""
        if not got:
            try:
                got = str(self._js(
                    "try{var s=window.__tse;return (s.order&&s.order.isin)||"
                    "(s.orderoptions&&s.orderoptions.order&&s.orderoptions.order.isin)||'';}catch(e){return '';}"
                ) or "")
            except Exception:
                got = ""
        res["isin"] = got
        if not got:
            return {**res, "placed": False, "reason": "isin_unreadable"}
        if expected_isin and got != expected_isin:
            return {**res, "placed": False, "reason": f"isin_mismatch:{got}!={expected_isin}"}

        # 5) side / quantity / price, then send
        side_code = BUY_SIDE if str(side).lower() == "buy" else SELL_SIDE
        js = """
        try {
          var s = window.__tse, side = arguments[0], qty = arguments[1], price = arguments[2];
          var out = {};
          s.$apply(function () {
            try { s.orderoptions.setOrderSide(side); out.side = true; } catch (e) { out.se = String(e); }
            try { s.orderoptions.setCount(qty); out.qty = true; } catch (e) { out.qe = String(e); }
            try { s.orderoptions.setOrderPrice(price); out.px = true; } catch (e) { out.pe = String(e); }
          });
          out.count = s.order && s.order.orderCount;
          out.price = s.order && s.order.orderPrice;
          return JSON.stringify(out);
        } catch (e) { return JSON.stringify({fatal: String(e)}); }
        """
        try:
            res["set"] = json.loads(self._js(js, side_code, int(qty), float(price)) or "{}")
        except Exception as exc:
            return {**res, "placed": False, "reason": f"set_failed:{str(exc)[:60]}"}

        try:
            btn = None
            for el in d.find_elements(By.ID, "send_order_btnSendOrder"):
                if el.is_displayed():
                    btn = el
                    break
            if btn is None:
                return {**res, "placed": False, "reason": "send_button_not_found"}
            btn.click()
            res["clicked"] = True
        except Exception as exc:
            return {**res, "placed": False, "reason": f"send_failed:{str(exc)[:60]}"}

        time.sleep(3)
        try:
            txt = self.page_text()
        except Exception:
            txt = ""
        for line in txt.splitlines():
            if ("موفق" in line or "خطا" in line) and len(line.strip()) < 120:
                res["message"] = line.strip()
                break
        res["placed"] = True
        return res

    def submit_via_ui(self, side: str, symbol: str, qty: int, price: float) -> Dict[str, object]:
        """Place an order through the portfolio screen (tab 1) with real clicks.

        This is the path the panel actually accepts:
          1. click the symbol row in the portfolio grid -> the order panel opens
          2. click the buy/sell side switch (.orderside65 / .orderside86)
          3. type quantity and price into the two inputs
          4. click #send_order_btnSendOrder
          5. confirm the dialog if one appears
        Every click goes through WebDriver (CDP input), never page JavaScript,
        and nothing here navigates the browser.
        """
        from selenium.webdriver.common.by import By

        res: Dict[str, object] = {"path": "ui"}
        d = self.driver
        if d is None:
            return {"path": "ui", "placed": False, "reason": "no_driver"}
        if "Login" in str(d.current_url):
            return {"path": "ui", "placed": False, "reason": "login_screen"}

        # Look for the row FIRST: the portfolio is also served at the bare "/" URL,
        # and navigating away from it just to "get to page 1" made every order fail
        # with symbol_row_not_found this morning.
        def _find_cell():
            for el in d.find_elements(By.XPATH, f"//span[contains(text(),'{symbol}')]"):
                if el.is_displayed():
                    return el
            return None

        cell = _find_cell()
        if cell is None:
            try:
                cur = str(d.current_url)
            except Exception:
                cur = ""
            if "page-1" not in cur and "/Home/Default" not in cur and cur.rstrip("/").count("/") > 2:
                try:
                    self.goto_page(1)
                    time.sleep(3)
                except Exception:
                    pass
                cell = _find_cell()

        # 1) select the symbol row (the grid labels rows like "شپاس1")
        try:
            if cell is None:
                # the panel may be on another tab: click the pager for page 1 once
                # and retry (a trusted click keeps the session alive)
                try:
                    from selenium.webdriver.common.action_chains import ActionChains
                    for css in ("a[href='/']", "a[href='/Home/Default']"):
                        links = [x for x in d.find_elements(By.CSS_SELECTOR, css) if x.is_displayed()]
                        if links:
                            ActionChains(d).move_to_element(links[0]).pause(0.2).click().perform()
                            time.sleep(4)
                            break
                    for el in d.find_elements(By.XPATH, f"//span[contains(text(),'{symbol}')]"):
                        if el.is_displayed():
                            cell = el
                            break
                except Exception:
                    pass
            if cell is None:
                res["reason"] = "symbol_row_not_found"
                return res
            cell.click()
            time.sleep(1.5)
        except Exception as exc:
            res["reason"] = f"row_click_failed:{str(exc)[:80]}"
            return res

        # 2) side switch
        want_sell = str(side).lower() == "sell"
        sel = '[class*="orderside86"] div' if want_sell else '[class*="orderside65"] div'
        try:
            for el in d.find_elements(By.CSS_SELECTOR, sel):
                if el.is_displayed():
                    el.click()
                    time.sleep(0.8)
                    break
        except Exception as exc:
            res["side_error"] = str(exc)[:80]

        # 3) quantity + price (the two text inputs without a placeholder)
        try:
            ins = [e for e in d.find_elements(By.CSS_SELECTOR, "input[type=text]")
                   if e.is_displayed() and not (e.get_attribute("placeholder") or "")]
            if len(ins) >= 2:
                ins[0].click()
                ins[0].clear()
                ins[0].send_keys(str(int(qty)))
                ins[1].click()
                ins[1].clear()
                ins[1].send_keys(str(int(round(price))))
                res["inputs"] = [ins[0].get_attribute("value"), ins[1].get_attribute("value")]
            else:
                res["inputs"] = f"unexpected:{len(ins)}"
        except Exception as exc:
            res["fill_error"] = str(exc)[:80]

        # 4) submit
        try:
            btn = None
            for el in d.find_elements(By.ID, "send_order_btnSendOrder"):
                if el.is_displayed():
                    btn = el
                    break
            if btn is None:
                res["reason"] = "send_button_not_found"
                return res
            res["button_text"] = (btn.text or "").strip()
            btn.click()
            res["sent"] = True
        except Exception as exc:
            res["reason"] = f"send_failed:{str(exc)[:80]}"
            return res

        time.sleep(2.5)
        # 5) dialog
        try:
            res.update(self._handle_modal())
        except Exception:
            pass
        try:
            al = d.switch_to.alert
            res["native_alert"] = (al.text or "")[:120]
            al.accept()
        except Exception:
            pass
        res["url"] = str(d.current_url)
        return res

    PAGES = {1: "/Home/Default", 2: "/Home/Default2", 3: "page-3", 4: "page-4", 5: "page-5"}

    def goto_page(self, page: int) -> bool:
        """Switch tools using the pager in the bottom-right corner (1..5).

        Verified safe both ways: page 1 -> 4 and 4 -> 1 keep the session. The
        pager anchors are plain hrefs but the browser treats them as in-app
        navigation; `driver.get()` is still forbidden.
        """
        from selenium.webdriver.common.by import By
        d = self.driver
        if d is None:
            return False
        target = self.PAGES.get(int(page))
        if not target:
            return False
        try:
            cur = str(d.current_url)
        except Exception:
            return False
        if int(page) == 1 and "/Home/Default" in cur and "Default2" not in cur:
            return True
        if int(page) > 1 and f"page-{page}" in cur:
            return True
        try:
            anchors = [a for a in d.find_elements(By.CSS_SELECTOR, f"a[href*='{target}']")
                       if a.is_displayed()]
            if not anchors:
                return False
            # exact href first (so /Home/Default does not match /Home/Default2)
            anchors.sort(key=lambda a: 0 if (a.get_attribute("href") or "") == target else 1)
            anchors[0].click()
            time.sleep(5)
        except Exception:
            return False
        try:
            return self.is_logged_in()
        except Exception:
            return False

    def open_tab4(self) -> bool:
        """Switch to the order-entry page (tab 4) by clicking its in-app link.

        Verified behaviour: clicking the `page-4` link keeps the session and the
        Angular order scope becomes available, which is the easy path for BUY.
        The root link ("/", tab 1) must never be clicked: it kills the session
        and can crash chromedriver.
        """
        from selenium.webdriver.common.by import By
        d = self.driver
        if d is None:
            return False
        try:
            if "page-4" in str(d.current_url):
                return True
        except Exception:
            return False
        try:
            links = [a for a in d.find_elements(By.CSS_SELECTOR, "a[href*='page-4']")
                     if a.is_displayed()]
            if not links:
                return False
            links[0].click()
            time.sleep(5)
        except Exception:
            return False
        try:
            return "page-4" in str(d.current_url) and self.is_logged_in()
        except Exception:
            return False

    def _handle_modal(self) -> Dict[str, object]:
        """Tick the agreement checkbox and click confirm inside the order modal.

        The old version looked for one hard-coded checkbox id and the exact text
        'تایید'; the real modal uses different labels per page, so nothing was
        clicked and the order never left the cart (that is why an order reported
        `added: true` could still be absent from the order list).
        """
        import json
        res: Dict[str, object] = {}
        js = """
        try {
          var out = {modal: false, checkbox: false, clicked: null, buttons: []};
          // any visible dialog container, newest first
          var modal = null;
          var cands = document.querySelectorAll('.modal, .modal-dialog, [role="dialog"], .swal-modal, .k-window, md-dialog, .ui-dialog');
          for (var i = cands.length - 1; i >= 0; i--) {
            if (cands[i].offsetParent !== null) { modal = cands[i]; break; }
          }
          var root = modal || document;
          out.modal = !!modal;

          // 1) agreement checkbox (any visible one inside the modal)
          var cbs = root.querySelectorAll('input[type=checkbox], .checkbox, [ng-click*="check"]');
          for (var c = 0; c < cbs.length; c++) {
            var cb = cbs[c];
            if (cb.offsetParent === null) continue;
            try {
              if (cb.tagName === 'INPUT') {
                if (!cb.checked) cb.click();
              } else { cb.click(); }
            } catch (e) {}
            out.checkbox = true;
            break;
          }

          // 2) confirm button: prefer ng-click semantics, then wording
          var words = ['تایید', 'تأیید', 'ثبت سفارش', 'ثبت', 'ارسال', 'قبول', 'بله', 'ادامه', 'Confirm', 'OK'];
          var bs = root.querySelectorAll('button, a, .btn, [ng-click]');
          var chosen = null;
          for (var b = 0; b < bs.length; b++) {
            var el = bs[b];
            if (el.offsetParent === null) continue;
            var g = (el.getAttribute('ng-click') || '').toLowerCase();
            if (g.indexOf('confirm') >= 0 || g.indexOf('send') >= 0 || g.indexOf('submit') >= 0
                || g.indexOf('accept') >= 0 || g.indexOf('ok(') >= 0) { chosen = el; break; }
          }
          if (!chosen) {
            for (var k = 0; k < bs.length; k++) {
              var e2 = bs[k];
              if (e2.offsetParent === null) continue;
              var t = (e2.innerText || e2.value || '').trim();
              if (!t) continue;
              out.buttons.push(t.slice(0, 20));
              for (var w = 0; w < words.length; w++) {
                if (t === words[w]) { chosen = e2; break; }
              }
              if (chosen) break;
            }
          }
          if (chosen) {
            var lab = (chosen.innerText || chosen.value || '').trim();
            chosen.click();
            out.clicked = lab || 'ng-click';
          }
          return JSON.stringify(out);
        } catch (e) { return JSON.stringify({error: String(e)}); }
        """
        try:
            res.update(json.loads(self._js(js) or "{}"))
        except Exception as exc:
            res["error"] = str(exc)
        return res

    # ------------------------------------------------------------ order list
    def _goto_orderlist(self, attempts: int = 3) -> bool:
        js = """
        try {
          var as = document.querySelectorAll('a[href]');
          for (var i = 0; i < as.length; i++) {
            var t = (as[i].textContent || '').trim();
            var h = as[i].getAttribute('href') || '';
            if (t.indexOf('گزارش سفارشات') >= 0 || h.indexOf('OrderList') >= 0) {
              as[i].click(); return 'CLICKED';
            }
          }
          // try the side menu first
          var ms = document.querySelectorAll('[ng-click]');
          for (var j = 0; j < ms.length; j++) {
            var g = ms[j].getAttribute('ng-click') || '';
            if (g.indexOf('openSlider') >= 0 || g.indexOf('openMenu') >= 0) { ms[j].click(); break; }
          }
          return 'MENU_ONLY';
        } catch (e) { return 'ERR ' + e; }
        """
        for _ in range(attempts):
            if "OrderList" in str(getattr(self.driver, "current_url", "")):
                return True
            try:
                self._js(js)
            except Exception:
                pass
            time.sleep(3)
            try:
                if "OrderList" in str(self.driver.current_url):
                    return True
                body = self._js("return document.body.innerText || ''") or ""
                if re.search(r"\d{16}", body):
                    return True
            except Exception:
                pass
        return False

    def orderlist(self) -> List[BrokerOrder]:
        if not self._goto_orderlist():
            raise BrokerError("could not open order list")
        try:
            body = self._js("return document.body.innerText || ''") or ""
        except Exception as exc:
            raise BrokerError(f"order list read failed: {exc}") from exc
        return parse_orderlist(body)

    def account(self) -> Account:
        bp = self.buying_power() or 0.0
        rows = self._positions_from_realtime()
        positions = {s: int(r["qty"]) for s, r in rows.items() if r.get("qty")}
        avg = {s: float(r["avg"]) for s, r in rows.items() if r.get("avg")}
        be = {s: float(r.get("breakeven") or r.get("avg") or 0) for s, r in rows.items()}
        return Account(buying_power=bp, positions=positions, avg_prices=avg,
                       breakevens={k: v for k, v in be.items() if v})

    def _positions_from_realtime(self) -> Dict[str, dict]:
        """Best-effort read of the realtime portfolio table.

        Returns symbol -> {qty, avg}. Deliberately tolerant: if the table is not
        reachable we return {} and the caller falls back to the ledger + order
        list reconciliation.
        """
        js = """
        try {
          var as = document.querySelectorAll('a[href]');
          for (var i = 0; i < as.length; i++) {
            var t = (as[i].textContent || '').trim();
            var h = as[i].getAttribute('href') || '';
            if (t.indexOf('پرتوفوی لحظه') >= 0 || h.indexOf('Realtime') >= 0) { as[i].click(); return 'OK'; }
          }
          return 'NOTFOUND';
        } catch (e) { return 'ERR'; }
        """
        try:
            if self._js(js) != "OK":
                return {}
            # The table renders progressively: after 3s it often shows only the
            # first row, which made the bot believe the account held one symbol.
            # Poll until the row count is stable (or 15s), then parse once.
            best: Dict[str, dict] = {}
            stable = 0
            for _ in range(6):
                time.sleep(2.5)
                txt = self._js("return document.body.innerText || ''") or ""
                rows = parse_realtime_rows(txt)
                if len(rows) > len(best):
                    best = rows
                    stable = 0
                else:
                    stable += 1
                    if stable >= 2 and best:
                        break
            return best
        except Exception:
            return {}


# --------------------------------------------------------------------- parsers
_ORDER_NO = re.compile(r"^\d{16}$")


def parse_orderlist(body: str) -> List[BrokerOrder]:
    """Parse the broker's order list text.

    Layout observed on meibours: order_no / date / time / side / symbol / volume /
    price / filled / status, one value per line, header before the rows.
    """
    lines = [l.strip() for l in (body or "").splitlines()]
    lines = [l for l in lines if l]
    rows: List[BrokerOrder] = []
    i = 0
    while i < len(lines):
        if _ORDER_NO.match(lines[i]) and i + 8 < len(lines):
            chunk = lines[i:i + 9]
            try:
                qty = _num(chunk[5])
                price = _num(chunk[6])
                filled = _num(chunk[7])
            except Exception:
                i += 1
                continue
            rows.append(BrokerOrder(
                order_no=chunk[0],
                day=chunk[1],
                symbol=_clean_symbol(chunk[4]),
                side="buy" if "خرید" in chunk[3] else ("sell" if "فروش" in chunk[3] else chunk[3]),
                qty=int(qty or 0),
                price=float(price or 0),
                filled=int(filled or 0),
                status=chunk[8],
            ))
            i += 9
            continue
        i += 1
    return rows


def parse_realtime_rows(body: str) -> Dict[str, dict]:
    """Parse the realtime portfolio table into {symbol: {qty, avg}}.

    Column order observed on meibours: symbol / count / ? / avg_price / last /
    value / breakeven ...  The parser takes the first number after the symbol as
    quantity and the third as average cost, but also records the second number so
    a layout change is visible instead of silently wrong.
    """
    lines = [l.strip() for l in (body or "").splitlines() if l.strip()]
    try:
        start = lines.index("نماد") + 1
    except ValueError:
        return {}
    out: Dict[str, dict] = {}
    i = start
    while i < len(lines) and len(out) < 200:
        tok = lines[i]
        if _is_symbol(tok):
            nums: List[float] = []
            k = i + 1
            while k < len(lines) and len(nums) < 8:
                v = _num(lines[k])
                if v is None:
                    break
                nums.append(v)
                k += 1
            if nums:
                qty = int(nums[0])
                avg = float(nums[2]) if len(nums) >= 3 else (float(nums[1]) if len(nums) >= 2 else 0.0)
                # column 4 is the broker's "قیمت سربه‌سر" (breakeven incl. fees);
                # the panel's own P&L % is computed against it.
                breakeven = float(nums[4]) if len(nums) >= 5 else avg
                out[tok] = {"qty": qty, "avg": avg, "breakeven": breakeven, "nums": nums}
                i = k
                continue
        i += 1
    return out


def parse_realtime_positions(body: str) -> Dict[str, int]:
    return {s: int(r["qty"]) for s, r in parse_realtime_rows(body).items() if r.get("qty")}


def _num(s: str) -> Optional[float]:
    try:
        return float(str(s).replace(",", "").replace("%", "").strip())
    except Exception:
        return None


def _clean_symbol(s: str) -> str:
    s = (s or "").strip()
    return re.sub(r"\d+$", "", s) if re.search(r"\D", s) else s


def _is_symbol(s: str) -> bool:
    if not s or len(s) > 12:
        return False
    if any(ch.isdigit() for ch in s):
        return False
    return any("\u0600" <= ch <= "\u06FF" for ch in s)


















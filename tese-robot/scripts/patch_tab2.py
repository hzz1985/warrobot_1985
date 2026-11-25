"""Rewrite submit_tab2 with the interaction sequence proven on 2026-10-21:
search box -> result click (mouse events) -> side tab -> count/price -> send.
"""
from pathlib import Path

P = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\TeseRobot\src\tsebot\broker_selenium.py")
src = P.read_text(encoding="utf-8")

NEW = r'''
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

        # 1) search box (the visible one, placeholder "جستجوی سهم")
        typed = self._js("""
        var box = null;
        document.querySelectorAll('input').forEach(function(e){
          var ph=(e.placeholder||'');
          var r=e.getBoundingClientRect();
          if (!box && (ph.indexOf('جستجوی')>=0) && r.width>0) box=e;
        });
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

        # 5) send
        clicked = self._js("""
        var s = document.querySelector('#send_order_btnSendOrder');
        if (!s) return 'no_btn';
        s.scrollIntoView({block:'center'});
        s.click();
        return 'clicked';
        """)
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
'''

start = src.index("    def submit_tab2(self")
end = src.index("    def place_order(self")
src = src[:start] + NEW.strip("\n") + "\n\n" + src[end:]
P.write_text(src, encoding="utf-8")
print("submit_tab2 rewritten")

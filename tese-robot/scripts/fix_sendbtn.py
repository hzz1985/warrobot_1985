"""Click the anchor wrapper of the send button (the inner div does nothing)."""
from pathlib import Path

P = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\TeseRobot\src\tsebot\broker_selenium.py")
s = P.read_text(encoding="utf-8")

old = '''        # 5) send
        clicked = self._js("""
        var s = document.querySelector('#send_order_btnSendOrder');
        if (!s) return 'no_btn';
        s.scrollIntoView({block:'center'});
        s.click();
        return 'clicked';
        """)
        res["clicked"] = clicked'''

new = '''        # 5) send: the clickable element is the ANCHOR wrapper (#send_order_btnBuySell);
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
        res["clicked"] = clicked'''

if old in s:
    s = s.replace(old, new)
    P.write_text(s, encoding="utf-8")
    print("patched send step")
else:
    print("pattern not found")

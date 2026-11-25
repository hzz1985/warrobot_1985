"""Place the ShaPas sell with TRUSTED WebDriver clicks (not JS .click()).

The panel ignores synthetic `element.click()` calls made from page JavaScript;
WebDriver's click goes through CDP input and produces isTrusted=true events.
"""
import sys
import time

sys.path.insert(0, r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\TeseRobot\src")

from selenium.webdriver.common.by import By  # noqa: E402
from selenium.webdriver.support.ui import WebDriverWait  # noqa: E402
from selenium.webdriver.support import expected_conditions as EC  # noqa: E402

from tsebot.broker_selenium import SeleniumBroker  # noqa: E402

QTY = 258
PRICE = 55880

b = SeleniumBroker()
b.connect()
d = b.driver
print("url:", d.current_url)

# 1) order-entry page
if "page-4" not in d.current_url:
    try:
        link = d.find_element(By.CSS_SELECTOR, "a[href*='page-4']")
        link.click()
        time.sleep(4)
    except Exception as exc:
        print("nav fail:", str(exc)[:120])
print("url2:", d.current_url)

# 2) sell side
try:
    side = WebDriverWait(d, 10).until(
        EC.element_to_be_clickable((By.CSS_SELECTOR, "div[ng-click='orderoptions.setOrderSide(86)']")))
    side.click()
    print("side=sell clicked")
except Exception as exc:
    print("side fail:", str(exc)[:140])

time.sleep(1)

# 3) quantity + price inputs (the two text inputs without a placeholder)
inputs = [e for e in d.find_elements(By.CSS_SELECTOR, "input[type=text]")
          if e.is_displayed() and not (e.get_attribute("placeholder") or "")]
print("inputs:", len(inputs))
if len(inputs) >= 2:
    q = inputs[0]
    p = inputs[1]
    q.click()
    q.clear()
    q.send_keys(str(QTY))
    p.click()
    p.clear()
    p.send_keys(str(PRICE))
    print("filled qty/price")
else:
    print("input layout unexpected")

time.sleep(1)

# 4) submit (trusted click on the real anchor)
try:
    btn = WebDriverWait(d, 10).until(
        EC.element_to_be_clickable((By.CSS_SELECTOR, "a[ng-click='orderoptions.addOrder()']")))
    d.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
    time.sleep(0.5)
    btn.click()
    print("submit clicked (trusted)")
except Exception as exc:
    print("submit fail:", str(exc)[:160])

time.sleep(3)

# 5) confirmation modal, if any
for sel in ["input[type=checkbox]", "button", ".btn"]:
    for el in d.find_elements(By.CSS_SELECTOR, sel):
        try:
            if not el.is_displayed():
                continue
            txt = (el.text or "").strip()
            if sel == "input[type=checkbox]":
                if not el.is_selected():
                    el.click()
                    print("checkbox ticked")
            elif txt in ("تایید", "تأیید", "ثبت", "ثبت سفارش", "بله", "ارسال"):
                el.click()
                print("confirmed:", txt)
        except Exception:
            pass

time.sleep(2)
print("url3:", d.current_url)

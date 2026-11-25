"""Wire the saved contract into the code and the daily self-check.

1) intraday_exit.py + preopen_exits.py use profit_transfer.transfer (single source)
2) preflight.py records the buying power + candidate-list verdict explicitly
3) selfcheck_rules.py verifies the ten clauses of docs/TRADING_SPEC.md
"""
from pathlib import Path

WAR = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot")

# ------------------------------------------------- 1) shared profit transfer
ie = WAR / "intraday_exit.py"
s = ie.read_text(encoding="utf-8")
if "import profit_transfer" not in s:
    s = s.replace('''def transfer_profit_to_afran(profit_rial: float) -> dict:''',
'''def transfer_profit_to_afran(profit_rial: float) -> dict:
    import profit_transfer
    return profit_transfer.transfer(profit_rial, source="intraday_exit")


def _legacy_transfer(profit_rial: float) -> dict:''')
    ie.write_text(s, encoding="utf-8")
    print("intraday_exit -> profit_transfer")

pe = WAR / "preopen_exits.py"
t = pe.read_text(encoding="utf-8")
if "profit_transfer" not in t:
    t = t.replace('''        out["placed"].append(rec)''',
'''        # realised profit goes to افران (contract clause 6)
        if rec["submitted"]:
            try:
                import profit_transfer as _pt
                profit = (float(rec["price"]) - float(p.get("avg_cost") or 0)) * int(rec["qty"])
                rec["afran_transfer"] = _pt.transfer(profit, source="preopen_exit")
            except Exception as exc:
                rec["afran_error"] = str(exc)[:80]
        out["placed"].append(rec)''')
    pe.write_text(t, encoding="utf-8")
    print("preopen_exits -> profit_transfer")

# --------------------------------------------- 2) preflight: power + list gate
pf = WAR / "preflight.py"
p = pf.read_text(encoding="utf-8")
if "buying_power" not in p:
    p = p.replace('''    out["ready"] = (bool(out["login"].get("ok")) and out["free_balance"] > 5_000_000
                    and bool(out["candidates"]) and all(c["ok"] for c in out["candidates"]))''',
'''    out["buying_power"] = out["free_balance"]
    out["candidate_list_ok"] = bool(out["candidates"]) and all(c["ok"] for c in out["candidates"])
    out["ready"] = (bool(out["login"].get("ok")) and out["free_balance"] > 5_000_000
                    and out["candidate_list_ok"])
    if not out["candidate_list_ok"]:
        out["blocking_reason"] = "invalid or empty candidate list"''')
    pf.write_text(p, encoding="utf-8")
    print("preflight: buying power + explicit list verdict")

# ------------------------------------------------- 3) selfcheck: contract rules
sc = WAR / "selfcheck_rules.py"
c = sc.read_text(encoding="utf-8")
if "TRADING_SPEC" not in c:
    c = c.replace('''    # live web check: catches a 500 that a static grep can never see''',
'''    # ---- contract (docs/TRADING_SPEC.md) clauses ----
    flow_js = (BASE / "pw" / "flow.js").read_text(encoding="utf-8")
    add("بند۱: قیمت فیلد و قیمت ثبتشده بررسی میشود",
        "setNumberField" in flow_js and "placed_price" in flow_js,
        "pw/flow.js")
    add("بند۱: لغو سفارش با قیمت نادرست و تلاش مجدد",
        "mismatch_fixed" in flow_js, "pw/flow.js")
    add("بند۲: preflight قدرت خرید و لیست را ثبت میکند",
        "buying_power" in (BASE / "preflight.py").read_text(encoding="utf-8"),
        "preflight.py")
    add("بند۴: چرخش ۱۰:۰۰ با مسیر لغو واقعی",
        "cancel_orders" in (BASE / "rebalance.py").read_text(encoding="utf-8"),
        "rebalance.py")
    add("بند۵: سهم مساوی (هدف = کل دارایی/۳)",
        "target_each" in (BASE / "order_candidates.py").read_text(encoding="utf-8"),
        "order_candidates.py")
    add("بند۶: انتقال سود به افران (ماژول مرکزی)",
        (BASE / "profit_transfer.py").exists()
        and "import profit_transfer" in (BASE / "intraday_exit.py").read_text(encoding="utf-8"),
        "profit_transfer.py")
    add("بند۷: تقویم بازار (پنجشنبه/جمعه تعطیل)",
        "market_cal" in (BASE / "scheduler.py").read_text(encoding="utf-8"),
        "market_cal.py")
    add("بند۸: ایمیل یک‌باره",
        "send_once" in (BASE / "ready_check.py").read_text(encoding="utf-8"),
        "ready_check.py")
    add("قرارداد مکتوب (TRADING_SPEC.md)",
        (BASE / "docs" / "TRADING_SPEC.md").exists(), "docs/TRADING_SPEC.md")

    # live web check: catches a 500 that a static grep can never see''')
    sc.write_text(c, encoding="utf-8")
    print("selfcheck: 9 contract clauses added")

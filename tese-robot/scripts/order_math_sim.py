"""Exact order-sizing maths + a live buy/sell simulation.

Formulas
--------
buy shares      n   = floor(C / (P * (1 + fb)))          fb = 1.5%
buy tickets         = ceil(n / Qmax)                     Qmax = «آستانه حجم»
ticket sizes        = [Qmax, Qmax, ..., n mod Qmax]
buy cost            = n * P * (1 + fb)

sell gross          = Q * P
sell net            = Q * P * (1 - fs)                   fs = 0.5%
sell tickets        = ceil(Q / Qmax)

round-trip break-even price (net 0):
    P_sell = P_buy * (1 + fb) / (1 - fs)
net return for a given exit:
    r = P_sell * (1 - fs) / (P_buy * (1 + fb)) - 1
price needed for a target net r*:
    P_sell = P_buy * (1 + r*) * (1 + fb) / (1 - fs)

Run: python scripts/order_math_sim.py
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from tsebot import config as config_mod  # noqa: E402
from tsebot.broker_selenium import SeleniumBroker  # noqa: E402
from tsebot.engine import Engine  # noqa: E402
from tsebot.ledger import Ledger  # noqa: E402

FB = 0.015
FS = 0.005


def chunks(total: int, qmax: int) -> list:
    out = []
    left = total
    while left > 0 and len(out) < 50:
        n = min(left, qmax) if qmax > 0 else left
        out.append(n)
        left -= n
    return out


def main() -> None:
    cfg = config_mod.load()
    b = SeleniumBroker(chromedriver=r"C:\TSE_Strategy\chromedriver.exe",
                       debugger_address="127.0.0.1:9333")
    eng = Engine(cfg, b, logger=lambda m: None)
    b.connect()
    led = Ledger(cfg.path("state_dir") / "ledger.jsonl")

    for sym in ("وایرا", "چکاوه"):
        q = eng.market_quote(sym)
        b.bind_symbol(sym)
        lo, qmax = b.order_limits()
        bid, ask = b.best_bid_ask()
        pos = led.get(sym)
        held = int(pos.qty) if pos else 0
        avg = float(pos.avg_cost) if pos else 0.0
        print(f"\n=== {sym} ===")
        print(f"last={q.last:.0f} ceiling={q.ceiling:.0f} floor={q.floor:.0f} "
              f"best_bid={bid:.0f} best_ask={ask:.0f} | آستانه حجم={qmax:,} "
              f"| held={held:,} avg_cost={avg:.1f}")

        cash = 2_364_000_000          # last read of the buying power
        px_buy = ask or q.last
        n = math.floor(cash / (px_buy * (1 + FB)))
        print(f"BUY : cash={cash:,} price={px_buy:.0f} -> n={n:,} "
              f"tickets={chunks(n, qmax)} cost={n * px_buy * (1 + FB):,.0f}")

        if held:
            px_sell = bid or q.last
            print(f"SELL: qty={held:,} price={px_sell:.0f} -> tickets={chunks(held, qmax)} "
                  f"net={held * px_sell * (1 - FS):,.0f}")
            if avg:
                be = avg * (1 + FB) / (1 - FS)
                tgt6 = avg * (1.06) * (1 + FB) / (1 - FS)
                tgt9 = avg * (1.09) * (1 + FB) / (1 - FS)
                stop3 = avg * (0.97) * (1 + FB) / (1 - FS)
                r_now = px_sell * (1 - FS) / (avg * (1 + FB)) - 1
                print(f"      break-even={be:,.0f} (+6%={tgt6:,.0f} +9%={tgt9:,.0f} "
                      f"-3%={stop3:,.0f}) | net now={r_now * 100:+.2f}%")


if __name__ == "__main__":
    main()

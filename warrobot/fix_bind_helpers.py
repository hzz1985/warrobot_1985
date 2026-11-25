from pathlib import Path

P = Path(r"C:\Users\Administrator\Desktop\DeepSeek DeskTop\WarRobot\order_candidates.py")
s = P.read_text(encoding="utf-8")

broken_start = "    # ---- holdings-first equal weight (holdings_first) --------------------"
broken_end = 'MANUAL_ONLY = {"افران"}'
i = s.find(broken_start)
j = s.find(broken_end)
if i < 0 or j < 0:
    raise SystemExit("broken chunk not found")
j += len(broken_end)

fixed = broken_start + "\n" + broken_end
s = s[:i] + fixed + s[j:]

helper = '''
BIND_FAILS = Path(__file__).resolve().parent / "state" / "bind_failures.json"


def _bind_failures() -> dict:
    try:
        return json.loads(BIND_FAILS.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _note_bind_failure(symbol: str) -> None:
    data = _bind_failures()
    rec = data.get(symbol) or {"count": 0, "last": ""}
    rec["count"] = int(rec.get("count") or 0) + 1
    rec["last"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    data[symbol] = rec
    try:
        BIND_FAILS.parent.mkdir(parents=True, exist_ok=True)
        BIND_FAILS.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                              encoding="utf-8")
    except Exception:
        pass


def _bind_bad(symbol: str) -> bool:
    return int((_bind_failures().get(symbol) or {}).get("count") or 0) >= 3

'''

anchor = "def main("
if "BIND_FAILS = Path(__file__)" not in s:
    s = s.replace(anchor, helper.lstrip("\n") + "\n" + anchor, 1)

P.write_text(s, encoding="utf-8")
print("bind helpers moved to module level")

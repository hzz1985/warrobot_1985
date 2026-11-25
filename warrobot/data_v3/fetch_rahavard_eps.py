"""Point-in-time EPS history per symbol from rahavard365's public API (Codal itself is not reachable at scale:
search.codal.ir answers 429 and report pages show a captcha after a few dozen views).
  1) /api/v2/search?keyword=<sym>  -> entity_id of the exact trade_symbol (type سهام)
  2) /api/v2/asset/<id>/eps         -> every published EPS report: date_time (publish day), fiscal_year,
                                       fiscal_period (3/6/9/12 months, 0 = AGM), pure_realized_eps (cumulative), capital
Cache: fund/eps/<sym>.json, fund/asset_ids.json"""
import json, os, sys, time, urllib.request, urllib.parse, concurrent.futures as cf
TSEDATA = os.environ.get("TSEDATA", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tsedata"))
D = TSEDATA + "/fund"
os.makedirs(D + "/eps", exist_ok=True)
H = {"User-Agent": "Mozilla/5.0"}
def n(s): return s.replace("ي", "ی").replace("ك", "ک").replace(" ", "").strip()
def get(url):
    err = ""
    for a in range(4):
        try:
            return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=H), timeout=40).read())
        except Exception as e:
            err = str(e); time.sleep(1 + 2 * a)
    raise RuntimeError(err)
def work(sym):
    fp = f"{D}/eps/{sym}.json"
    if os.path.exists(fp): return sym, "skip"
    try:
        d = get("https://rahavard365.com/api/v2/search?keyword=" + urllib.parse.quote(sym)).get("data") or []
        c = [x for x in d if n(x.get("trade_symbol", "")) == n(sym) and x.get("entity_type") == "exchange.asset"]
        c.sort(key=lambda x: (x.get("type") != "سهام", x.get("unlisted_item", False)))
        if not c:
            json.dump({"sym": sym, "id": None}, open(fp, "w"), ensure_ascii=False); return sym, "noid"
        aid = c[0]["entity_id"]
        e = get(f"https://rahavard365.com/api/v2/asset/{aid}/eps").get("data") or {}
        json.dump({"sym": sym, "id": aid, "type": c[0].get("type"), "eps": e}, open(fp, "w"), ensure_ascii=False)
        return sym, "ok"
    except Exception as ex:
        return sym, "ERR " + str(ex)[:80]
if __name__ == "__main__":
    syms = json.load(open(sys.argv[1]))
    with cf.ThreadPoolExecutor(int(sys.argv[2]) if len(sys.argv) > 2 else 4) as ex:
        for s, r in ex.map(work, syms):
            if r != "skip": print(s, r, flush=True)
    print("DONE", flush=True)

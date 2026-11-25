"""tsetmc instrument info (sector code/name, shares outstanding zTitad, market) -> fund/instinfo/<ins>.json"""
import json, os, sys, time, urllib.request, concurrent.futures as cf
TSEDATA = os.environ.get("TSEDATA", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tsedata"))
D = TSEDATA
os.makedirs(D + "/fund/instinfo", exist_ok=True)
def work(ins):
    fp = f"{D}/fund/instinfo/{ins}.json"
    if os.path.exists(fp): return ins, "skip"
    err = ""
    for a in range(5):
        try:
            r = urllib.request.Request(f"https://cdn.tsetmc.com/api/Instrument/GetInstrumentInfo/{ins}",
                                       headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.tsetmc.com/"})
            d = json.loads(urllib.request.urlopen(r, timeout=30).read())["instrumentInfo"]
            json.dump(d, open(fp, "w"), ensure_ascii=False); return ins, "ok"
        except Exception as e:
            err = str(e); time.sleep(1 + a)
    return ins, "ERR " + err[:60]
if __name__ == "__main__":
    codes = list(json.load(open(D + "/clienttype/ins_map.json")))
    with cf.ThreadPoolExecutor(6) as ex:
        for c, r in ex.map(work, codes):
            if r.startswith("ERR"): print(c, r, flush=True)
    print("DONE", flush=True)

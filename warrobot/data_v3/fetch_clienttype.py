"""Fetch TSETMC ClientType (حقیقی/حقوقی) daily history per insCode -> clienttype/{ins}.json (rows from 2022-06)."""
import json, os, sys, time, urllib.request, concurrent.futures as cf
API = "https://cdn.tsetmc.com/api/ClientType/GetClientTypeHistory/{}"
TSEDATA = os.environ.get("TSEDATA", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tsedata"))
D = TSEDATA + "/clienttype"
K = ["recDate", "buy_I_Volume", "buy_N_Volume", "buy_I_Value", "buy_N_Value", "buy_I_Count", "buy_N_Count",
     "sell_I_Volume", "sell_N_Volume", "sell_I_Value", "sell_N_Value", "sell_I_Count", "sell_N_Count"]
def get(code):
    fp = f"{D}/{code}.json"
    if os.path.exists(fp): return code, "skip"
    err = ""
    for a in range(4):
        try:
            req = urllib.request.Request(API.format(code), headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.tsetmc.com/"})
            rows = json.loads(urllib.request.urlopen(req, timeout=40).read()).get("clientType") or []
            rows = [[r.get(k) for k in K] for r in rows if r["recDate"] >= 20220601]
            json.dump(rows, open(fp, "w")); return code, len(rows)
        except Exception as e:
            err = str(e); time.sleep(1 + a * 2)
    return code, "ERR " + err
if __name__ == "__main__":
    codes = list(json.load(open(f"{D}/ins_map.json")))
    t = time.time(); n = 0
    with cf.ThreadPoolExecutor(int(sys.argv[1]) if len(sys.argv) > 1 else 8) as ex:
        for c, r in ex.map(get, codes):
            n += 1
            if isinstance(r, str) and r.startswith("ERR"): print(c, r, flush=True)
    print("done", n, round(time.time() - t))

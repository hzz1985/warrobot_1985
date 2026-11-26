"""Full TSETMC daily history (from 2017-03-21) per insCode -> raw_full/<code>.json  (same 10 fields as fetch.py)."""
import json, os, sys, time, urllib.request, concurrent.futures as cf
API="https://cdn.tsetmc.com/api/ClosingPrice/GetClosingPriceDailyList/{}/0"
codes=json.load(open('/workspace/work/tsedata/universe_codes.json'))
OUT='/workspace/work/tsedata/raw_full'
F=['dEven','priceYesterday','priceFirst','priceMin','priceMax','pClosing','pDrCotVal','zTotTran','qTotTran5J','qTotCap']
T0=time.time(); LIM=float(sys.argv[2]) if len(sys.argv)>2 else 100
def get(code):
    fp=f'{OUT}/{code}.json'
    if os.path.exists(fp): return code,'skip'
    err=''
    for a in range(3):
        if time.time()-T0>LIM: return code,'TIME'
        try:
            req=urllib.request.Request(API.format(code),headers={'User-Agent':'Mozilla/5.0','Referer':'https://www.tsetmc.com/'})
            rows=json.loads(urllib.request.urlopen(req,timeout=25).read()).get('closingPriceDaily') or []
            rows=[[r[k] for k in F] for r in rows if r['dEven']>=20170321]
            json.dump(rows,open(fp,'w'))
            return code,len(rows)
        except Exception as e:
            err=str(e); time.sleep(1)
    return code,'ERR '+err[:60]
SH=int(sys.argv[3]) if len(sys.argv)>3 else 0; NS=int(sys.argv[4]) if len(sys.argv)>4 else 1
items=[c for i,c in enumerate(codes) if i%NS==SH and not os.path.exists(f"{OUT}/{c}.json")]
ok=0;bad=0
with cf.ThreadPoolExecutor(int(sys.argv[1])) as ex:
    for c,r in ex.map(get,items):
        if isinstance(r,int): ok+=1
        elif r!='skip': bad+=1
print('todo',len(items),'ok',ok,'bad',bad,'left',len([c for c in codes if not os.path.exists(f'{OUT}/{c}.json')]),round(time.time()-T0))

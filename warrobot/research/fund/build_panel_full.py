import json, os, glob, pandas as pd, numpy as np
T='/workspace/work/tsedata'
old=pd.read_pickle(T+'/panel.pkl')
meta=old.groupby('ins').agg(sym=('sym','last'),board=('board','last'),current=('current','last')).reset_index()
codes=json.load(open(T+'/universe_codes.json'))
COLS=["dEven","py","o","l","h","c","last","ntr","vol","val"]
fr=[]
miss=[]
for code,(sym,src) in codes.items():
    fp=f'{T}/raw_full/{code}.json'
    if not os.path.exists(fp): miss.append(code); continue
    rows=json.load(open(fp))
    if not rows: continue
    d=pd.DataFrame(rows,columns=COLS); d['ins']=code; fr.append(d)
p=pd.concat(fr,ignore_index=True)
p['date']=pd.to_datetime(p.dEven.astype(str)); p=p.drop(columns='dEven')
p=p.merge(meta,on='ins',how='inner')
# codes missing from raw_full: keep their 2022+ rows from the old panel
oldm=old[old.ins.isin(miss)]
p=pd.concat([p,oldm[p.columns]],ignore_index=True).drop_duplicates(['ins','date'])
p=p.sort_values(['sym','date']).reset_index(drop=True)
print('rows',len(p),'ins',p.ins.nunique(),'syms',p.sym.nunique(),'missing raw',len(miss),'date',p.date.min(),p.date.max())
# duplicate sym on same date (old/new insCode): keep larger value
p=p.sort_values('val').drop_duplicates(['sym','date'],keep='last').sort_values(['sym','date']).reset_index(drop=True)
print('after dedup',len(p))
yr=p[p.ntr>0].groupby(p.date.dt.year).sym.nunique(); print(yr.to_dict())
dd=p[p.ntr>0].groupby('date').size(); 
gaps=dd.index.to_series().diff().dt.days; print('big gaps', gaps[gaps>10].to_dict())
p.to_pickle(T+'/panel_full.pkl')

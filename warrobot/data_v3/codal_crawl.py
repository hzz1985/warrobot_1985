"""Throttled, resumable Codal crawler (runs on the user's server; stdlib only).

search.codal.ir  : letters per symbol (LetterType 6 = financial statements ن-10, 58 = monthly activity ن-30)
excel.codal.ir   : structured report (HTML-as-xls) -> compact extraction (income statement, balance sheet,
                   monthly sales totals). Raw kept gz-compressed for re-extraction.
Politeness: one search request every 5-8 s, one excel request every 3-5 s (separate hosts, separate threads),
exponential backoff on 429 / captcha / errors, 35 min pause after 3 consecutive blocks.  Never parallel per host.
Files: letters/<sym>_<lt>.json, raw/<TracingNo>.html.gz, ext/<TracingNo>.json, crawl.log, status.json, STOP (create to stop)
"""
import json, os, sys, time, random, gzip, re, threading, urllib.request, urllib.parse, datetime, traceback
from html.parser import HTMLParser

D = os.path.dirname(os.path.abspath(__file__))
for sub in ("letters", "raw", "ext"):
    os.makedirs(os.path.join(D, sub), exist_ok=True)
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Accept": "application/json, text/html, */*"}
FROM = {6: "1403/01/01", 58: "1403/05/01"}
LOCK = threading.Lock()
STAT = {"search_ok": 0, "search_block": 0, "excel_ok": 0, "excel_block": 0, "excel_empty": 0, "err": 0,
        "started": time.strftime("%Y-%m-%d %H:%M:%S")}
FA = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def log(*a):
    s = time.strftime("%m-%d %H:%M:%S ") + " ".join(str(x) for x in a)
    with LOCK:
        with open(os.path.join(D, "crawl.log"), "a", encoding="utf-8") as f:
            f.write(s + "\n")
        print(s, flush=True)


def save_status(extra=None):
    with LOCK:
        st = dict(STAT, now=time.strftime("%Y-%m-%d %H:%M:%S"), **(extra or {}))
        json.dump(st, open(os.path.join(D, "status.json"), "w"), ensure_ascii=False)


def stopped():
    return os.path.exists(os.path.join(D, "STOP"))


def j2g(jy, jm, jd):
    jy += 1595
    days = -355668 + 365 * jy + (jy // 33) * 8 + ((jy % 33) + 3) // 4 + jd + ((jm - 1) * 31 if jm < 7 else (jm - 7) * 30 + 186)
    gy = 400 * (days // 146097); days %= 146097
    if days > 36524:
        days -= 1; gy += 100 * (days // 36524); days %= 36524
        if days >= 365: days += 1
    gy += 4 * (days // 1461); days %= 1461
    if days > 365:
        gy += (days - 1) // 365; days = (days - 1) % 365
    gd = days + 1
    sal = [0, 31, 29 if (gy % 4 == 0 and gy % 100 != 0) or gy % 400 == 0 else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    gm = 0
    for gm in range(1, 13):
        if gd <= sal[gm]: break
        gd -= sal[gm]
    return "%04d-%02d-%02d" % (gy, gm, gd)


def jdate(s):
    s = (s or "").translate(FA)
    m = re.search(r"(\d{4})/(\d{2})/(\d{2})", s)
    return j2g(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


class Blocked(Exception):
    pass


def fetch(url, want_json):
    req = urllib.request.Request(url, headers=H)
    try:
        r = urllib.request.urlopen(req, timeout=60)
        b = r.read()
    except urllib.error.HTTPError as e:
        if e.code in (429, 403, 503):
            raise Blocked("HTTP %s" % e.code)
        raise
    if want_json:
        try:
            return json.loads(b.decode("utf-8"))
        except Exception:
            raise Blocked("non-json (captcha?) %r" % b[:80])
    return b


class Throttle:
    def __init__(self, name, lo, hi):
        self.name, self.lo, self.hi, self.fails = name, lo, hi, 0
        self.lo0, self.hi0 = lo, hi

    def wait(self):
        time.sleep(random.uniform(self.lo, self.hi))

    def blocked(self, why):
        self.fails += 1
        self.lo, self.hi = min(self.lo * 1.4, 30.0), min(self.hi * 1.4, 40.0)
        self.oks = 0
        log(self.name, "interval now %.1f-%.1fs" % (self.lo, self.hi))
        STAT[self.name + "_block"] += 1
        if self.fails >= 3:
            log(self.name, "BLOCKED x%d (%s) -> pause 35 min" % (self.fails, why))
            for _ in range(35 * 6):
                if stopped(): return
                time.sleep(10)
            self.fails = 1
        else:
            w = 60 * (2 ** (self.fails - 1)) + random.uniform(0, 20)
            log(self.name, "block (%s) -> sleep %ds" % (why, w))
            time.sleep(w)

    def ok(self):
        self.fails = 0
        self.oks = getattr(self, "oks", 0) + 1
        if self.oks >= 60 and self.lo > self.lo0:
            self.lo, self.hi, self.oks = max(self.lo0, self.lo * 0.9), max(self.hi0, self.hi * 0.9), 0


# ------------------------------------------------------------------ extraction
class TP(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.h3, self.in_h3, self.tables, self.cur, self.row, self.cell, self.depth = "", False, [], None, None, None, 0

    def handle_starttag(self, t, a):
        if t == "h3": self.in_h3, self.h3 = True, ""
        elif t == "table":
            self.depth += 1
            if self.depth == 1: self.cur = {"h3": self.h3.strip(), "rows": []}
        elif t == "tr" and self.cur is not None: self.row = []
        elif t in ("td", "th") and self.row is not None: self.cell = ""

    def handle_endtag(self, t):
        if t == "h3": self.in_h3 = False
        elif t == "table":
            self.depth -= 1
            if self.depth == 0 and self.cur is not None:
                self.tables.append(self.cur); self.cur = None
        elif t == "tr" and self.row is not None:
            if self.cur is not None: self.cur["rows"].append(self.row)
            self.row = None
        elif t in ("td", "th") and self.cell is not None:
            if self.row is not None: self.row.append(re.sub(r"\s+", " ", self.cell).strip())
            self.cell = None

    def handle_data(self, d):
        if self.in_h3: self.h3 += d
        if self.cell is not None: self.cell += d


KEEP_IS = re.compile("درآمد|فروش|ناخالص|عملياتي|عملیاتی|خالص|سود|زيان|زیان|سرمايه|سرمایه")
KEEP_BS = re.compile("جمع|سرمايه|سرمایه|تسهيلات|تسهیلات|حقوق|سود انباشته")


def norm(s):
    return s.replace("ي", "ی").replace("ك", "ک")


def extract(html, lt):
    p = TP(); p.feed(html)
    out = {"n_tables": len(p.tables)}
    if lt == 6:
        for key, pat, keep in (("is", "صورت سود و زیان", KEEP_IS), ("bs", "صورت وضعیت مالی", KEEP_BS)):
            for t in p.tables:
                if norm(t["h3"]) == pat and t["rows"]:
                    rows = t["rows"]
                    out[key] = {"head": rows[:2], "rows": [r[:5] for r in rows[2:] if r and keep.search(r[0] or "")]}
                    break
        if "is" not in out:   # banks / insurers / old layouts: keep first table with a net-profit row
            for t in p.tables:
                if any("خالص" in (r[0] if r else "") for r in t["rows"]):
                    out["is"] = {"h3": t["h3"], "head": t["rows"][:2], "rows": [r[:5] for r in t["rows"][2:] if r and KEEP_IS.search(r[0] or "")]}
                    break
    else:
        if p.tables:
            t = p.tables[0]
            out["h3"] = t["h3"]
            out["head"] = t["rows"][:2]
            out["tot"] = [r for r in t["rows"] if r and norm(r[0]).startswith("جمع")]
    return out


# ------------------------------------------------------------------ workers
CFG = json.load(open(os.path.join(D, "needs.json"), encoding="utf-8"))
ORDER, NEEDS = CFG["order"], CFG["needs"]
MONTHLY_TOP = int(os.environ.get("MONTHLY_TOP", "160"))


def letters_path(sym, lt):
    return os.path.join(D, "letters", "%s_%d.json" % (sym, lt))


def search_worker():
    th = Throttle("search", float(os.environ.get("S_LO", "18")), float(os.environ.get("S_HI", "26")))
    for lt in (6, 58):
        for k, sym in enumerate(ORDER if lt == 6 else ORDER[:MONTHLY_TOP]):
            if stopped(): return
            fp = letters_path(sym, lt)
            if os.path.exists(fp): continue
            allL, page, total = [], 1, None
            while True:
                if stopped(): return
                q = dict(Symbol=sym, LetterType=lt, FromDate=FROM[lt], Audited="true", NotAudited="true",
                         Consolidatable="true", NotConsolidatable="true", Childs="false", Mains="true",
                         Publisher="false", search="true", PageNumber=page)
                th.wait()
                try:
                    j = fetch("https://search.codal.ir/api/search/v2/q?" + urllib.parse.urlencode(q), True)
                except Blocked as b:
                    th.blocked(str(b)); continue
                except Exception as e:
                    STAT["err"] += 1; log("search err", sym, lt, page, str(e)[:100]); th.blocked("err"); continue
                th.ok(); STAT["search_ok"] += 1
                L = j.get("Letters") or []
                allL += [{kk: x.get(kk) for kk in ("TracingNo", "Symbol", "Title", "LetterCode", "PublishDateTime",
                                                   "SentDateTime", "HasExcel", "ExcelUrl", "Url")} for x in L]
                total = j.get("Total") or 0
                if page * 20 >= total or not L or page >= 8: break
                page += 1
            json.dump({"sym": sym, "lt": lt, "total": total, "letters": allL}, open(fp, "w", encoding="utf-8"), ensure_ascii=False)
            if k % 10 == 0:
                log("search", lt, k, sym, "letters", len(allL)); save_status({"search_pos": [lt, k]})
    log("search DONE")


PER = re.compile(r"(\d+)\s*ماهه")


def needed(sym, lt):
    """earliest-published letter per reporting period, then only the ones that are the latest known on a needed date"""
    fp = letters_path(sym, lt)
    if not os.path.exists(fp): return None
    L = json.load(open(fp, encoding="utf-8"))["letters"]
    byper = {}
    for x in L:
        t = norm(x.get("Title") or "")
        if not x.get("HasExcel") or not x.get("ExcelUrl"): continue
        pend = jdate(t.split("منتهی به")[-1]) if "منتهی به" in t else None
        pub = jdate(x.get("PublishDateTime"))
        if not pend or not pub: continue
        months = 12 if "سال مالی" in t else (int(PER.search(t.translate(FA)).group(1)) if PER.search(t.translate(FA)) else 0)
        key = (pend, months)
        corr = "اصلاحیه" in t
        cand = (corr, pub, x)
        if key not in byper or (cand[0], cand[1]) < (byper[key][0], byper[key][1]):
            byper[key] = cand
    ev = sorted([(v[1], v[2]) for v in byper.values()], key=lambda z: z[0])
    want = set()
    for d in NEEDS.get(sym, []):
        last = None
        for pub, x in ev:
            if pub <= d: last = x
            else: break
        if last: want.add(last["TracingNo"])
        # previous report too (for growth / TTM fallbacks)
    for pub, x in ev:
        pass
    return [x for pub, x in ev if x["TracingNo"] in want]


def excel_worker():
    th = Throttle("excel", 3.0, 5.0)
    idle = 0
    while not stopped():
        todo = []
        for lt in (6, 58):
            for rank, sym in enumerate(ORDER):
                xs = needed(sym, lt)
                if xs is None: continue
                for x in xs:
                    if not os.path.exists(os.path.join(D, "ext", "%s.json" % x["TracingNo"])):
                        todo.append((lt, rank, sym, x))
            if todo: break
        if not todo:
            idle += 1
            if idle > 30 and not any(t.is_alive() for t in threading.enumerate() if t.name == "search"):
                log("excel DONE"); return
            time.sleep(60); continue
        idle = 0
        for lt, rank, sym, x in todo[:40]:
            if stopped(): return
            tn = x["TracingNo"]
            th.wait()
            try:
                b = fetch(x["ExcelUrl"], False)
            except Blocked as e:
                th.blocked(str(e)); continue
            except Exception as e:
                STAT["err"] += 1; log("excel err", sym, tn, str(e)[:100])
                if "404" in str(e) or "500" in str(e):
                    json.dump({"sym": sym, "lt": lt, "tn": tn, "error": str(e)[:200]}, open(os.path.join(D, "ext", "%s.json" % tn), "w", encoding="utf-8"), ensure_ascii=False)
                th.blocked("err"); continue
            th.ok()
            html = b.decode("utf-8", "replace")
            if "<table" not in html and len(html) < 5000 and ("captcha" in html.lower() or "<form" in html.lower()):
                th.blocked("captcha page"); continue
            with open(os.path.join(D, "raw", "%s.html.gz" % tn), "wb") as f:
                f.write(gzip.compress(b))
            try:
                ex = extract(html, lt)
            except Exception as e:
                ex = {"parse_error": str(e)[:200]}
            ex.update(sym=sym, lt=lt, tn=tn, title=x.get("Title"), pub=x.get("PublishDateTime"), pub_g=jdate(x.get("PublishDateTime")))
            if not ex.get("n_tables"): STAT["excel_empty"] += 1
            json.dump(ex, open(os.path.join(D, "ext", "%s.json" % tn), "w", encoding="utf-8"), ensure_ascii=False)
            STAT["excel_ok"] += 1
            if STAT["excel_ok"] % 20 == 0:
                log("excel", STAT["excel_ok"], "lt", lt, "rank", rank, sym); save_status({"excel_pos": [lt, rank]})


if __name__ == "__main__":
    log("START", len(ORDER), "symbols")
    sd = int(os.environ.get("START_DELAY", "0"))
    if sd: log("start delay", sd); time.sleep(sd)
    ts = threading.Thread(target=search_worker, name="search", daemon=True); ts.start()
    te = threading.Thread(target=excel_worker, name="excel", daemon=True); te.start()
    while ts.is_alive() or te.is_alive():
        time.sleep(30); save_status()
        if stopped(): log("STOP file seen"); break
    save_status({"finished": True}); log("EXIT")

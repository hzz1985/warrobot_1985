"""Codal fundamentals for strategy V3.1 — parsing + point-in-time metrics + a polite live fetcher.

Sources (all public Codal):
  search.codal.ir/api/search/v2/q   letters per symbol: LetterType 6 (ن-10 financial statements),
                                    LetterType 58 (ن-30 monthly activity)
  excel.codal.ir/service/Excel/GetAll/<serial>/0   the structured report (HTML served as .xls)

One ن-10 report is enough for TTM: its income statement carries the current cumulative period, the same
period last year and the last full fiscal year (TTM = cur + FY - prev).  Its balance sheet gives equity.
One ن-30 report gives fiscal-YTD sales now vs the same period last year.
All amounts are million rial (Codal standard).  Everything here is stdlib only and never raises to the
caller: any failure -> metrics are NaN -> strategy filters fail open.
"""
from __future__ import annotations

import gzip
import json
import math
import os
import random
import re
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

NAN = float("nan")
FA = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def norm(s: str) -> str:
    return (s or "").replace("ي", "ی").replace("ك", "ک").replace("\u200c", "").replace("\xad", "") \
        .replace("ى", "ی").replace("‏", "")


def tight(s: str) -> str:
    return re.sub(r"\s+", "", norm(s))


def num(s):
    s = (s or "").translate(FA).replace(",", "").replace("٬", "").strip()
    if not s or s in ("-", "--"):
        return NAN
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()").replace("−", "-")
    try:
        v = float(s)
    except ValueError:
        return NAN
    return -v if neg else v


def j2g(jy: int, jm: int, jd: int) -> str:
    jy += 1595
    days = -355668 + 365 * jy + (jy // 33) * 8 + ((jy % 33) + 3) // 4 + jd + ((jm - 1) * 31 if jm < 7 else (jm - 7) * 30 + 186)
    gy = 400 * (days // 146097); days %= 146097
    if days > 36524:
        days -= 1; gy += 100 * (days // 36524); days %= 36524
        if days >= 365:
            days += 1
    gy += 4 * (days // 1461); days %= 1461
    if days > 365:
        gy += (days - 1) // 365; days = (days - 1) % 365
    gd = days + 1
    sal = [0, 31, 29 if (gy % 4 == 0 and gy % 100 != 0) or gy % 400 == 0 else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    gm = 1
    for gm in range(1, 13):
        if gd <= sal[gm]:
            break
        gd -= sal[gm]
    return "%04d-%02d-%02d" % (gy, gm, gd)


def jdate(s: str):
    m = re.search(r"(\d{4})/(\d{1,2})/(\d{1,2})", (s or "").translate(FA))
    return j2g(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def jdates(s: str):
    return [j2g(int(a), int(b), int(c)) for a, b, c in re.findall(r"(\d{4})/(\d{1,2})/(\d{1,2})", (s or "").translate(FA))]


def period_of(title: str):
    """(period_end_gregorian, months) from a Codal title, months=12 for an annual report."""
    t = norm(title).translate(FA)
    pend = jdate(t.split("منتهی به")[-1]) if "منتهی به" in t else None
    if "سال مالی" in t and "ماهه" not in t:
        months = 12
    else:
        m = re.search(r"(\d+)\s*ماهه", t)
        months = int(m.group(1)) if m else 0
    return pend, months


# ------------------------------------------------------------------ HTML table extraction
class _TP(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.h3, self.in_h3, self.tables, self.cur, self.row, self.cell, self.depth = "", False, [], None, None, None, 0

    def handle_starttag(self, t, a):
        if t == "h3":
            self.in_h3, self.h3 = True, ""
        elif t == "table":
            self.depth += 1
            if self.depth == 1:
                self.cur = {"h3": self.h3.strip(), "rows": []}
        elif t == "tr" and self.cur is not None:
            self.row = []
        elif t in ("td", "th") and self.row is not None:
            self.cell = ""

    def handle_endtag(self, t):
        if t == "h3":
            self.in_h3 = False
        elif t == "table":
            self.depth -= 1
            if self.depth == 0 and self.cur is not None:
                self.tables.append(self.cur); self.cur = None
        elif t == "tr" and self.row is not None:
            if self.cur is not None:
                self.cur["rows"].append(self.row)
            self.row = None
        elif t in ("td", "th") and self.cell is not None:
            if self.row is not None:
                self.row.append(re.sub(r"\s+", " ", self.cell).strip())
            self.cell = None

    def handle_data(self, d):
        if self.in_h3:
            self.h3 += d
        if self.cell is not None:
            self.cell += d


KEEP_IS = re.compile("درآمد|فروش|ناخالص|عملياتي|عملیاتی|خالص|سود|زيان|زیان|سرمايه|سرمایه")
KEEP_BS = re.compile("جمع|سرمايه|سرمایه|تسهيلات|تسهیلات|حقوق|سود انباشته")


def extract(html: str, lt: int) -> dict:
    """compact extraction (same format as the server crawler's ext/*.json)."""
    p = _TP(); p.feed(html)
    out = {"n_tables": len(p.tables)}
    if lt == 6:
        for key, pat, keep in (("is", "صورت سود و زیان", KEEP_IS), ("bs", "صورت وضعیت مالی", KEEP_BS)):
            for t in p.tables:
                if norm(t["h3"]).strip() == norm(pat) and t["rows"]:
                    rows = t["rows"]
                    out[key] = {"head": rows[:2], "rows": [r[:5] for r in rows[2:] if r and keep.search(r[0] or "")]}
                    break
        if "is" not in out:
            for t in p.tables:
                if any("خالص" in (r[0] if r else "") for r in t["rows"]):
                    out["is"] = {"h3": t["h3"], "head": t["rows"][:2],
                                 "rows": [r[:5] for r in t["rows"][2:] if r and KEEP_IS.search(r[0] or "")]}
                    break
    elif p.tables:
        t = p.tables[0]
        out.update(h3=t["h3"], head=t["rows"][:2], tot=[r for r in t["rows"] if r and norm(r[0]).startswith("جمع")])
    return out


# ------------------------------------------------------------------ statement / monthly parsing
def _row(rows, *preds, col=0):
    """first row (by predicate priority) whose label matches and whose column `col` holds a number."""
    for pred in preds:
        for r in rows:
            if r and pred(tight(r[0])) and len(r) > 1 + col and not math.isnan(num(r[1 + col])):
                return r
    return None


def parse_statement(ex: dict) -> dict | None:
    """-> dict(pend, months, cols: {cur, prev, fy}, rev_*, np_*, gp_*, op_*, equity, capital, debt) or None."""
    t = ex.get("title") or ""
    pend, months = period_of(t)
    IS = ex.get("is") or {}
    rows = IS.get("rows") or []
    head = (IS.get("head") or [[]])[0]
    if not rows or not pend or not months:
        return None
    # Codal's own column dates are unreliable (template quirks), so roles are positional:
    # data columns = header cells that are not a '% change' column; 1st = current cumulative period,
    # 2nd = same period last year (restated), 3rd = last full fiscal year (interim reports only).
    cols = [k for k, h in enumerate(head[1:], start=0) if "درصد" not in norm(h)][:3]
    roles = {}
    if cols:
        roles["cur"] = cols[0]
    rest = cols[1:]
    if months == 12:
        if rest:
            roles["prev"] = rest[0]
    else:
        for k in rest:
            h = norm(head[1 + k])
            if "سال مالی" in h and "fy" not in roles:
                roles["fy"] = k
            elif "prev" not in roles:
                roles["prev"] = k
            elif "fy" not in roles:
                roles["fy"] = k
    def vals(r):
        return {k: (num(r[1 + c]) if r and len(r) > 1 + c else NAN) for k, c in roles.items()}

    rev = _row(rows, lambda s: s.startswith("درآمدهایعملیاتی"), lambda s: s.startswith("درآمدعملیاتی"),
               lambda s: s in ("فروش", "فروشخالص", "درآمدهایعملیاتیوفروش"), lambda s: s.startswith("جمعدرآمدهای"),
               lambda s: s.startswith("جمعدرآمد"), lambda s: s.startswith("فروش"), col=roles.get("cur", 0))
    net = _row(rows, lambda s: s in ("سود(زیان)خالص", "سودخالص", "سود(زیان)خالصدوره", "سودوزیانخالص"),
               lambda s: s.startswith("سود(زیان)خالص") and "عملیات" not in s and "هرسهم" not in s,
               lambda s: "خالص" in s and "سود" in s and "هرسهم" not in s and "متوقف" not in s and "تداوم" not in s, col=roles.get("cur", 0))
    gp = _row(rows, lambda s: s.startswith("سود(زیان)ناخالص") or s.startswith("سودناخالص"), col=roles.get("cur", 0))
    op = _row(rows, lambda s: s.startswith("سود(زیان)عملیاتی") or s.startswith("سودعملیاتی"), col=roles.get("cur", 0))
    out = dict(pend=pend, months=months, roles=roles, pub=ex.get("pub_g"))
    for k, r in (("rev", rev), ("np", net), ("gp", gp), ("op", op)):
        v = vals(r) if r else {}
        for c in ("cur", "prev", "fy"):
            out[f"{k}_{c}"] = v.get(c, NAN)
    BS = ex.get("bs") or {}
    br = BS.get("rows") or []
    eq = _row(br, lambda s: s.startswith("جمعحقوقمالکانه") and "بدهی" not in s,
              lambda s: s.startswith("جمعحقوقصاحبان") and "بدهی" not in s)
    cap = _row(br, lambda s: s == "سرمایه")
    li = _row(br, lambda s: s == "جمعبدهیها")
    debt = [r for r in br if r and tight(r[0]).startswith("تسهیلاتمالی")]
    out["equity"] = num(eq[1]) if eq and len(eq) > 1 else NAN
    out["capital"] = num(cap[1]) if cap and len(cap) > 1 else NAN
    out["liabilities"] = num(li[1]) if li and len(li) > 1 else NAN
    dv = [num(r[1]) for r in debt if len(r) > 1]
    out["debt"] = sum(x for x in dv if not math.isnan(x)) if dv else NAN
    return out


def _ord(d):
    import datetime
    return datetime.date.fromisoformat(d).toordinal()


def _ttm(cur, prev, fy, months):
    if months == 12:
        return cur, False
    if not any(math.isnan(x) for x in (cur, prev, fy)):
        return cur + fy - prev, False
    if not math.isnan(cur) and months:
        return cur * 12.0 / months, True
    return NAN, False


def statement_metrics(st: dict) -> dict:
    """TTM revenue / net profit, YoY cumulative profit growth, equity."""
    m = st["months"]
    np_ttm, ann = _ttm(st["np_cur"], st["np_prev"], st["np_fy"], m)
    rev_ttm, _ = _ttm(st["rev_cur"], st["rev_prev"], st["rev_fy"], m)
    pv = st["np_prev"]
    yoy = (st["np_cur"] - pv) / abs(pv) if not math.isnan(pv) and pv != 0 and not math.isnan(st["np_cur"]) else NAN
    rv = st["rev_prev"]
    ryoy = (st["rev_cur"] - rv) / abs(rv) if not math.isnan(rv) and rv > 0 and not math.isnan(st["rev_cur"]) else NAN
    eq = st["equity"]
    roe = np_ttm / eq if not math.isnan(np_ttm) and not math.isnan(eq) and eq > 0 else (NAN if math.isnan(eq) or math.isnan(np_ttm) else -1.0)
    npm = np_ttm / rev_ttm if not math.isnan(np_ttm) and not math.isnan(rev_ttm) and rev_ttm > 0 else NAN
    return dict(np_ttm=np_ttm, rev_ttm=rev_ttm, np_yoy=yoy, rev_yoy=ryoy, roe=roe, npm=npm, ttm_annualised=ann,
                equity=eq, capital=st["capital"], debt=st["debt"], pend=st["pend"], months=m, pub=st.get("pub"))


def parse_monthly(ex: dict) -> dict | None:
    """fiscal-YTD sales now vs same period last year from a ن-30 report."""
    head = (ex.get("head") or [[]])[0]
    tot = ex.get("tot") or []
    if not head or not tot:
        return None
    pend, _ = period_of(ex.get("title") or "")
    r = None
    for x in tot:
        if tight(x[0]) == "جمع":
            r = x
    if r is None:
        for x in tot:
            if tight(x[0]) in ("جمعکل", "جمعفروشودرآمد", "جمعدرآمدها", "جمعدرآمد"):
                r = x
    if r is None:
        return None
    vals = [num(c) for c in r[1:]]
    vals = [v for v in vals if not math.isnan(v)]
    groups = [h for h in head[1:] if "وضعیت" not in norm(h) and "توضیح" not in norm(h)]
    cur = ly = NAN
    if len(vals) == len(groups) and pend:
        for g, v in zip(groups, vals):
            n = norm(g)
            ds = jdates(g)
            if "از ابتدای سال" in n and "اصلاح" not in n and ds:
                if ds[0] == pend:
                    cur = v
                elif abs(_ord(ds[0]) - _ord(pend) + 365) <= 5:
                    ly = v
    if (math.isnan(cur) or math.isnan(ly)) and len(vals) >= 2 and len(groups) >= 2:
        g1, g2 = norm(groups[-2]), norm(groups[-1])
        if "از ابتدای سال" in g1 and "از ابتدای سال" in g2 and len(vals) == len(groups):
            cur, ly = vals[-2], vals[-1]
    if math.isnan(cur) or math.isnan(ly):
        return None
    return dict(pend=pend, sales_ytd=cur, sales_ytd_ly=ly,
                sales_yoy=(cur - ly) / ly if ly > 0 else NAN, pub=ex.get("pub_g"))


# ------------------------------------------------------------------ point-in-time selection
def first_publications(letters: list[dict]) -> list[dict]:
    """earliest publication per reporting period (corrections only when no original), sorted by publish day."""
    best = {}
    for x in letters:
        if not x.get("HasExcel") or not x.get("ExcelUrl"):
            continue
        pend, months = period_of(x.get("Title") or "")
        pub = jdate(x.get("PublishDateTime"))
        if not pend or not pub:
            continue
        key = (pend, months)
        cand = ("اصلاحیه" in norm(x.get("Title") or ""), pub)
        if key not in best or cand < best[key][0]:
            best[key] = (cand, dict(x, pub_g=pub, pend=pend, months=months))
    return sorted((v[1] for v in best.values()), key=lambda z: (z["pub_g"], z["months"]))


# ------------------------------------------------------------------ live fetcher (server side, polite)
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Accept": "application/json, text/html, */*"}


class Fetcher:
    """Daily-cached, throttled Codal access for the live bot.  Letters per symbol are cached for
    `letters_ttl_h` hours, Excel extractions forever (keyed by TracingNo).  At most one request every
    `min_gap_s` seconds; any 429 / captcha stops further Codal calls for `block_cooldown_min` minutes."""

    def __init__(self, cache_dir, min_gap_s=3.0, letters_ttl_h=20.0, block_cooldown_min=45, budget_s=90.0):
        self.dir = Path(cache_dir); (self.dir / "letters").mkdir(parents=True, exist_ok=True)
        (self.dir / "ext").mkdir(parents=True, exist_ok=True)
        self.gap, self.ttl, self.cool, self.budget = min_gap_s, letters_ttl_h * 3600, block_cooldown_min * 60, budget_s
        self.t_last, self.t0 = 0.0, time.time()

    def _blocked(self):
        f = self.dir / "BLOCKED_UNTIL"
        try:
            return time.time() < float(f.read_text())
        except Exception:
            return False

    def _get(self, url, want_json):
        if self._blocked() or time.time() - self.t0 > self.budget:
            raise RuntimeError("codal budget/blocked")
        w = self.t_last + self.gap + random.uniform(0, 1.0) - time.time()
        if w > 0:
            time.sleep(w)
        self.t_last = time.time()
        try:
            b = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20).read()
        except urllib.error.HTTPError as e:
            if e.code in (403, 429, 503):
                (self.dir / "BLOCKED_UNTIL").write_text(str(time.time() + self.cool))
            raise
        if want_json:
            try:
                return json.loads(b.decode("utf-8"))
            except Exception:
                (self.dir / "BLOCKED_UNTIL").write_text(str(time.time() + self.cool))
                raise
        return b

    def letters(self, sym, lt, from_date):
        fp = self.dir / "letters" / f"{sym}_{lt}.json"
        try:
            if time.time() - fp.stat().st_mtime < self.ttl:
                return json.loads(fp.read_text(encoding="utf-8"))["letters"]
        except Exception:
            pass
        q = dict(Symbol=sym, LetterType=lt, FromDate=from_date, Audited="true", NotAudited="true",
                 Consolidatable="true", NotConsolidatable="true", Childs="false", Mains="true",
                 Publisher="false", search="true", PageNumber=1)
        try:
            j = self._get("https://search.codal.ir/api/search/v2/q?" + urllib.parse.urlencode(q), True)
            L = [{k: x.get(k) for k in ("TracingNo", "Symbol", "Title", "PublishDateTime", "HasExcel", "ExcelUrl")}
                 for x in (j.get("Letters") or [])]
            fp.write_text(json.dumps({"letters": L}, ensure_ascii=False), encoding="utf-8")
            return L
        except Exception:
            try:                                   # stale cache beats nothing
                return json.loads(fp.read_text(encoding="utf-8"))["letters"]
            except Exception:
                return None

    def ext(self, x, lt):
        fp = self.dir / "ext" / f"{x['TracingNo']}.json"
        try:
            return json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            pass
        try:
            b = self._get(x["ExcelUrl"], False)
            ex = extract(b.decode("utf-8", "replace"), lt)
            ex.update(title=x.get("Title"), pub=x.get("PublishDateTime"), pub_g=jdate(x.get("PublishDateTime")), lt=lt)
            fp.write_text(json.dumps(ex, ensure_ascii=False), encoding="utf-8")
            return ex
        except Exception:
            return None


def _jalali_back(days: int) -> str:
    """Jalali date `days` ago (only the year/month matter for Codal's FromDate)."""
    import datetime
    g = datetime.date.today() - datetime.timedelta(days=days)
    # rough Gregorian->Jalali (good to a few days, enough for a search window)
    jy = g.year - 621 - (1 if (g.month, g.day) < (3, 21) else 0)
    jm = ((g.month - 3) % 12) + 1
    return f"{jy}/{max(1, min(12, jm)):02d}/01"


def live_metrics(sym: str, fetcher: Fetcher, want_monthly: bool = True) -> dict:
    """latest known statement + monthly metrics for one symbol (NaN when unknown)."""
    out = dict(np_ttm=NAN, rev_ttm=NAN, roe=NAN, npm=NAN, np_yoy=NAN, sales_yoy=NAN, capital=NAN, equity=NAN)
    try:
        L = fetcher.letters(sym, 6, _jalali_back(400))
        if L:
            for x in reversed(first_publications(L)):
                ex = fetcher.ext(x, 6)
                st = parse_statement(ex) if ex else None
                if st:
                    out.update({k: v for k, v in statement_metrics(st).items() if k in out or k in ("pend", "months", "pub")})
                    break
        if want_monthly:
            M = fetcher.letters(sym, 58, _jalali_back(120))
            if M:
                for x in reversed(first_publications(M)):
                    ex = fetcher.ext(x, 58)
                    mo = parse_monthly(ex) if ex else None
                    if mo:
                        out["sales_yoy"] = mo["sales_yoy"]
                        break
    except Exception:
        pass
    return out

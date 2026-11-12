#!/usr/bin/env python3
"""STEP 2 of 4 - VALIDATE.  Reads work/raw.json, tests every number, writes work/checked.json.

    python3 validate.py

Every check ends as one of three results:
  ok    the value passed
  warn  the value is used, but the page shows a flag (odd jump, slightly old, feed half down)
  fail  the value is thrown away ("quarantined"): impossible number, wrong date, stale, empty

What is tested
  Range      each value sits inside a plausible band (a 10Y yield of 0.49 or an S&P of 756900 is a typo)
  Dates      no future dates; markets must be recent; CPI and payrolls may lag by their release schedule
  Jumps      a one-day move far outside normal is flagged, so you look at it before trusting it
  Frozen     the same close five days in a row means the feed has stopped updating
  Cross      Brent vs WTI spread, effective funds rate vs 3-month bill, inflation breakeven, SA vs NSA CPI
  Headlines  future-dated, too old, too short or too long, and duplicate titles are dropped
"""
import sys
from datetime import date, datetime, timedelta, timezone

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))  # find the sibling scripts however it is launched
import common as C

BOUNDS = {"spx": (500, 50000), "ndx": (500, 100000), "dji": (2000, 200000), "vix": (5, 120),
          "dxy": (60, 160), "brent": (10, 300), "wti": (5, 300), "gold": (300, 20000),
          "btc": (100, 10000000)}
JUMP_PCT = {"spx": 12, "ndx": 15, "dji": 12, "vix": 100, "dxy": 3.5, "brent": 20, "wti": 25,
            "gold": 10, "btc": 30}
NAMES = {"spx": "S&P 500", "ndx": "Nasdaq", "dji": "Dow", "vix": "VIX", "dxy": "Dollar index",
         "brent": "Brent", "wti": "WTI", "gold": "Gold", "btc": "Bitcoin"}
MARKET_WARN_DAYS, MARKET_FAIL_DAYS = 4, 10


class Checks:
    def __init__(self):
        self.items = []

    def add(self, cid, group, status, detail):
        self.items.append({"id": cid, "group": group, "status": status, "detail": detail})

    def summary(self):
        n = {"ok": 0, "warn": 0, "fail": 0}
        for i in self.items:
            n[i["status"]] += 1
        n["total"] = len(self.items)
        return n


def months_between(today, d):
    return (today.year * 12 + today.month) - (d.year * 12 + d.month)


def check_series(ck, key, pts, today):
    """Price-type series from Yahoo/FRED: [[iso date, close], ...]. Returns cleaned list or None."""
    nm = NAMES.get(key, key)
    lo, hi = BOUNDS[key]
    clean, bad_range, future = [], 0, 0
    for d, v in pts:
        try:
            dd = C.parse_date(d)
        except ValueError:
            continue
        if dd > today + timedelta(days=1):
            future += 1
            continue
        if not (lo <= v <= hi):
            bad_range += 1
            continue
        clean.append([dd.isoformat(), v])
    if future:
        ck.add("yahoo.%s.future" % key, "Yahoo Finance", "warn",
               "%s: dropped %d future-dated bars" % (nm, future))
    if bad_range:
        ck.add("yahoo.%s.range" % key, "Yahoo Finance", "warn" if len(clean) >= 2 else "fail",
               "%s: dropped %d value%s outside %g to %g (a typo?); the previous close is used instead"
               % (nm, bad_range, "" if bad_range == 1 else "s", lo, hi))
    if len(clean) < 2:
        ck.add("yahoo.%s.rows" % key, "Yahoo Finance", "fail", "%s: fewer than 2 usable closes" % nm)
        return None
    last_d = C.parse_date(clean[-1][0])
    age = (today - last_d).days
    if age > MARKET_FAIL_DAYS:
        ck.add("yahoo.%s.fresh" % key, "Yahoo Finance", "fail",
               "%s: newest close is %d days old, so it is thrown away" % (nm, age))
        return None
    if age > MARKET_WARN_DAYS:
        ck.add("yahoo.%s.fresh" % key, "Yahoo Finance", "warn",
               "%s: newest close is %d days old" % (nm, age))
    else:
        ck.add("yahoo.%s.fresh" % key, "Yahoo Finance", "ok",
               "%s: newest close %s%s" % (nm, clean[-1][0],
                                          " (today, may be intraday)" if age == 0 else ""))
    last, prev = clean[-1][1], clean[-2][1]
    pct = (last / prev - 1) * 100
    if abs(pct) > JUMP_PCT[key]:
        ck.add("yahoo.%s.jump" % key, "Yahoo Finance", "warn",
               "%s moved %+.1f%% in one day, far outside normal: confirm it before trusting it" % (nm, pct))
    else:
        ck.add("yahoo.%s.jump" % key, "Yahoo Finance", "ok", "%s one-day move %+.1f%%" % (nm, pct))
    tail = [v for _, v in clean[-5:]]
    if len(tail) == 5 and len(set(tail)) == 1:
        ck.add("yahoo.%s.frozen" % key, "Yahoo Finance", "warn",
               "%s: the same close five days in a row, the feed may be frozen" % nm)
    return clean


def check_treasury(ck, raw_t, today):
    out = {"nominal": None, "real": None}
    nom = raw_t.get("nominal") if raw_t else None
    if not nom:
        ck.add("treasury.rows", "Treasury", "fail", "no nominal yield-curve rows")
        return out
    rows, future, bad = [], 0, 0
    for r in nom:
        d = C.parse_date(r["d"])
        if d > today + timedelta(days=1):
            future += 1
            continue
        v = {k: x for k, x in r["v"].items() if -1.0 <= x <= 15.0}
        if len(v) != len(r["v"]):
            bad += 1
        if v:
            rows.append({"d": d.isoformat(), "v": v})
    if future:
        ck.add("treasury.future", "Treasury", "warn", "dropped %d future-dated curve rows" % future)
    if bad:
        ck.add("treasury.range", "Treasury", "warn", "dropped yields outside -1% to 15% on %d rows" % bad)
    if len(rows) < 5:
        ck.add("treasury.rows", "Treasury", "fail", "only %d usable rows" % len(rows))
        return out
    last = rows[-1]
    age = (today - C.parse_date(last["d"])).days
    if age > MARKET_FAIL_DAYS:
        ck.add("treasury.fresh", "Treasury", "fail", "newest curve is %d days old, so it is thrown away" % age)
        return out
    ck.add("treasury.fresh", "Treasury", "warn" if age > MARKET_WARN_DAYS else "ok",
           "newest curve %s (%d days old)" % (last["d"], age))
    for need in ("2y", "10y"):
        if need not in last["v"]:
            ck.add("treasury.tenor", "Treasury", "fail", "the %s yield is missing on the newest row" % need)
            return out
    prev = rows[-2]["v"] if len(rows) > 1 else {}
    for t in ("2y", "10y"):
        if t in prev:
            d = (last["v"][t] - prev[t]) * 100
            if abs(d) > 40:
                ck.add("treasury.jump." + t, "Treasury", "warn",
                       "%s yield moved %+.0fbp in one day: confirm it" % (t.upper(), d))
    short = last["v"].get("3m", last["v"].get("6m"))
    if short is not None:
        sl = last["v"]["10y"] - short
        if not -3.0 <= sl <= 4.0:
            ck.add("treasury.curve", "Treasury", "warn",
                   "10Y minus short bill is %+.0fbp, an unusual curve: check the source" % (sl * 100))
    out["nominal"] = rows

    real = raw_t.get("real") if raw_t else None
    if real:
        rr = [{"d": r["d"], "v": {k: x for k, x in r["v"].items() if -3.0 <= x <= 8.0}} for r in real
              if C.parse_date(r["d"]) <= today + timedelta(days=1)]
        rr = [r for r in rr if r["v"]]
        if rr and "10y" in rr[-1]["v"]:
            nm = {r["d"]: r["v"] for r in rows}.get(rr[-1]["d"])
            if nm and "10y" in nm:
                be = nm["10y"] - rr[-1]["v"]["10y"]
                if -0.5 <= be <= 5.0:
                    ck.add("treasury.breakeven", "Treasury", "ok", "10Y breakeven %.2f%%" % be)
                    out["real"] = rr
                else:
                    ck.add("treasury.breakeven", "Treasury", "warn",
                           "10Y breakeven would be %.2f%%, which is implausible, so real yields are not used" % be)
            else:
                ck.add("treasury.breakeven", "Treasury", "warn",
                       "real-yield date %s has no matching nominal row" % rr[-1]["d"])
        else:
            ck.add("treasury.real", "Treasury", "warn", "real-yield curve has no usable 10Y")
    else:
        ck.add("treasury.real", "Treasury", "warn", "no real-yield curve, so no inflation breakeven this run")
    return out


def yoy_series(pts):
    out = []
    byd = {p[0]: p[1] for p in pts}
    for d, v in pts:
        y = C.parse_date(d)
        prev = byd.get(date(y.year - 1, y.month, 1).isoformat())
        if prev:
            out.append((d, (v / prev - 1) * 100))
    return out


def check_bls(ck, raw_b, today):
    if not raw_b:
        ck.add("bls.rows", "BLS", "fail", "no BLS series came back")
        return None
    clean = {}
    for key, pts in raw_b.items():
        good, seen = [], set()
        for d, v in pts:
            dd = C.parse_date(d)
            if dd > today or d in seen:
                continue
            ok = {"ur": 1 <= v <= 25, "pay": 50000 <= v <= 400000}.get(key, 50 <= v <= 1000)
            if ok:
                good.append([d, v])
                seen.add(d)
        if len(good) < 4:
            ck.add("bls.%s.rows" % key, "BLS", "fail", "%s: only %d usable months" % (key, len(good)))
            continue
        # impossible month-to-month jumps
        bad = 0
        for a, b in zip(good, good[1:]):
            if key == "pay" and abs(b[1] - a[1]) > 1500:
                bad += 1
            elif key == "ur" and abs(b[1] - a[1]) > 2.5:
                bad += 1
            elif key not in ("pay", "ur") and abs(b[1] / a[1] - 1) > 0.03:
                bad += 1
        if bad:
            ck.add("bls.%s.jump" % key, "BLS", "warn",
                   "%s: %d month-to-month moves look impossible; the series is kept but flagged" % (key, bad))
        lag = months_between(today, C.parse_date(good[-1][0]))
        if lag > 3:
            ck.add("bls.%s.fresh" % key, "BLS", "warn",
                   "%s: newest month is %s, %d months back" % (key, good[-1][0][:7], lag))
        else:
            ck.add("bls.%s.fresh" % key, "BLS", "ok", "%s newest month %s" % (key, good[-1][0][:7]))
        clean[key] = good
    # CPI y/y should be plausible; SA and NSA should tell the same story
    for base in ("cpi", "core"):
        n, s = clean.get(base + "_nsa"), clean.get(base + "_sa")
        yn = yoy_series(n)[-1] if n and yoy_series(n) else None
        if yn and not -5 <= yn[1] <= 25:
            ck.add("bls.%s.range" % base, "BLS", "fail", "%s y/y %.1f%% is implausible, dropped" % (base, yn[1]))
            clean.pop(base + "_nsa", None)
            continue
        ys = yoy_series(s)[-1] if s and yoy_series(s) else None
        if yn and ys and abs(yn[1] - ys[1]) > 0.35:
            ck.add("bls.%s.sa_vs_nsa" % base, "BLS", "warn",
                   "%s: adjusted y/y %.2f%% and unadjusted %.2f%% disagree by more than 0.35 pt" % (base, ys[1], yn[1]))
    return clean or None


def check_nyfed(ck, nf, today):
    if not nf:
        ck.add("nyfed.rows", "NY Fed", "fail", "no policy-rate data")
        return None
    lo, hi, ef = nf["lo"], nf["hi"], nf.get("effr")
    if not (0 <= lo < hi <= 15):
        ck.add("nyfed.range", "NY Fed", "fail", "target range %s-%s is implausible, dropped" % (lo, hi))
        return None
    if abs((hi - lo) - 0.25) > 0.01:
        ck.add("nyfed.width", "NY Fed", "warn", "target range is %.2f points wide, not the usual 0.25" % (hi - lo))
    if ef is not None and not (lo - 0.05 <= ef <= hi + 0.05):
        ck.add("nyfed.effr", "NY Fed", "warn",
               "effective rate %.2f%% sits outside the %.2f-%.2f%% target range" % (ef, lo, hi))
        nf = dict(nf, effr=None)
    age = (today - C.parse_date(nf["d"])).days
    ck.add("nyfed.fresh", "NY Fed", "warn" if age > 5 else "ok", "policy rate dated %s (%d days old)" % (nf["d"], age))
    return nf


def check_headlines(ck, feeds, stats, today):
    total = len(feeds)
    seen, kept = set(), []
    drop = {"future": 0, "old": 0, "length": 0, "duplicate": 0}
    import re
    for h in sorted(feeds, key=lambda x: x.get("t") or x["d"] + "T00:00:00+00:00", reverse=True):
        d = C.parse_date(h["d"])
        if d > today + timedelta(days=1):
            drop["future"] += 1
            continue
        if (today - d).days > 10:
            drop["old"] += 1
            continue
        if not 25 <= len(h["title"]) <= 220:
            drop["length"] += 1
            continue
        key = re.sub(r"[^a-z0-9]", "", h["title"].lower())[:60]
        if key in seen:
            drop["duplicate"] += 1
            continue
        seen.add(key)
        kept.append(h)
    kept = kept[:120]
    ok_n, tot_n = stats.get("ok", 0), stats.get("total", 0)
    if tot_n:
        status = "fail" if ok_n == 0 else "warn" if ok_n < tot_n / 2 else "ok"
        ck.add("feeds.coverage", "Headlines", status, "%d of %d feeds answered" % (ok_n, tot_n))
    if total:
        ck.add("feeds.clean", "Headlines", "ok",
               "kept %d of %d (dropped %s)" % (len(kept), total,
                                                ", ".join("%d %s" % (v, k) for k, v in drop.items() if v) or "none"))
    if kept:
        age = (today - C.parse_date(kept[0]["d"])).days
        ck.add("feeds.fresh", "Headlines", "warn" if age > 2 else "ok",
               "newest headline is %d day%s old" % (age, "" if age == 1 else "s"))
    return kept


def validate(raw, today):
    ck = Checks()
    clean = {"today": today.isoformat(), "fetched_at": raw.get("fetched_at"), "yahoo": {}}
    clean["treasury"] = check_treasury(ck, raw.get("treasury"), today)
    for key, pts in (raw.get("yahoo") or {}).items():
        if key in BOUNDS:
            c = check_series(ck, key, pts, today)
            if c:
                clean["yahoo"][key] = c
    b, w = clean["yahoo"].get("brent"), clean["yahoo"].get("wti")
    if b and w:
        sp = b[-1][1] - w[-1][1]
        ck.add("cross.brent_wti", "Cross-checks", "ok" if -5 <= sp <= 25 else "warn",
               "Brent minus WTI is $%.2f" % sp)
    clean["bls"] = check_bls(ck, raw.get("bls"), today)
    clean["nyfed"] = check_nyfed(ck, raw.get("nyfed"), today)
    nom = clean["treasury"]["nominal"]
    if clean["nyfed"] and clean["nyfed"].get("effr") is not None and nom and "3m" in nom[-1]["v"]:
        gap = nom[-1]["v"]["3m"] - clean["nyfed"]["effr"]
        ck.add("cross.effr_3m", "Cross-checks", "ok" if abs(gap) <= 1.0 else "warn",
               "3-month bill minus effective funds rate is %+.0fbp" % (gap * 100))
    clean["feeds"] = check_headlines(ck, raw.get("feeds") or [], raw.get("feed_stats") or {}, today)
    clean["feed_stats"] = raw.get("feed_stats") or {}
    clean["sources"] = raw.get("sources") or []
    def dataset_of(cid):
        parts = cid.split(".")
        return ".".join(parts[:2]) if parts[0] in ("yahoo", "bls") and len(parts) > 2 else parts[0]
    quarantined = sorted({dataset_of(i["id"]) for i in ck.items if i["status"] == "fail"})
    return clean, {"summary": ck.summary(), "items": ck.items, "quarantined": quarantined}


def main(argv=None):
    C.safe_console()
    raw = C.read_json(C.WORK / "raw.json")
    if not raw:
        print("work/raw.json not found. Run collect.py first.", file=sys.stderr)
        return 1
    today = C.parse_date(raw["today"])
    clean, checks = validate(raw, today)
    C.write_json(C.WORK / "checked.json", {"clean": clean, "checks": checks})
    s = checks["summary"]
    print("%d checks: %d ok, %d warnings, %d failed" % (s["total"], s["ok"], s["warn"], s["fail"]))
    for i in checks["items"]:
        if i["status"] != "ok":
            print("  %-4s %s" % (i["status"].upper(), i["detail"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

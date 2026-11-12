#!/usr/bin/env python3
"""STEP 1 of 4 - COLLECT.  Gets the raw data and writes it to work/raw.json. Nothing is judged here.

    python3 collect.py

Sources (all free, no keys):
  Treasury   daily yield curve and real-yield curve (gives the market's inflation expectation)
  BLS        CPI (headline and core, adjusted and not), unemployment rate, payrolls
  NY Fed     effective fed funds rate and the target range
  Yahoo      S&P 500, Nasdaq, Dow, VIX, dollar index, Brent, WTI, gold, bitcoin (3 months of closes)
  Feeds      Fed, BLS, CNBC and MarketWatch feeds plus Google News searches
FRED is used only as a fallback for a few Yahoo series, because its CSV service is often slow.
"""
import csv
import io
import json
import os
import re
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))  # find the sibling scripts however it is launched
import common as C

YAHOO = {"spx": "^GSPC", "ndx": "^IXIC", "dji": "^DJI", "vix": "^VIX", "dxy": "DX-Y.NYB",
         "brent": "BZ=F", "wti": "CL=F", "gold": "GC=F", "btc": "BTC-USD"}
FRED_FALLBACK = {"spx": "SP500", "ndx": "NASDAQCOM", "vix": "VIXCLS", "brent": "DCOILBRENTEU",
                 "wti": "DCOILWTICO"}
BLS_IDS = {"cpi_nsa": "CUUR0000SA0", "core_nsa": "CUUR0000SA0L1E",
           "cpi_sa": "CUSR0000SA0", "core_sa": "CUSR0000SA0L1E",
           "ur": "LNS14000000", "pay": "CES0000000001"}


# ------------------------------------------------------------------ treasury
def treasury_curve(year, kind):
    typ = {"nominal": "daily_treasury_yield_curve", "real": "daily_treasury_real_yield_curve"}[kind]
    url = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
           "daily-treasury-rates.csv/%d/all?type=%s&field_tdr_date_value=%d&page&_format=csv"
           % (year, typ, year))
    rows = []
    for r in csv.DictReader(io.StringIO(C.fetch(url))):
        try:
            d = datetime.strptime(r["Date"], "%m/%d/%Y").date()
        except (KeyError, ValueError):
            continue
        vals = {}
        for k, v in r.items():
            t = C.norm_tenor(k) if k != "Date" else None
            f = C.to_float(v)
            if t and f is not None:
                vals[t] = f
        if vals:
            rows.append({"d": d.isoformat(), "v": vals})
    rows.sort(key=lambda x: x["d"])
    if not rows:
        raise ValueError("no rows")
    return rows


def treasury_both(src, today, kind):
    rows = src.call("Treasury", treasury_curve, today.year, kind)
    if rows is not None and len(rows) < 30:
        older = src.call("Treasury", treasury_curve, today.year - 1, kind)
        rows = (older or []) + rows
    return rows


# ------------------------------------------------------------------ yahoo and fred
def yahoo_series(sym):
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/%s?range=3mo&interval=1d"
           % urllib.parse.quote(sym))
    res = json.loads(C.fetch(url))["chart"]["result"][0]
    off = res.get("meta", {}).get("gmtoffset", 0) or 0
    closes = res["indicators"]["quote"][0]["close"]
    pts = []
    for t, c in zip(res["timestamp"], closes):
        if c is None:
            continue
        pts.append([datetime.fromtimestamp(t + off, tz=timezone.utc).date().isoformat(), float(c)])
    if len(pts) < 2:
        raise ValueError("fewer than 2 closes")
    return pts


def fred_series(sid, since):
    url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=%s&cosd=%s" % (sid, since)
    out = []
    for r in list(csv.reader(io.StringIO(C.fetch(url, timeout=40))))[1:]:
        if len(r) < 2:
            continue
        v = C.to_float(r[1])  # "." marks a missing day
        if v is None:
            continue
        try:
            out.append([datetime.strptime(r[0], "%Y-%m-%d").date().isoformat(), v])
        except ValueError:
            continue
    out.sort(key=lambda x: x[0])
    if len(out) < 2:
        raise ValueError("no rows")
    return out


# ------------------------------------------------------------------ bls and ny fed
def bls_series(year):
    """BLS public API v1: no key, 25 requests a day. One request carries every series."""
    body = json.dumps({"seriesid": list(BLS_IDS.values()),
                       "startyear": str(year - 2), "endyear": str(year)}).encode("utf-8")
    j = json.loads(C.fetch("https://api.bls.gov/publicAPI/v1/timeseries/data/", data=body,
                           headers={"Content-Type": "application/json"}))
    if j.get("status") != "REQUEST_SUCCEEDED":
        raise ValueError("BLS: %s" % (j.get("message") or j.get("status")))
    names = {v: k for k, v in BLS_IDS.items()}
    out = {}
    for ser in j["Results"]["series"]:
        pts = []
        for r in ser.get("data", []):
            if not re.fullmatch(r"M(0[1-9]|1[0-2])", r.get("period", "")):
                continue  # M13 is the annual average
            v = C.to_float(r.get("value"))
            if v is None:
                continue
            pts.append([date(int(r["year"]), int(r["period"][1:]), 1).isoformat(), v])
        pts.sort(key=lambda x: x[0])
        if pts and ser["seriesID"] in names:
            out[names[ser["seriesID"]]] = pts
    if not out:
        raise ValueError("no series")
    return out


def nyfed():
    j = json.loads(C.fetch("https://markets.newyorkfed.org/api/rates/unsecured/effr/last/1.json"))
    r = j["refRates"][0]
    lo, hi = C.to_float(r.get("targetRateFrom")), C.to_float(r.get("targetRateTo"))
    if lo is None or hi is None:
        raise ValueError("no target range")
    return {"lo": lo, "hi": hi, "effr": C.to_float(r.get("percentRate")), "d": r["effectiveDate"]}


# ------------------------------------------------------------------ headline feeds
def parse_dt(text):
    """Returns (UTC datetime, has_time) or None. Date-only values have has_time False."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        dt = parsedate_to_datetime(text)
        has_time = True
    except (TypeError, ValueError, IndexError):
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            has_time = len(text) > 10
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc), has_time


def parse_feed(xml_text, label):
    xml_text = re.sub(r"<!DOCTYPE[^>\[]*(\[.*?\])?\s*>", "", xml_text, flags=re.S)
    root = ET.fromstring(xml_text)

    def child(el, name):
        for c in el:
            if c.tag.split("}")[-1] == name:
                return c
        return None

    items = []
    for el in root.iter():
        if el.tag.split("}")[-1] not in ("item", "entry"):
            continue
        t = child(el, "title")
        title = re.sub(r"\s+", " ", (t.text or "")).strip() if t is not None else ""
        l = child(el, "link")
        link = ((l.text or "").strip() or l.get("href", "")) if l is not None else ""
        s = child(el, "source")
        src_name = (s.text or "").strip() if s is not None and s.text else label
        if s is not None and title.endswith(" - " + src_name):
            title = title[: -(len(src_name) + 3)].strip()
        d = None
        for name in ("pubDate", "published", "updated", "date"):
            c = child(el, name)
            if c is not None:
                d = parse_dt(c.text)
                if d:
                    break
        if title and d:
            item = {"d": d[0].date().isoformat(), "title": title, "src": src_name, "url": link}
            if d[1]:
                item["t"] = d[0].isoformat(timespec="seconds")
            items.append(item)
    if not items:
        raise ValueError("no items")
    return items


def gnews(q):
    return ("https://news.google.com/rss/search?q=%s&hl=en-US&gl=US&ceid=US:en"
            % urllib.parse.quote(q + " when:7d"))


FEEDS = [
    ("Fed press releases", "https://www.federalreserve.gov/feeds/press_all.xml"),
    ("Fed monetary policy", "https://www.federalreserve.gov/feeds/press_monetary.xml"),
    ("Fed speeches", "https://www.federalreserve.gov/feeds/speeches.xml"),
    ("BLS latest", "https://www.bls.gov/feed/bls_latest.rss"),
    ("CNBC economy", "https://www.cnbc.com/id/20910258/device/rss/rss.html"),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_marketpulse"),
    ("Google News", gnews("Treasury yields")),
    ("Google News", gnews("Federal Reserve rate hike")),
    ("Google News", gnews("oil prices Brent Hormuz")),
    ("Google News", gnews("CPI inflation report")),
    ("Google News", gnews("jobs report payrolls")),
    ("Google News", gnews("Wall Street stocks Nasdaq")),
    ("Google News", gnews("gold price dollar")),
    ("Google News", gnews("tariffs trade")),
]


def get_feed(url, label):
    return parse_feed(C.fetch(url), label)


# ------------------------------------------------------------------ the step
def collect(today, say=print):
    src = C.Src()
    raw = {"fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "today": today.isoformat()}

    say("  Treasury yield curves ...")
    raw["treasury"] = {"nominal": treasury_both(src, today, "nominal"),
                       "real": treasury_both(src, today, "real")}

    say("  Yahoo Finance quotes (3 months each) ...")
    raw["yahoo"] = {}
    for key, sym in YAHOO.items():
        pts = src.call("Yahoo Finance", yahoo_series, sym)
        if not pts and key in FRED_FALLBACK:
            pts = src.call("FRED", fred_series, FRED_FALLBACK[key],
                           (today - timedelta(days=100)).isoformat())
        if pts:
            raw["yahoo"][key] = pts

    say("  BLS and NY Fed ...")
    raw["bls"] = src.call("BLS", bls_series, today.year)
    raw["nyfed"] = src.call("NY Fed", nyfed)
    if not raw["nyfed"]:  # fallback for the target range only
        lo = src.call("FRED", fred_series, "DFEDTARL", (today - timedelta(days=60)).isoformat())
        hi = src.call("FRED", fred_series, "DFEDTARU", (today - timedelta(days=60)).isoformat())
        if lo and hi:
            raw["nyfed"] = {"lo": lo[-1][1], "hi": hi[-1][1], "effr": None, "d": lo[-1][0]}

    say("  Headline feeds ...")
    heads, answered = [], 0
    for label, url in FEEDS:
        got = src.call("Headline feeds", get_feed, url, label)
        if got:
            heads.extend(got)
            answered += 1
    raw["feeds"] = heads
    raw["feed_stats"] = {"ok": answered, "total": len(FEEDS), "raw": len(heads)}
    raw["sources"] = src.report()
    return raw


def main(argv=None):
    C.safe_console()
    today = C.parse_date(os.environ["MW_TODAY"]) if os.environ.get("MW_TODAY") else datetime.now(timezone.utc).date()
    raw = collect(today)
    C.write_json(C.WORK / "raw.json", raw)
    print("Wrote", C.WORK / "raw.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""STEP 3 of 4 - UNDERSTAND.  Reads work/checked.json and works out what the numbers MEAN.

    python3 understand.py

Only validated data comes in. Out go:
  levels     the eight tiles on the page, rebuilt from the checked data
  read       plain-language findings: rates and curve shape, what bills say about the Fed's path,
             inflation expectations, the latest CPI and jobs numbers, market tone
  leads      "hawkish or dovish next?" evidence computed from the data, for the October-style
             releases the page tracks (jobs report, CPI, the next Fed decision). Each lead has a
             value from -1 (points dovish) to +1 (points hawkish), a weight and the sentence behind it.
  changes    what moved since the last run

Headline text is read in the page itself (it holds the keyword engine), so it is not scored here.
Every formula below is plain arithmetic; change the numbers at the top of a function to retune it.
"""
import sys
from datetime import date, timedelta

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))  # find the sibling scripts however it is launched
import common as C

clamp = C.clamp
BILL_PREMIUM_BP = 5   # bills normally yield a few bp above the effective funds rate even when no move is expected


# ------------------------------------------------------------------ small maths
def last(pts):
    return pts[-1] if pts else None


def pct(pts, n):
    """Percent change of the newest close versus n bars earlier."""
    if not pts or len(pts) <= n:
        return None
    return (pts[-1][1] / pts[-1 - n][1] - 1) * 100


def bp(rows, key, n):
    """Change in a yield, in basis points, versus n rows earlier (rows are {'d','v':{tenor: yield}})."""
    have = [r for r in rows if key in r["v"]]
    if len(have) <= n:
        return None
    return (have[-1]["v"][key] - have[-1 - n]["v"][key]) * 100


def ma(pts, n):
    if len(pts) < n:
        return None
    return sum(v for _, v in pts[-n:]) / n


def yoy_at(pts, i=-1):
    """Year-on-year percent change at index i of a monthly series [[iso, value], ...]."""
    d = C.parse_date(pts[i][0])
    base = {p[0]: p[1] for p in pts}.get(date(d.year - 1, d.month, 1).isoformat())
    return (pts[i][1] / base - 1) * 100 if base else None


def mom(pts, i=-1):
    return (pts[i][1] / pts[i - 1][1] - 1) * 100 if len(pts) >= abs(i) + 1 else None


def sgnf(x, fmt="%.2f"):
    return ("+" if x >= 0 else "−") + fmt % abs(x)


def fmt_d(iso_s):
    return C.lab(C.parse_date(iso_s))


# ------------------------------------------------------------------ levels (the page's tiles)
def build_levels(cl, today):
    out = {}
    nom = (cl.get("treasury") or {}).get("nominal")
    y = cl.get("yahoo") or {}
    bl = cl.get("bls") or {}
    nf = cl.get("nyfed")

    if nf:
        nxt = next((x for x in C.FOMC if x > today), None)
        s = "Target range" + (" · next decision %s" % C.lab(nxt) if nxt else "")
        if nf.get("effr") is not None:
            s += " · effective rate %.2f%%" % nf["effr"]
        out["ffr"] = {"k": "Fed funds target", "v": "%.2f–%.2f%%" % (nf["lo"], nf["hi"]),
                      "s": s, "d": fmt_d(nf["d"])}

    cpi = bl.get("cpi_nsa")
    if cpi and yoy_at(cpi) is not None:
        h = yoy_at(cpi)
        s = "Year on year, not seasonally adjusted"
        core = bl.get("core_nsa")
        if core and yoy_at(core) is not None:
            s = "Core %.1f%% · year on year" % yoy_at(core)
            cs = bl.get("core_sa")
            if cs and mom(cs) is not None:
                s += " · core %s%% on the month" % sgnf(mom(cs))
        out["cpi"] = {"k": "US CPI, %s" % C.parse_date(cpi[-1][0]).strftime("%B"), "v": "%.1f%%" % h,
                      "s": s, "d": C.parse_date(cpi[-1][0]).strftime("%b %Y")}

    ur, pay = bl.get("ur"), bl.get("pay")
    if ur:
        s = "Unemployment rate"
        if pay and len(pay) >= 2:
            s = "Payrolls {:+,.0f}K in {}".format(pay[-1][1] - pay[-2][1],
                                                  C.parse_date(pay[-1][0]).strftime("%b"))
            if len(pay) >= 4:
                s += " · 3-month average {:,.0f}K".format((pay[-1][1] - pay[-4][1]) / 3)
        out["un"] = {"k": "Unemployment", "v": "%.1f%%" % ur[-1][1], "s": s,
                     "d": C.parse_date(ur[-1][0]).strftime("%b %Y")}

    if nom and len(nom) >= 2 and "10y" in nom[-1]["v"]:
        row = nom[-1]["v"]
        y10 = row["10y"]
        d1 = bp(nom, "10y", 1)
        s = "%+.0fbp on the day" % d1 if d1 is not None else ""
        d5 = bp(nom, "10y", 5)
        if d5 is not None:
            s += " · %+.0fbp in 5 days" % d5
        if "2y" in row:
            s = "2Y %.2f%% · 10Y–2Y %+.0fbp · %s" % (row["2y"], (y10 - row["2y"]) * 100, s)
        out["y10"] = {"k": "US 10Y yield", "v": "%.2f%%" % y10, "s": s,
                      "d": fmt_d(nom[-1]["d"]) + " close"}

    def q(key):
        p = y.get(key)
        if not p or len(p) < 2:
            return None
        return {"last": p[-1][1], "pct": pct(p, 1), "d": C.parse_date(p[-1][0]), "p5": pct(p, 5)}

    dx = q("dxy")
    if dx:
        out["dxy"] = {"k": "Dollar index", "v": "%.2f" % dx["last"],
                      "s": "%+.2f%% on the day" % dx["pct"], "d": C.lab(dx["d"])}
    br, wt = q("brent"), q("wti")
    if br:
        s = "%+.1f%% on the day" % br["pct"]
        if br["p5"] is not None:
            s += " · %+.1f%% in 5 days" % br["p5"]
        if wt:
            s += " · WTI $%.2f" % wt["last"]
        out["brent"] = {"k": "Brent crude", "v": "$%.2f" % br["last"], "s": s, "d": C.lab(br["d"])}
    gd = q("gold")
    if gd:
        out["gold"] = {"k": "Gold, front future", "v": "$%s" % format(gd["last"], ",.0f"),
                       "s": "%+.1f%% on the day" % gd["pct"], "d": C.lab(gd["d"])}
    sp = q("spx")
    if sp:
        s = "%+.2f%% on the day" % sp["pct"]
        nd, vx = q("ndx"), q("vix")
        if nd:
            s += " · Nasdaq %s (%+.1f%%)" % (format(nd["last"], ",.0f"), nd["pct"])
        if vx:
            s += " · VIX %.1f" % vx["last"]
        out["spx"] = {"k": "S&P 500", "v": format(sp["last"], ",.0f"), "s": s,
                      "d": C.lab(sp["d"]) + " close"}
    return out


# ------------------------------------------------------------------ findings
def curve_move(d2, d10):
    """Names the way the curve moved over five trading days from the 2Y and 10Y changes (bp)."""
    if d2 is None or d10 is None:
        return None
    if abs(d2) < 3 and abs(d10) < 3:
        return "little changed"
    spread = d10 - d2
    if d2 > 0 and d10 > 0:
        return "bear steepening (long yields rising faster)" if spread > 3 else \
               "bear flattening (short yields rising faster)" if spread < -3 else "a parallel rise"
    if d2 < 0 and d10 < 0:
        return "bull steepening (short yields falling faster)" if spread > 3 else \
               "bull flattening (long yields falling faster)" if spread < -3 else "a parallel fall"
    return "a twist (2Y and 10Y moved in opposite directions)"


def vix_word(v):
    return "calm" if v < 15 else "normal" if v < 20 else "elevated" if v < 30 else "stressed"


def month_mean(pts, y, m):
    v = [x for d, x in pts if C.parse_date(d).year == y and C.parse_date(d).month == m]
    return (sum(v) / len(v), len(v)) if v else (None, 0)


def lead(key, text, v, w, today):
    return {"key": key, "n": text, "v": round(clamp(v), 2), "w": w, "d": today.isoformat(),
            "src": ["live"], "auto": True}


def understand(cl, today, prev_read=None, history=None):
    nom = (cl.get("treasury") or {}).get("nominal") or []
    real = (cl.get("treasury") or {}).get("real") or []
    y, bl, nf = cl.get("yahoo") or {}, cl.get("bls") or {}, cl.get("nyfed")
    read = {"asof": today.isoformat(), "summary": [], "leads": {"dec": [], "cpi": [], "nfp": []}}
    S = read["summary"]

    # rates and curve
    if nom and "10y" in nom[-1]["v"]:
        row = nom[-1]["v"]
        d1, d5 = bp(nom, "10y", 1), bp(nom, "10y", 5)
        d2_5 = bp(nom, "2y", 5)
        read["curve"] = {"d": nom[-1]["d"], "y10": row["10y"], "y2": row.get("2y"), "y3m": row.get("3m"),
                         "y30": row.get("30y"), "d10_1": d1, "d10_5": d5, "d2_5": d2_5,
                         "move5": curve_move(d2_5, d5)}
        line = "Rates: the 10Y yield is %.2f%%" % row["10y"]
        if d1 is not None and d5 is not None:
            line += " (%+.0fbp on the day, %+.0fbp over 5 days)" % (d1, d5)
        if "2y" in row:
            line += "; 2Y %.2f%%, 10Y–2Y %+.0fbp" % (row["2y"], (row["10y"] - row["2y"]) * 100)
        mv = read["curve"]["move5"]
        if mv and mv != "little changed":
            line += ". Over five days the curve showed " + mv
        S.append(line + ".")

    # what the bills say about the Fed's path (rough: bills are not futures)
    pol = None
    if nf and nf.get("effr") is not None and nom:
        row = nom[-1]["v"]
        pol = {"effr": nf["effr"], "lo": nf["lo"], "hi": nf["hi"]}
        for t in ("3m", "6m", "1y"):
            if t in row:
                pol["gap_" + t] = round((row[t] - nf["effr"]) * 100, 1)
        if "gap_6m" in pol:
            pol["steps_6m"] = round(pol["gap_6m"] / 25, 1)
        read["policy"] = pol
        if "gap_6m" in pol:
            S.append("Policy path: the 6-month bill yields %.2f%% against an effective funds rate of %.2f%%, "
                     "so bills price about %+.0fbp (%+.1f quarter-point steps) over six months. A rough proxy: "
                     "bills trade a few bp away from the policy rate even when no change is expected." %
                     (row["6m"], nf["effr"], pol["gap_6m"], pol["steps_6m"]))

    # inflation expectations from real yields
    be = None
    if real and nom and "10y" in real[-1]["v"]:
        nm = {r["d"]: r["v"] for r in nom}
        series = [(r["d"], nm[r["d"]]["10y"] - r["v"]["10y"]) for r in real if r["d"] in nm and "10y" in nm[r["d"]]]
        if series:
            be = {"now": series[-1][1], "d": series[-1][0],
                  "d5": (series[-1][1] - series[-6][1]) * 100 if len(series) > 5 else None,
                  "d20": (series[-1][1] - series[-21][1]) * 100 if len(series) > 20 else None}
            read["breakeven"] = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in be.items()}
            line = "Inflation expectations: the 10Y breakeven (nominal minus real yield) is %.2f%%" % be["now"]
            if be["d5"] is not None:
                line += ", %+.0fbp over 5 days" % be["d5"]
            S.append(line + ".")

    # inflation and jobs prints
    core_mom = core_prev = None
    cpi, core = bl.get("cpi_nsa"), bl.get("core_nsa")
    cs, hs = bl.get("core_sa"), bl.get("cpi_sa")
    if cpi and yoy_at(cpi) is not None:
        infl = {"ref": cpi[-1][0][:7], "cpi_yoy": round(yoy_at(cpi), 2)}
        if core and yoy_at(core) is not None:
            infl["core_yoy"] = round(yoy_at(core), 2)
        if cs and mom(cs) is not None:
            core_mom = mom(cs)
            infl["core_mom"] = round(core_mom, 3)
            if len(cs) >= 5:
                core_prev = sum(mom(cs, i) for i in (-2, -3, -4)) / 3
                infl["core_mom_prev3"] = round(core_prev, 3)
        if hs and mom(hs) is not None:
            infl["cpi_mom"] = round(mom(hs), 3)
        read["inflation"] = infl
        line = "Inflation (%s CPI): %.1f%% a year" % (C.parse_date(cpi[-1][0]).strftime("%b"), infl["cpi_yoy"])
        if "core_yoy" in infl:
            line += ", core %.1f%%" % infl["core_yoy"]
        if core_mom is not None:
            line += "; core rose %.2f%% on the month" % core_mom
            if core_prev is not None:
                line += ", %s the %.2f%% average of the three months before" % (
                    "faster than" if core_mom > core_prev + .03 else "slower than" if core_mom < core_prev - .03
                    else "close to", core_prev)
        S.append(line + ".")

    ur, pay = bl.get("ur"), bl.get("pay")
    ur3 = avg3 = prev3 = None
    if ur and len(ur) >= 4:
        ur3 = ur[-1][1] - ur[-4][1]
    if pay and len(pay) >= 7:
        avg3 = (pay[-1][1] - pay[-4][1]) / 3
        prev3 = (pay[-4][1] - pay[-7][1]) / 3
    if ur or pay:
        lab_ = {"ref": (ur or pay)[-1][0][:7]}
        line = "Jobs (%s):" % C.parse_date((ur or pay)[-1][0]).strftime("%b")
        bits = []
        if pay and len(pay) >= 2:
            lab_["pay_last"] = round(pay[-1][1] - pay[-2][1])
            bits.append("payrolls %s" % "{:+,.0f}K".format(pay[-1][1] - pay[-2][1]))
        if avg3 is not None:
            lab_["pay_avg3"], lab_["pay_prev3"] = round(avg3), round(prev3)
            bits.append("3-month average {:,.0f}K (the three months before: {:,.0f}K)".format(avg3, prev3))
        if ur:
            lab_["ur"] = ur[-1][1]
            t = "unemployment %.1f%%" % ur[-1][1]
            if ur3 is not None:
                lab_["ur_chg3"] = round(ur3, 2)
                t += " (%+.1f pt over 3 months)" % ur3
            bits.append(t)
        read["labor"] = lab_
        S.append(line + " " + ", ".join(bits) + ".")

    # market tone
    mk = {}
    for key in ("spx", "vix", "brent", "dxy", "gold"):
        p = y.get(key)
        if p and len(p) >= 6:
            mk[key] = {"last": p[-1][1], "d": p[-1][0], "chg1": round(pct(p, 1), 2), "chg5": round(pct(p, 5), 2),
                       "chg20": round(pct(p, 20), 2) if len(p) > 20 else None}
    if "spx" in mk:
        p = y["spx"]
        hi = max(v for _, v in p)
        mk["spx"]["from_high"] = round((p[-1][1] / hi - 1) * 100, 2)
        line = "Stocks: the S&P 500 is %s (%+.1f%% on the day, %+.1f%% over 5 days), %.1f%% below its %d-day high" % (
            format(p[-1][1], ",.0f"), mk["spx"]["chg1"], mk["spx"]["chg5"], abs(mk["spx"]["from_high"]), len(p))
        if "vix" in mk:
            line += "; VIX %.1f is %s" % (mk["vix"]["last"], vix_word(mk["vix"]["last"]))
        S.append(line + ".")
    bits = []
    if "brent" in mk:
        bits.append("Brent $%.2f (%+.1f%% in 5 days)" % (mk["brent"]["last"], mk["brent"]["chg5"]))
    if "dxy" in mk:
        bits.append("dollar index %.2f (%+.1f%% in 5 days)" % (mk["dxy"]["last"], mk["dxy"]["chg5"]))
    if "gold" in mk:
        bits.append("gold $%s (%+.1f%% in 5 days)" % (format(mk["gold"]["last"], ",.0f"), mk["gold"]["chg5"]))
    if bits:
        S.append("Oil, dollar and gold: " + "; ".join(bits) + ".")
    read["market"] = mk

    # ---- hawkish/dovish leads, computed from the numbers (each v: -1 dovish ... +1 hawkish)
    L = read["leads"]
    if pol and "gap_6m" in pol:
        net = pol["gap_6m"] - BILL_PREMIUM_BP
        L["dec"].append(lead("bills", "The 6-month bill yields %.0fbp above the policy rate; after a normal %dbp bill premium that "
                             "is about %+.0fbp (%+.1f quarter-point steps) priced over six months, a rough read of the market's path"
                             % (pol["gap_6m"], BILL_PREMIUM_BP, net, net / 25), net / 30, .8, today))
    d2 = bp(nom, "2y", 5) if nom else None
    if d2 is not None and nom and "2y" in nom[-1]["v"]:
        L["dec"].append(lead("y2", "The 2Y yield, the market's read of the Fed, moved %+.0fbp in 5 trading days (now %.2f%%)"
                             % (d2, nom[-1]["v"]["2y"]), d2 / 12, .6, today))
    if be and be["d5"] is not None:
        L["dec"].append(lead("be", "The 10Y inflation breakeven is %.2f%%, %+.0fbp over 5 days" % (be["now"], be["d5"]),
                             be["d5"] / 8, .4, today))
    if "brent" in mk:
        L["dec"].append(lead("oil5", "Brent moved %+.1f%% over 5 trading days (now $%.2f)" % (mk["brent"]["chg5"], mk["brent"]["last"]),
                             mk["brent"]["chg5"] / 8, .5, today))
    if "vix" in mk:
        L["dec"].append(lead("vix", "VIX at %.1f (%s): stress makes the Fed less willing to tighten" % (
            mk["vix"]["last"], vix_word(mk["vix"]["last"])), clamp((18 - mk["vix"]["last"]) / 12, -1, .3), .4, today))
    if core_mom is not None:
        t = ("Latest core CPI (%s) rose %.2f%% on the month; 0.25%% a month is about 3%% a year"
             % (C.parse_date(cpi[-1][0]).strftime("%b"), core_mom))
        L["dec"].append(lead("core", t, (core_mom - .25) / .12, .7, today))
        L["cpi"].append(lead("corelast", t, (core_mom - .25) / .12, .5, today))
    if ur3 is not None:
        t = "Unemployment is %.1f%%, %+.1f pt over 3 months; a rising rate argues for patience" % (ur[-1][1], ur3)
        L["dec"].append(lead("ur", t, -ur3 / .3, .5, today))
        L["nfp"].append(lead("ur", t, -ur3 / .3, .5, today))
    if avg3 is not None:
        L["nfp"].append(lead("paymom", "Payroll growth averaged {:,.0f}K over the last 3 months against {:,.0f}K in the 3 before"
                             .format(avg3, prev3), (avg3 - prev3) / 60, .5, today))
    # CPI: oil in the reference month versus the month before it
    if cpi and "brent" in y:
        ly, lm = C.parse_date(cpi[-1][0]).year, C.parse_date(cpi[-1][0]).month
        ry, rm = (ly, lm + 1) if lm < 12 else (ly + 1, 1)
        py, pm = (ry, rm - 1) if rm > 1 else (ry - 1, 12)
        a, na = month_mean(y["brent"], ry, rm)
        b, nb = month_mean(y["brent"], py, pm)
        if a and b and na >= 3 and nb >= 10:
            ch = (a / b - 1) * 100
            L["cpi"].append(lead("oilm", "Brent averaged $%.2f in %s against $%.2f in the month before, %+.1f%%; "
                                 "oil feeds pump prices within weeks" % (a, date(ry, rm, 1).strftime("%b"), b, ch),
                                 ch / 10, .8, today))
    if be and be["d20"] is not None:
        L["cpi"].append(lead("be20", "The 10Y breakeven moved %+.0fbp over 20 trading days" % be["d20"], be["d20"] / 10, .5, today))

    for k, v in list(L.items()):
        if not v:
            L.pop(k)
    read["auto_lean"] = {k: round(auto_lean(v), 3) for k, v in L.items()}
    # what the next BLS prints are (for the scorecard)
    read["refs"] = {}
    if cs:
        read["refs"]["cpi"] = next_month(cs[-1][0])
    if pay:
        read["refs"]["nfp"] = next_month(pay[-1][0])

    read["changes"] = changes(read, prev_read)
    return read


def next_month(iso_s):
    d = C.parse_date(iso_s)
    return (date(d.year + (d.month == 12), d.month % 12 + 1, 1)).isoformat()[:7]


def auto_lean(leads):
    den = sum(l["w"] for l in leads)
    return sum(l["v"] * l["w"] for l in leads) / den if den else 0.0


def changes(read, prev):
    """Plain sentences on what moved since the last run."""
    out = []
    if not prev:
        return out
    pc, c = (prev.get("curve") or {}), (read.get("curve") or {})
    if pc.get("y10") is not None and c.get("y10") is not None and abs(c["y10"] - pc["y10"]) >= .05:
        out.append("The 10Y yield moved from %.2f%% to %.2f%% since the last run." % (pc["y10"], c["y10"]))
    pm, m = (prev.get("market") or {}), (read.get("market") or {})
    if "brent" in pm and "brent" in m and pm["brent"]["last"] and abs(m["brent"]["last"] / pm["brent"]["last"] - 1) >= .03:
        out.append("Brent moved from $%.2f to $%.2f." % (pm["brent"]["last"], m["brent"]["last"]))
    if "vix" in pm and "vix" in m and abs(m["vix"]["last"] - pm["vix"]["last"]) >= 2:
        out.append("VIX moved from %.1f to %.1f." % (pm["vix"]["last"], m["vix"]["last"]))
    names = {"dec": "next Fed decision", "cpi": "CPI", "nfp": "jobs report"}
    pl = prev.get("auto_lean") or {}
    for k, v in (read.get("auto_lean") or {}).items():
        if k in pl and abs(v - pl[k]) >= .1:
            out.append("The data-driven lean for the %s moved from %+.2f to %+.2f (%s)." % (
                names.get(k, k), pl[k], v, "more hawkish" if v > pl[k] else "more dovish"))
    pi, i = (prev.get("inflation") or {}), (read.get("inflation") or {})
    if pi.get("ref") and i.get("ref") and pi["ref"] != i["ref"]:
        out.append("A new CPI month arrived: %s." % i["ref"])
    pj, j = (prev.get("labor") or {}), (read.get("labor") or {})
    if pj.get("ref") and j.get("ref") and pj["ref"] != j["ref"]:
        out.append("A new jobs month arrived: %s." % j["ref"])
    return out


def main(argv=None):
    C.safe_console()
    ck = C.read_json(C.WORK / "checked.json")
    if not ck:
        print("work/checked.json not found. Run validate.py first.", file=sys.stderr)
        return 1
    cl = ck["clean"]
    today = C.parse_date(cl["today"])
    prev = (C.load_previous_data() or {}).get("read")
    read = understand(cl, today, prev)
    levels = build_levels(cl, today)
    C.write_json(C.WORK / "read.json", {"read": read, "levels": levels})
    for line in read["summary"]:
        print(" -", line)
    for k, v in read["leads"].items():
        print("%s: auto lean %+.2f from %d leads" % (k, read["auto_lean"][k], len(v)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

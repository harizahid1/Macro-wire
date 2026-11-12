#!/usr/bin/env python3
"""STEP 4 of 4 - PROJECT.  Puts what was understood onto the page and keeps the run history.

    python3 project.py

Reads work/checked.json and work/read.json, then:
  * writes data.js (levels, clean headlines, data checks, the findings and the data-driven leads)
  * copies the local model's reads (llm_cache.json) onto the headlines, with the accuracy-test result
  * keeps the last good value for any tile whose data was quarantined, and marks it "not refreshed"
  * appends one line to history.jsonl (the run's numbers and leads)
  * updates scorecard.json: after each CPI or jobs release it compares the lean this system gave
    BEFORE the release with what the data then did, so you can see whether the leads are any good
  * writes last_report.txt, the same plain-language report that is printed
"""
import json
import sys
from datetime import datetime, timezone

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))  # find the sibling scripts however it is launched
import common as C
import llm_read

TILE_KEYS = ["ffr", "cpi", "un", "y10", "dxy", "brent", "gold", "spx"]
MIN_CALL = 0.15   # leans smaller than this are "no call" and are not scored


# ------------------------------------------------------------------ scorecard
def outcome(kind, ref, bls):
    """What the data did versus its own recent trend, for a release month 'YYYY-MM'. None if not out yet."""
    key = ref + "-01"
    if kind == "cpi":
        s = (bls or {}).get("core_sa")
        if not s:
            return None
        idx = next((i for i, p in enumerate(s) if p[0] == key), None)
        if idx is None or idx < 4:
            return None
        m = lambda i: (s[i][1] / s[i - 1][1] - 1) * 100
        return m(idx) - (m(idx - 1) + m(idx - 2) + m(idx - 3)) / 3
    if kind == "nfp":
        s = (bls or {}).get("pay")
        if not s:
            return None
        idx = next((i for i, p in enumerate(s) if p[0] == key), None)
        if idx is None or idx < 4:
            return None
        return (s[idx][1] - s[idx - 1][1]) - (s[idx - 1][1] - s[idx - 4][1]) / 3
    return None


def update_scorecard(history, bls, today):
    card = C.read_json(C.SCORECARD, {"calls": []}) or {"calls": []}
    done = {(c["id"], c["ref"]) for c in card["calls"]}
    last_call = {}
    for h in history:
        for c in h.get("calls", []):
            last_call[(c["id"], c["ref"])] = dict(c, asof=h.get("today"))
    for (kid, ref), c in last_call.items():
        if (kid, ref) in done or abs(c["lean"]) < MIN_CALL:
            continue
        a = outcome(kid, ref, bls)
        if a is None:
            continue
        card["calls"].append({"id": kid, "ref": ref, "asof": c["asof"], "lean": round(c["lean"], 2),
                              "actual": round(a, 3), "hit": (a > 0) == (c["lean"] > 0)})
    summ = {}
    for c in card["calls"]:
        s = summ.setdefault(c["id"], {"n": 0, "hits": 0})
        s["n"] += 1
        s["hits"] += 1 if c["hit"] else 0
    card["summary"] = summ
    card["note"] = ("Machine lean only (no typed leads). 'Hit' means the release came in above (or below) its own "
                    "3-month trend in the direction the lean pointed. It is a proxy, not a score against consensus, "
                    "and a few calls prove little.")
    return card


# ------------------------------------------------------------------ build
def project(clean, understood, today, now, prev, history):
    levels_new, status = understood["levels"], {}
    levels = {}
    for k in TILE_KEYS:
        if k in levels_new:
            levels[k] = levels_new[k]
            status[k] = "refreshed"
        elif isinstance((prev.get("levels") or {}).get(k), dict):
            kept = dict(prev["levels"][k])
            kept["stale"] = True
            levels[k] = kept
            status[k] = "kept previous value"
        else:
            status[k] = "no data (page keeps its built-in snapshot)"

    heads = clean.get("feeds") or []
    kept_heads = False
    if not heads and prev.get("headlines"):
        heads, kept_heads = prev["headlines"], True

    heads, llm_meta = llm_read.attach(heads, today)

    checks = understood["checks"]
    report = list(clean.get("sources") or [])
    s = checks["summary"]
    report.append({"name": "Data checks", "ok": s["fail"] == 0,
                   "detail": "%d checks: %d ok, %d warnings, %d failed" % (s["total"], s["ok"], s["warn"], s["fail"])})
    if kept_heads:
        report.append({"name": "Headlines", "ok": False, "detail": "no feed answered; kept the previous run's headlines"})

    refreshed = any(v == "refreshed" for v in status.values())
    fresh = refreshed or (bool(heads) and not kept_heads)
    generated = now.isoformat(timespec="seconds") if fresh else prev.get("generated") or now.isoformat(timespec="seconds")
    fs = clean.get("feed_stats") or {}
    stats = {"feeds_ok": fs.get("ok", 0), "feeds_total": fs.get("total", 0), "raw": fs.get("raw", 0), "kept": len(heads)}
    if heads:
        stats["newest_t"] = heads[0].get("t") or heads[0]["d"]
        stats["newest_src"] = heads[0].get("src", "")

    read = understood["read"]
    page_read = {k: v for k, v in read.items() if k != "refs"}
    items = [i for i in checks["items"] if i["status"] != "ok"][:40]
    data = {"pipeline": 2, "generated": generated, "levels": levels, "headlines": heads, "stats": stats,
            "report": report,
            "checks": {"summary": s, "items": items, "quarantined": checks["quarantined"]},
            "read": page_read}
    if llm_meta:
        data["llm"] = llm_meta

    calls = [{"id": k, "ref": read["refs"][k], "lean": v} for k, v in (read.get("auto_lean") or {}).items()
             if k in read.get("refs", {})]
    entry = {"run": generated, "today": today.isoformat(),
             "levels": {k: v["v"] for k, v in levels.items()}, "auto_lean": read.get("auto_lean"),
             "checks": s, "calls": calls}
    hist = history + [entry]
    card = update_scorecard(hist, clean.get("bls"), today)
    data["scorecard"] = {"summary": card["summary"], "note": card["note"], "n": len(card["calls"])}
    return data, status, kept_heads, fresh, hist, card


def apply_llm():
    """Re-copies the local model's reads onto the headlines already in data.js (no new download). True if it did."""
    prev = C.load_previous_data()
    if not prev or not prev.get("headlines"):
        return False
    heads, meta = llm_read.attach(prev["headlines"])
    prev["headlines"] = heads
    if meta:
        prev["llm"] = meta
    else:
        prev.pop("llm", None)
    write_data(prev)
    return True


def write_data(data):
    body = ("/* Written by update.py (collect, validate, understand, project). Safe to delete; "
            "the page then shows its built-in snapshot. */\n"
            "window.MW_DATA = " + json.dumps(data, ensure_ascii=True, indent=1) + ";\n")
    tmp = C.DATA_JS.with_suffix(".js.tmp")
    tmp.write_text(body, encoding="utf-8")
    tmp.replace(C.DATA_JS)


def load_history():
    out = []
    try:
        for line in C.HISTORY.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
    except Exception:  # noqa: BLE001
        pass
    return out


def save_history(hist):
    hist = hist[-400:]
    tmp = C.HISTORY.with_suffix(".jsonl.tmp")
    tmp.write_text("\n".join(json.dumps(h, ensure_ascii=True) for h in hist) + "\n", encoding="utf-8")
    tmp.replace(C.HISTORY)


def plain_report(data, status, kept_heads, card):
    L = []
    L.append("MACRO WIRE RUN  %s" % data["generated"])
    L.append("")
    L.append("1. COLLECT - sources")
    for r in data["report"]:
        L.append("   %-22s %s  %s" % (r["name"], "ok  " if r["ok"] else "FAIL", r["detail"]))
    s = data["checks"]["summary"]
    L.append("")
    L.append("2. VALIDATE - %d checks: %d ok, %d warnings, %d failed" % (s["total"], s["ok"], s["warn"], s["fail"]))
    for i in data["checks"]["items"]:
        L.append("   %-4s %s" % (i["status"].upper(), i["detail"]))
    if data["checks"]["quarantined"]:
        L.append("   Quarantined (not used): " + ", ".join(data["checks"]["quarantined"]))
    L.append("")
    L.append("3. UNDERSTAND - what the data says")
    for line in data["read"]["summary"]:
        L.append("   - " + line)
    for line in data["read"].get("changes", []):
        L.append("   * changed: " + line)
    L.append("")
    L.append("   Data-driven leans (-1 dovish ... +1 hawkish; the page adds its typed leads to these)")
    names = {"dec": "next Fed decision", "cpi": "CPI", "nfp": "jobs report"}
    for k, leads in data["read"]["leads"].items():
        L.append("   %-18s %+.2f  from %d leads" % (names.get(k, k), data["read"]["auto_lean"][k], len(leads)))
        for l in sorted(leads, key=lambda x: -abs(x["v"] * x["w"])):
            L.append("      %+.2f (weight %.1f)  %s" % (l["v"], l["w"], l["n"]))
    L.append("")
    L.append("4. PROJECT - onto the page")
    for k, v in status.items():
        L.append("   tile %-6s %s" % (k, v))
    L.append("   headlines: %d kept of %s fetched from %s of %s feeds%s" % (
        data["stats"]["kept"], data["stats"].get("raw", "?"), data["stats"]["feeds_ok"], data["stats"]["feeds_total"],
        " (kept from previous run)" if kept_heads else ""))
    lm = data.get("llm")
    if lm:
        L.append("   local model %s: read %d of %d headlines; %s" % (
            lm.get("model") or "?", lm.get("reads", 0), lm.get("total", 0),
            "accuracy test passed, its reads are counted" if lm.get("verified") else "NOT counted (" + (lm.get("note") or "no passing accuracy test") + ")"))
    if card["summary"]:
        L.append("   scorecard (machine lean vs what the data then did): " + "; ".join(
            "%s %d of %d" % ({"cpi": "CPI", "nfp": "jobs"}.get(k, k), v["hits"], v["n"]) for k, v in card["summary"].items()))
    else:
        L.append("   scorecard: no scored calls yet (needs a CPI or jobs release after a run that made a call)")
    L.append("   wrote data.js - reload macro-wire.html")
    return "\n".join(L)


def main(argv=None):
    C.safe_console()
    ck = C.read_json(C.WORK / "checked.json")
    rd = C.read_json(C.WORK / "read.json")
    if not ck or not rd:
        print("work/checked.json or work/read.json missing. Run the earlier steps first.", file=sys.stderr)
        return 1
    clean = ck["clean"]
    today = C.parse_date(clean["today"])
    now = datetime.now(timezone.utc)
    data, status, kept, fresh, hist, card = project(
        clean, {"levels": rd["levels"], "read": rd["read"], "checks": ck["checks"]}, today, now,
        C.load_previous_data() or {}, load_history())
    write_data(data)
    save_history(hist)
    C.write_json(C.SCORECARD, card)
    txt = plain_report(data, status, kept, card)
    C.REPORT.write_text(txt + "\n", encoding="utf-8")
    print(txt)
    return 0 if fresh else 1


if __name__ == "__main__":
    sys.exit(main())

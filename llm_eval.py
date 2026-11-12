#!/usr/bin/env python3
"""ACCURACY TEST for the local model.   python3 llm_eval.py

Gives your model the 50 hand-labelled headlines in llm_eval_set.json (none of them is used as a teaching
example in the prompt), compares its answers with the labels, and writes llm_grade.json.

The page only COUNTS the model's reads when this test says PASS for that same model. Pass rules:
    valid answers          at least 95%   (the answer must be valid JSON, a real driver, a number from -1 to +1)
    right driver           at least 70%   (the main driver the headline is about, or "none" for non-macro)
    right direction        at least 80%   (hawkish vs dovish, hotter vs cooler ... on headlines that have a direction)
    false alarms           at most 20%    (a strong call on a headline that is neutral or not macro)

The keyword reader is graded on the same headlines so you can see which one is better. The labels were
written by hand and are simply my judgement; open llm_eval_set.json and fix or add lines you disagree with.
Standard library only.
"""
import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))  # find the sibling scripts however it is launched
import common as C
import llm_read as L

RULES = {"valid": 0.95, "driver": 0.70, "direction": 0.80, "false_alarm": 0.20}
MIN_SCORE = 0.15     # a score smaller than this counts as "no direction"
ALARM_SCORE = 0.5    # a score this large on a neutral headline counts as a false alarm


def sign(x):
    return 1 if x > 0 else -1 if x < 0 else 0


def grade(items, preds):
    """items: labelled headlines; preds: list of {driver, score} or None. Returns the four rates."""
    n = len(items)
    valid = sum(1 for p in preds if p)
    drv_ok = sum(1 for it, p in zip(items, preds) if p and p["driver"] == it["driver"])
    dir_items = [(it, p) for it, p in zip(items, preds) if it["sign"] != 0]
    dir_ok = sum(1 for it, p in dir_items if p and abs(p["score"]) >= MIN_SCORE and sign(p["score"]) == it["sign"])
    flat = [(it, p) for it, p in zip(items, preds) if it["sign"] == 0]
    alarms = sum(1 for it, p in flat if p and p["driver"] != "none" and abs(p["score"]) >= ALARM_SCORE)
    return {"n": n, "valid": round(valid / n, 3) if n else 0, "driver": round(drv_ok / n, 3) if n else 0,
            "direction": round(dir_ok / len(dir_items), 3) if dir_items else 0,
            "false_alarm": round(alarms / len(flat), 3) if flat else 0}


def passes(g):
    return (g["valid"] >= RULES["valid"] and g["driver"] >= RULES["driver"]
            and g["direction"] >= RULES["direction"] and g["false_alarm"] <= RULES["false_alarm"])


def load_items():
    d = C.read_json(C.EVAL_SET, None) or {}
    items = [i for i in d.get("items", []) if isinstance(i, dict) and i.get("title") and i.get("sign") in (-1, 0, 1)
             and (i.get("driver") in L.DRIVERS or i.get("driver") == "none")]
    return items


def main(argv=None):
    C.safe_console()
    ap = argparse.ArgumentParser(description="Grade the local model on labelled headlines.")
    ap.add_argument("--url")
    ap.add_argument("--model")
    args = ap.parse_args(argv)
    items = load_items()
    if len(items) < 20:
        print("llm_eval_set.json has fewer than 20 usable items; the test would mean little.", file=sys.stderr)
        return 1
    shots = {L.norm_title(e[0]) for e in L.EXAMPLES}
    clash = [i["title"] for i in items if L.norm_title(i["title"]) in shots]
    if clash:
        print("These test headlines are also teaching examples in the prompt, remove one or the other:\n  " + "\n  ".join(clash), file=sys.stderr)
        return 1
    cfg = L.load_config(args.url, args.model)
    print("Asking %s ..." % cfg["url"])
    try:
        mdl = L.pick_model(L.list_models(cfg["url"]), cfg["model"])
    except L.LLMError as e:
        print("Could not reach the model: %s\nStart Ollama (or LM Studio) and pull/load the model, then try again. See SETUP_QWEN.md." % e)
        return 1
    print("Model: %s. Reading %d headlines (this can take a few minutes on a laptop)..." % (mdl, len(items)))
    t0 = time.time()
    try:
        preds = L.read_titles([i["title"] for i in items], cfg["url"], mdl, int(cfg["batch"]), int(cfg["timeout"]),
                              int(cfg["max_tokens"]), lambda m: print("   " + m))
    except L.LLMError as e:
        print("The model stopped answering: %s" % e)
        return 1
    pred2 = [{"driver": p["driver"], "score": p["score"]} if p else None for p in preds]
    g = grade(items, pred2)
    kw_items = [i for i in items if isinstance(i.get("kw"), dict)]
    gk = grade(kw_items, [{"driver": i["kw"].get("driver", "none"), "score": float(i["kw"].get("score", 0))} for i in kw_items]) if len(kw_items) == len(items) else None
    ok = passes(g)

    print("\nHeadlines the model got wrong:")
    wrong = 0
    for it, p in zip(items, pred2):
        bad = (p is None or p["driver"] != it["driver"]
               or (it["sign"] != 0 and (abs(p["score"]) < MIN_SCORE or sign(p["score"]) != it["sign"]))
               or (it["sign"] == 0 and p["driver"] != "none" and abs(p["score"]) >= ALARM_SCORE))
        if bad:
            wrong += 1
            print("  expected %-9s %+d | got %-30s | %s" % (it["driver"], it["sign"],
                  "INVALID" if p is None else "%s %+.2f" % (p["driver"], p["score"]), it["title"][:70]))
    if not wrong:
        print("  none")

    def pct(x):
        return "%3.0f%%" % (100 * x)
    print("\n                         model    keyword reader    rule")
    rows = [("valid answers", "valid", ">= %d%%" % (100 * RULES["valid"])), ("right driver", "driver", ">= %d%%" % (100 * RULES["driver"])),
            ("right direction", "direction", ">= %d%%" % (100 * RULES["direction"])), ("false alarms", "false_alarm", "<= %d%%" % (100 * RULES["false_alarm"]))]
    for label, k, rule in rows:
        print("  %-18s %8s %12s        %s" % (label, pct(g[k]), pct(gk[k]) if gk else "  n/a", rule))
    print("\n%s  (%d headlines, %.0f s)" % ("PASS - the page will count this model's reads." if ok else
          "NOT PASSED - the page will show this model's reads but not count them.", g["n"], time.time() - t0))

    h = hashlib.sha1(json.dumps(items, sort_keys=True).encode("utf-8")).hexdigest()[:10]
    C.write_json(C.LLM_GRADE, {"model": mdl, "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "n": g["n"],
                               "valid": g["valid"], "driver": g["driver"], "direction": g["direction"],
                               "false_alarm": g["false_alarm"], "pass": ok, "rules": RULES, "kw": gk, "set": h})
    print("Saved llm_grade.json" + (" - commit and push it together with llm_cache.json if you use GitHub." if ok else "."))
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())

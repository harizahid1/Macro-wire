#!/usr/bin/env python3
"""Macro Wire updater: one command runs all four steps.

    python3 update.py            collect, validate, understand, project
    python3 update.py --open     ... then open the page in your browser
    python3 update.py --quiet    print nothing except errors
    python3 update.py --no-llm   do not ask your local model (see llm_read.py)

  1 collect.py     gets the raw data (Treasury, BLS, NY Fed, Yahoo, headline feeds)   -> work/raw.json
  2 validate.py    tests every number; bad or stale values are thrown out             -> work/checked.json
  3 understand.py  works out what the numbers mean and the hawkish-or-dovish leads    -> work/read.json
    llm_read.py    (optional) your local model reads the new headlines                 -> llm_cache.json
  4 project.py     writes data.js for the page, the run history and the scorecard

Each step is also a script you can run on its own, and each leaves its result in work/ so you can
open it and see exactly what happened. last_report.txt holds the plain-language report of the run.
Needs Python 3.8+ and nothing else (standard library only, no API keys).
"""
import argparse
import sys
import webbrowser
from datetime import datetime, timezone

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))  # find the sibling scripts however it is launched
import collect
import common as C
import llm_read
import project
import understand
import validate


def main(argv=None):
    C.safe_console()
    ap = argparse.ArgumentParser(description="Refresh Macro Wire.")
    ap.add_argument("--open", action="store_true", help="open the page afterwards")
    ap.add_argument("--quiet", action="store_true", help="print only errors")
    ap.add_argument("--no-llm", action="store_true", help="do not ask the local model (earlier reads in llm_cache.json are still used)")
    ap.add_argument("--fixtures", metavar="DIR", help="read recorded files instead of the internet (testing)")
    ap.add_argument("--today", metavar="YYYY-MM-DD", help="pretend today is this date (testing)")
    args = ap.parse_args(argv)
    C.set_fixtures(args.fixtures)

    def say(*a):
        if not args.quiet:
            print(*a)

    now = datetime.now(timezone.utc)
    today = C.parse_date(args.today) if args.today else now.date()

    say("1 of 4  COLLECT")
    raw = collect.collect(today, say)
    C.write_json(C.WORK / "raw.json", raw)

    say("2 of 4  VALIDATE")
    clean, checks = validate.validate(raw, today)
    C.write_json(C.WORK / "checked.json", {"clean": clean, "checks": checks})

    say("3 of 4  UNDERSTAND")
    prev = C.load_previous_data() or {}
    read = understand.understand(clean, today, prev.get("read"))
    levels = understand.build_levels(clean, today)
    C.write_json(C.WORK / "read.json", {"read": read, "levels": levels})
    llm_read.run(clean, today, say, enabled=not args.no_llm)

    say("4 of 4  PROJECT\n")
    data, status, kept, fresh, hist, card = project.project(
        clean, {"levels": levels, "read": read, "checks": checks}, today, now, prev, project.load_history())
    project.write_data(data)
    project.save_history(hist)
    C.write_json(C.SCORECARD, card)
    txt = project.plain_report(data, status, kept, card)
    C.REPORT.write_text(txt + "\n", encoding="utf-8")
    say(txt)

    if not fresh:
        print("Nothing could be fetched. Check your internet connection or any proxy/VPN, "
              "then run it again. The previous data was kept.", file=sys.stderr)
        return 1
    if args.open and C.HTML.exists():
        webbrowser.open(C.HTML.as_uri())
    return 0


if __name__ == "__main__":
    sys.exit(main())

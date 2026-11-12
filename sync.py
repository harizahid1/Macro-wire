#!/usr/bin/env python3
"""Reads the newest headlines with your local model and sends the readings to your GitHub repository.

    python3 sync.py            get the latest data from GitHub, read new headlines with your model, push the readings
    python3 sync.py --grade    run the accuracy test first (do this once, and again whenever you change the model)

Why this exists: the GitHub Action refreshes the data on GitHub's servers, but your model lives on your
computer, so GitHub can not ask it anything. This script does the part that needs your model and uploads only
two small files (llm_cache.json and llm_grade.json). The Action then starts by itself and puts the readings on the page.

Needs git installed and this folder to be a clone of your repository (SETUP_GITHUB.md, option B).
It never uploads data.js or anything else, and it never uses a password: git uses the sign-in you already have.
"""
import argparse
import subprocess
import sys

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))  # find the sibling scripts however it is launched
import common as C
import llm_eval
import llm_read

BOT_FILES = ["data.js", "history.jsonl", "scorecard.json", "last_report.txt"]   # written by the Action, never by you
MY_FILES = ["llm_cache.json", "llm_grade.json"]


def git(*args, check=True):
    r = subprocess.run(["git", *args], cwd=str(C.HERE), capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError("git %s failed:\n%s" % (" ".join(args), (r.stderr or r.stdout).strip()))
    return r


def put_aside_bot_files():
    """The Action owns data.js and friends. A local copy (from running update.py here) would block the pull:
    restore the tracked ones, and keep an untracked one as <name>.local."""
    for f in BOT_FILES:
        p = C.HERE / f
        if not p.exists():
            continue
        if git("ls-files", "--error-unmatch", f, check=False).returncode == 0:
            git("checkout", "--", f, check=False)
        else:
            p.replace(C.HERE / (f + ".local"))


def main(argv=None):
    C.safe_console()
    ap = argparse.ArgumentParser(description="Read headlines with your local model and push the readings to GitHub.")
    ap.add_argument("--grade", action="store_true", help="run the accuracy test first")
    args = ap.parse_args(argv)

    try:
        inside = git("rev-parse", "--is-inside-work-tree", check=False)
    except FileNotFoundError:
        inside = None
    if inside is None or inside.returncode != 0 or inside.stdout.strip() != "true":
        print("This folder is not a git clone of your repository (or git is not installed).\n"
              "Without git you can still do it by hand: run  python3 llm_read.py --no-project  and then upload\n"
              "llm_cache.json (and llm_grade.json) on your repository page: Add file > Upload files > Commit.\n"
              "See SETUP_GITHUB.md.")
        return 1

    try:
        print("1. Getting the latest data from GitHub ...")
        put_aside_bot_files()
        git("pull", "--rebase")

        if args.grade:
            print("\n2. Accuracy test")
            rc = llm_eval.main([])
            if rc == 1:
                return 1
        else:
            print("\n2. Accuracy test skipped (use --grade to run it)")

        print("\n3. Reading new headlines with your local model")
        if llm_read.main(["--no-project"]) != 0:
            print("Nothing was read, so there is nothing to upload.")
            return 1

        print("\n4. Sending the readings to GitHub")
        git("add", *[f for f in MY_FILES if (C.HERE / f).exists()])
        if git("diff", "--cached", "--quiet", check=False).returncode == 0:
            print("No new readings or test results, so nothing to upload.")
            return 0
        git("commit", "-m", "Headline readings from the local model")
        git("pull", "--rebase")
        git("push")
        print("Done. The GitHub Action now starts and puts the readings on the page (about a minute).")
        return 0
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        print("\nIf this is a sign-in problem, make sure you can run  git push  from this folder by hand once.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

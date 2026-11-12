"""Shared helpers for the Macro Wire pipeline. Standard library only (Python 3.8+)."""
import hashlib
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
WORK = HERE / "work"            # one JSON file per step, so every step can be inspected
DATA_JS = HERE / "data.js"      # what the page reads
HISTORY = HERE / "history.jsonl"
SCORECARD = HERE / "scorecard.json"
REPORT = HERE / "last_report.txt"
HTML = HERE / "macro-wire.html"
LLM_CONFIG = HERE / "llm_config.json"   # which local model to ask (url, model name)
LLM_CACHE = HERE / "llm_cache.json"     # what the local model said about each headline
LLM_GRADE = HERE / "llm_grade.json"     # the accuracy test result that decides whether the page trusts those reads
EVAL_SET = HERE / "llm_eval_set.json"   # labelled headlines used by llm_eval.py

UA = "Mozilla/5.0 (compatible; MacroWire/2.0; personal dashboard updater)"
TIMEOUT = 25

# FOMC decision days for 2026 (second day of each meeting).
FOMC = [date(2026, m, d) for m, d in
        [(1, 28), (3, 18), (4, 29), (6, 17), (7, 29), (9, 16), (10, 28), (12, 9)]]

FIXTURE_DIR = Path(os.environ["MW_FIXTURES"]) if os.environ.get("MW_FIXTURES") else None  # testing: recorded files instead of the internet


def safe_console():
    """A terminal that can not print every symbol should not crash the run; replace instead."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:  # noqa: BLE001
            pass


def set_fixtures(path):
    global FIXTURE_DIR
    FIXTURE_DIR = Path(path) if path else None


def fixture_name(url, data=None):
    """Maps a request to a recorded file name (used by --fixtures and by the tests)."""
    if "api.bls.gov" in url:
        return "bls.json"
    if "newyorkfed.org" in url:
        return "nyfed.json"
    if "daily_treasury_real_yield_curve" in url:
        return "treasury_real.csv"
    if "treasury.gov" in url:
        return "treasury_nominal.csv"
    if "finance/chart" in url:
        sym = urllib.parse.unquote(url.split("/chart/")[1].split("?")[0])
        return "yahoo_" + re.sub(r"[^A-Za-z0-9]", "_", sym) + ".json"
    if "fredgraph.csv" in url:
        return "fred_" + url.split("id=")[1].split("&")[0] + ".csv"
    return "feed_" + hashlib.md5(url.encode("utf-8")).hexdigest()[:10] + ".xml"


def fetch(url, timeout=TIMEOUT, data=None, headers=None):
    if FIXTURE_DIR is not None:
        p = FIXTURE_DIR / fixture_name(url, data)
        return p.read_text(encoding="utf-8")
    h = {"User-Agent": UA, "Accept": "*/*"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=data, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, ssl.SSLError) as e:
        ctx = mac_ca_context() if is_cert_error(e) else None
        if ctx is None:
            raise
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:   # same request, macOS's own root certificates
            return r.read().decode("utf-8", errors="replace")


def is_cert_error(e):
    r = getattr(e, "reason", e)
    return isinstance(r, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(e)


def mac_ca_context():
    """Python from python.org on a Mac has no root certificates until its 'Install Certificates.command' is run, so
    every HTTPS request fails with CERTIFICATE_VERIFY_FAILED. macOS keeps the roots in /etc/ssl/cert.pem; use those.
    Certificate checking is never switched off. Returns None when that file is not there."""
    for path in ("/etc/ssl/cert.pem", "/private/etc/ssl/cert.pem"):
        if os.path.exists(path):
            try:
                return ssl.create_default_context(cafile=path)
            except Exception:  # noqa: BLE001
                return None
    return None


FAST_FAIL = {"FRED"}  # slow service: after one timeout, skip it for the rest of this run


class Src:
    """Runs one request at a time, never raises, and keeps score per source."""

    def __init__(self):
        self.stats = {}
        self.dead = set()

    def call(self, group, fn, *args):
        s = self.stats.setdefault(group, {"ok": 0, "fail": 0, "skip": 0, "err": ""})
        if group in self.dead:
            s["skip"] += 1
            return None
        try:
            result = fn(*args)
            s["ok"] += 1
            return result
        except Exception as e:  # noqa: BLE001 - any failure just means "no data"
            s["fail"] += 1
            s["err"] = "%s: %s" % (type(e).__name__, str(e)[:90])
            if group in FAST_FAIL and "timed out" in str(e).lower():
                self.dead.add(group)
            return None

    def report(self):
        out = []
        for name, s in self.stats.items():
            n = s["ok"] + s["fail"]
            detail = "%d of %d requests ok" % (s["ok"], n)
            if s["skip"]:
                detail += "; %d skipped after a timeout" % s["skip"]
            if s["fail"]:
                detail += "; last error " + s["err"]
            out.append({"name": name, "ok": s["ok"] > 0, "detail": detail})
        return out


# ------------------------------------------------------------------ small utilities
def to_float(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None


def lab(d):
    return "%s %d" % (d.strftime("%b"), d.day)


def iso(d):
    return d.isoformat() if hasattr(d, "isoformat") else str(d)


def parse_date(s):
    return date.fromisoformat(str(s)[:10])


def clamp(x, lo=-1.0, hi=1.0):
    return max(lo, min(hi, x))


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=True, indent=1), encoding="utf-8")
    tmp.replace(path)


def load_previous_data():
    """The MW_DATA object from the last data.js, or {}."""
    try:
        txt = DATA_JS.read_text(encoding="utf-8")
        m = re.search(r"window\.MW_DATA\s*=\s*(\{.*\})\s*;?\s*$", txt, re.S)
        return json.loads(m.group(1)) if m else {}
    except Exception:  # noqa: BLE001
        return {}


def norm_tenor(name):
    """'10 Yr' / '10 YR' -> '10y', '3 Mo' -> '3m', '1.5 Month' -> '1.5m'."""
    m = re.match(r"\s*([\d.]+)\s*(mo|month|yr|year)", str(name), re.I)
    if not m:
        return None
    return m.group(1) + ("m" if m.group(2).lower() in ("mo", "month") else "y")

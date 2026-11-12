#!/usr/bin/env python3
"""OPTIONAL STEP - READ HEADLINES with a model running on your own computer (free, no API key).

    python3 llm_read.py            read the new headlines in data.js with your local model
    python3 llm_read.py --check    only test the connection and show one sample answer
    python3 llm_read.py --all      read every headline again (use after changing the model)
    python3 llm_read.py --no-project   only update llm_cache.json, leave data.js alone

Works with anything that speaks the OpenAI chat format on your machine: Ollama (default,
http://localhost:11434/v1) or LM Studio (http://localhost:1234/v1). Settings live in llm_config.json.

What it does, in plain words:
  * asks the model, five headlines at a time, which macro driver each headline is about and which
    way it pushes (hawkish or dovish, hotter or cooler, stronger or weaker ...)
  * accepts an answer only if it is valid and in range; anything else is thrown away
  * remembers every answer in llm_cache.json, so a headline is only ever read once
  * project.py then copies the answers onto the headlines in data.js. The page uses them only after
    llm_eval.py has shown that the model is accurate enough (llm_grade.json). Until then they are shown
    next to the keyword reading as "not counted".

If no model answers, nothing breaks: this step is skipped and the page keeps using its keyword reader.
Standard library only.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import timedelta

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parent))  # find the sibling scripts however it is launched
import common as C

DRIVERS = ["fed", "inflation", "labor", "energy", "geo", "growth", "bonds", "fiscal", "earnings", "trade", "fx", "credit"]
CONFS = ("high", "medium", "low")
DEFAULTS = {"url": "http://localhost:11434/v1", "model": "", "batch": 5, "max_new": 60,
            "timeout": 240, "max_tokens": 1800}
KEEP_DAYS = 21          # cache entries for headlines older than this are dropped
RETRY_BAD_AFTER_H = 20  # a headline the model failed on is tried again after this many hours

SYSTEM = """You read US macro and markets news headlines for a dashboard. For each numbered headline decide:
- driver: the ONE macro driver it is mainly about, from the list below, or "none" if it is not about the macro economy or markets (sport, celebrity, how-to, opinion, a company story with no market-wide meaning).
- score: a number from -1 to +1, how far the news pushes that driver in its "+" direction. 0 means in line, unclear, or no direction.
- confidence: "high", "medium" or "low".
- reason: at most 12 words.

Drivers and what + and - mean:
fed        Fed and central banks.  + = more hawkish (hikes, tighter policy, higher hike odds)   - = more dovish (cuts, pause, easing)
inflation  Inflation data (CPI, PCE, PPI, prices).  + = hotter than expected   - = cooler
labor      Jobs, wages, claims, layoffs.  + = stronger labor market   - = weaker (more unemployment, claims or layoffs is weaker)
energy     Oil, gas, OPEC.  + = prices up   - = prices down
geo        War, sanctions, geopolitics.  + = escalation   - = de-escalation
growth     GDP, retail sales, PMI/ISM, confidence, housing.  + = stronger   - = weaker
bonds      Treasury yields and auctions.  + = yields up   - = yields down
fiscal     Budget, deficit, Treasury borrowing.  + = more borrowing or deficit worry   - = less
earnings   Company results and guidance.  + = beats, raises   - = misses, cuts
trade      Tariffs and trade deals.  + = more tariffs or restrictions   - = fewer
fx         Currencies.  + = US dollar stronger   - = US dollar weaker
credit     Credit and banking stress.  + = more stress   - = less stress

Size: 0.3 is mild, 0.6 is clear, 0.9 is a very large surprise. Use only what the headline says; do not guess.
A Fed decision to hold is neutral (score near 0) unless the headline gives dissents or guidance.
Answer with JSON only, in this exact shape:
{"reads":[{"n":1,"driver":"fed","score":0.4,"confidence":"medium","reason":"short reason"}]}"""

EXAMPLES = [  # teaching examples; llm_eval.py checks that none of them is in the test set
    ("Treasury yields climb as 10-year auction draws weak demand", "bonds", 0.5, "medium", "weak auction pushes yields up"),
    ("Fed's Hammack says policy should stay restrictive for some time", "fed", 0.4, "medium", "wants tight policy to persist"),
    ("Jobless claims fall to lowest level in two months", "labor", 0.5, "medium", "fewer claims means a stronger market"),
    ("Oil slides 3% as Saudi Arabia raises output", "energy", -0.6, "high", "bigger supply pushes oil down"),
    ("Apple unveils new colors for its iPhone case", "none", 0.0, "high", "not a macro story"),
    ("Retail sales rise 0.1%, well below forecasts", "growth", -0.5, "high", "spending far weaker than expected"),
    ("Core CPI rises 0.3% as expected", "inflation", 0.0, "medium", "in line with forecasts"),
    ("Dollar slumps as traders bet on ECB rate cuts", "fx", -0.5, "medium", "dollar falls"),
]


# ------------------------------------------------------------------ settings
class LLMError(Exception):
    def __init__(self, kind, msg):
        Exception.__init__(self, msg)
        self.kind = kind   # unreachable | timeout | bad_request | other


def load_config(url=None, model=None):
    cfg = dict(DEFAULTS)
    cfg.update({k: v for k, v in (C.read_json(C.LLM_CONFIG, {}) or {}).items() if k in DEFAULTS})
    if os.environ.get("MW_LLM_URL"):
        cfg["url"] = os.environ["MW_LLM_URL"]
    if os.environ.get("MW_LLM_MODEL"):
        cfg["model"] = os.environ["MW_LLM_MODEL"]
    if url:
        cfg["url"] = url
    if model:
        cfg["model"] = model
    cfg["url"] = str(cfg["url"]).rstrip("/")
    return cfg


def disabled():
    return os.environ.get("MW_LLM", "").lower() in ("off", "0", "no", "false")


# ------------------------------------------------------------------ talking to the model
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # never route a local model through a proxy


def _http(url, payload=None, timeout=10):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", "Authorization": "Bearer local"})
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:200]
        except Exception:  # noqa: BLE001
            pass
        raise LLMError("bad_request" if e.code in (400, 422) else "other", "HTTP %d %s" % (e.code, body))
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        if "timed out" in str(reason).lower():
            raise LLMError("timeout", "timed out after %ss" % timeout)
        raise LLMError("unreachable", str(reason)[:120])
    except (TimeoutError, OSError) as e:
        if "timed out" in str(e).lower():
            raise LLMError("timeout", "timed out after %ss" % timeout)
        raise LLMError("unreachable", str(e)[:120])
    except ValueError as e:
        raise LLMError("other", "the server did not answer with JSON (%s)" % str(e)[:60])


def list_models(url):
    d = _http(url + "/models", None, 5)
    rows = d.get("data") if isinstance(d, dict) else None
    ids = [r.get("id") for r in (rows or []) if isinstance(r, dict) and r.get("id")]
    return ids


def pick_model(ids, wanted=""):
    """Your named model if you set one; otherwise a Qwen 4B if present, any Qwen, then the first model."""
    if wanted:
        return wanted
    if not ids:
        raise LLMError("other", "the server lists no models; pull or load one first")
    low = [(i, i.lower()) for i in ids]
    four_b = re.compile(r"(?<![\d.])4b")     # 4b, but not 14b
    for pred in (lambda s: "qwen" in s and four_b.search(s), lambda s: "qwen" in s, lambda s: "embed" not in s):
        for i, s in low:
            if pred(s):
                return i
    return ids[0]


def chat(url, model, system, user, max_tokens, timeout):
    """One question, one text answer. Retries once without the optional JSON-mode field if the server rejects it."""
    if "qwen" in model.lower():
        user = user + "\n/no_think"   # Qwen's switch for "answer directly"; other models ignore the line
    base = {"model": model, "temperature": 0, "max_tokens": max_tokens, "stream": False,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    variants = [dict(base, response_format={"type": "json_object"}), base]
    resp = None
    for i, payload in enumerate(variants):
        try:
            resp = _http(url + "/chat/completions", payload, timeout)
            break
        except LLMError as e:
            if e.kind == "bad_request" and i + 1 < len(variants):
                continue
            raise
    try:
        ch = resp["choices"][0]
        text = (ch.get("message") or {}).get("content") or ""
        finish = ch.get("finish_reason")
    except (KeyError, IndexError, TypeError, AttributeError):
        raise LLMError("other", "unexpected answer format from the server")
    if not text.strip() and finish == "length":
        raise LLMError("other", "the model ran out of tokens while thinking; use a non-thinking variant or raise max_tokens in llm_config.json")
    return text


# ------------------------------------------------------------------ prompts and answers
def build_user(titles):
    shots = []
    for i, (t, d, s, c, w) in enumerate(EXAMPLES, 1):
        shots.append('%d. %s\n   -> {"driver":"%s","score":%s,"confidence":"%s","reason":"%s"}' % (i, t, d, s, c, w))
    lines = ["Examples (for style only, do not repeat them):", *shots, "", "Now read these headlines:"]
    for i, t in enumerate(titles, 1):
        lines.append("%d. %s" % (i, re.sub(r"\s+", " ", t).strip()[:300]))
    lines.append("")
    lines.append('Return {"reads":[...]} with one entry per headline above, using the same "n".')
    return "\n".join(lines)


def extract_json(text):
    """Pulls the first JSON value out of a model answer (ignores thinking blocks and code fences)."""
    t = str(text or "")
    t = re.sub(r"<think>.*?</think>", "", t, flags=re.S | re.I)
    if "</think>" in t.lower():
        t = re.split(r"</think>", t, flags=re.I)[-1]
    if "<think>" in t.lower():
        return None   # thinking never finished, so there is no answer
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S | re.I)
    if m:
        t = m.group(1)
    dec = json.JSONDecoder()
    for i, ch in enumerate(t):
        if ch in "{[":
            try:
                obj, _ = dec.raw_decode(t[i:])
                return obj
            except ValueError:
                continue
    return None


def check_read(r):
    """One entry from the model -> a clean read, or None when it is not valid."""
    if not isinstance(r, dict):
        return None
    d = str(r.get("driver", "")).strip().lower()
    if d not in DRIVERS and d != "none":
        return None
    try:
        s = float(r.get("score"))
    except (TypeError, ValueError):
        return None
    if s != s or abs(s) > 1.05:
        return None
    s = max(-1.0, min(1.0, s))
    c = str(r.get("confidence", "")).strip().lower()
    c = {"mid": "medium", "moderate": "medium"}.get(c, c)
    if c not in CONFS:
        c = "low"
    why = re.sub(r"\s+", " ", str(r.get("reason", ""))).strip()[:140]
    if d == "none":
        s = 0.0
    return {"driver": d, "score": round(s, 2), "conf": c, "why": why}


def parse_reads(obj, n):
    """-> list of n entries (a clean read or None), matched by the "n" the model echoed back."""
    rows = None
    if isinstance(obj, dict):
        for k in ("reads", "results", "headlines", "items"):
            if isinstance(obj.get(k), list):
                rows = obj[k]
                break
        if rows is None and "driver" in obj:
            rows = [obj]
    elif isinstance(obj, list):
        rows = obj
    out = [None] * n
    if not rows:
        return out
    for pos, r in enumerate(rows):
        if not isinstance(r, dict):
            continue
        try:
            idx = int(r.get("n")) - 1
        except (TypeError, ValueError):
            idx = pos if len(rows) == n else -1
        if 0 <= idx < n and out[idx] is None:
            out[idx] = check_read(r)
    return out


def read_titles(titles, url, model, batch=5, timeout=240, max_tokens=1800, log=None):
    """Reads headlines in small batches. Returns a list aligned with titles: a clean read or None.
    Raises LLMError only when the server can not be reached at all."""
    out = [None] * len(titles)
    timeouts = 0
    for a in range(0, len(titles), batch):
        chunk = titles[a:a + batch]
        try:
            text = chat(url, model, SYSTEM, build_user(chunk), max_tokens, timeout)
        except LLMError as e:
            if e.kind == "unreachable":
                raise
            if e.kind == "timeout":
                timeouts += 1
                if timeouts >= 2:
                    raise
            if log:
                log("batch of %d failed: %s" % (len(chunk), e))
            continue
        got = parse_reads(extract_json(text), len(chunk))
        for j, t in enumerate(chunk):          # one more try, alone, for any headline the batch answer missed
            if got[j] is None and len(chunk) > 1:
                try:
                    one = parse_reads(extract_json(chat(url, model, SYSTEM, build_user([t]), max_tokens, timeout)), 1)
                    got[j] = one[0]
                except LLMError as e:
                    if e.kind == "unreachable":
                        raise
        for j, g in enumerate(got):
            out[a + j] = g
        if log:
            log("read %d of %d" % (min(a + batch, len(titles)), len(titles)))
    return out


# ------------------------------------------------------------------ cache
def norm_title(t):
    return re.sub(r"[^a-z0-9]+", " ", str(t).lower()).strip()


def key(title):
    return hashlib.sha1(norm_title(title).encode("utf-8")).hexdigest()[:16]


def load_cache():
    c = C.read_json(C.LLM_CACHE, {}) or {}
    if not isinstance(c.get("reads"), dict):
        c = {"version": 1, "reads": {}}
    return c


def save_cache(cache, today):
    cut = (today - timedelta(days=KEEP_DAYS)).isoformat()
    cache["reads"] = {k: v for k, v in cache["reads"].items() if str(v.get("d", "9999")) >= cut}
    cache["version"] = 1
    C.write_json(C.LLM_CACHE, cache)


def load_grade():
    g = C.read_json(C.LLM_GRADE, None)
    return g if isinstance(g, dict) else None


def _hours_ago(iso_txt, now):
    try:
        from datetime import datetime
        return (now - datetime.fromisoformat(str(iso_txt))).total_seconds() / 3600.0
    except Exception:  # noqa: BLE001
        return 9999.0


def need_read(h, cache, model, now):
    e = cache["reads"].get(key(h.get("title", "")))
    if not e or e.get("model") != model:
        return True
    if e.get("ok"):
        return False
    return _hours_ago(e.get("at"), now) >= RETRY_BAD_AFTER_H


# ------------------------------------------------------------------ the step update.py calls
def run(clean, today, say, enabled=True, url=None, model=None, max_new=None, force_all=False, heads=None):
    """Reads headlines that are not in the cache yet. Never raises; returns a small stats dict."""
    from datetime import datetime, timezone
    pre = "   local model: "
    if not enabled or disabled():
        say(pre + "off (the cache from earlier runs is still used)")
        return {"ran": False}
    cfg = load_config(url, model)
    heads = heads if heads is not None else (clean.get("feeds") or [])
    heads = [h for h in heads if isinstance(h, dict) and h.get("title")]
    if not heads:
        say(pre + "no headlines to read")
        return {"ran": False}
    try:
        ids = list_models(cfg["url"])
        mdl = pick_model(ids, cfg["model"])
    except LLMError as e:
        say(pre + "skipped, nothing answered at %s (%s). That is fine: the page uses its keyword reader." % (cfg["url"], e))
        return {"ran": False, "skipped": str(e)}
    cache = load_cache()
    now = datetime.now(timezone.utc)
    todo = heads if force_all else [h for h in heads if need_read(h, cache, mdl, now)]
    cap = int(max_new if max_new is not None else cfg["max_new"])
    todo = todo[:cap] if cap > 0 else todo
    if not todo:
        say(pre + "%s, nothing new to read (%d headlines already in the cache)" % (mdl, len(heads)))
        return {"ran": True, "model": mdl, "read": 0}
    say(pre + "reading %d headlines with %s ..." % (len(todo), mdl))
    t0 = time.time()
    try:
        reads = read_titles([h["title"] for h in todo], cfg["url"], mdl, int(cfg["batch"]), int(cfg["timeout"]),
                            int(cfg["max_tokens"]), None)
    except LLMError as e:
        say(pre + "stopped, the model stopped answering (%s). Nothing was saved from this attempt." % e)
        return {"ran": False, "skipped": str(e)}
    good = bad = 0
    for h, r in zip(todo, reads):
        ent = {"model": mdl, "at": now.isoformat(timespec="seconds"), "d": str(h.get("d", today.isoformat()))[:10]}
        if r:
            ent.update(ok=True, driver=r["driver"], score=r["score"], conf=r["conf"], why=r["why"])
            good += 1
        else:
            ent.update(ok=False)
            bad += 1
        cache["reads"][key(h["title"])] = ent
    save_cache(cache, today)
    say(pre + "read %d headlines in %.0f s (%d valid, %d invalid and thrown away)" % (len(todo), time.time() - t0, good, bad))
    return {"ran": True, "model": mdl, "read": len(todo), "valid": good, "invalid": bad}


# ------------------------------------------------------------------ used by project.py
def attach(heads, today=None):
    """Copies cached reads onto the headlines (field "llm"). Returns (headlines, summary for the page or None)."""
    cache, grade = load_cache(), load_grade()
    reads = cache["reads"]
    counts = {}
    for e in reads.values():
        if e.get("ok"):
            counts[e.get("model")] = counts.get(e.get("model"), 0) + 1
    model = (grade or {}).get("model")
    if not model:
        model = max(counts, key=counts.get) if counts else None
    if not model and not grade:
        return heads, None
    out, n = [], 0
    for h in heads:
        h = dict(h)
        h.pop("llm", None)
        e = reads.get(key(h.get("title", "")))
        if e and e.get("ok") and e.get("model") == model:
            h["llm"] = {"t": e["driver"], "s": e["score"], "c": e["conf"], "why": e.get("why", "")}
            n += 1
        out.append(h)
    verified = bool(grade and grade.get("pass") is True and grade.get("model") == model)
    other = [m for m in counts if m != model]
    g = None
    if grade:
        g = {k: grade.get(k) for k in ("model", "at", "n", "valid", "driver", "direction", "false_alarm", "pass", "kw")}
    note = ""
    if n == 0 and other:
        verified = False
        note = "The accuracy test is for %s but the saved reads are from %s. Run llm_eval.py with the model you read with." % (model, ", ".join(sorted(str(m) for m in other)))
    elif grade and not verified:
        note = "The accuracy test for this model did not pass, so its reads are shown but not counted."
    elif not grade:
        note = "No accuracy test has been run for this model yet (python llm_eval.py), so its reads are shown but not counted."
    return out, {"model": model or "", "verified": verified, "reads": n, "total": len(heads), "grade": g, "note": note}


# ------------------------------------------------------------------ command line
def main(argv=None):
    C.safe_console()
    ap = argparse.ArgumentParser(description="Read headlines with a local model.")
    ap.add_argument("--check", action="store_true", help="test the connection and show one sample answer")
    ap.add_argument("--all", action="store_true", help="read every headline again")
    ap.add_argument("--url", help="e.g. http://localhost:1234/v1 for LM Studio")
    ap.add_argument("--model", help="model name exactly as your server lists it")
    ap.add_argument("--max", type=int, help="most new headlines to read this time")
    ap.add_argument("--no-project", action="store_true", help="only update llm_cache.json, leave data.js alone")
    args = ap.parse_args(argv)
    cfg = load_config(args.url, args.model)
    if args.check:
        print("Asking %s ..." % cfg["url"])
        try:
            ids = list_models(cfg["url"])
            mdl = pick_model(ids, cfg["model"])
            print("Models found: " + (", ".join(ids) if ids else "none"))
            print("Using: " + mdl)
            sample = ["Fed's Hammack says policy should stay restrictive for some time",
                      "Oil slides 3% as Saudi Arabia raises output", "Lakers beat Celtics in overtime"]
            t0 = time.time()
            txt = chat(cfg["url"], mdl, SYSTEM, build_user(sample), int(cfg["max_tokens"]), int(cfg["timeout"]))
            secs = time.time() - t0
            got = parse_reads(extract_json(txt), len(sample))
        except LLMError as e:
            print("Could not use the model: %s" % e)
            print("Is Ollama (or LM Studio) running, and is the model pulled/loaded? See SETUP_QWEN.md.")
            return 1
        for t, g in zip(sample, got):
            print("  %-68s %s" % (t[:68], ("%s %+.2f %s - %s" % (g["driver"], g["score"], g["conf"], g["why"])) if g else "INVALID ANSWER"))
        ok = sum(1 for g in got if g)
        print("%d of %d answers valid, %.0f s for 3 headlines." % (ok, len(sample), secs))
        if ok < len(sample):
            print("Raw answer was: " + str(txt)[:400].replace("\n", " "))
        return 0 if ok == len(sample) else 1
    prev = C.load_previous_data() or {}
    heads = prev.get("headlines") or []
    if not heads:
        print("data.js has no headlines yet. Run update.py first (or git pull if you use GitHub).", file=sys.stderr)
        return 1
    from datetime import date
    st = run({}, date.today(), print, True, args.url, args.model, args.max, args.all, heads)
    if st.get("ran") and not args.no_project:
        import project
        if project.apply_llm():
            print("   data.js updated with the reads - reload macro-wire.html")
    return 0 if st.get("ran") else 1


if __name__ == "__main__":
    sys.exit(main())

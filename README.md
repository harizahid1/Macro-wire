# Macro Wire

A news and data dashboard for macro and markets. One page (`macro-wire.html`) plus a few scripts that
collect data, check it, work out what it means, and put it on the page.

Three ways to use it. They stack, so start with 1.

1. **On your Mac.** Double-click `run.command` (first time: right-click it > Open > Open). It refreshes the data and
   opens the page. Or in Terminal: type `cd ` (with a space), drag this folder into the window, press Return, then
   `python3 update.py --open`. Nothing to install except Python 3.8+: type `python3 --version`; if it is missing,
   macOS offers to install the Command Line Tools (which also gives you git), or use python.org. No keys. The page
   opens by double-clicking; it needs no server.
2. **With your own local model** (free). A model such as Qwen running in Ollama or LM Studio reads the
   headlines. See `SETUP_QWEN.md`.
3. **Automatically on GitHub** (free). GitHub refreshes the data on a schedule and hosts the page at your own
   address. See `SETUP_GITHUB.md`.

## What happens when you run it

`update.py` runs four steps in order. Each can also be run alone (`python3 collect.py` and so on) and each
leaves its result in `work/` so you can open it.

| Step | Script | What it does | Result |
|---|---|---|---|
| 1 | `collect.py` | Gets the raw data: Treasury yield and real-yield curves, BLS (CPI, core CPI, unemployment, payrolls), NY Fed policy rate, Yahoo Finance (S&P 500, Nasdaq, Dow, VIX, dollar, Brent, WTI, gold, bitcoin), headline feeds. Judges nothing. | `work/raw.json` |
| 2 | `validate.py` | Tests every number before anything uses it. A bad or stale value is thrown out ("quarantined"); its tile keeps the last good value and says "not refreshed". | `work/checked.json` |
| 2b | `llm_read.py` | Optional. Your local model reads the new headlines. Invalid answers are thrown away; every answer is remembered. | `llm_cache.json` |
| 3 | `understand.py` | Works out what the checked numbers mean: curve shape, what bills say about the Fed path, inflation breakeven, CPI and jobs against their own trend, market tone, and the hawkish-or-dovish leads. | `work/read.json` |
| 4 | `project.py` | Writes `data.js` for the page, appends to `history.jsonl`, updates `scorecard.json`, writes `last_report.txt`. | `data.js` |

What `validate.py` checks: range (a 10Y yield of 0.49 or an S&P of 756900 is a typo), dates (future dates dropped;
markets older than 10 days thrown out), jumps (a huge one-day move is flagged), frozen feeds (same close five days
running), cross-checks (Brent vs WTI, funds rate vs 3-month bill, inflation breakeven, seasonally adjusted vs
unadjusted CPI) and headlines (duplicates, future-dated, too old, too short or too long). Each check ends `ok`,
`warn` (used but flagged) or `fail` (thrown away).

## Two readers for every headline

The page always has its keyword reader. If your local model has read a headline too, the page compares the two,
but only after the model has passed `llm_eval.py` (50 hand-labelled headlines; the page shows the score). Then:

* both agree: counted, high confidence
* only the model finds a signal: counted, medium confidence
* they point opposite ways, or the model sees no direction: **not counted**

Before the model passes, its reads are shown next to each headline as "not counted" so you can judge it yourself.

## Where to look when something looks wrong

* `last_report.txt` - start here; it names every source that failed and every check that did not pass
* `work/raw.json` - exactly what came back from the internet
* `work/checked.json` - what survived validation, and the full list of checks
* `work/read.json` - what was concluded, and every lead with its number and reason
* `history.jsonl` - one line per run, newest last
* `llm_cache.json` - what the local model said about each headline, with its one-line reason

## What still has to be typed

Edit `macro-wire.html`; the lists are near the data block: `SEED` (researched news memory), `UP` (release
calendar), `PRINTS` (actual vs expected figures), `PRICED` (December hike odds), `LEAD` (JOLTS, ISM, nowcast) and
`FEDSPEAK` (what each Fed official said). No free feed gives consensus forecasts, hike odds or speeches reliably,
so they carry their own dates and fade (a typed lead loses weight after 7 days). The computed leads refresh on every
run. Typed items were last researched Nov 3, 2026.

## Honest limits

* It is not real time. Every run is a snapshot, and Yahoo quotes can be delayed.
* The downloads (Treasury, BLS, NY Fed, Yahoo, feeds) follow the services' published formats and were tested on
  recorded-style files; the live services and a real GitHub run could not be reached while this was built. The first
  real run may show a source that changed. The report says which, and every other source still works.
* Yahoo Finance and Google News are unofficial feeds and can rate-limit, change, or block GitHub's servers.
* The local model's accuracy is unknown until you run `llm_eval.py` on your machine. The 50 labels are one person's
  judgement; edit `llm_eval_set.json` to test the cases you care about. A model that passes can still misread a headline.
* Leads are judgement turned into arithmetic (see the top of each function in `understand.py`, for example
  `BILL_PREMIUM_BP`). The lean says where the evidence points, not what will happen. Not investment advice.

## Files

`macro-wire.html` the page · `index.html` sends visitors to it · `data.js` what the page reads ·
`update.py collect.py validate.py understand.py project.py common.py` the pipeline ·
`llm_read.py llm_eval.py llm_eval_set.json llm_config.json llm_cache.json llm_grade.json` the local model ·
`sync.py` sends the model's reads to GitHub · `.github/workflows/update.yml` the scheduled run ·
`run.command` the double-click launcher.

Run on a schedule on your own Mac instead (optional, and the GitHub route is better because a sleeping Mac skips runs):
Terminal, `crontab -e`, add `0 7 * * 1-5  cd /full/path/to/this/folder && python3 update.py --quiet`. macOS may ask
Terminal for permission to the folder the first time.

If `last_report.txt` shows `CERTIFICATE_VERIFY_FAILED` for every source: Python from python.org has no root
certificates until you run `Install Certificates.command` (in /Applications/Python 3.x). This package already retries
with the certificates macOS keeps in /etc/ssl/cert.pem, and never turns certificate checking off.

Delete `data.js` (or replace it with the placeholder) at any time to go back to the page's built-in snapshot.

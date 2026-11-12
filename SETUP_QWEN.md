# Let your local Qwen read the headlines (free, on your Mac)

A model running on your Mac reads each new headline and says which macro driver it is about and which way it pushes
(hawkish or dovish, hotter or cooler ...). Headlines go only to your own machine. No key, no cost. Works with Ollama
(default) and LM Studio. Both use your Mac's graphics chip automatically on Apple Silicon.

All commands below go in **Terminal**, in this folder: type `cd ` (with a space), drag the folder into the window,
press Return.

## 1. Make the model available

**Ollama.** Open the Ollama app (its icon sits in the menu bar). In Terminal run `ollama list` and note the exact
name of your Qwen (for example `qwen3.5:4b`). If it is not listed, `ollama pull <name>`.

**LM Studio.** Load the model, open the **Developer** tab, turn on **Start Server** (it uses port 1234), and in
`llm_config.json` change the url to `http://localhost:1234/v1`. To edit the file: right-click it > Open With > TextEdit.

## 2. Settings

`llm_config.json` holds the address and the model name. Leave `model` empty to let the script pick a Qwen
(preferring a 4B one), or type the name exactly as your server lists it.

## 3. Test the connection

    python3 llm_read.py --check

It lists the models it found, asks three sample headlines and shows the answers. You want `3 of 3 answers valid`. The
first call can take a minute while the model loads.

## 4. Accuracy test - do this before trusting it

    python3 llm_eval.py

It gives the model 50 hand-labelled headlines it has never been shown as examples, lists every one it got wrong, and
prints a table next to the page's keyword reader. It writes `llm_grade.json`. **The page counts the model's reads only
if this says PASS for the same model** (valid answers 95%+, right driver 70%+, right direction 80%+, false alarms 20% or
less). Expect a few minutes. Change the model, and you run it again.

The labels are one person's judgement. Open `llm_eval_set.json`, fix any you disagree with, add your own.

## 5. Use it

* `python3 update.py --open` (or double-click `run.command`) now reads new headlines as part of the run (step 2b),
  remembers each answer in `llm_cache.json` (a headline is read once), and puts them on the page.
* `python3 llm_read.py` reads without refreshing the data. `--all` reads everything again.
* `python3 update.py --no-llm` skips the model but still uses what it read before.

On the page, each headline shows how it was read: *Model and keywords agree*, *Read by model*, *Readers disagree*
(not counted), or *Model saw no direction* (not counted). Before the model passes the test the badge says
*Model: ... (not counted)*.

## 6. With GitHub

GitHub's servers cannot reach your Mac, so your model does its part and uploads only the result:

    python3 sync.py --grade     first time (runs the accuracy test too)
    python3 sync.py             afterwards: pull latest headlines, read new ones, push llm_cache.json

This needs the copy of the repository from `SETUP_GITHUB.md` Part 2; run it inside that folder. The Action starts by
itself when the file arrives. Headlines that appear after your last sync are read by the keyword reader until the
next sync. Run it whenever you like; once or twice a day is plenty.

## If something is off

* **"nothing answered at ..." / Connection refused.** The server is not running or the port differs. Ollama is
  11434, LM Studio 1234. Open the Ollama app, or press Start Server in LM Studio.
* **INVALID ANSWER / answers take minutes.** Some Qwen builds "think" before answering. Use a non-thinking
  (instruct) build, or raise `max_tokens` in `llm_config.json`. The script sends Qwen's "answer directly" switch, but a
  build may ignore it.
* **Slow.** Lower `batch`, quit other heavy apps, or cap each run with `max_new`. Check that Activity Monitor shows
  the model using the GPU.
* **NOT PASSED.** Read the "wrong" list. If one kind of headline is always flipped, that is useful to know; send
  Claude the output and the prompt in `llm_read.py` can be adjusted.
* **Wrong model graded.** If you read with one model and graded another, the page says so and ignores the reads.

# Put Macro Wire on your own GitHub (free, updates itself)

What you get: a web page at your own address, `https://YOURNAME.github.io/macro-wire/`, and a scheduled job on
GitHub's servers that runs the same four steps as `update.py` on weekdays and saves the new `data.js`. Your Mac does
not need to be on. It costs nothing on a public repository.

Nothing is sent to Anthropic or anyone else; it all lives in your GitHub account. Claude cannot create the repository
or push for you, so you do the clicks below (about 10 minutes).

## Part 1 - put it on GitHub (in the browser, no Terminal)

1. Sign in at github.com. Click **+** (top right) > **New repository**. Name it `macro-wire`. Choose **Public**
   (free GitHub Pages needs a public repository). Leave every other box unticked. **Create repository**.
2. Unzip the package (double-click `macro-wire.zip`). In Finder, folders whose names start with a dot are hidden, so
   press **Command-Shift-.** (period) to show them; you should now see a `.github` folder.
3. On the new repository page click **uploading an existing file**. Select everything *inside* the unzipped
   `macro-wire` folder (Command-A in that folder, with hidden items showing) and drag it into the browser window. Click
   **Commit changes**.
4. Check the repository's file list. If there is no `.github` folder, the upload skipped it. Do this instead:
   **Add file > Create new file**, type the name `.github/workflows/update.yml` (typing each `/` makes a folder),
   double-click `WORKFLOW_FILE.txt` in the package (it opens in TextEdit), press Command-A then Command-C, paste into the
   GitHub editor with Command-V, and click **Commit changes**.
5. **Settings > Pages**. Under *Build and deployment* choose **Deploy from a branch**, Branch **main**, folder
   **/ (root)**, **Save**. After a minute or two the address appears at the top of that page.
6. **Actions** tab. If GitHub asks, click the green button that enables workflows. Open **Update Macro Wire** on the
   left, click **Run workflow** > **Run workflow**. After about a minute there is a green tick. Click the run to see
   the report: it lists every source and every data check.
7. Reload your page address. If the data is still the old snapshot, wait a few minutes (the page is cached for a
   short while) and reload.

If step 6 ends with a red cross at "Save the new data" and the words *permission denied*: **Settings > Actions >
General > Workflow permissions > Read and write permissions > Save**, then run it again.

## Part 2 - only if you want your local Qwen to feed the page (Terminal)

The model runs on your Mac, so it needs a copy of the repository on your Mac that can push to GitHub.

1. Open **Terminal** (Spotlight: Command-Space, type Terminal). Type `git --version`. If git is missing, macOS offers
   to install the Command Line Tools; accept and wait for it to finish.
2. Get a copy (any folder you like):

       cd ~/Documents
       git clone https://github.com/YOURNAME/macro-wire.git
       cd macro-wire

3. Let git sign in. On github.com: your picture > **Settings > Developer settings > Personal access tokens > Tokens
   (classic) > Generate new token**, tick **repo**, generate, copy it. The first time you push, Terminal asks for your
   username and a password: paste the token as the password. macOS Keychain remembers it after that. (Alternative:
   install the GitHub command-line tool and run `gh auth login`.)
4. Now follow `SETUP_QWEN.md` and use **this** folder. `python3 sync.py --grade` does everything in one go.

(If you would rather upload the first time with git instead of the browser, the token also needs the **workflow**
box ticked, because the upload contains `.github/workflows/update.yml`.)

## What runs when

* Monday to Friday at about 11:07, 14:07, 17:07, 20:07 and 23:07 UTC. Change the `cron:` line in
  `.github/workflows/update.yml` to change that.
* Whenever you push new `llm_cache.json` or `llm_grade.json` (see `SETUP_QWEN.md`).
* Whenever you press **Run workflow**.

Each run takes about a minute. If a run fails, GitHub emails you.

## Good to know

* **Public means public.** Anyone can read the files. They contain no keys and nothing personal. Do not type private
  notes into the lists inside `macro-wire.html` in this repository.
* **Pausing.** GitHub switches scheduled runs off in a public repository after about 60 days with no activity from
  you. If the schedule stops, open the Actions tab and re-enable the workflow.
* **Late or skipped runs.** GitHub starts scheduled runs when it has room, sometimes minutes late.
* **Blocked sources.** Yahoo Finance sometimes refuses GitHub's servers. The report on each run says which sources
  answered; the others still update, and a tile that cannot refresh keeps its last value and says "not refreshed".
  If one source keeps failing, send Claude your repository link (a public repo can be read) and ask for a fix.
* **The typed lists** (calendar, hike odds, Fed speakers) are not fetched. To change them, edit `macro-wire.html` in
  the copy on your Mac, then `git add macro-wire.html`, `git commit -m "Update lists"`, `git push`; or edit the file on
  github.com with the pencil icon.
* **Where Claude's published page fits.** The page Claude published in the chat is a separate snapshot and does not
  update. Your GitHub page is the live one.

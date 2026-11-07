---
name: refresh
description: Refresh all referee-card data and the claude.ai dashboard in one go - download the latest matches, look up announced referees for upcoming fixtures on official appointment pages, rebuild the card model's projections and the Hot Prediction picks, settle logged bets and upload everything to the dashboard. Use whenever the user types "refresh" (alone or with a few words like "refresh now", "refresh my referee dashboard", "refresh referees") in this project.
---

# Refresh (referee cards)

Runs everything automatically: the user asked for no confirmation steps. Explain briefly what
each step does as you go (the user is a beginner), and finish with a short summary.
Project facts and rules are in `CLAUDE.md`; this is the routine.

**Shell setup for every `uv` command** (machine quirk, see CLAUDE.md):
`export PATH="/c/Users/worsh/.local/bin:$PATH" UV_PYTHON_INSTALL_DIR="C:/Users/worsh/.local/share/uv/python" PYTHONIOENCODING=utf-8`

## 1. Get the latest data
1. `git pull --ff-only` (the GitHub bot commits `data/` every morning). If it can't fast-forward,
   stop and explain. Never force anything.
2. `uv run referees fetch` (~1.5 minutes; football-data.org + football-data.co.uk). If a download
   fails, report it and stop: never publish partial data.

## 2. Look up announced referees (automatic)
1. `uv run referees upcoming` lists the next 14 days' fixtures. Work on the rows marked `MISSING`.
2. Sources, in this order (tested 2026-11-07):
   - **First: referee.observer.** WebFetch `https://www.referee.observer/fixtures` and ask for every
     Premier League, Bundesliga and Ligue 1 fixture with its referee. One page covers three leagues,
     and on 2026-11-07 it agreed with every official pairing we had. It has no La Liga.
   - **Official pages** (confirm, or fill gaps):
     - Bundesliga: WebFetch `https://datencenter.dfb.de/global_referee_schedule` (every match).
     - Ligue 1: ligue1.com "Les arbitres de la Ne journée de Ligue 1" (full list per matchday).
     - **La Liga (RFEF PDFs):** WebFetch `https://rfef.es/es/federacion/arbitros/designaciones`
       for the newest "Estos son los árbitros para los partidos del <día>" articles, WebFetch each
       article for its PDF link, then run `uv run referees rfef-pdf "<pdf url>"`. It prints lines like
       `07-11-2026 <home> <away> <time>` followed by `Árbitro:<name>`. Use the main referee only.
     - Premier League: premierleague.com "referee appointments: Matchweek N" lists names without
       fixtures, so it only checks plausibility (each referee once per round).
   - **Second check:** statsbet.org/football/referees (its free view shows the top 5 upcoming matches).
   - **Gaps only:** reputable news or club sites that name the exact match.
   - **Don't use:** Sofascore, WorldReferee and OddAlerts (they block automated access, so never work
     around that); premierleague.com/en/referees, NinetyMinutesPlus and the DFB season page (no pairings).
   - **If sources disagree,** use the official page. If there's no official page, save nothing for
     that match and mention it in the summary.
3. **Save only pairings a source states for that exact match.** Never infer from list order, and
   never guess. Append rows to `config/referee_appointments.csv` with the exact team names from
   `upcoming`, the page URL as `source_url`, and today's date as `found_at`. Keep old rows.
   No confirmation is needed; list what was saved in the final summary.

## 3. Rebuild and publish the dashboard
1. `uv run referees export-dashboard`. It must print no `PROBLEM:` lines. Fix any: usually a
   team-name typo in the appointments file.
2. Upload these 10 files from `data/dashboard/` as assets (Artifact publish, `url`
   https://claude.ai/artifact/AAbaGSrga4xFAG6SG47hMZ, `asset: true`, one call each):
   `hot_picks`, `hot_treble`, `fixtures`, `fixture_distribution`, `referee_stats`, `team_stats`,
   `league_summary`, `league_bars`, `data_checks`, `bets`. Re-upload `model_quality`, `calibration`,
   `hot_picks_record` and `treble_record` only after a new `referees backtest`.
3. `ArtifactData` `list` collection `datasets` to get each document's current `version`. Then one
   `batch` of `update`s, each pinned with `if_version`, setting `source.url` (the new `/_blob/…`),
   `updated: {at: <"Data generated at" time>, by: "Claude Code refresh"}` and `edit: {via: "claude", at: <now>}`.
   If `dashboard/index.html` changed since the last publish, also `set` `files/index.html`.
4. Delete the replaced assets (the old `/_blob/` ids from the list in step 3).
5. Read `data/users/me` doc `check` (up to 5 reads). It only runs while the dashboard is open;
   "no document" can also mean it's closed. Say which it was. Don't screenshot or browse the dashboard.

## 4. Finish
1. `uv run pytest -q` must pass.
2. Commit `config/referee_appointments.csv` (and anything else changed) locally with an imperative
   message. **Don't push** unless the user says so (global rule: ask before pushing).
3. Summary for the user: **the Hot Prediction picks first** (bet, match, kick-off, model chance,
   "bet only at odds of" minimum, referee; from `data/dashboard/hot_picks.json`), **then the safe
   treble** (3 legs from `hot_treble.json`, combined model chance, fair odds and minimum combined odds =
   ceil(105 / chance) / 100; remind that at 2.00 it is about break-even), then when the data is from, referees found (match, referee, source), what's still
   missing, any problems, the check result, and that the commit isn't pushed.

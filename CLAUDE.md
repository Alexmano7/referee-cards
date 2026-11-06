# Referees: card tracker (project guide for Claude)

> **Status (2026-11-06):** the full pipeline runs with the user's token. Results: 3,096 matches
> joined, 0 unmatched, 111 referees, `data/referees.json` built. The mapping file has 97 rows
> (reviewed by Claude, still `checked=no` until the user confirms). Phase 2 hand-checks by the
> user are pending. Since 2026-11-06 it is on GitHub and updates itself daily (see "GitHub and
> automation"). Keep this file up to date.

## Verified data facts (first real run, 2026-11-06)
- The free tier returns all three seasons (2024–2026) for all four leagues.
- Referee coverage of finished matches: PL 100%, BL1 100%, FL1 689/691, **PD 820/869**
  (La Liga gaps: 92% in 2025/26, 84% in 2026/27). Those matches count in league averages only.
- **No upcoming fixture had a referee** (0 of 1,019), even for the next weekend. Re-checked on
  2026-11-06: still none, with `lastUpdated` the day before. Upcoming referees therefore come from the
  web lookup (see Dashboard).
- The API uses status `AWARDED` for 3 decided-at-the-table matches. These are joined like `FINISHED`.
- PL cross-check: the CSV and API referee agree on 844/850 matches. The 6 disagreements are listed
  in `data_quality.referee_disagreements`; which source is right is unknown.

## What this project does
A local Python tool that counts **yellow and red cards per referee** in four leagues, works out
adjusted per-referee rates, and projects cards for **upcoming fixtures**. Everything is written
to `data/referees.json`, which a dashboard will read later (plan phase 5).

| League          | football-data.org code | football-data.co.uk file | Timezone       |
|-----------------|------------------------|--------------------------|----------------|
| Premier League  | `PL`                   | `E0`                     | Europe/London  |
| La Liga         | `PD`                   | `SP1`                    | Europe/Madrid  |
| Bundesliga      | `BL1`                  | `D1`                     | Europe/Berlin  |
| Ligue 1         | `FL1`                  | `F1`                     | Europe/Paris   |

The user is a **beginner**. Explain each step in simple terms *before* running anything, define
jargon the first time it appears, and wait for an OK before installs, deletions or anything
that leaves the machine (pushing to GitHub especially).

## Data sources
1. **football-data.org API v4** (free tier): gives referee names for finished matches, plus upcoming fixtures.
   - `GET https://api.football-data.org/v4/competitions/{code}/matches?season=<start year>`,
     header `X-Auth-Token`. We use the `referees[]` entry with `type == "REFEREE"` (others are
     assistants and VAR).
   - Free tier: **10 requests/minute**. A run makes up to 12 calls (4 leagues × 3 seasons), spaced
     6.5 s apart. Finished seasons are cached in `data/raw/api/<season>/<code>.json` and only
     re-fetched for the current season. A 429 response waits for `X-RequestCounter-Reset`.
   - **Unverified:** whether the free tier returns referees for all four leagues and for past
     seasons. `uv run referees check` prints referee coverage per league.
2. **football-data.co.uk CSVs**: give the cards for each match.
   - `https://football-data.co.uk/mmz4281/<2627>/<E0>.csv`. Use the host without `www`, because `www`
     answers with a 302 redirect. Files start with a UTF-8 BOM.
   - Columns used: `Date` (dd/mm/yyyy), `HomeTeam`, `AwayTeam`, `HY`, `AY`, `HR`, `AR`. Only `E0`
     has `Referee` (used as a cross-check: `csv_referee_agrees` in `data/matches.csv`).
   - **Second yellows (from notes.txt):** in English files a second-yellow dismissal is counted
     as a red only. In Spanish, German and French files it is a yellow plus a red. So PL yellow counts
     run slightly low. This can't be corrected from these files, and it's noted in `referees.json`.
   - Known gap: Union Berlin v Bochum, 14/12/2024 (abandoned, awarded) has blank card columns.
     Card columns are `Int64` (they allow blanks). Such matches are left out of the stats and listed in
     `data_quality.matches_without_card_data`.

## How the join works (`join.py`)
- `config/team_name_mapping.csv` (`league, api_name, csv_name, checked`) is committed and
  hand-reviewed. `uv run referees suggest-mapping` *adds* suggestions (checked=no) for unknown
  teams by voting on which CSV match was played the same day. It never changes existing rows.
- Primary key: **league + season + local date + mapped home + mapped away**. The API's UTC time
  is converted to the league's timezone first.
- Fallback: same league, season and teams with the date off by ±1 day (`date_diff_days`). This is safe
  because each home/away pairing happens once per league season.
- Nothing is dropped silently. Rows that fail go to `data/unmatched.csv` with a reason.

## Stats engine (`stats.py`)
- Per match: cards = yellows + reds. Booking points = 10 × yellow + 25 × red.
- Per referee, per season and league, plus overall: matches, yellows, reds, cards, booking points,
  per-match rates, and % of matches over 3.5 / 4.5 / 5.5 cards.
- Adjusted rates: recency weight `0.6^(seasons ago)`. Then shrinkage toward the referee's league
  average, as if they had `PRIOR_MATCHES = 8` extra average matches.
- Projection for upcoming fixtures: referee's adjusted rate × the average of the two teams'
  card factors (a team's adjusted cards per match ÷ league average). If no referee is appointed, the
  league average is used instead. The panel covers the next 14 days. `next_fixture` is the referee's
  next appointed match on any date.

## Stack and why
- **Python 3.13** via **uv** (one tool for Python, the virtual environment and dependencies).
- `requests`, `python-dotenv`, `pandas`. Dev: `pytest`, `ruff`.
- **Machine quirk:** the Claude desktop app redirects writes to `AppData\Roaming`, which broke
  uv's default Python location. Python is installed in `C:\Users\worsh\.local\share\uv\python`.
  In Claude sessions, set `UV_PYTHON_INSTALL_DIR` to that path and add
  `C:\Users\worsh\.local\bin` to `PATH` before running `uv`.

## Folder structure
```
Referees/
├─ pyproject.toml / uv.lock     # project + pinned dependencies
├─ .env.example                 # template: FOOTBALL_DATA_API_TOKEN=
├─ .env                         # real token. NEVER committed (in .gitignore)
├─ config/team_name_mapping.csv # created by suggest-mapping, then reviewed by hand
├─ config/referee_appointments.csv # upcoming referees found by Claude's web lookup, with sources
├─ dashboard/index.html         # the claude.ai dashboard page (stored there as files/index.html)
├─ src/referees/
│  ├─ config.py      # leagues, seasons, paths, token
│  ├─ fetch_csv.py   # football-data.co.uk download + read
│  ├─ fetch_api.py   # football-data.org download (rate-limited, cached) + parse
│  ├─ team_names.py  # mapping file, suggestions
│  ├─ join.py        # link API matches to CSV rows, unmatched report
│  ├─ stats.py       # referee stats, adjustments, projections, referees.json content
│  ├─ appointments.py    # looked-up referees: load, match names, apply to fixtures
│  ├─ dashboard_export.py # flat row tables for the dashboard
│  └─ cli.py         # `referees` command
├─ data/raw/{api,csv}/          # downloads (git-ignored)
├─ data/matches.csv             # joined match history (committed)
├─ data/unmatched.csv           # failed joins with reasons (committed)
├─ data/referees.json           # final output (committed; the dashboard reads it)
└─ tests/                       # pytest, offline, fixtures in tests/fixtures/
```

## Commands
- Install deps: `uv sync`
- Download everything: `uv run referees fetch`
- Suggest team-name mappings: `uv run referees suggest-mapping`
- Show 10 joined matches, failed joins and referee coverage: `uv run referees check`
- Build outputs: `uv run referees build` (or `update` = fetch + build)
- Upcoming fixtures and which lack a referee: `uv run referees upcoming`
- Dashboard tables: `uv run referees export-dashboard` (after `fetch`)
- Tests: `uv run pytest`. Lint and format: `uv run ruff check .` / `uv run ruff format .`

## Conventions
- **Secrets:** the token lives only in `.env`. Never print or log it, and never ask the user to paste it in chat.
- Tests never call the network.
- Small commits, imperative messages. Git identity is set for this repo only.
- Dates are ISO `YYYY-MM-DD`. Seasons are stored as start year (2026) and shown as `2026/27`.

## Decisions log
- 2026-11-05: Python + uv (user request). Join on league, date and teams via a mapping file.
- 2026-11-06: Following the user's Word plan: three seasons, outputs in `data/referees.json`
  (+ matches.csv / unmatched.csv), booking points 10/25, recency weighting and shrinkage,
  over-line percentages, projections.
- 2026-11-06: Matches with blank card data are excluded from stats but listed. They're never counted as 0.

## GitHub and automation (Phase 4, done 2026-11-06)
- Public repo: https://github.com/Alexmano7/referee-cards (branch `main`, remote `origin`).
  Pushing uses Git Credential Manager (browser sign-in). `gh` CLI is not installed.
- `.github/workflows/daily-update.yml` runs at 04:00 and 05:00 UTC. A gate step lets only the run
  at 07:00 Athens time continue (it checks the `+0300`/`+0200` offset against `github.event.schedule`).
  It can also be started manually (`workflow_dispatch`). Steps: install uv 0.13.7 → `uv sync --locked`
  → pytest → `referees update` → commit the `data/*` outputs as github-actions[bot].
- The token is the repository secret `FOOTBALL_DATA_API_TOKEN`.
- The first manual run (#1) succeeded and committed `cfbc65a` with complete data.
- **The bot commits `data/` every day.** Always `git pull` before working locally, and don't commit
  locally built `data/*` files (it causes merge conflicts).
- GitHub's scheduler uses UTC, so `date.today()` in CI is the UTC date (same day at 04:00–05:00 UTC).

## Dashboard (claude.ai Artifact, built 2026-11-06)
- **URL:** https://claude.ai/artifact/AAbaGSrga4xFAG6SG47hMZ (type "Dashboard", private to the user).
  The user chose this over GitHub Pages. It does not update by itself.
- Page source: `dashboard/index.html` (committed). It is stored in the dashboard as `files/index.html`
  (document `{text, edit}`). Written to the type's one-writer rules: text in the HTML, data in
  `data-draw` drawings, and every number marked with `data-source`. Each `<script>` has its own
  scope, so share helpers via `window.rc`.
- Data: `uv run referees export-dashboard` writes 5 row tables to `data/dashboard/` (git-ignored):
  `referee_stats` (key `referee_id|league|season`, league `ALL` or a code, season `All seasons` or
  `2026/27`), `league_summary`, `league_bars`, `fixtures`, `data_checks`. Each is an uploaded asset
  referenced by `datasets/<same id>`. The page also defines a calc `fx_counts`.
- Filters are dash params (`league`, `season`, `minm`, `ref`); no query uses them.
- **"Refresh my referee dashboard" procedure:**
  1. `git pull`, then `uv run referees fetch` (~1.5 min; downloads to the git-ignored `data/raw/`).
  2. **Referee lookup** (the user chose this, option 2): `uv run referees upcoming` lists the next
     14 days' fixtures, marking each MISSING, "lookup file" or football-data.org. For the MISSING ones,
     search the official appointment pages:
     - Bundesliga: DFB Datencenter, https://datencenter.dfb.de/global_referee_schedule (WebFetch
       works and lists every match).
     - Premier League: premierleague.com "referee appointments: Matchweek N". The page lists
       referees without fixtures, so pair them only from a source naming the match (club or news sites).
     - La Liga: rfef.es designaciones (the lists are PDFs, often unreadable) and news sites.
     - Ligue 1: ligue1.com / club and news sites.
     Only use a pairing a source states for that exact match, and never infer one from list order.
     **Show the user the found list with sources and wait for OK**, then append rows to
     `config/referee_appointments.csv` using the exact team names from `upcoming`. Keep old rows:
     they're history, and the API's own referee wins once it appears.
  3. `uv run referees export-dashboard`. It builds in memory from `data/raw` and never touches the
     bot's `data/*` files. It prints "Referees from web lookup used" and any PROBLEM lines: fix them.
  4. Upload each of the 5 JSON files as an asset (`Artifact` publish, `url` above, `asset: true`,
     one call each, since they're JSON).
  5. `ArtifactData` batch `update` of each `datasets/<id>`: `source.url` (new `/_blob/…`),
     `updated: {at: <"Data generated at" time>, by: …}` and `edit`, pinned with `if_version`.
     If the page changed, `set` `files/index.html` from `dashboard/index.html` in the same batch.
  6. Delete the old assets. Read `data/users/me/check` (it only runs while the dashboard is open;
     no document can also mean it is closed), then commit the appointments file.
- Referee lookup details: `appointments.py`. Names match a known referee when every word of the
  announced name is in the full name (single letters count as initials, so "S. Jablonski" works and
  so do Spanish surname-only names). Two possible matches count as none. Unmatched names are still
  shown, with the league-average projection, and listed in `data_quality.appointment_problems`.

## Next steps (from the user's plan)
- Phase 2 leftover: the user hand-checks 3 joined matches against match reports and marks the
  mapping rows `checked=yes`.
- Phase 5 was done here instead (the user decided everything is built in this project): see Dashboard.
- Watch whether football-data.org ever fills in upcoming referees. If it does, the API's referee
  automatically wins over the looked-up one.
- Every season: run `suggest-mapping` to add promoted teams.

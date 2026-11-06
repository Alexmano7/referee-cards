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

## Team stats (`team_stats.py`, added 2026-11-06)
- `team_long`: two rows per match (one per side) with the team's own yellows/reds/cards/booking
  points, the opponent's cards and the match total. All teams' cards add up to all match cards
  (checked: 12,952).
- Per team, league and season: cards received, yellows, reds, booking points, opponents' cards and
  match total per match, home/away cards per match, % of matches where the team got over 1.5 / 2.5,
  and % of its matches over 4.5 in total. Adjusted rates use the same recency + shrinkage as referees,
  toward the league's average cards per team (overall, at home, away).
- Fixture split: the match projection is shared in proportion to the home team's adjusted home rate
  and the away team's adjusted away rate, so `projected_home_cards + projected_away_cards =
  projected_cards`. Fixtures also carry `referee_home/away_matches` and `..._cards_per_match`
  (the referee's history with each team, which is often a small sample).
- `referees.json` has a `teams` section, and the dashboard has a `team_stats` table (key
  `league|team|season`).

## Fouls, home/away bias, settlement (added 2026-11-07)
- Fouls (`HF`/`AF`) are read from football-data.co.uk. Referees and leagues get `fouls_per_match`,
  `cards_per_10_fouls` (strictness per foul), `home/away_cards_per_match` and `away_share_pct`.
  Teams get fouls committed and suffered, and cards per 10 fouls.
- `config.RED_CARD_COUNTS_AS` (default 1) sets how many "cards" a red counts as, for card markets and
  the bet log. The user bets with **Stoiximan/Betano**; their exact rule wasn't confirmed online, so ask
  the user before changing it. A 35-point cap per player and second-yellow handling need player-level
  card events (possibly from API-Football).

## Probability model (`model.py`, added 2026-11-07)
- Per side of each match: `base[league, venue] x referee x receives[team] x draws[opponent]`, fitted
  together by weighted Poisson with Gamma(mean 1) priors (`k_referee`, `k_team` = strength in expected
  cards). Recency weight is `0.5^(age_days / half_life_days)`. Negative binomial with per-league `phi`
  (method of moments) gives the probabilities.
- `stats.apply_model` replaces fixture projections: `projected_cards`, `prob_over_3_5/4_5/5_5` (as %),
  fair odds (1/p, no margin), side means and `home/away_prob_over_1_5/2_5`, `distribution` (0..10, 11+),
  and factors (`referee_factor`, `home/away_receives_factor`, `home/away_draws_factor`). `referees.json`
  referee/team blocks get `model_factor` / `model_receives_factor` / `model_draws_factor`.
- Settings live in `config/model_params.json`, tuned by **`uv run referees backtest`**
  (`backtest.py`, about 1 minute). That run is a weekly walk-forward: tuned on 2025/26 by log score,
  then scored on 2026/27 so far against the old method and the league average. It writes
  `docs/backtest.md` and `docs/backtest.json`. The first grid picked its edge, so the grid was widened;
  the chosen settings are interior (half-life 180 d, k_ref 80, k_team 40).
- Result (2026-11-07, 352 holdout matches): the model is best on every score but only slightly
  (Brier for over 4.5: 0.230 vs 0.231 for the old method and 0.234 for the league average). It is well
  calibrated in the populated bins. Card counts are noisy, so **don't overstate the edge** to the user.

## Hot Prediction (`picks.py`, added 2026-11-07)
- Each refresh picks the 2-3 card bets in the next 7 days where the model differs most from a
  **typical match** (the same model with referee/team effects switched off, `k = 1e9`). The
  candidates are match total over/under 3.5/4.5/5.5 and each side over/under 1.5/2.5. Only bets with
  a model chance of at least 55% count. The score is model chance minus typical chance; the best
  candidate per fixture is taken, then the top 3 fixtures, with a known referee first.
- Each pick shows fair odds (1/p) and **minimum odds** (fair + 5%, rounded up). Tell the user to bet only at
  or above that. `report["hot_picks"]`; dashboard datasets `hot_picks` and `hot_picks_record`.
- Backtest of the exact rule (`backtest.hot_pick_record`, in `docs/backtest.md`): 2026/27 so far, 22/33
  won (66.7%) against 64.4% predicted and 51.5% for a typical match; 2025/26, 82/111 (73.9%) against
  66.3% and 49.6%. The sample is small and bookmakers price better than "typical", so present it as a guide.

## Bet log (`bets.py`, added 2026-11-07)
- `config/bets.csv`: placed_at, league, date, home, away (football-data.org names, as in `upcoming`),
  market (`match_cards` | `home_cards` | `away_cards` | `match_booking_points`), selection (over/under),
  line (half lines only), odds, stake, model_prob (the model's % for that selection at logging time), note.
- **"Log a bet …"** from the user: find the fixture in `uv run referees upcoming` or the fixtures table,
  take model_prob from the dashboard data (`prob_over_X`, or 100 minus it for under), append the row,
  confirm it back to the user, then refresh the dashboard's `bets` dataset (export, upload, update).
- `settle()` marks bets won/lost once the match is in the joined data. The dashboard shows them with totals
  (calc `bets_summary`).

## API-Football: free key added, staying on the free plan (decided 2026-11-07)
- `api_football.py` and **`uv run referees api-football-probe`** (a few of the 100 daily requests)
  report the plan, seasons and coverage per league, the referee field on upcoming fixtures, and card
  bet types. The key is in `.env` as `API_FOOTBALL_KEY` (never print it).
- Probe result: **the free plan has no access to the current season** ("try from 2022 to 2024"), so it
  gives no upcoming fixtures, referees, card events or odds for now. Paid plans offer 21 card markets
  (e.g. 80 Cards Over/Under, 82/83 Home/Away Team Total Cards, 252 Both Teams to Receive a Card).
- **Decision:** stay free. Odds are entered by hand in the dashboard's odds check, and bets go in the
  bet log. Revisit a one-month paid test only if the logged bets suggest the model finds value. Don't
  integrate API-Football data unless the user asks again.

## Sofascore / datafc: tried and rejected (2026-11-06)
- The user asked for Sofascore via the `datafc` package as a referee source. A throwaway trial
  (`uv run --no-project --with datafc`, not added to the project) got **HTTP 403 for every
  Sofascore URL, including the home page**. Sofascore blocks automated access, and opening it in the
  browser was refused as bot-check circumvention. **Do not retry or work around it.** Official
  appointment pages (see the refresh procedure) remain the referee source.

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
├─ config/model_params.json     # tuned model settings (written by `referees backtest`)
├─ config/bets.csv              # bet log (written by Claude on the user's request)
├─ docs/backtest.md / .json     # backtest report
├─ dashboard/index.html         # the claude.ai dashboard page (stored there as files/index.html)
├─ src/referees/
│  ├─ config.py      # leagues, seasons, paths, token
│  ├─ fetch_csv.py   # football-data.co.uk download + read
│  ├─ fetch_api.py   # football-data.org download (rate-limited, cached) + parse
│  ├─ team_names.py  # mapping file, suggestions
│  ├─ join.py        # link API matches to CSV rows, unmatched report
│  ├─ stats.py       # referee stats, adjustments, projections, referees.json content
│  ├─ appointments.py    # looked-up referees: load, match names, apply to fixtures
│  ├─ team_stats.py      # card statistics per team, fixture split between teams
│  ├─ model.py           # probability model (referee/team/opponent factors, negative binomial)
│  ├─ backtest.py        # walk-forward backtest + settings tuning
│  ├─ bets.py            # bet log settlement
│  ├─ picks.py           # Hot Prediction rule
│  ├─ api_football.py    # API-Football client + free-plan probe
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
- Backtest and tune the card model: `uv run referees backtest` (re-run after big data changes)
- Probe the API-Football free plan: `uv run referees api-football-probe`
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
- Data: `uv run referees export-dashboard` writes 10 row tables to `data/dashboard/` (git-ignored): `fixture_distribution`
  (key `fixture|i`), `model_quality` and `calibration` (from docs/backtest.json), `bets`,
  `team_stats` (key `league|team|season`; a Teams section and the match card read it),
  `referee_stats` (key `referee_id|league|season`, league `ALL` or a code, season `All seasons` or
  `2026/27`), `league_summary`, `league_bars`, `fixtures`, `data_checks`. Each is an uploaded asset
  referenced by `datasets/<same id>`. The page also defines a calc `fx_counts`.
- Filters are dash params (`league`, `season`, `minm`, `ref`, `fx` = the picked fixture); no query uses them.
- **The user types `refresh`** (or `/refresh`) to run everything. The routine is the project skill
  `.claude/skills/refresh/SKILL.md`, and that skill is the source of truth. Since 2026-11-07, at the
  user's request, referee lookups are **saved automatically** (no confirmation step), still only for
  pairings a source states for that exact match. Found names are reported in the summary.
- Older notes on the same procedure:
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
     (Since 2026-11-07 no OK is needed: see the refresh skill.) Append rows to
     `config/referee_appointments.csv` using the exact team names from `upcoming`. Keep old rows:
     they're history, and the API's own referee wins once it appears.
  3. `uv run referees export-dashboard`. It builds in memory from `data/raw` and never touches the
     bot's `data/*` files. It prints "Referees from web lookup used" and any PROBLEM lines: fix them.
  4. Upload each of the 10 JSON files as an asset (`Artifact` publish, `url` above, `asset: true`,
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

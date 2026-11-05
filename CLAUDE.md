# Referees: card tracker (project guide for Claude)

> **Status (2026-11-06):** code, tests and CSV download work. The football-data.org part has
> **not yet been run with a real token**. The team-name mapping file doesn't exist yet (it's
> created by `suggest-mapping` after the first API fetch). Keep this file up to date.

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
├─ src/referees/
│  ├─ config.py      # leagues, seasons, paths, token
│  ├─ fetch_csv.py   # football-data.co.uk download + read
│  ├─ fetch_api.py   # football-data.org download (rate-limited, cached) + parse
│  ├─ team_names.py  # mapping file, suggestions
│  ├─ join.py        # link API matches to CSV rows, unmatched report
│  ├─ stats.py       # referee stats, adjustments, projections, referees.json content
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

## Next steps (from the user's plan)
- Phase 2: the user adds the token to `.env`, then run `fetch` → `suggest-mapping` → review → `check`.
  The user hand-checks 3 of the 10 joined matches against match reports.
- Phase 4: push to a **public** GitHub repo, with a GitHub Actions workflow daily at 07:00 Athens time
  and the token as a repository secret. **Ask before pushing.**

# Referees: card tracker (project guide for Claude)

> **Status:** planning. This file describes what we intend to build. Nothing is set up yet,
> so the commands below won't work until the scaffold step is done. Keep this file
> up to date as decisions change.

## What this project does
A local Python tool that counts **yellow and red cards per referee** in four leagues and lists
the **upcoming fixtures**, with the referee shown once one is appointed.

| League          | football-data.org code | football-data.co.uk file |
|-----------------|------------------------|--------------------------|
| Premier League  | `PL`                   | `E0`                     |
| La Liga         | `PD`                   | `SP1`                    |
| Bundesliga      | `BL1`                  | `D1`                     |
| Ligue 1         | `FL1`                  | `F1`                     |

The user is a **beginner**. Explain each step in simple terms *before* running anything, define
jargon the first time it appears, and wait for an OK before installs, deletions or anything
that leaves the machine.

## Data sources
1. **football-data.org API v4** (free tier): gives referee names (for finished matches) and upcoming fixtures.
   - Base URL `https://api.football-data.org/v4`, auth header `X-Auth-Token: <token>`.
   - Main endpoint: `GET /competitions/{code}/matches?season=<start year>`. Each match has
     `utcDate`, `status`, `homeTeam.name`, `awayTeam.name` and `referees[]`. Use the entry whose
     `type == "REFEREE"` (the others are assistants and VAR).
   - Free tier allows **10 requests per minute**. We need about 4 calls per run (one per league).
     Cache raw responses to `data/raw/api/` and don't call the API more often than needed.
   - Unverified: whether the free tier returns `referees` for all four leagues. Check this
     on the first real fetch. Upcoming fixtures often have no referee until shortly before kick-off.
2. **football-data.co.uk CSVs**: give the cards for each match.
   - URL pattern `https://www.football-data.co.uk/mmz4281/<season>/<file>.csv`, where season is
     e.g. `2627` for 2026/27.
   - Columns used: `Div`, `Date` (dd/mm/yyyy), `HomeTeam`, `AwayTeam`, `HY`, `AY` (yellow cards),
     `HR`, `AR` (red cards). Only `E0` (England) has a `Referee` column. Use it as a cross-check,
     not as the main source.
   - Save downloads to `data/raw/csv/`.

## How the join works
The two sources spell team names differently (e.g. "Manchester United FC" vs "Man United"), so:
- `config/team_name_mapping.csv` maps names, with columns `league, api_name, csv_name`. It is
  committed to git and edited by hand.
- A match from the API is linked to a CSV row on **league + date + home team + away team**
  (after mapping). Convert the API's UTC kick-off time to the league's local date first.
- **Never drop rows silently.** Any match that can't be linked, and any team name missing from
  the mapping, is written to a "problems" report so the mapping can be fixed.

## Stack and why
- **Python 3.13**, managed with **uv** (installs Python, creates the virtual environment and
  manages dependencies with one tool). Python was not installed on this PC when the project started.
- `requests`: HTTP calls. `python-dotenv`: reads `.env`. `pandas`: tables and joins.
- `pytest`: tests. `ruff`: linting and formatting.
- Output is CSV files in `data/processed/`, which are easy to open in Excel. Add a database or
  dashboard later only if it's needed.

## Planned folder structure
```
Referees/
├─ pyproject.toml            # project + dependencies (managed by uv)
├─ .env.example              # template: FOOTBALL_DATA_API_TOKEN=
├─ .env                      # real token. NEVER committed
├─ config/team_name_mapping.csv
├─ src/referees/
│  ├─ config.py              # loads .env, league table above
│  ├─ fetch_api.py           # football-data.org client (+ caching)
│  ├─ fetch_csv.py           # football-data.co.uk downloads
│  ├─ team_names.py          # apply mapping, list unknown names
│  ├─ join.py                # link API matches to CSV rows
│  ├─ stats.py               # cards per referee
│  └─ cli.py                 # entry point
├─ data/raw/{api,csv}/       # downloads (git-ignored)
├─ data/processed/           # outputs (git-ignored)
└─ tests/                    # pytest, using small saved sample files and no network
```

## Commands (planned)
- Install deps: `uv sync`
- Fetch + build everything: `uv run referees update`
- Tests: `uv run pytest`
- Lint / format: `uv run ruff check .` and `uv run ruff format .`

## Conventions
- **Secrets:** the token lives only in `.env`, which is listed in `.gitignore` before the first
  commit. Never print the token, log it, or ask the user to paste it in chat.
- Tests never call the network. They use small fixture files in `tests/fixtures/`.
- Small commits with imperative messages (e.g. "Add football-data.co.uk downloader").
- Dates are stored as ISO `YYYY-MM-DD`.

## Decisions log
- 2026-11-05: Python + uv chosen (user request). Output as CSV files first. Join on league, date
  and teams through a hand-edited mapping file.

## Open questions
- Which seasons? Default: current season 2026/27 only.
- Do red cards from a second yellow count as both a yellow and a red? Check
  football-data.co.uk's `notes.txt` and record the answer here.

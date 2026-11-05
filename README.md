# Referee card tracker

Counts **yellow and red cards per referee** in the Premier League, La Liga, Bundesliga and
Ligue 1 over the current season and the two before it. It works out fair "adjusted" averages
and projects how many cards to expect in upcoming matches. The result is one file,
`data/referees.json`.

**Where the data comes from**
- [football-data.org](https://www.football-data.org): who refereed each match, and upcoming fixtures (needs a free token).
- [football-data.co.uk](https://www.football-data.co.uk): how many cards were shown in each match (free CSV files).

The two sites spell team names differently, so `config/team_name_mapping.csv` says which names
belong together.

## What you need
- Windows, with **uv** installed (it installs Python for you).
- A free football-data.org token (it arrives by email when you register).

## First-time setup
1. Open a terminal in this folder.
2. Install the libraries:
   ```bash
   uv sync
   ```
3. Copy `.env.example` to a new file called `.env` in the same folder. Open it and paste your token
   after `FOOTBALL_DATA_API_TOKEN=`. **Never share or commit `.env`.** Git already ignores it.

## Using it
| Command | What it does |
|---|---|
| `uv run referees fetch` | Downloads the data (takes about 1.5 minutes because of the API's 10-calls-per-minute limit). |
| `uv run referees suggest-mapping` | Fills the team-name mapping with suggestions. Open the file and check them, then change `checked` to `yes`. |
| `uv run referees check` | Shows 10 joined matches to check by hand, lists matches that failed to join, and says whether referee names arrived for every league. |
| `uv run referees build` | Writes `data/matches.csv`, `data/unmatched.csv` and `data/referees.json`. |
| `uv run referees update` | `fetch` + `build` in one go (what the daily job will run). |

## Understanding the numbers
- **Cards** = yellows + reds. **Booking points** = 10 per yellow, 25 per red.
- **Adjusted** rates count recent seasons more (this season 1, last season 0.6, the one before 0.36).
  They also pull referees with few matches toward their league's average, so a referee with 3 wild
  matches doesn't top the table.
- **pct_over_4_5** = the percentage of a referee's matches with 5 or more cards.
- **Projected cards** for a fixture = the referee's adjusted rate, scaled by how card-heavy the two
  teams' matches usually are.
- ⚠️ **Premier League caveat:** football-data.co.uk counts a second-yellow sending-off as *just a red*
  in English files, but as *a yellow plus a red* in the other three leagues.

## Testing
```bash
uv run pytest
```
The tests use small made-up files in `tests/fixtures/` and don't need the internet or a token.

Code style checks:
```bash
uv run ruff check .
```

## Project structure
See `CLAUDE.md` for the full layout, design decisions and known data quirks.

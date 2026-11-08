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

| Key   | League               | football-data.org | Card file | Past referees from            | Timezone        |
|-------|----------------------|-------------------|-----------|-------------------------------|-----------------|
| `PL`  | Premier League       | `PL`              | `E0`      | football-data.org             | Europe/London   |
| `PD`  | La Liga              | `PD`              | `SP1`     | football-data.org             | Europe/Madrid   |
| `BL1` | Bundesliga           | `BL1`             | `D1`      | football-data.org             | Europe/Berlin   |
| `SA`  | Serie A              | `SA`              | `I1`      | football-data.org             | Europe/Rome     |
| `FL1` | Ligue 1              | `FL1`             | `F1`      | football-data.org             | Europe/Paris    |
| `PPL` | Liga Portugal        | `PPL`             | `P1`      | football-data.org + referee.observer gaps (407/703) | Europe/Lisbon |
| `GR`  | Super League Greece  | none (CSV league) | `G1`      | referee.observer (mid-2025 →) | Europe/Athens   |
| `BEL` | Belgian Pro League   | none (CSV league) | `B1`      | referee.observer (mid-2025 →) | Europe/Brussels |
| `SCO` | Scottish Premiership | none (CSV league) | `SC0`     | the card file's Referee column | Europe/London  |

Leagues were added on 2026-11-07 at the user's request (Serie A, Liga Portugal, Greece, Belgium, Scotland).
**Switzerland was deliberately skipped**: no free source has Swiss card counts.

### CSV leagues (`csv_leagues.py`)
- GR/BEL/SCO aren't on football-data.org's free plan, so their matches are built from the card files and
  shaped like API rows (synthetic match ids; referee ids = `stable_referee_id(name)`, 2,000,000,000+).
  Their team names *are* the CSV names (`identity_mapping`, never written to the mapping file).
- Upcoming fixtures for them come from `football-data.co.uk/fixtures.csv` (the coming week; UK times).
- GR/BEL past referees: `referee_observer.py` reads public pages (robots.txt allows them; never `/api/`),
  1.5 s apart. A 404 past the last page means "no more"; a 403/429 means stop and report it.
- **Liga Portugal gap fill (2026-11-07):** football-data.org names a referee for only 260 of 703 finished
  PPL matches. `fill_api_referees` (csv_leagues.py) fills gaps from the referee.observer history
  (competition "Primeira Liga", id 2045), keyed by CSV team names via the team mapping. A filled name is
  linked to the same referee's API id via `match_referee`, so no referee is split in two (all 147 fills
  linked; 0 new ids). The API's own referee is never overwritten. Coverage now 407/703
  (2024/25: 15/306, because referee.observer starts mid-2025; 2025/26: 301/306; 2026/27: 91/91).
  `uv run referees fetch-ro [--full]` writes `data/referee_observer_history.csv` (committed, so the daily
  GitHub job doesn't contact the site) and `config/ro_team_mapping.csv` (same-day vote suggestions).
- Scottish card files abbreviate referee names ("N Walsh"), and `match_referee` accepts "Nicholas Walsh".

The user is a **beginner**. Explain each step in simple terms *before* running anything, define
jargon the first time it appears, and wait for an OK before installs, deletions or anything
else that leaves the machine.
**Pushing to GitHub needs no OK** (the user's standing instruction, 2026-11-08: "always push to github"):
after every commit, `git pull --rebase`, then `git push`, and report the pushed commit. Never force-push.

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
- Result with 9 leagues (2026-11-07, 763 holdout matches): the model is best (log score -2.148 vs -2.153
  for the old method and -2.163 for the league average; Brier for over 4.5 is 0.229 for both the model and
  the old method, vs 0.233). It is well calibrated (41.3% predicted → 41.7%, 56.9% → 55.6%). Tuned settings:
  half-life 365 d, k_ref 80, k_team 80. The grid was widened to 730 d to confirm 365 is interior.
  Re-run 2026-11-08 after the Portugal gap fill: half-life 540 d (k 80/80); holdout log score -2.146 vs
  -2.152 (old method) and -2.164 (league average). Card
  counts are noisy, so **don't overstate the edge** to the user.

## Hot Prediction (`picks.py`, added 2026-11-07)
- Each refresh picks up to 5 card bets (`N_PICKS`, raised from 3 on 2026-11-08 at the user's request) in the next 7 days where the model differs most from a
  **typical match** (the same model with referee/team effects switched off, `k = 1e9`). The
  candidates are match total over/under 3.5/4.5/5.5 and each side over/under 1.5/2.5. Only bets with
  a model chance of at least 55% count. The score is model chance minus typical chance; the best
  candidate per fixture is taken, then the top 5 fixtures, with a known referee first.
- Each pick shows fair odds (1/p) and **minimum odds** (fair + 5%, rounded up). Tell the user to bet only at
  or above that. `report["hot_picks"]`; dashboard datasets `hot_picks` and `hot_picks_record`.
- Backtest of the exact rule (`backtest.hot_pick_record`, in `docs/backtest.md`), 9 leagues, 5 picks a week
  (re-run 2026-11-08): 2026/27 so far 48/65 won (73.8%) against 64.9% predicted and 49.0% for a typical match;
  2025/26, 142/201 (70.6%) against 65.1% and 48.0%. Bookmakers price better than "typical", so present it as a guide.

- **Safe treble** (user request, 2026-11-07: "safest, combined odds around 2.00"): `best_treble` picks
  3 legs from 3 different matches. Each leg is at least 60% likely and above a typical match (top 3 legs
  per match by shift); combined fair odds must be 1.85-2.15; the choice maximises (combined model
  chance) / (combined typical chance); known referees first. The dashboard calc `treble_combined`
  gives the combined chance, fair odds and minimum odds (ceil(105/p)/100).
- Treble backtest (one per week, 9 leagues, re-run 2026-11-08): 2025/26 20/40 won (50.0%, model said 47.9%,
  break-even 2.00); 2026/27 so far 7/12. **At 2.00 it is about break-even at best**: always say so and point to the
  minimum odds. Single legs won 74-80%.

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

## Referee sources tested (2026-11-07, user's list)
- **referee.observer/fixtures**: the best. It listed 25 of the weekend's PL/BL1/FL1 referees and agreed with
  all 24 official pairings we had, and it gave Man United v Aston Villa = Chris Kavanagh. No La Liga.
- **RFEF**: per-day articles on `/es/federacion/arbitros/designaciones`; the referees are only in a linked PDF,
  which `referees rfef-pdf` reads (`<date> <home> <away> <time>` then `Árbitro:<name>`).
- StatsBet (free view: top 5 only) is a usable second check. WorldReferee and OddAlerts return 403 (blocked:
  don't work around it). premierleague.com/en/referees, NinetyMinutesPlus and the DFB season page have no pairings.
- The order is in the refresh skill. A preview article once named Brooks for Man United v Villa; two
  aggregators said Kavanagh. Lesson: conflicts are resolved by official sources or aggregators, never by guessing.
- Name matching: RFEF writes full middle names ("Francisco José Hernández" = "Francisco Hernández Maeso"),
  so `match_referee` has a fallback: first name and last announced word, only if exactly one referee fits.
- **Upcoming means the kick-off is still ahead** (fixed 2026-11-07). A run for today drops matches that
  already kicked off, so picks and trebles never include a started game. Appointments for today's started
  matches are skipped silently.

## EuroLeague referees (basketball, added 2026-11-08; local only for now)
- Asked for in a side chat: referee stats for EuroLeague only (the bookmaker offers no EuroCup), seasons
  E2024-E2026, same recency weighting (0.6 per season) and shrinkage (8 average games) as football.
- Modules: `euroleague.py` (download + parse) and `euroleague_stats.py` (figures). Commands `el-fetch`,
  `el-build`, `el-crew "A" "B" "C"`, `el-upcoming`. Output `data/euroleague_referees.json` (committed);
  raw files in `data/raw/euroleague/<season>/` (git-ignored). Tests in `tests/test_euroleague.py`.
- Sources (free, no key): `api-live.euroleague.net/v1/schedules?seasonCode=E2026` (XML schedule with
  `played`), `live.euroleague.net/api/Boxscore?gamecode=N&seasoncode=E2026` (crew as surname/first-name
  pairs; per player Minutes, FreeThrowsMade/Attempted, `FoulsCommited` (sic), `FoulsReceived`; team totals
  in `totr`), `/v1/games?seasonCode=&gameCode=` (`<referees>` filled close to game day). One request per
  second; network errors retried; 403/429 = stop. **Never use euroleaguebasketball.net** (blocks bots).
  referee.observer has no basketball.
- Home/away comes from the schedule's team codes, matched to each box-score team's `Team` code.
  Playoffs, play-in and Final Four count (`round` != RS; Final Four "home" is nominal).
- Per referee: fouls (total/home/away), FTM and FTA (total/home/away), raw and adjusted, `factor` =
  adjusted / league average. Each game counts for all three referees (partners mix, evens out).
- Player foul effect: a player's expected fouls = their season fouls per minute without this game
  (+100 league-rate prior minutes) x minutes; referee effect = actual / expected (shrunk toward 1), for
  fouls committed and drawn.
- Crew figures = league average x mean of the three factors; unknown names count as average and are
  listed; the exact crew's record together is shown when it exists.
- **Dashboard (built 2026-11-08):** a Football | EuroLeague switch at the top of the rail (dash param
  `sport=el`; `.rc[data-sport]` + CSS hide the other `.rc-pane`; football logic untouched). EuroLeague
  sections: Hot Prediction (`el_hot_picks`), Upcoming games (`el_games`, click -> game card via param
  `elg`: crew factors, both teams from `el_teams`, players from `el_game_players`), Players (`el_players`,
  "Last 10" or a season, search, sortable), Referee statistics (`el_referees`, click -> seasons via `elr`),
  Model check (`el_pick_record`, `el_calibration`). Header line from `el_league`.
- **Player model (`euroleague_model.py`):** minutes = last-10 average; per-minute rates (fouls committed,
  drawn, FT attempts) recency-weighted (half-life 365 d) and pulled toward the role (starter/bench) rate
  by 120 minutes; FT% pulled toward the league by 20 attempts; x venue x opponent factor x crew effect.
  Fouls Poisson; FT made = Binomial(2 x Poisson(trips), FT%). Picks: best bet per player (min 55%, 12+
  minutes, 5+ games this season), max 2 per game, crew-known games first, top 5 by model minus typical
  player (role rate, same minutes). Markets: fouls 1.5-3.5, fouls drawn 1.5-4.5, FT made 1.5-4.5.
- **Backtest (`el-backtest`, `docs/el_backtest.md`, ~30 s):** weekly walk-forward. Picks won 103/157
  (65.6%) in 2025/26 and 18/27 this season, but the raw model said ~71%, so shown pick chances use
  `PICK_SHRINK = 0.75` toward 50% (then 65.5% shown vs 65.6% won). All 115k over-line chances are well
  calibrated and beat the typical-player baseline (log loss 0.446 vs 0.488). Re-check PICK_SHRINK
  after re-running. The typical player is not the bookmaker's price: say so.
- `el-export` writes the 9 `data/dashboard/el_*.json` tables (needs the network for crews).
- Daily GitHub job: `el-fetch` + `el-build` (raw games kept with `actions/cache`, `continue-on-error`).

## Sofascore / datafc: tried and rejected (2026-11-06)
- The user asked for Sofascore via the `datafc` package as a referee source. A throwaway trial
  (`uv run --no-project --with datafc`, not added to the project) got **HTTP 403 for every
  Sofascore URL, including the home page**. Sofascore blocks automated access, and opening it in the
  browser was refused as bot-check circumvention. **Do not retry or work around it.** Official
  appointment pages (see the refresh procedure) remain the referee source.

## Stack and why
- **Python 3.13** via **uv** (one tool for Python, the virtual environment and dependencies).
- `requests`, `python-dotenv`, `pandas`, `pypdf` (added 2026-11-07 with the user's OK, to read the
  RFEF's La Liga designation PDFs). Dev: `pytest`, `ruff`.
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
│  ├─ rfef.py            # download + read RFEF La Liga designation PDFs (rfef.es only)
│  ├─ csv_leagues.py     # GR/BEL/SCO matches + fixtures shaped like API rows
│  ├─ referee_observer.py # Greek/Belgian referee history from referee.observer
│  ├─ dashboard_export.py # flat row tables for the dashboard
│  ├─ euroleague.py       # EuroLeague schedules + box scores (download, parse)
│  ├─ euroleague_stats.py # EuroLeague referee/crew figures and player foul effect
│  ├─ euroleague_model.py # EuroLeague player projections and Hot Prediction picks
│  ├─ euroleague_backtest.py # walk-forward check of the EuroLeague picks
│  ├─ euroleague_export.py   # EuroLeague dashboard tables (el_*.json)
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
- Greek/Belgian referee history: `uv run referees fetch-ro` (newest pages; `--full` for everything)
- Read an RFEF designation PDF: `uv run referees rfef-pdf "<pdf url on rfef.es>"` (saved to data/raw/rfef/)
- Dashboard tables: `uv run referees export-dashboard` (after `fetch`)
- EuroLeague: `uv run referees el-fetch` / `el-build` / `el-crew "A" "B" "C"` / `el-upcoming` /
  `el-backtest` / `el-export`
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

## Dashboard (claude.ai Artifact, built 2026-11-06; Floodlight design since 2026-11-07)
- **URL:** https://claude.ai/artifact/AAbaGSrga4xFAG6SG47hMZ (type "Dashboard", private to the user).
- **Design: "Floodlight"** (the user picked mockup C of 4 on 2026-11-07; mockups canvas:
  https://claude.ai/artifact/Mz64ooQi6gpRS35gXKKrTM). It's a deliberately single dark look: `.rc` re-points the
  dashboard colour tokens to the `--fl-*` palette (ground #0D1117, panels #161C25, yellow #F2C230, red
  #E5484D). Hot Prediction cards have a yellow-card rank badge; the safe treble is a light "bet slip"
  (`.rc-treble` re-points the tokens again); a league dropdown `#rc-league` replaced the tabs. Headings use
  a condensed system stack, because the dashboard can't load Google Fonts. The user asked for the logic to
  stay untouched: change looks via CSS and markup, and keep `data-draw` names and marks.
  Hot Prediction is a compact summary table (click a row to open the match; the full "why" is the row tooltip);
  smaller widths hide Why, then Kick-off/Typical/Referee. The safe treble sits full width above Upcoming fixtures
  (`.rc-stack`) as a compact slip: header with the minimum-odds pill, a legs table (click a leg to open the match)
  and a small track-record table; narrow widths hide Kick-off, Referee and Typical.
  Side rail (mockup A of 2, chosen 2026-11-08; canvas https://claude.ai/artifact/FDgQ5tWQXjZYgrQqU9ax21): `.rc-rail` holds
  the logo, the league dropdown and Bet / Research / Track links with live counts (draws `nav-hot`, `nav-treble`,
  `nav-fx`, `nav-refs`); the section in view is highlighted; the page content sits in `.rc-main` (now the `rc`
  container for the width queries). Below 56rem the rail becomes a sticky, sideways-scrolling link bar.
  Speed (2026-11-08): the referee, team and fixture tables are rebuilt only when their data, filters, sort or
  search change (`rc.unchanged`); picking a row only moves the highlight (`rc.markPicked`).
  The user chose this over GitHub Pages. It does not update by itself.
- Page source: `dashboard/index.html` (committed). It is stored in the dashboard as `files/index.html`
  (document `{text, edit}`). Written to the type's one-writer rules: text in the HTML, data in
  `data-draw` drawings, and every number marked with `data-source`. Each `<script>` has its own
  scope, so share helpers via `window.rc`.
- Data: `uv run referees export-dashboard` writes 14 row tables (incl. `hot_picks`, `hot_treble`,
  `hot_picks_record`, `treble_record`) to `data/dashboard/` (git-ignored): `fixture_distribution`
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

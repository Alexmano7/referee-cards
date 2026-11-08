"""Command line: `uv run referees <command>`.

Commands:
  fetch            download CSVs and API data (current season + two previous)
  suggest-mapping  add suggested team-name pairs to config/team_name_mapping.csv
  check            join the data, show 10 joined matches and list failed joins
  build            write data/matches.csv, data/unmatched.csv and data/referees.json
  update           fetch + build
  fetch-ro [--full] read Greek/Belgian referee history from referee.observer (slow, polite)
  rfef-pdf <url>   download an RFEF La Liga designation PDF (rfef.es only) and print its text
  api-football-probe  show what the free API-Football plan offers (needs API_FOOTBALL_KEY)
  backtest         test the card model on past weeks, tune its settings (docs/backtest.md)
  upcoming         list fixtures in the next 14 days and which still lack a referee
  export-dashboard build the dashboard tables (with looked-up referees) into
                   data/dashboard/ (run `fetch` first)

EuroLeague (basketball):
  el-fetch         download schedules and box scores (3 seasons, one request per second)
  el-build         write data/euroleague_referees.json (referees, players, league averages)
  el-crew A B C    expected fouls and free throws for a crew of three referees
  el-upcoming      next 7 days of games with each announced crew's figures
"""

from __future__ import annotations

import argparse
import json
import sys

import pandas as pd

from referees import fetch_api, fetch_csv
from referees.appointments import load_appointments
from referees.config import (
    DATA_DIR,
    MATCHES_FILE,
    REFEREES_JSON,
    UNMATCHED_FILE,
    seasons_to_fetch,
)
from referees.join import join_matches
from referees.stats import build_report
from referees.team_names import (
    load_mapping,
    merge_suggestions,
    save_mapping,
    suggest_mapping,
    unmapped_teams,
)


def _load():
    """All matches shaped like API rows (API leagues + CSV leagues) and all card rows.

    CSV leagues (Greece, Belgium, Scotland) are built from their card files, with past
    referees from the file (Scotland) or the committed referee.observer history (Greece,
    Belgium), and the coming week's fixtures from fixtures.csv.
    """
    from referees.config import CSV_LEAGUES, current_season_start_year
    from referees.csv_leagues import (
        csv_fixtures,
        csv_matches,
        load_ro_mapping,
        ro_referees_for_csv,
    )
    from referees.referee_observer import load_history

    seasons = seasons_to_fetch()
    api = fetch_api.load_all_api_matches(seasons)
    csv = fetch_csv.load_all_csv_matches(seasons)
    if csv.empty:
        return api, csv
    ro = ro_referees_for_csv(load_history(), csv, load_ro_mapping())
    extra = [
        csv_matches(csv, ro),
        csv_fixtures(fetch_csv.read_fixtures(CSV_LEAGUES), current_season_start_year()),
    ]
    from referees.csv_leagues import fill_api_referees

    if not api.empty:
        api["referee_id"] = api["referee_id"].astype("Int64")
        api, _ = fill_api_referees(api, ro, load_mapping())
    frames = [f for f in [api, *extra] if not f.empty]
    api = pd.concat(frames, ignore_index=True) if frames else api
    api["referee_id"] = api["referee_id"].astype("Int64")
    return api, csv


def _mapping(csv: pd.DataFrame) -> pd.DataFrame:
    """The team-name mapping file plus identity rows for the CSV leagues."""
    from referees.csv_leagues import identity_mapping

    return pd.concat([load_mapping(), identity_mapping(csv)], ignore_index=True)


def cmd_fetch() -> None:
    seasons = seasons_to_fetch()
    print(f"Downloading CSVs for seasons {seasons} ...")
    fetch_csv.download_all(seasons)
    fetch_csv.download_fixtures()
    print(
        "Downloading football-data.org matches (spaced out for the 10/minute limit) ..."
    )
    problems = fetch_api.download_all(seasons)
    if problems:
        # Stop here so an incomplete download never overwrites good output files.
        raise SystemExit("Some API downloads failed:\n  " + "\n  ".join(problems))


def cmd_suggest_mapping() -> None:
    api, csv = _load()
    from referees.config import API_LEAGUES

    api_keys = {lg.key for lg in API_LEAGUES}  # CSV leagues use their own (CSV) names
    api = api[api["league"].isin(api_keys)]
    merged, added = merge_suggestions(load_mapping(), suggest_mapping(api, csv))
    save_mapping(merged)
    print(
        f"Added {len(added)} suggested rows (checked=no). Review them in the mapping file."
    )
    weak = added[(added["share"] < 0.8) | (added["votes"] < 3)]
    if not weak.empty:
        print("Look carefully at these (few or mixed votes):")
        print(weak.to_string(index=False))


def cmd_check() -> None:
    api, csv = _load()
    mapping = _mapping(csv)
    missing = unmapped_teams(api, mapping)
    if missing:
        print(
            f"{len(missing)} team names missing from the mapping file, e.g. {missing[:5]}"
        )
    joined, unmatched = join_matches(api, csv, mapping)
    print(f"Joined matches: {len(joined)}   Unmatched rows: {len(unmatched)}")
    cols = [
        "league",
        "date",
        "api_home",
        "api_away",
        "referee",
        "home_yellows",
        "away_yellows",
        "home_reds",
        "away_reds",
    ]
    if not joined.empty:
        print(
            joined.sample(min(10, len(joined)), random_state=1)[cols].to_string(
                index=False
            )
        )
    if not unmatched.empty:
        print("\nFailed joins:\n" + unmatched.to_string(index=False))
    finished = api[api["status"] == "FINISHED"]
    print("\nFinished API matches WITH a referee name, per league:")
    print(
        finished.groupby("league")["referee_id"]
        .apply(lambda s: f"{s.notna().sum()}/{len(s)}")
        .to_string()
    )


def cmd_build() -> None:
    api, csv = _load()
    joined, unmatched = join_matches(api, csv, _mapping(csv))
    MATCHES_FILE.parent.mkdir(parents=True, exist_ok=True)
    joined.to_csv(MATCHES_FILE, index=False, lineterminator="\n")
    unmatched.to_csv(UNMATCHED_FILE, index=False, lineterminator="\n")
    report = build_report(joined, api, unmatched, appointments=load_appointments())
    REFEREES_JSON.write_text(
        json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
    )
    print(
        f"Wrote {REFEREES_JSON} ({len(report['referees'])} referees, {len(joined)} matches)"
    )


def cmd_export_dashboard() -> None:
    """Build the dashboard tables from the downloaded data (run `fetch` first).

    Works in memory: data/matches.csv and data/referees.json (updated daily by the
    GitHub job) are left untouched, so `git pull` never conflicts.
    """
    from referees.dashboard_export import build_tables, write_tables

    api, csv = _load()
    if api.empty or csv.empty:
        raise SystemExit("No downloaded data found. Run `uv run referees fetch` first.")
    joined, unmatched = join_matches(api, csv, _mapping(csv))
    report = build_report(joined, api, unmatched, appointments=load_appointments())
    from referees.backtest import RESULTS_FILE
    from referees.bets import load_bets, settle

    backtest = (
        json.loads(RESULTS_FILE.read_text(encoding="utf-8"))
        if RESULTS_FILE.exists()
        else None
    )
    tables = build_tables(
        joined, report, bets=settle(load_bets(), joined), backtest=backtest
    )
    for path in write_tables(tables, DATA_DIR / "dashboard"):
        rows = len(json.loads(path.read_text(encoding="utf-8")))
        print(f"  {path.name}: {rows} rows, {path.stat().st_size / 1024:.0f} KB")
    dq = report["data_quality"]
    print(f"Data generated at {report['generated_at']}")
    print(f"Referees from web lookup used: {dq['appointments_used']}")
    for problem in dq["appointment_problems"]:
        print(f"  PROBLEM: {problem}")


def cmd_upcoming() -> None:
    """Upcoming fixtures (next 14 days) and where each referee comes from."""
    from zoneinfo import ZoneInfo

    from referees.stats import UPCOMING_DAYS

    api, _ = _load()
    if api.empty:
        raise SystemExit("No downloaded data found. Run `uv run referees fetch` first.")
    today = pd.Timestamp.now(tz="Europe/Athens").date()
    horizon = today + pd.Timedelta(days=UPCOMING_DAYS)
    up = api[api["status"].isin(["SCHEDULED", "TIMED"])]
    up = up[(up["date"] >= today) & (up["date"] <= horizon)].sort_values("utc_kickoff")
    appts = {
        (a.league, a.date, a.home, a.away): a.referee
        for a in load_appointments().itertuples()
    }
    athens = ZoneInfo("Europe/Athens")
    missing = 0
    print("league,date,home,away,kickoff_athens,referee,source")
    for r in up.itertuples():
        kickoff = (
            pd.Timestamp(r.utc_kickoff).tz_convert(athens).strftime("%a %d %b %H:%M")
        )
        if isinstance(r.referee, str):
            ref, src = r.referee, "football-data.org"
        elif (r.league, str(r.date), r.api_home, r.api_away) in appts:
            ref, src = (
                appts[(r.league, str(r.date), r.api_home, r.api_away)],
                "lookup file",
            )
        else:
            ref, src = "", "MISSING"
            missing += 1
        print(f"{r.league},{r.date},{r.api_home},{r.api_away},{kickoff},{ref},{src}")
    print()
    print(
        f"{len(up)} fixtures in the next {UPCOMING_DAYS} days, {missing} without a referee."
    )


def cmd_backtest() -> None:
    """Walk-forward backtest, tune the model settings, write docs/backtest.md."""
    from referees import backtest

    api, csv = _load()
    if api.empty or csv.empty:
        raise SystemExit("No downloaded data found. Run `uv run referees fetch` first.")
    joined, _ = join_matches(api, csv, _mapping(csv))
    results = backtest.run(joined)
    backtest.save(results)
    print()
    print(backtest.render_markdown(results))


def cmd_api_football_probe() -> None:
    """Show what the free API-Football plan offers (about 8 of the 100 daily requests)."""
    from referees.api_football import probe
    from referees.config import current_season_start_year

    for line in probe(current_season_start_year()):
        print(line)


def cmd_rfef_pdf(url: str) -> None:
    """Download an RFEF designation PDF (rfef.es only) and print its text."""
    from referees.rfef import download_pdf, pdf_text

    path = download_pdf(url)
    print(f"Saved {path.name} ({path.stat().st_size // 1024} KB)")
    print()
    print(pdf_text(path))


def cmd_fetch_ro(full: bool, leagues: list[str] | None = None) -> None:
    """Read Greek/Belgian referee history from referee.observer and suggest team names."""
    from referees.csv_leagues import (
        RO_MAPPING_FILE,
        load_ro_mapping,
        suggest_ro_mapping,
    )
    from referees.referee_observer import update_history

    history = update_history(full=full or None, leagues=leagues)
    _, csv = _load()
    mapping = suggest_ro_mapping(history, csv, load_ro_mapping())
    RO_MAPPING_FILE.parent.mkdir(parents=True, exist_ok=True)
    mapping.to_csv(RO_MAPPING_FILE, index=False, lineterminator="\n")
    print(f"Team-name mapping: {len(mapping)} rows in {RO_MAPPING_FILE.name}")


def cmd_el_fetch() -> None:
    from referees import euroleague as el

    print(
        "Downloading EuroLeague schedules and box scores (about 1 request per second)..."
    )
    try:
        counts = el.fetch()
    except el.FeedBlocked as e:
        raise SystemExit(f"PROBLEM: {e}")
    print(
        f"Done: {counts['downloaded']} new games, {counts['cached']} already saved, "
        f"{counts['skipped']} still in progress (skipped)."
    )


def cmd_el_build() -> dict:
    import json

    from referees import euroleague as el
    from referees.euroleague_stats import build

    games, players, problems = el.load()
    if games.empty:
        raise SystemExit(
            "No EuroLeague games saved yet: run `uv run referees el-fetch` first."
        )
    report = build(games, players, problems)
    el.EL_JSON.write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(
        f"{report['games']} games, {len(report['referees'])} referees, "
        f"{len(report['players'])} player-seasons -> {el.EL_JSON.relative_to(el.DATA_DIR.parent)}"
    )
    avg = report["league_average"]
    print(
        f"League average per game: {avg['fouls_total']:.1f} fouls "
        f"({avg['fouls_home']:.1f} home / {avg['fouls_away']:.1f} away), "
        f"{avg['ftm_total']:.1f} free throws made ({avg['ftm_home']:.1f} / {avg['ftm_away']:.1f})"
    )
    for p in problems[:10]:
        print(f"  PROBLEM: {p}")
    if len(problems) > 10:
        print(f"  ... and {len(problems) - 10} more (see data_quality in the JSON)")
    return report


def _el_report() -> dict:
    import json

    from referees import euroleague as el

    if not el.EL_JSON.exists():
        raise SystemExit(
            "No EuroLeague figures yet: run `uv run referees el-build` first."
        )
    return json.loads(el.EL_JSON.read_text(encoding="utf-8"))


def _print_crew(names: list[str], report: dict, games=None) -> None:
    from referees.euroleague_stats import crew_figures

    c = crew_figures(names, report, games)
    lg = c["league_average"]
    print(f"  Crew: {', '.join(c['referees']) or '-'}")
    if c["unknown"]:
        print(f"  Not found (counted as average): {', '.join(c['unknown'])}")
    e, f = c["expected"], c["factor"]
    print(f"  {'':24}{'crew':>8}{'league':>8}{'factor':>8}")
    for m, label in (
        ("fouls_total", "Fouls (both teams)"),
        ("fouls_home", "Fouls, home team"),
        ("fouls_away", "Fouls, away team"),
        ("ftm_total", "FT made (both teams)"),
        ("ftm_home", "FT made, home team"),
        ("ftm_away", "FT made, away team"),
        ("fta_total", "FT attempted (both)"),
    ):
        print(f"  {label:24}{e[m]:>8.1f}{lg[m]:>8.1f}{f[m]:>8.2f}")
    pfe = c["player_foul_effect"]
    print(
        f"  Player foul effect: committed x{pfe['committed']:.2f}, drawn x{pfe['drawn']:.2f}"
    )
    if c["together"]:
        t = c["together"]
        print(
            f"  Together before: {t['games']} games, {t['fouls_total']:.1f} fouls and "
            f"{t['ftm_total']:.1f} FT made per game"
        )


def cmd_el_crew(names: list[str]) -> None:
    from referees import euroleague as el

    report = _el_report()
    games, _, _ = el.load()
    _print_crew(names, report, games)


def cmd_el_upcoming() -> None:
    from referees import euroleague as el

    report = _el_report()
    games, _, _ = el.load()
    print("Next 7 days of EuroLeague games (crews appear close to game day):")
    try:
        upcoming = el.upcoming()
    except el.FeedBlocked as e:
        raise SystemExit(f"PROBLEM: {e}")
    if not upcoming:
        print("  No games in the next 7 days.")
    for g in upcoming:
        print(f"\n{g['date']} {g['time']} (CET)  {g['home']} v {g['away']}")
        if len(g["referees"]) == 3:
            _print_crew(g["referees"], report, games)
        else:
            print("  Crew not announced yet.")


def main() -> None:
    # Show accents (Atlético, Köln) correctly even in older Windows terminals.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        prog="referees",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "command",
        choices=[
            "fetch",
            "suggest-mapping",
            "check",
            "build",
            "update",
            "export-dashboard",
            "upcoming",
            "backtest",
            "api-football-probe",
            "rfef-pdf",
            "fetch-ro",
            "el-fetch",
            "el-build",
            "el-crew",
            "el-upcoming",
        ],
    )
    parser.add_argument(
        "--full", action="store_true", help="for fetch-ro: read every history page"
    )
    parser.add_argument(
        "args",
        nargs="*",
        help="rfef-pdf: the PDF link on rfef.es; fetch-ro: a league key; "
        "el-crew: the three referee names",
    )
    args = parser.parse_args()
    args.url = args.args[0] if args.args else None
    if args.command == "fetch":
        cmd_fetch()
    elif args.command == "suggest-mapping":
        cmd_suggest_mapping()
    elif args.command == "check":
        cmd_check()
    elif args.command == "build":
        cmd_build()
    elif args.command == "export-dashboard":
        cmd_export_dashboard()
    elif args.command == "upcoming":
        cmd_upcoming()
    elif args.command == "backtest":
        cmd_backtest()
    elif args.command == "api-football-probe":
        cmd_api_football_probe()
    elif args.command == "fetch-ro":
        cmd_fetch_ro(args.full, [args.url] if args.url else None)
    elif args.command == "rfef-pdf":
        if not args.url:
            raise SystemExit("Usage: uv run referees rfef-pdf <pdf link on rfef.es>")
        cmd_rfef_pdf(args.url)
    elif args.command == "el-fetch":
        cmd_el_fetch()
    elif args.command == "el-build":
        cmd_el_build()
    elif args.command == "el-crew":
        if len(args.args) != 3:
            raise SystemExit(
                'Usage: uv run referees el-crew "Name 1" "Name 2" "Name 3"'
            )
        cmd_el_crew(args.args)
    elif args.command == "el-upcoming":
        cmd_el_upcoming()
    else:
        cmd_fetch()
        cmd_build()

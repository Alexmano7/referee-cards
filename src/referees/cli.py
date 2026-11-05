"""Command line: `uv run referees <command>`.

Commands:
  fetch            download CSVs and API data (current season + two previous)
  suggest-mapping  add suggested team-name pairs to config/team_name_mapping.csv
  check            join the data, show 10 joined matches and list failed joins
  build            write data/matches.csv, data/unmatched.csv and data/referees.json
  update           fetch + build
  export-dashboard turn data/matches.csv + data/referees.json into the dashboard's
                   tables in data/dashboard/ (no downloads; run `git pull` first)
"""

from __future__ import annotations

import argparse
import json
import sys

from referees import fetch_api, fetch_csv
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
    seasons = seasons_to_fetch()
    return fetch_api.load_all_api_matches(seasons), fetch_csv.load_all_csv_matches(
        seasons
    )


def cmd_fetch() -> None:
    seasons = seasons_to_fetch()
    print(f"Downloading CSVs for seasons {seasons} ...")
    fetch_csv.download_all(seasons)
    print(
        "Downloading football-data.org matches (spaced out for the 10/minute limit) ..."
    )
    problems = fetch_api.download_all(seasons)
    if problems:
        # Stop here so an incomplete download never overwrites good output files.
        raise SystemExit("Some API downloads failed:\n  " + "\n  ".join(problems))


def cmd_suggest_mapping() -> None:
    api, csv = _load()
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
    mapping = load_mapping()
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
    joined, unmatched = join_matches(api, csv, load_mapping())
    MATCHES_FILE.parent.mkdir(parents=True, exist_ok=True)
    joined.to_csv(MATCHES_FILE, index=False, lineterminator="\n")
    unmatched.to_csv(UNMATCHED_FILE, index=False, lineterminator="\n")
    report = build_report(joined, api, unmatched)
    REFEREES_JSON.write_text(
        json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
    )
    print(
        f"Wrote {REFEREES_JSON} ({len(report['referees'])} referees, {len(joined)} matches)"
    )


def cmd_export_dashboard() -> None:
    from referees.dashboard_export import build_tables, load_matches_csv, write_tables

    report = json.loads(REFEREES_JSON.read_text(encoding="utf-8"))
    tables = build_tables(load_matches_csv(MATCHES_FILE), report)
    for path in write_tables(tables, DATA_DIR / "dashboard"):
        rows = len(json.loads(path.read_text(encoding="utf-8")))
        print(f"  {path.name}: {rows} rows, {path.stat().st_size / 1024:.0f} KB")
    print(f"Data generated at {report['generated_at']}")


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
        ],
    )
    args = parser.parse_args()
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
    else:
        cmd_fetch()
        cmd_build()

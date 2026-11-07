"""Referee history for leagues without a free API (Greece, Belgium) from referee.observer.

Only public HTML pages are read (its robots.txt allows them; /api/ is not used), slowly:
- /referees?competition=<id>&page=N      lists the referees active in a competition
- /referee/<cc>/<slug>/matches?page=N     one referee's matches, newest first (~50 a page)

Each history row looks like (visible text): competition, dd/mm/yyyy, home, [score – score], away,
icon, role ("Referee", "VAR", "Fourth Official", ...), season. Only rows where the person was
the main referee in the league's own competition are kept.

`update_history()` writes data/referee_observer_history.csv (committed), so the daily GitHub
job uses the history without contacting the site. The first run reads every page; later runs
read only page 1 per referee (new matches appear at the top) unless `full=True`.
"""

from __future__ import annotations

import html as html_lib
import re
import time
from datetime import date, datetime

import pandas as pd
import requests

from referees.config import LEAGUES, RO_HISTORY_FILE, USER_AGENT, League

BASE_URL = "https://www.referee.observer"
DELAY_SECONDS = 1.5
MAX_PAGES = 12
COLUMNS = ["league", "date", "ro_home", "ro_away", "referee", "ro_path"]

_DATE = re.compile(r"^\d{2}/\d{2}/\d{4}$")
_TIME = re.compile(r"^\d{1,2}:\d{2}$")
_LINK = re.compile(r'href="(/referee/[a-z]{3}/[a-z0-9-]+)"')


class RefereeObserverError(RuntimeError):
    pass


def _get(url: str, session: requests.Session) -> str:
    for attempt in range(3):
        resp = session.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
        if resp.status_code == 200:
            time.sleep(DELAY_SECONDS)
            return resp.text
        if resp.status_code == 404:  # past the last page of a list: no more rows
            time.sleep(DELAY_SECONDS)
            return ""
        if resp.status_code in (403, 429):
            raise RefereeObserverError(
                f"referee.observer refused the request (HTTP {resp.status_code}); "
                "don't work around it, report it to the user"
            )
        time.sleep(5 * (attempt + 1))
    raise RefereeObserverError(f"HTTP {resp.status_code} for {url}")


def _visible_lines(page: str) -> list[str]:
    body = re.sub(r"<script.*?</script>", " ", page, flags=re.DOTALL)
    text = html_lib.unescape(re.sub(r"<[^>]+>", "\n", body))
    return [line.strip() for line in text.split("\n") if line.strip()]


def referee_name(page: str) -> str | None:
    m = re.search(r"<title>([^<]+?)\s+—", page)
    return html_lib.unescape(m.group(1)).strip() if m else None


def parse_history(page: str) -> list[dict]:
    """All matches on one history page: competition, date, home, away, role."""
    lines = _visible_lines(page)
    rows = []
    for i, line in enumerate(lines):
        if not _DATE.match(line) or i == 0 or i + 3 >= len(lines):
            continue
        j = i + 1
        home = lines[j]
        j += 1
        if (
            j + 2 < len(lines)
            and lines[j].isdigit()
            and lines[j + 1] in ("–", "-")
            and lines[j + 2].isdigit()
        ):
            j += 3  # a played match: "2 – 0"
        elif lines[j] in ("vs", "v", "–", "-") or _TIME.match(lines[j]):
            j += 1  # not played yet
        if j >= len(lines):
            continue
        away = lines[j]
        role = next(
            (x for x in lines[j + 1 : j + 5] if re.search(r"[A-Za-z]", x)), None
        )
        rows.append(
            {
                "competition": lines[i - 1],
                "date": datetime.strptime(line, "%d/%m/%Y").date(),
                "home": home,
                "away": away,
                "role": role,
            }
        )
    return rows


def list_referees(competition_id: int, session: requests.Session) -> list[str]:
    paths: list[str] = []
    for page in range(1, 30):
        found = _LINK.findall(
            _get(
                f"{BASE_URL}/referees?competition={competition_id}&page={page}", session
            )
        )
        new = [p for p in dict.fromkeys(found) if p not in paths]
        if not new:
            break
        paths.extend(new)
    return paths


def referee_history(
    path: str, session: requests.Session, full: bool
) -> tuple[str | None, list[dict]]:
    name, rows = None, []
    for page in range(1, (MAX_PAGES if full else 1) + 1):
        html = _get(f"{BASE_URL}{path}/matches?page={page}", session)
        name = name or referee_name(html)
        page_rows = parse_history(html)
        if not page_rows:
            break
        rows.extend(page_rows)
    return name, rows


def load_history() -> pd.DataFrame:
    if not RO_HISTORY_FILE.exists():
        return pd.DataFrame(columns=COLUMNS)
    df = pd.read_csv(RO_HISTORY_FILE, dtype=str, keep_default_na=False)
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df[COLUMNS]


def _save(old: pd.DataFrame, rows: list[dict]) -> pd.DataFrame:
    new = pd.DataFrame(rows, columns=COLUMNS)
    merged = pd.concat([old, new], ignore_index=True).drop_duplicates(
        subset=["league", "date", "ro_home", "ro_away"], keep="last"
    )
    merged = merged.sort_values(["league", "date", "ro_home"]).reset_index(drop=True)
    RO_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = RO_HISTORY_FILE.with_suffix(".tmp")
    merged.to_csv(tmp, index=False, lineterminator="\n")
    tmp.replace(RO_HISTORY_FILE)
    return merged


def update_history(
    full: bool | None = None, leagues: list[str] | None = None, progress=print
) -> pd.DataFrame:
    """Fetch the history of every referee in the referee.observer leagues and save it.

    Saves after each league, so an interrupted run keeps what it already read. `leagues`
    limits the run to some league keys (e.g. ["BEL"]).
    """
    targets: list[League] = [
        lg
        for lg in LEAGUES
        if lg.ro_competition_id and (not leagues or lg.key in leagues)
    ]
    merged = load_history()
    start_count = len(merged)
    session = requests.Session()
    for lg in targets:
        league_full = (
            full if full is not None else merged[merged["league"] == lg.key].empty
        )
        paths = list_referees(lg.ro_competition_id, session)
        progress(
            f"  {lg.name}: {len(paths)} officials listed; reading "
            f"{'all' if league_full else 'the newest'} history pages"
        )
        rows = []
        for path in paths:
            name, history = referee_history(path, session, league_full)
            for h in history:
                if (
                    h["role"] == "Referee"
                    and h["competition"] == lg.ro_competition
                    and h["date"] <= date.today()
                ):
                    rows.append(
                        {
                            "league": lg.key,
                            "date": h["date"],
                            "ro_home": h["home"],
                            "ro_away": h["away"],
                            "referee": name,
                            "ro_path": path,
                        }
                    )
        merged = _save(merged, rows)
        progress(f"  {lg.name}: saved; {len(merged)} refereed matches in the file")
    progress(f"  {len(merged) - start_count} new rows in {RO_HISTORY_FILE.name}")
    return merged

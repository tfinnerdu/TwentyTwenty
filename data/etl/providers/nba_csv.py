"""
data/etl/providers/nba_csv.py
=============================
NBA provider that ingests per-player *season* stats from CSV and rolls them
up into career lines, the same season->career shape as the Lahman provider.

Why CSV and not the wyattowalsh "nbadb" SQLite directly: that Kaggle dataset
is excellent but *game-level* (team box scores + play-by-play) -- it does not
carry per-player career averages, which is exactly what our PPG/RPG/APG/games
categories need. So this provider reads a per-player-season table, which you
can produce from:
  - a Kaggle "NBA player season stats" CSV export, or
  - an nba_api dump (stats.nba.com is the canonical source), or
  - any source mapped to the two canonical files below.

Honors (rings, MVP, Finals MVP, All-NBA, All-Star) are NOT in box-score data;
they come from the curated awards overlay (data/etl/awards/nba.json), applied
by the pipeline after this provider runs.

Expected files in --source-dir (season totals, not per-game):
  nba_players.csv : player_id,name,position,college,birth_year,birth_city,
                    birth_state,birth_country,height_inches,weight_lbs,
                    draft_year,draft_round,draft_number
  nba_seasons.csv : player_id,season,team,g,pts,trb,ast

Usage:
    python -m data.etl.run_provider --provider nba_csv --source-dir ./nba_csv
    python -m data.etl.providers.nba_csv --inspect --source-dir ./nba_csv
"""

import csv
import logging
import os
import sys
from typing import Iterable

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))
from data.etl.providers.base import Provider

log = logging.getLogger(__name__)

# NBA team abbreviation -> franchise nickname (matches the team categories)
NBA_TEAMS = {
    "ATL": "Hawks", "BOS": "Celtics", "BKN": "Nets", "NJN": "Nets",
    "CHA": "Hornets", "CHO": "Hornets", "CHI": "Bulls", "CLE": "Cavaliers",
    "DAL": "Mavericks", "DEN": "Nuggets", "DET": "Pistons", "GSW": "Warriors",
    "HOU": "Rockets", "IND": "Pacers", "LAC": "Clippers", "LAL": "Lakers",
    "MEM": "Grizzlies", "MIA": "Heat", "MIL": "Bucks", "MIN": "Timberwolves",
    "NOP": "Pelicans", "NOH": "Pelicans", "NYK": "Knicks", "OKC": "Thunder",
    "SEA": "SuperSonics", "ORL": "Magic", "PHI": "76ers", "PHX": "Suns",
    "PHO": "Suns", "POR": "Trail Blazers", "SAC": "Kings", "SAS": "Spurs",
    "TOR": "Raptors", "UTA": "Jazz", "WAS": "Wizards", "WSB": "Bullets",
}


def _i(s) -> int:
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return 0


def _year(s) -> int:
    s = (s or "").strip()
    return int(s[:4]) if s[:4].isdigit() else 0


class NBACsvProvider(Provider):
    sport = "NBA"
    source_name = "nba_csv"

    def __init__(self, source_dir: str | None = None, **_):
        self.data_dir = source_dir or os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "cache", "nba")

    def _path(self, name: str) -> str:
        return os.path.join(self.data_dir, name)

    def _rows(self, name: str):
        path = self._path(name)
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"{path} not found. Supply nba_players.csv and nba_seasons.csv "
                f"via --source-dir (see module docstring for the schema).")
        with open(path, newline="", encoding="utf-8-sig") as fh:
            yield from csv.DictReader(fh)

    def inspect(self):
        for name in ("nba_players.csv", "nba_seasons.csv"):
            path = self._path(name)
            if not os.path.exists(path):
                print(f"{name}: MISSING")
                continue
            with open(path, newline="", encoding="utf-8-sig") as fh:
                rdr = csv.reader(fh)
                header = next(rdr, [])
                n = sum(1 for _ in rdr)
            print(f"{name}: {n} rows, columns={header}")

    def fetch(self) -> Iterable[dict]:
        # aggregate seasons -> career
        agg: dict = {}
        for r in self._rows("nba_seasons.csv"):
            pid = r["player_id"]
            a = agg.setdefault(pid, {"g": 0, "pts": 0, "trb": 0, "ast": 0,
                                     "teams": [], "years": set()})
            a["g"]   += _i(r.get("g"));   a["pts"] += _i(r.get("pts"))
            a["trb"] += _i(r.get("trb")); a["ast"] += _i(r.get("ast"))
            a["years"].add(_year(r.get("season")))
            nick = NBA_TEAMS.get((r.get("team") or "").upper())
            if nick and nick not in a["teams"]:
                a["teams"].append(nick)

        for r in self._rows("nba_players.csv"):
            pid = r["player_id"]
            a = agg.get(pid)
            if not a or a["g"] == 0:
                continue  # no games -> skip
            years = {y for y in a["years"] if y}
            decades = sorted({f"{(y // 10) * 10}s" for y in years})
            yield {
                "sport": "NBA",
                "sr_id": f"nba_{pid}",
                "name": r.get("name", "").strip(),
                "position": (r.get("position") or "").strip(),
                "college": (r.get("college") or "").strip(),
                "birth_year": _i(r.get("birth_year")) or None,
                "birth_city": (r.get("birth_city") or "").strip(),
                "birth_state": (r.get("birth_state") or "").strip(),
                "birth_country": (r.get("birth_country") or "USA").strip() or "USA",
                "height_inches": _i(r.get("height_inches")) or None,
                "weight_lbs": _i(r.get("weight_lbs")) or None,
                "draft_year": _i(r.get("draft_year")) or None,
                "draft_round": _i(r.get("draft_round")) or None,
                "draft_pick": _i(r.get("draft_number")) or None,
                "teams": a["teams"],
                "active_decades": decades,
                "debut_year": min(years) if years else None,
                "final_year": max(years) if years else None,
                "nba_points":   round(a["pts"] / a["g"], 1) if a["g"] else 0.0,
                "nba_rebounds": round(a["trb"] / a["g"], 1) if a["g"] else 0.0,
                "nba_assists":  round(a["ast"] / a["g"], 1) if a["g"] else 0.0,
                "nba_games": a["g"],
                # honors filled by the curated awards overlay (awards/nba.json)
            }


def main():
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-dir", required=True)
    ap.add_argument("--inspect", action="store_true")
    args = ap.parse_args()
    p = NBACsvProvider(source_dir=args.source_dir)
    if args.inspect:
        p.inspect()
    else:
        print(f"{sum(1 for _ in p.fetch())} players parsed")


if __name__ == "__main__":
    main()

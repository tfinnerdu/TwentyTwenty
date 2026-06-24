"""
data/etl/providers/nhl_export.py
================================
Pull NHL career data from the official NHL API (api-web.nhle.com) into the
canonical nhl_players.csv / nhl_seasons.csv that NHLProvider ingests.

    python -m data.etl.providers.nhl_export --start 2000 --end 2024 --out ./nhl_csv
    python -m data.etl.run_provider --provider nhl_api --source-dir ./nhl_csv

Approach: crawl current-team rosters across the season range to collect player
IDs, then read each player's /landing endpoint for career bio + per-season
skater totals. Uses only `requests`.

Caveats:
  - The roster crawl uses present-day team codes, so players whose careers
    were entirely with relocated/defunct franchises (pre-2000) may be missed;
    honors still come from awards/nhl.json. Widen --start to go deeper.
  - Verify against the live API on first run; it's polled politely with a delay.
"""

import argparse
import csv
import os
import time

BASE = "https://api-web.nhle.com/v1"
TEAMS = ["ANA", "BOS", "BUF", "CGY", "CAR", "CHI", "COL", "CBJ", "DAL", "DET",
         "EDM", "FLA", "LAK", "MIN", "MTL", "NSH", "NJD", "NYI", "NYR", "OTT",
         "PHI", "PIT", "SJS", "SEA", "STL", "TBL", "TOR", "VAN", "VGK", "WSH",
         "WPG", "ARI", "UTA"]
# full team name -> tricode (so per-season teamName maps to NHLProvider's map)
NAME_TO_TRI = {
    "Anaheim Ducks": "ANA", "Boston Bruins": "BOS", "Buffalo Sabres": "BUF",
    "Calgary Flames": "CGY", "Carolina Hurricanes": "CAR", "Chicago Blackhawks": "CHI",
    "Colorado Avalanche": "COL", "Columbus Blue Jackets": "CBJ", "Dallas Stars": "DAL",
    "Detroit Red Wings": "DET", "Edmonton Oilers": "EDM", "Florida Panthers": "FLA",
    "Los Angeles Kings": "LAK", "Minnesota Wild": "MIN", "Montreal Canadiens": "MTL",
    "Montréal Canadiens": "MTL", "Nashville Predators": "NSH", "New Jersey Devils": "NJD",
    "New York Islanders": "NYI", "New York Rangers": "NYR", "Ottawa Senators": "OTT",
    "Philadelphia Flyers": "PHI", "Pittsburgh Penguins": "PIT", "San Jose Sharks": "SJS",
    "St. Louis Blues": "STL", "Tampa Bay Lightning": "TBL", "Toronto Maple Leafs": "TOR",
    "Vancouver Canucks": "VAN", "Washington Capitals": "WSH", "Winnipeg Jets": "WPG",
    "Quebec Nordiques": "QUE", "Hartford Whalers": "HFD",
}
POS_NORM = {"L": "LW", "R": "RW"}


def _get(path, delay=0.4):
    import requests
    time.sleep(delay)
    r = requests.get(BASE + path, timeout=30)
    if r.status_code != 200:
        return None
    return r.json()


def _name(obj):
    return obj.get("default", "") if isinstance(obj, dict) else (obj or "")


def export(out_dir: str, start: int = 2000, end: int | None = None, delay: float = 0.4):
    from datetime import date
    if end is None:                     # track the current season, don't freeze at a fixed year
        end = date.today().year
    os.makedirs(out_dir, exist_ok=True)

    # 1. collect player ids from rosters
    ids = set()
    for team in TEAMS:
        for y in range(start, end + 1):
            data = _get(f"/roster/{team}/{y}{y + 1}", delay)
            if not data:
                continue
            for group in ("forwards", "defensemen", "goalies"):
                for pl in data.get(group, []):
                    if pl.get("id"):
                        ids.add(pl["id"])

    # 2. per-player landing -> bio + season rows
    players, seasons = [], []
    for pid in ids:
        lp = _get(f"/player/{pid}/landing", delay)
        if not lp:
            continue
        pos = POS_NORM.get(lp.get("position", ""), lp.get("position", ""))
        birth = str(lp.get("birthDate", ""))
        draft = lp.get("draftDetails") or {}
        players.append([pid, f"{_name(lp.get('firstName'))} {_name(lp.get('lastName'))}".strip(),
                        pos, "", birth[:4] if birth[:4].isdigit() else "",
                        "", "", lp.get("birthCountry", "USA") or "USA",
                        lp.get("heightInInches", ""), lp.get("weightInPounds", ""),
                        draft.get("year", ""), draft.get("round", ""), draft.get("overallPick", "")])
        for st in lp.get("seasonTotals", []):
            if st.get("gameTypeId") != 2 or st.get("leagueAbbrev") != "NHL":
                continue
            season = str(st.get("season", ""))   # e.g. "20102011"
            year = int(season[:4]) if season[:4].isdigit() else 0
            tri = NAME_TO_TRI.get(_name(st.get("teamName")), "")
            seasons.append([pid, year, tri, st.get("gamesPlayed", 0),
                            st.get("goals", 0), st.get("assists", 0), st.get("points", 0)])

    with open(os.path.join(out_dir, "nhl_players.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["player_id", "name", "position", "college", "birth_year", "birth_city",
                    "birth_state", "birth_country", "height_inches", "weight_lbs",
                    "draft_year", "draft_round", "draft_number"])
        w.writerows(players)
    with open(os.path.join(out_dir, "nhl_seasons.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["player_id", "season", "team", "g", "goals", "assists", "points"])
        w.writerows(seasons)
    return len(players), len(seasons)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, default=2000)
    ap.add_argument("--end", type=int, default=2024)
    ap.add_argument("--out", default="./nhl_csv")
    args = ap.parse_args()
    p, s = export(args.out, args.start, args.end)
    print(f"Wrote {p} players, {s} player-seasons to {args.out}")


if __name__ == "__main__":
    main()

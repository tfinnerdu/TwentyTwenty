"""
data/etl/providers/wnba_export.py
=================================
Pull WNBA season stats from the official stats.wnba.com (same API shape as
stats.nba.com) into the canonical wnba_players.csv / wnba_seasons.csv that
WNBAProvider ingests.

    python -m data.etl.providers.wnba_export --start 1997 --end 2024 --out ./wnba_csv
    python -m data.etl.run_provider --provider wnba_csv --source-dir ./wnba_csv

EXPERIMENTAL: stats.wnba.com is undocumented and param/header-strict; this
mirrors nba_api's request shape. Honors come from awards/wnba.json; bio
(position/college) is left blank here. Verify on first run -- run_full falls
back to the bundled fixture if this fails.
"""

import argparse
import csv
import logging
import os
import time

log = logging.getLogger("wnba_export")

URL = "https://stats.wnba.com/stats/leaguedashplayerstats"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Referer": "https://stats.wnba.com/",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://stats.wnba.com",
    "x-nba-stats-origin": "stats",
    "x-nba-stats-token": "true",
    "Connection": "keep-alive",
}


def _params(season: int) -> dict:
    p = {k: "" for k in (
        "College", "Conference", "Country", "DateFrom", "DateTo", "Division",
        "DraftPick", "DraftYear", "GameScope", "GameSegment", "Height", "Location",
        "Outcome", "PlayerExperience", "PlayerPosition", "SeasonSegment",
        "ShotClockRange", "StarterBench", "VsConference", "VsDivision", "Weight")}
    p.update({"LastNGames": 0, "LeagueID": "10", "MeasureType": "Base", "Month": 0,
              "OpponentTeamID": 0, "PORound": 0, "PaceAdjust": "N", "PerMode": "Totals",
              "Period": 0, "PlusMinus": "N", "Rank": "N", "Season": str(season),
              "SeasonType": "Regular Season", "TeamID": 0, "TwoWay": 0})
    return p


def export(out_dir: str, start: int = 1997, end: int = 2024, delay: float = 0.6,
           timeout: int = 60, retries: int = 4):
    import requests
    os.makedirs(out_dir, exist_ok=True)
    seasons, pids = [], {}

    for year in range(start, end + 1):
        data = None
        for attempt in range(1, retries + 1):
            try:
                r = requests.get(URL, headers=HEADERS, params=_params(year), timeout=timeout)
                if r.status_code == 200:
                    data = r.json()
                    break
                log.warning(f"  WNBA {year}: HTTP {r.status_code} (attempt {attempt}/{retries})")
            except Exception as e:
                log.warning(f"  WNBA {year}: {type(e).__name__} (attempt {attempt}/{retries})")
            time.sleep(3 * attempt)
        if not data:
            if not pids:   # first season already unreachable -> bail instead of hours of retries
                raise RuntimeError(
                    f"stats.wnba.com unreachable (season {year} failed all {retries} retries -- "
                    f"often blocked on corporate/campus networks); bailing to fixture")
            continue
        rs = data["resultSets"][0]
        idx = {h: i for i, h in enumerate(rs["headers"])}
        for row in rs["rowSet"]:
            pid = row[idx["PLAYER_ID"]]
            pids[pid] = row[idx["PLAYER_NAME"]]
            seasons.append([pid, year, row[idx.get("TEAM_ABBREVIATION", -1)] if "TEAM_ABBREVIATION" in idx else "",
                            row[idx["GP"]], row[idx["PTS"]], row[idx["REB"]], row[idx["AST"]]])
        time.sleep(delay)

    if not pids:
        raise RuntimeError("stats.wnba.com returned no data (unreachable / blocked)")

    players = [[pid, name, "", "", "", "", "", "USA", "", "", "", "", ""]
               for pid, name in pids.items()]

    with open(os.path.join(out_dir, "wnba_players.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["player_id", "name", "position", "college", "birth_year", "birth_city",
                    "birth_state", "birth_country", "height_inches", "weight_lbs",
                    "draft_year", "draft_round", "draft_number"])
        w.writerows(players)
    with open(os.path.join(out_dir, "wnba_seasons.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["player_id", "season", "team", "g", "pts", "reb", "ast"])
        w.writerows(seasons)
    return len(players), len(seasons)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, default=1997)
    ap.add_argument("--end", type=int, default=2024)
    ap.add_argument("--out", default="./wnba_csv")
    ap.add_argument("--timeout", type=int, default=60)
    ap.add_argument("--retries", type=int, default=4)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    p, s = export(args.out, args.start, args.end, timeout=args.timeout, retries=args.retries)
    print(f"Wrote {p} players, {s} player-seasons to {args.out}")


if __name__ == "__main__":
    main()

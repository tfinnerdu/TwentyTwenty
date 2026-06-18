"""
data/etl/providers/nba_github_export.py
=======================================
NBA season stats from a GitHub-hosted dataset -- the reliable path when
stats.nba.com is blocked/throttled (it's reachable the same way MLB is).

Source: jacobbaruch/NBA_data_scraping_and_analysis -- per-player season totals
for the NBA, 1999-2020, with bio + draft. Writes the canonical
nba_players.csv / nba_seasons.csv that NBAProvider ingests.

    python -m data.etl.providers.nba_github_export --out ./nba_csv
    python -m data.etl.run_provider --provider nba_csv --source-dir ./nba_csv

Trade-off vs the live stats.nba.com pull: this covers 1999-2020 (no current
seasons, no pre-1999) and has no position/college. Honors still come from the
curated awards overlay; draft round/pick come through for the cross-sport
draft categories.
"""

import argparse
import csv
import io
import logging
import os
import re
import urllib.request

log = logging.getLogger("nba_github_export")

SRC = ("https://raw.githubusercontent.com/jacobbaruch/NBA_data_scraping_and_analysis/"
       "master/Basketball%20Players%20Statistics%20per%20Season/"
       "players_stats_by_season_full_details.csv")

# aggregate/placeholder team markers to skip so we don't double-count a
# traded player's season (per-team rows already sum to the season total)
SKIP_TEAMS = {"TOT", "TOTAL", "2TM", "3TM", "4TM", ""}


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def _height_in(h: str):
    try:
        ft, inch = str(h).split("-")
        return int(ft) * 12 + int(inch)
    except Exception:
        return ""


def _i(s):
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return 0


def export(out_dir: str, src_url: str = SRC):
    os.makedirs(out_dir, exist_ok=True)
    log.info(f"  downloading NBA dataset <- {src_url}")
    with urllib.request.urlopen(src_url, timeout=90) as r:
        text = r.read().decode("utf-8", "ignore")

    seasons, bio = [], {}
    for row in csv.DictReader(io.StringIO(text)):
        if row.get("League") != "NBA" or row.get("Stage") != "Regular_Season":
            continue
        team = (row.get("Team") or "").strip().upper()
        if team in SKIP_TEAMS:
            continue
        pid = _slug(row.get("Player", ""))
        if not pid:
            continue
        yr = (row.get("Season") or "")[:4]
        seasons.append([pid, yr, team, _i(row.get("GP")), _i(row.get("PTS")),
                        _i(row.get("REB")), _i(row.get("AST"))])
        if pid not in bio:
            nat = (row.get("nationality") or "").strip()
            country = "USA" if nat in ("United States", "USA", "") else nat
            bio[pid] = [pid, row.get("Player", "").strip(), "", "",
                        _i(row.get("birth_year")) or "", "", "", country,
                        _height_in(row.get("height", "")), _i(row.get("weight")) or "",
                        "", _i(row.get("draft_round")) or "", _i(row.get("draft_pick")) or ""]

    if not seasons:
        raise RuntimeError("no NBA rows parsed from the GitHub dataset (schema changed?)")

    with open(os.path.join(out_dir, "nba_players.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["player_id", "name", "position", "college", "birth_year", "birth_city",
                    "birth_state", "birth_country", "height_inches", "weight_lbs",
                    "draft_year", "draft_round", "draft_number"])
        w.writerows(bio.values())
    with open(os.path.join(out_dir, "nba_seasons.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["player_id", "season", "team", "g", "pts", "trb", "ast"])
        w.writerows(seasons)
    return len(bio), len(seasons)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="./nba_csv")
    ap.add_argument("--src", default=SRC)
    args = ap.parse_args()
    p, s = export(args.out, args.src)
    print(f"Wrote {p} players, {s} player-seasons to {args.out}")


if __name__ == "__main__":
    main()

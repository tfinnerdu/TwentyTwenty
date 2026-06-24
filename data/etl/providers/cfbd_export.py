"""
data/etl/providers/cfbd_export.py
=================================
Pulls NCAAF player season stats from the CollegeFootballData API and writes
them in the canonical ncaaf_players.csv / ncaaf_seasons.csv schema that
NCAAFProvider (run_provider --provider ncaaf_csv) ingests.

Run this on a networked machine with your CFBD key (it can't run from the
firewalled build sandbox):

    export CFBD_API_KEY=...          # https://collegefootballdata.com/key
    python -m data.etl.providers.cfbd_export --start 2015 --end 2023 --out ./ncaaf_csv
    python -m data.etl.run_provider --provider ncaaf_csv --source-dir ./ncaaf_csv

Honors (Heisman/All-American) aren't in the stat feed -- they come from the
curated awards/ncaaf.json overlay, applied automatically by the pipeline.

Notes / caveats:
  - CFBD's /stats/player/season is pivoted (one row per player x category x
    statType); we sum YDS/TD per category into career-style season rows.
  - Position and games-played aren't in that endpoint, so position is inferred
    from which category dominates and games is approximated. Good enough for
    the threshold + school categories; refine against /roster if you need more.
"""

import argparse
import csv
import json
import os
import sys
import urllib.request

API = "https://api.collegefootballdata.com"


def _get(path: str, key: str):
    req = urllib.request.Request(API + path, headers={"Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def _position(pass_yds, rush_yds, rec_yds, sacks) -> str:
    if sacks and sacks >= max(pass_yds, rush_yds, rec_yds, 1) / 20:
        return "DL"
    biggest = max(("QB", pass_yds), ("RB", rush_yds), ("WR", rec_yds), key=lambda t: t[1])
    return biggest[0] if biggest[1] else ""


def export(start: int, end: int, out_dir: str, key: str):
    os.makedirs(out_dir, exist_ok=True)
    # (playerId, year) -> stat row ;  playerId -> bio meta
    seasons: dict = {}
    meta: dict = {}

    for year in range(start, end + 1):
        print(f"  fetching {year}...")
        for row in _get(f"/stats/player/season?year={year}", key):
            pid = str(row.get("playerId") or row.get("player"))
            cat, st = row.get("category"), row.get("statType")
            try:
                val = float(row.get("stat") or 0)
            except (TypeError, ValueError):
                continue
            s = seasons.setdefault((pid, year), {"pass_yds": 0, "rush_yds": 0,
                                                 "rec_yds": 0, "tds": 0, "sacks": 0,
                                                 "team": row.get("team", "")})
            if cat == "passing" and st == "YDS":   s["pass_yds"] += val
            elif cat == "passing" and st == "TD":  s["tds"] += val
            elif cat == "rushing" and st == "YDS": s["rush_yds"] += val
            elif cat == "rushing" and st == "TD":  s["tds"] += val
            elif cat == "receiving" and st == "YDS": s["rec_yds"] += val
            elif cat == "receiving" and st == "TD":  s["tds"] += val
            elif cat == "defensive" and st == "SACKS": s["sacks"] += val
            m = meta.setdefault(pid, {"name": row.get("player", ""), "teams": {}, "career": {
                "pass_yds": 0, "rush_yds": 0, "rec_yds": 0, "sacks": 0}})
            m["teams"][row.get("team", "")] = m["teams"].get(row.get("team", ""), 0) + 1

    # accumulate career totals for position inference
    for (pid, year), s in seasons.items():
        c = meta[pid]["career"]
        for k in ("pass_yds", "rush_yds", "rec_yds", "sacks"):
            c[k] += s[k]

    players, season_rows = [], []
    for pid, m in meta.items():
        college = max(m["teams"], key=m["teams"].get) if m["teams"] else ""
        c = m["career"]
        pos = _position(c["pass_yds"], c["rush_yds"], c["rec_yds"], c["sacks"])
        players.append([pid, m["name"], pos, college, "", "", "", "USA", "", "", "", "", ""])
    for (pid, year), s in seasons.items():
        season_rows.append([pid, year, s["team"], 12, int(s["pass_yds"]),
                            int(s["rush_yds"]), int(s["rec_yds"]), int(s["tds"]),
                            round(s["sacks"], 1)])

    with open(os.path.join(out_dir, "ncaaf_players.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["player_id", "name", "position", "college", "birth_year", "birth_city",
                    "birth_state", "birth_country", "height_inches", "weight_lbs",
                    "draft_year", "draft_round", "draft_number"])
        w.writerows(players)
    with open(os.path.join(out_dir, "ncaaf_seasons.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["player_id", "season", "team", "g", "pass_yds", "rush_yds",
                    "rec_yds", "tds", "sacks"])
        w.writerows(season_rows)
    print(f"Wrote {len(players)} players, {len(season_rows)} player-seasons to {out_dir}")


def main():
    from datetime import date
    from data.etl.dbconfig import load_env
    load_env()                          # pull CFBD_API_KEY out of .env into os.environ
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, default=2015)
    # latest COMPLETE CFB season (numbered by its August start, so current year
    # isn't played yet -> year-1). Don't freeze at a fixed year.
    ap.add_argument("--end", type=int, default=date.today().year - 1)
    ap.add_argument("--out", default="./ncaaf_csv")
    ap.add_argument("--key", default=os.environ.get("CFBD_API_KEY"))
    args = ap.parse_args()
    if not args.key:
        sys.exit("Set CFBD_API_KEY or pass --key (https://collegefootballdata.com/key)")
    export(args.start, args.end, args.out, args.key)


if __name__ == "__main__":
    main()

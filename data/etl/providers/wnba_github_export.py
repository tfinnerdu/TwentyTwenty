"""
data/etl/providers/wnba_github_export.py
========================================
WNBA season stats from the wehoop / sportsdataverse GitHub data repo -- the
reliable path when stats.wnba.com is blocked/throttled (raw.githubusercontent.com
is reachable the same way the MLB and NBA datasets are).

Source: sportsdataverse/wehoop-wnba-data, per-game ESPN player box scores
(one parquet per season, 2003-present). We aggregate to per-player/per-season
totals and write the canonical wnba_players.csv / wnba_seasons.csv that
WNBAProvider ingests.

    python -m data.etl.providers.wnba_github_export --out ./wnba_csv
    python -m data.etl.run_provider --provider wnba_csv --source-dir ./wnba_csv

Coverage / trade-offs vs the live stats.wnba.com pull:
  - 2003-present (ESPN box scores don't go back to the 1997-2002 Comets era).
  - Real per-game stats, teams and position come through; PPG/RPG/APG, games,
    teams, position and active-decade categories all populate.
  - No birth/draft bio (box scores don't carry it), so birth-decade / draft
    categories stay blank for WNBA. Honors come from the curated awards
    overlay (awards/wnba.json), matched by name.
  - A handful of Commissioner's Cup games ESPN tags as regular season inflate
    games/points by ~5%; immaterial to the coarse category buckets.

Needs pandas + pyarrow (already pulled in by nfl_data_py / requirements-etl.txt).
"""

import argparse
import csv
import datetime
import logging
import os

log = logging.getLogger("wnba_github_export")

BASE = ("https://raw.githubusercontent.com/sportsdataverse/wehoop-wnba-data/"
        "main/wnba/player_box/parquet/player_box_{year}.parquet")

# ESPN team_display_name -> canonical franchise nickname. Doubles as the
# real-team filter: All-Star rosters (EAST/WEST/"Team Wilson"/...) aren't here
# so they're skipped. Relocations/renames collapse to one nickname so a
# franchise reads as a single team across its history.
FRANCHISES = {
    "Atlanta Dream": "Dream",
    "Charlotte Sting": "Sting",
    "Chicago Sky": "Sky",
    "Cleveland Rockers": "Rockers",
    "Connecticut Sun": "Sun",
    "Dallas Wings": "Wings",
    "Detroit Shock": "Shock",
    "Tulsa Shock": "Shock",
    "Golden State Valkyries": "Valkyries",
    "Houston Comets": "Comets",
    "Indiana Fever": "Fever",
    "Los Angeles Sparks": "Sparks",
    "Las Vegas Aces": "Aces",
    "Minnesota Lynx": "Lynx",
    "New York Liberty": "Liberty",
    "Phoenix Mercury": "Mercury",
    "Sacramento Monarchs": "Monarchs",
    "San Antonio Stars": "Stars",
    "San Antonio Silver Stars": "Stars",
    "Seattle Storm": "Storm",
    "Washington Mystics": "Mystics",
}


def _pos(p) -> str:
    """'G-F' / 'Guard' -> first canonical group letter."""
    s = str(p or "").strip().upper()
    if not s or s == "NAN":
        return ""
    head = s.replace("GUARD", "G").replace("FORWARD", "F").replace("CENTER", "C")
    return head.split("-")[0].strip()[:1]


def export(out_dir: str, start: int = 2003, end: int | None = None):
    import pandas as pd

    if end is None:
        end = datetime.date.today().year
    os.makedirs(out_dir, exist_ok=True)

    frames = []
    for year in range(start, end + 1):
        url = BASE.format(year=year)
        try:
            df = pd.read_parquet(url)
        except Exception as e:
            log.info(f"  WNBA {year}: no data ({type(e).__name__}) -- skipping")
            continue
        # regular season only, games the player actually played
        df = df[(df["season_type"] == 2) & (df["did_not_play"] == False)].copy()  # noqa: E712
        if df.empty:
            continue
        df["franchise"] = df["team_display_name"].map(FRANCHISES)
        df = df[df["franchise"].notna()]          # drop All-Star / exhibition rows
        for col in ("points", "rebounds", "assists"):
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
        frames.append(df[["season", "athlete_id", "athlete_display_name",
                          "athlete_position_abbreviation", "franchise",
                          "points", "rebounds", "assists"]])
        log.info(f"  WNBA {year}: {df['athlete_id'].nunique()} players")

    if not frames:
        raise RuntimeError("no WNBA data fetched from wehoop (network/schema issue)")

    import pandas as pd
    box = pd.concat(frames, ignore_index=True)

    # one season row per (player, season, franchise) so traded players keep
    # every franchise; the provider sums stats across a player's rows.
    grp = box.groupby(["athlete_id", "season", "franchise"], as_index=False).agg(
        g=("points", "size"), pts=("points", "sum"),
        reb=("rebounds", "sum"), ast=("assists", "sum"))

    seasons = [[int(r.athlete_id), int(r.season), r.franchise,
                int(r.g), int(r.pts), int(r.reb), int(r.ast)]
               for r in grp.itertuples(index=False)]

    # bio: one row per player -- most-frequent name + position across career
    bio_rows = []
    for pid, sub in box.groupby("athlete_id"):
        name = sub["athlete_display_name"].mode()
        name = name.iloc[0] if len(name) else ""
        pos = ""
        for cand in sub["athlete_position_abbreviation"]:
            pos = _pos(cand)
            if pos:
                break
        bio_rows.append([int(pid), name, pos, "", "", "", "", "USA", "", "", "", "", ""])

    with open(os.path.join(out_dir, "wnba_players.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["player_id", "name", "position", "college", "birth_year", "birth_city",
                    "birth_state", "birth_country", "height_inches", "weight_lbs",
                    "draft_year", "draft_round", "draft_number"])
        w.writerows(bio_rows)
    with open(os.path.join(out_dir, "wnba_seasons.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["player_id", "season", "team", "g", "pts", "reb", "ast"])
        w.writerows(seasons)
    return len(bio_rows), len(seasons)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="./wnba_csv")
    ap.add_argument("--start", type=int, default=2003)
    ap.add_argument("--end", type=int, default=None)
    args = ap.parse_args()
    p, s = export(args.out, args.start, args.end)
    print(f"Wrote {p} players, {s} player-seasons to {args.out}")


if __name__ == "__main__":
    main()

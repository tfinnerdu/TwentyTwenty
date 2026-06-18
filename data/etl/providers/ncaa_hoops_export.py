"""
data/etl/providers/ncaa_hoops_export.py
=======================================
NCAA basketball season stats from the sportsdataverse GitHub data repos --
bot-safe (raw.githubusercontent.com), the same path WNBA uses:

  NCAAB (men's)   sportsdataverse/hoopR-mbb-data    (ESPN box scores, 2003+)
  NCAAW (women's) sportsdataverse/wehoop-wbb-data   (ESPN box scores, 2004+)

ESPN per-game player box scores aggregated to per-player/per-season totals,
written as the canonical <sport>_players.csv / <sport>_seasons.csv that the
NCAAB/NCAAW providers ingest. The "team" is the school (team_location, e.g.
"Duke") -> the franchise/team category.

    python -m data.etl.providers.ncaa_hoops_export --sport NCAAB --out ./ncaab_csv
    python -m data.etl.providers.ncaa_hoops_export --sport NCAAW --out ./ncaaw_csv

Trade-offs vs the curated seed list: thousands of real players (2003/2004+),
real PPG/RPG/APG, school and position -- but no birth/draft (box scores don't
carry it). Honors stay with the curated overlay (the 24/16 legends keep their
championships / All-American / POY, and pre-2003 names it adds aren't in this
bulk set). A career contributor filter (min games + points) drops walk-ons so
the pool stays recognizable; curated names are excluded here so the honored
version wins.

Needs pandas + pyarrow (already pulled in by nfl_data_py / requirements-etl.txt).
"""

import argparse
import csv
import datetime
import json
import logging
import os
import re

log = logging.getLogger("ncaa_hoops_export")

SRC = {
    "NCAAB": ("https://raw.githubusercontent.com/sportsdataverse/hoopR-mbb-data/"
              "main/mbb/player_box/parquet/player_box_{year}.parquet", 2003),
    "NCAAW": ("https://raw.githubusercontent.com/sportsdataverse/wehoop-wbb-data/"
              "main/wbb/player_box/parquet/player_box_{year}.parquet", 2004),
}


def _slug(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _pos(p):
    s = str(p or "").strip().upper()
    if not s or s == "NAN":
        return ""
    return s.replace("GUARD", "G").replace("FORWARD", "F").replace("CENTER", "C").split("-")[0][:1]


def _curated_slugs(sport):
    """Name-slugs of the curated honors players for this sport -- excluded from
    the bulk pull so the curated (honored) row is authoritative, no duplicate."""
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "curated", f"{sport.lower()}.json")
    if not os.path.exists(path):
        return set()
    try:
        return {_slug(r.get("name", "")) for r in json.load(open(path, encoding="utf-8"))}
    except Exception:
        return set()


def export(sport, out_dir, start=None, end=None, min_games=25, min_points=100):
    import pandas as pd

    sport = sport.upper()
    if sport not in SRC:
        raise ValueError(f"sport must be one of {list(SRC)}")
    url_tmpl, default_start = SRC[sport]
    start = start or default_start
    end = end or datetime.date.today().year
    os.makedirs(out_dir, exist_ok=True)
    excluded = _curated_slugs(sport)

    frames = []
    for year in range(start, end + 1):
        try:
            df = pd.read_parquet(url_tmpl.format(year=year))
        except Exception as e:
            log.info(f"  {sport} {year}: no data ({type(e).__name__}) -- skipping")
            continue
        df = df[(df["season_type"] == 2) & (df["did_not_play"] == False)].copy()  # noqa: E712
        if df.empty:
            continue
        df["school"] = df["team_location"].astype(str).str.strip()
        df = df[(df["school"] != "") & (df["school"].str.lower() != "nan")]
        for c in ("points", "rebounds", "assists"):
            df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)
        frames.append(df[["season", "athlete_id", "athlete_display_name",
                          "athlete_position_abbreviation", "school",
                          "points", "rebounds", "assists"]])
        log.info(f"  {sport} {year}: {df['athlete_id'].nunique()} players")

    if not frames:
        raise RuntimeError(f"no {sport} data fetched (network/schema issue)")

    box = pd.concat(frames, ignore_index=True)
    grp = box.groupby(["athlete_id", "season", "school"], as_index=False).agg(
        g=("points", "size"), pts=("points", "sum"),
        reb=("rebounds", "sum"), ast=("assists", "sum"))

    # career totals -> contributor filter (drop walk-ons / cup-of-coffee careers)
    career = grp.groupby("athlete_id").agg(G=("g", "sum"), P=("pts", "sum"))
    keep = set(career[(career["G"] >= min_games) & (career["P"] >= min_points)].index)

    # bio: most-frequent name + position, primary school (most games)
    bio_rows, kept = [], set()
    for pid, sub in box.groupby("athlete_id"):
        if pid not in keep:
            continue
        name = sub["athlete_display_name"].mode()
        name = name.iloc[0] if len(name) else ""
        if _slug(name) in excluded:        # curated honors version wins
            continue
        pos = ""
        for cand in sub["athlete_position_abbreviation"]:
            pos = _pos(cand)
            if pos:
                break
        school = sub.groupby("school").size().idxmax()   # primary school
        bio_rows.append([int(pid), name, pos, school, "", "", "", "USA", "", "", "", "", ""])
        kept.add(pid)

    seasons = [[int(r.athlete_id), int(r.season), r.school, int(r.g), int(r.pts), int(r.reb), int(r.ast)]
               for r in grp.itertuples(index=False) if r.athlete_id in kept]

    with open(os.path.join(out_dir, f"{sport.lower()}_players.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["player_id", "name", "position", "college", "birth_year", "birth_city",
                    "birth_state", "birth_country", "height_inches", "weight_lbs",
                    "draft_year", "draft_round", "draft_number"])
        w.writerows(bio_rows)
    with open(os.path.join(out_dir, f"{sport.lower()}_seasons.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["player_id", "season", "team", "g", "pts", "reb", "ast"])
        w.writerows(seasons)
    return len(bio_rows), len(seasons)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--sport", required=True, choices=sorted(SRC))
    ap.add_argument("--out", default=None)
    ap.add_argument("--start", type=int, default=None)
    ap.add_argument("--end", type=int, default=None)
    ap.add_argument("--min-games", type=int, default=25)
    ap.add_argument("--min-points", type=int, default=100)
    args = ap.parse_args()
    out = args.out or f"./{args.sport.lower()}_csv"
    p, s = export(args.sport, out, args.start, args.end, args.min_games, args.min_points)
    print(f"Wrote {p} players, {s} player-seasons to {out}")


if __name__ == "__main__":
    main()

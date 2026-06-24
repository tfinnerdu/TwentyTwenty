#!/usr/bin/env python3
"""
data/etl/backfill_from_cache.py
===============================
Fill missing player bio (birth_state, birth_city, draft, position, college,
height/weight) in the game DB straight from the Sports-Reference pages ALREADY
saved in data/cache/sr/ -- the pages a prior crawl fetched.

No network, no SR search, no cf_clearance cookie: it re-parses what's on disk and
matches players to the DB by name (disambiguating same-name players by birth
year). This is the fast alternative to `backfill_sr`, whose per-player live SR
*search* is what makes that one slow even when the pages are cached.

Run it after a TEAM_MAP re-ingest (python run_full.py --skip-puzzles), which
resets bio to the nflverse baseline:

    python -m data.etl.backfill_from_cache --sport NFL --dry-run   # preview
    python -m data.etl.backfill_from_cache --sport NFL            # write bio
    python -m data.etl.backfill_from_cache --sport NFL --rebuild-teams --rebuild-stats
    python -m data.etl.backfill_from_cache --sport NBA --rebuild-teams --rebuild-stats

Bio: only EMPTY fields are filled; existing values are never overwritten.
--rebuild-teams overwrites the teams column with the page's franchises (every team
+ mid-season trades + seasons newer than the bulk). NFL: PFR /teams hrefs; NBA:
bbref team_name_abbr.
--rebuild-stats refreshes career stats from the page so seasons newer than the
frozen bulk count. NFL totals (yds/TDs) ONLY raise -- never lower. NBA averages
(pts/reb/ast) overwrite ONLY when the page shows MORE games than stored, so a stale
or mis-parsed page can't regress a good average. Both NFL & NBA.
"""

import argparse
import json
import logging
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, ROOT)

from data.etl.backfill_sr import is_empty, CACHE_DIR
from data.etl.cache_index import refresh as refresh_cache_index, by_name, _norm
from data.etl.load import rebuild_categories
from data.etl.schema import get_conn, migrate

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("backfill_cache")

DB_PATH = os.path.join(ROOT, "data", "nfl.db")
DEFAULT_FIELDS = ("birth_state,birth_city,draft_year,draft_round,draft_pick,"
                  "position,college,height_inches,weight_lbs")


# NFL & NBA are the sports with a cached-page teams/stats rebuild. The actual
# parsing now lives in cache_index (NFL keeps pass_td separate from rush+rec TD so
# a QB's total isn't undercounted; NBA teams come off per_game_stats, no unmapped
# junk) -- see cache_index.INDEX_CFG / _derive_stats.
REBUILD_SPORTS = {"NFL", "NBA"}


def build_cache_index(sport="NFL", with_teams=False, with_stats=False):
    """{norm_name: [(meta, birth_year, teams, stats), ...]} for `sport`, served from
    the PERSISTENT cache index (data/cache/sr_index.json): only pages that are new
    or changed since last time are parsed, the rest are reused. teams/stats are
    filled only when --rebuild-* asked for them. Drop-in for the old full re-scan."""
    return by_name(refresh_cache_index(), sport, with_teams=with_teams, with_stats=with_stats)


def _pick(cands, birth_year):
    """Among same-name cache hits, prefer the one whose birth year matches the DB.
    Returns (meta, teams, stats)."""
    if len(cands) == 1 or not birth_year:
        c = cands[0]
        return c[0], c[2], c[3]
    for meta, by, teams, stats in cands:
        if by and int(by) == int(birth_year):
            return meta, teams, stats
    c = cands[0]
    return c[0], c[2], c[3]


def main():
    ap = argparse.ArgumentParser(description="Fill bio (and optionally rebuild teams) "
                                             "from already-cached SR pages -- no network")
    ap.add_argument("--sport", default="NFL")
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--fields", default=DEFAULT_FIELDS)
    ap.add_argument("--rebuild-teams", action="store_true",
                    help="overwrite the teams column with the page's franchises "
                         "(fixes wrong/relocated teams + mid-trades; NFL & NBA)")
    ap.add_argument("--rebuild-stats", action="store_true",
                    help="refresh career stats from the page -- fills seasons newer than the "
                         "frozen bulk. NFL totals only ever raise; NBA averages overwrite only "
                         "when the page has MORE games (proof it's fresher). NFL & NBA.")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    fields = [f.strip() for f in args.fields.split(",") if f.strip()]
    rebuild_teams = args.rebuild_teams and args.sport in REBUILD_SPORTS
    rebuild_stats = args.rebuild_stats and args.sport in REBUILD_SPORTS

    if not os.path.isdir(CACHE_DIR):
        sys.exit(f"no SR cache at {CACHE_DIR} -- nothing to read")

    idx = build_cache_index(sport=args.sport, with_teams=rebuild_teams, with_stats=rebuild_stats)

    migrate(args.db)
    conn = get_conn(args.db)
    rows = conn.execute("SELECT * FROM players WHERE sport=?", (args.sport,)).fetchall()
    matched = filled = reteamed = restatted = 0
    for row in rows:
        cands = idx.get(_norm(row["name"]))
        if not cands:
            continue
        matched += 1
        meta, teams, stats = _pick(cands, row["birth_year"])
        updates = {f: meta[f] for f in fields
                   if f in meta and meta[f] not in (None, "", 0) and is_empty(row[f], f)}
        # standardized teams overwrite -- only when it actually changes something
        if rebuild_teams and teams and json.dumps(teams) != (row["teams"] or ""):
            updates["teams"] = json.dumps(teams)
        # stats refresh. NFL totals: only-raise each (never regress). NBA averages
        # can't only-raise, so overwrite ALL four only when the page shows MORE games
        # -- proof it's a fuller/fresher career line than the frozen bulk.
        if rebuild_stats and stats:
            if args.sport == "NBA":
                if (stats.get("nba_games") or 0) > (row["nba_games"] or 0):
                    updates.update(stats)
            else:
                for col, val in stats.items():
                    if val is not None and val > (row[col] or 0):
                        updates[col] = val
        if updates:
            shown = {k: (json.loads(v) if k == "teams" else v) for k, v in updates.items()}
            log.info(f"  {row['name']}: {shown}")
            if not args.dry_run:
                sets = ", ".join(f"{k}=?" for k in updates)
                conn.execute(f"UPDATE players SET {sets} WHERE id=?", [*updates.values(), row["id"]])
                filled += 1
                if "teams" in updates:
                    reteamed += 1
                if any(k in updates for k in ("pass_yds", "rush_yds", "rec_yds", "total_tds",
                                              "nba_points", "nba_rebounds", "nba_assists", "nba_games")):
                    restatted += 1

    if not args.dry_run:
        conn.commit()
    log.info(f"matched {matched} of {len(rows)} {args.sport} players; updated {filled}"
             + (f" ({reteamed} teams rebuilt)" if rebuild_teams else "")
             + (f" ({restatted} stat lines raised)" if rebuild_stats else "")
             + (" (dry run -- nothing written)" if args.dry_run else ""))
    if filled and not args.dry_run:
        rebuild_categories(conn, args.sport)
        log.info("categories rebuilt")
    conn.close()


if __name__ == "__main__":
    main()

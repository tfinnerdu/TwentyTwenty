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
    python -m data.etl.backfill_from_cache --sport NFL            # write

Only EMPTY fields are filled; existing values are never overwritten. Teams are
NOT touched here -- those come from the (fixed) provider map at ingest.
"""

import argparse
import glob
import json
import logging
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, ROOT)

from data.etl.backfill_sr import _soup, parse_meta, is_empty, CACHE_DIR
from data.etl.load import rebuild_categories
from data.etl.schema import get_conn, migrate
from data.etl.teams import nfl_teams_from_page

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("backfill_cache")

DB_PATH = os.path.join(ROOT, "data", "nfl.db")
DEFAULT_FIELDS = ("birth_state,birth_city,draft_year,draft_round,draft_pick,"
                  "position,college,height_inches,weight_lbs")


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def build_cache_index(with_teams=False):
    """Parse every cached SR page -> {norm_name: [(meta, birth_year, teams), ...]}.
    teams (canonical franchises off the page's season tables) only parsed when
    with_teams, since it's only needed for --rebuild-teams."""
    files = glob.glob(os.path.join(CACHE_DIR, "*.html"))
    log.info(f"scanning {len(files)} cached pages in {CACHE_DIR}")
    idx, parsed = {}, 0
    for fn in files:
        try:
            soup = _soup(open(fn, encoding="utf-8", errors="ignore").read())
        except Exception:
            continue
        if not soup.select_one("div#meta"):     # not a player page (search/index/etc.)
            continue
        h = soup.select_one("div#meta h1")
        name = h.get_text(strip=True) if h else None
        if not name:
            continue
        meta = parse_meta(soup)
        teams = nfl_teams_from_page(soup) if with_teams else []
        if not meta and not teams:
            continue
        idx.setdefault(_norm(name), []).append((meta, meta.get("birth_year"), teams))
        parsed += 1
    log.info(f"parsed {parsed} player pages -> {len(idx)} distinct names")
    return idx


def _pick(cands, birth_year):
    """Among same-name cache hits, prefer the one whose birth year matches the DB.
    Returns (meta, teams)."""
    if len(cands) == 1 or not birth_year:
        return cands[0][0], cands[0][2]
    for meta, by, teams in cands:
        if by and int(by) == int(birth_year):
            return meta, teams
    return cands[0][0], cands[0][2]


def main():
    ap = argparse.ArgumentParser(description="Fill bio (and optionally rebuild teams) "
                                             "from already-cached SR pages -- no network")
    ap.add_argument("--sport", default="NFL")
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--fields", default=DEFAULT_FIELDS)
    ap.add_argument("--rebuild-teams", action="store_true",
                    help="overwrite the teams column with the page's standardized "
                         "franchises (fixes wrong/relocated teams; NFL only)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    fields = [f.strip() for f in args.fields.split(",") if f.strip()]
    rebuild_teams = args.rebuild_teams and args.sport == "NFL"

    if not os.path.isdir(CACHE_DIR):
        sys.exit(f"no SR cache at {CACHE_DIR} -- nothing to read")

    idx = build_cache_index(with_teams=rebuild_teams)

    migrate(args.db)
    conn = get_conn(args.db)
    rows = conn.execute("SELECT * FROM players WHERE sport=?", (args.sport,)).fetchall()
    matched = filled = reteamed = 0
    for row in rows:
        cands = idx.get(_norm(row["name"]))
        if not cands:
            continue
        matched += 1
        meta, teams = _pick(cands, row["birth_year"])
        updates = {f: meta[f] for f in fields
                   if f in meta and meta[f] not in (None, "", 0) and is_empty(row[f], f)}
        # standardized teams overwrite -- only when it actually changes something
        if rebuild_teams and teams and json.dumps(teams) != (row["teams"] or ""):
            updates["teams"] = json.dumps(teams)
        if updates:
            shown = {k: (json.loads(v) if k == "teams" else v) for k, v in updates.items()}
            log.info(f"  {row['name']}: {shown}")
            if not args.dry_run:
                sets = ", ".join(f"{k}=?" for k in updates)
                conn.execute(f"UPDATE players SET {sets} WHERE id=?", [*updates.values(), row["id"]])
                filled += 1
                if "teams" in updates:
                    reteamed += 1

    if not args.dry_run:
        conn.commit()
    log.info(f"matched {matched} of {len(rows)} {args.sport} players; updated {filled}"
             + (f" ({reteamed} teams rebuilt)" if rebuild_teams else "")
             + (" (dry run -- nothing written)" if args.dry_run else ""))
    if filled and not args.dry_run:
        rebuild_categories(conn, args.sport)
        log.info("categories rebuilt")
    conn.close()


if __name__ == "__main__":
    main()

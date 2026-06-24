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
from data.etl.sr_history import _career_stats, _find_table

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("backfill_cache")

DB_PATH = os.path.join(ROOT, "data", "nfl.db")
DEFAULT_FIELDS = ("birth_state,birth_city,draft_year,draft_round,draft_pick,"
                  "position,college,height_inches,weight_lbs")


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


# PFR career-stat parse for NFL. Mirrors the db_all passing / rush-rec split but
# ALSO takes passing TDs -- db_all's cfg sums only rush+rec TDs, which is fine for
# its rough totals but undercounts a QB badly. total_tds = pass + rush + rec TDs.
NFL_STAT_CFG = {"stat_tables": [
    ("passing", {"pass_yds": "pass_yds", "pass_td": "pass_td"}),
    ("rushing_and_receiving", {"rush_yds": "rush_yds", "rec_yds": "rec_yds",
                               "rush_receive_td": "rr_td"}),
]}


def _nfl_stats_from_page(soup):
    """Career {pass_yds, rush_yds, rec_yds, total_tds} off the PFR page, or {}.
    total_tds sums passing + rushing + receiving TDs so QBs aren't undercounted."""
    raw = _career_stats(soup, NFL_STAT_CFG)
    if not raw:
        return {}
    out = {c: int(raw[c]) for c in ("pass_yds", "rush_yds", "rec_yds") if c in raw}
    if "pass_td" in raw or "rr_td" in raw:
        out["total_tds"] = int(raw.get("pass_td", 0)) + int(raw.get("rr_td", 0))
    return out


# bbref career-stat parse for NBA: the per_game_stats footer carries career averages
# + total games (validated on Wembanyama: 23.4/11.0/3.5 over 181 G). No stat_league
# (the footer row is "N Yrs"/"Career", not league-tagged).
NBA_STAT_CFG = {"stat_table": "per_game_stats",
                "stat_cols": {"pts_per_g": "nba_points", "trb_per_g": "nba_rebounds",
                              "ast_per_g": "nba_assists", "games": "nba_games"}}


def _nba_stats_from_page(soup):
    """Career {nba_points, nba_rebounds, nba_assists, nba_games} off the bbref page."""
    raw = _career_stats(soup, NBA_STAT_CFG)
    return {k: raw[k] for k in ("nba_points", "nba_rebounds", "nba_assists", "nba_games") if k in raw}


def _nba_teams_from_page(soup):
    """Ordered, de-duped NBA franchises off the bbref per_game_stats rows
    (team_name_abbr -> NBAProvider.TEAM_MAP). Skips the multi-team TOT/2TM rows."""
    from data.etl.providers.csv_season import NBAProvider
    tmap = NBAProvider.TEAM_MAP
    table = _find_table(soup, "per_game_stats")
    teams = []
    if not table:
        return teams
    for row in table.select("tbody tr"):
        cell = (row.select_one("[data-stat='team_name_abbr']")
                or row.select_one("[data-stat='team_id']") or row.select_one("[data-stat='team']"))
        abbr = cell.get_text(strip=True) if cell else ""
        if not abbr or abbr.upper() in ("TOT", "2TM", "3TM", "4TM", ""):
            continue
        nick = tmap.get(abbr.upper())
        if nick and nick not in teams:
            teams.append(nick)
    return teams


# Sports with a cached-page teams/stats refresh, and how to parse each.
_PAGE_TEAMS = {"NFL": nfl_teams_from_page, "NBA": _nba_teams_from_page}
_PAGE_STATS = {"NFL": _nfl_stats_from_page, "NBA": _nba_stats_from_page}
REBUILD_SPORTS = set(_PAGE_TEAMS)            # NFL, NBA


def build_cache_index(sport="NFL", with_teams=False, with_stats=False):
    """Parse every cached SR page -> {norm_name: [(meta, birth_year, teams, stats), ...]}.
    teams (canonical franchises off the page's tables) and stats (career line) are
    parsed with the SPORT's parser only when requested -- solely for --rebuild-*."""
    teams_fn = _PAGE_TEAMS.get(sport)
    stats_fn = _PAGE_STATS.get(sport)
    files = glob.glob(os.path.join(CACHE_DIR, "*.html"))
    log.info(f"scanning {len(files)} cached pages in {CACHE_DIR}")
    idx, parsed = {}, 0
    for i, fn in enumerate(files, 1):
        if i % 5000 == 0 or i == len(files):       # progress -- the scan is silent otherwise
            log.info(f"  scanned {i}/{len(files)} pages ({parsed} player pages so far)")
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
        teams = teams_fn(soup) if (with_teams and teams_fn) else []
        stats = stats_fn(soup) if (with_stats and stats_fn) else {}
        if not meta and not teams and not stats:
            continue
        idx.setdefault(_norm(name), []).append((meta, meta.get("birth_year"), teams, stats))
        parsed += 1
    log.info(f"parsed {parsed} player pages -> {len(idx)} distinct names")
    return idx


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

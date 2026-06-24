#!/usr/bin/env python3
"""
data/etl/nfl_debuts.py
======================
Add players who DEBUTED in a given NFL season from Pro-Football-Reference's
per-year debuts page (/years/<YEAR>/debuts.htm) -- the ENUMERATION source for
rookies nflverse hasn't published yet (its seasonal data lags the live season,
and the cache-missing crawl can only enrich players already in the DB).

The debuts page lists every player who debuted that year with position + career
stats, but NO team/college. So this CREATES the rows (name, pos, debut year,
stats); their teams + bio are then filled by the existing enrich pipeline:

    python -m data.etl.nfl_debuts --year 2025                      # add 2025 rookies
    python -m data.etl.backfill_sr --sport NFL --cache-missing     # fetch their PFR pages
    python -m data.etl.backfill_from_cache --sport NFL --rebuild-teams --rebuild-stats

One page fetch, but PFR is Cloudflare-gated -> needs SR_CF_CLEARANCE in .env.
"""

import argparse
import logging
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, ROOT)

from data.etl.backfill_sr import make_session, fetch_raw, _soup, _cell, _load_env_file
from data.etl.sr_history import _sr_id_from_href, _find_table
from data.etl.providers.base import upsert_players
from data.etl.load import derive_fields, rebuild_categories
from data.etl.schema import get_conn, migrate

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("nfl_debuts")

DB_PATH = os.path.join(ROOT, "data", "nfl.db")


def _i(s):
    s = (s or "").replace(",", "").strip()
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return 0


def _f(s):
    s = (s or "").replace(",", "").strip()
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def parse_debuts(soup, year):
    """Player rows from /years/<year>/debuts.htm -> minimal NFL records (no team/
    college -- those aren't on the page; the enrich pipeline fills them). total_tds
    sums passing+rushing+receiving TDs straight off the row."""
    table = _find_table(soup, "debuts")
    if not table:
        return []
    out = []
    for tr in table.select("tbody tr"):
        a = tr.select_one("[data-stat='player'] a[href*='/players/']")
        if not a:
            continue
        srid = _sr_id_from_href(a.get("href", ""))
        name = a.get_text(strip=True)
        if not srid or not name:
            continue
        ymax = _i(_cell(tr, "year_max")) or year
        total_tds = _i(_cell(tr, "pass_td")) + _i(_cell(tr, "rush_td")) + _i(_cell(tr, "rec_td"))
        out.append({
            "sport": "NFL",
            "sr_id": f"nfl_{srid}",
            "name": name,
            "position": _cell(tr, "pos"),
            "debut_year": year,
            "final_year": ymax,
            "active_decades": sorted({f"{(y // 10) * 10}s" for y in range(year, ymax + 1)}),
            "pass_yds": _i(_cell(tr, "pass_yds")),
            "rush_yds": _i(_cell(tr, "rush_yds")),
            "rec_yds": _i(_cell(tr, "rec_yds")),
            "total_tds": total_tds,
            "sacks": _f(_cell(tr, "sacks")),
            "interceptions": _i(_cell(tr, "def_int")),
        })
    return out


def crawl(year, db, limit, delay, dry_run, cf_clearance, user_agent):
    session = make_session("NFL", cf_clearance, user_agent)
    url = f"https://www.pro-football-reference.com/years/{year}/debuts.htm"
    log.info(f"fetching {url}")
    text, _ = fetch_raw(session, url, delay)
    if not text:
        log.error("could not fetch the debuts page (cookie expired? set SR_CF_CLEARANCE in .env)")
        return
    recs = parse_debuts(_soup(text), year)
    log.info(f"parsed {len(recs)} players who debuted in {year}")

    migrate(db)
    conn = get_conn(db)
    have = {r[0] for r in conn.execute("SELECT sr_id FROM players WHERE sport='NFL'")}
    new = [r for r in recs if r["sr_id"] not in have]
    log.info(f"{len(new)} are new to the DB ({len(recs) - len(new)} already present)")
    if limit:
        new = new[:limit]
    if dry_run:
        for r in new[:25]:
            log.info(f"    would add: {r['name']:24} {r['position']:3} tds={r['total_tds']}")
        log.info(f"(dry run -- {len(new)} would be added, nothing written)")
        conn.close()
        return

    cur = conn.cursor()
    cur.execute("INSERT INTO etl_runs (sport, source, run_at, status, notes) "
                "VALUES ('NFL', 'nfl_debuts', datetime('now'), 'running', ?)",
                (f"{year} debuts",))
    conn.commit()
    added, updated = upsert_players(conn, new, "nfl_debuts", cur.lastrowid)
    derive_fields(conn)
    rebuild_categories(conn, "NFL")
    conn.close()
    log.info(f"added {added}, updated {updated} NFL players from {year} debuts. "
             f"Next: backfill_sr --sport NFL --cache-missing, then "
             f"backfill_from_cache --sport NFL --rebuild-teams --rebuild-stats")


def main():
    ap = argparse.ArgumentParser(description="Add a season's debuting NFL players from "
                                             "PFR's /years/<year>/debuts.htm (no network for "
                                             "team/bio -- the enrich pipeline does that)")
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--limit", type=int, default=None, help="cap players added (validation)")
    ap.add_argument("--delay", type=float, default=5.0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--cf-clearance")
    ap.add_argument("--user-agent")
    args = ap.parse_args()
    _load_env_file(os.path.join(ROOT, ".env"))
    cf = args.cf_clearance or os.environ.get("SR_CF_CLEARANCE")
    ua = args.user_agent or os.environ.get("SR_CF_UA")
    crawl(args.year, args.db, args.limit, args.delay, args.dry_run, cf, ua)


if __name__ == "__main__":
    main()

"""
data/etl/run_etl.py
-------------------
Master ETL orchestrator. Run this from your machine (not a hosted server).

Usage:
    python data/etl/run_etl.py --sport NFL
    python data/etl/run_etl.py --sport NBA
    python data/etl/run_etl.py --sport ALL          # NFL + NBA + MLB + NHL
    python data/etl/run_etl.py --sport NFL --from-cache
    python data/etl/run_etl.py --sport NFL --limit 20   # quick test

After this completes:
  - data/nfl.db is updated with fresh player data + timestamps
  - data/cache/<sport>_raw.json is saved for future --from-cache runs
  - etl_runs table has a new row you can query for the "Stats as of" date
  - Run python generate_puzzles.py --days 7 --force afterward to refresh puzzles

Typical refresh cadence: monthly for active sports, off-season once for retired players.
"""

import argparse
import logging
import os
import subprocess
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from data.etl.schema import get_conn, migrate, get_data_vintage
from data.etl.load import derive_fields, rebuild_categories, record_load_run

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "nfl.db")

SPORT_SCRAPERS = {
    "NFL":   "data/etl/scrape_nfl.py",
    "NBA":   "data/etl/scrape_nba.py",
    "MLB":   "data/etl/scrape_mlb.py",
    "NHL":   "data/etl/scrape_nhl.py",
    "WNBA":  "data/etl/scrape_wnba.py",
    "NCAAF": "data/etl/scrape_ncaaf.py",
    # NCAAB and NCAAW use the same scraper with --gender flag
    # Run manually: python data/etl/scrape_ncaab.py --gender mens
    #               python data/etl/scrape_ncaab.py --gender womens
}


def run_scraper(sport: str, args: argparse.Namespace) -> bool:
    scraper = SPORT_SCRAPERS.get(sport)
    if not scraper:
        log.warning(f"No scraper for {sport} yet")
        return False

    root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    script = os.path.join(root, scraper)

    cmd = [sys.executable, script, "--db", args.db]
    if args.from_cache: cmd.append("--from-cache")
    if args.refresh:    cmd.append("--refresh")
    if args.delay:      cmd.extend(["--delay", str(args.delay)])
    if args.limit:      cmd.extend(["--limit", str(args.limit)])

    log.info(f"--- Running {sport} scraper ---")
    result = subprocess.run(cmd, cwd=root)
    return result.returncode == 0


def main():
    parser = argparse.ArgumentParser(description="20/20 ETL orchestrator")
    parser.add_argument("--sport",      default="NFL",
                        help="NFL, NBA, or ALL (default: NFL)")
    parser.add_argument("--from-cache", action="store_true",
                        help="Skip scraping, load from existing cache files")
    parser.add_argument("--refresh",    action="store_true",
                        help="Force re-scrape even if cache exists")
    parser.add_argument("--delay",      type=float, default=1.0,
                        help="Seconds between HTTP requests (default: 1.0)")
    parser.add_argument("--limit",      type=int, default=None,
                        help="Max players per sport (for testing)")
    parser.add_argument("--db",         default=DB_PATH,
                        help="Database path")
    parser.add_argument("--skip-load",  action="store_true",
                        help="Skip the load/rebuild step (just scrape)")
    args = parser.parse_args()

    log.info(f"=== 20/20 ETL Orchestrator ===")
    log.info(f"Sport: {args.sport}  |  DB: {args.db}  |  Delay: {args.delay}s")
    if args.limit:
        log.info(f"TEST MODE: limit={args.limit} players per sport")

    # Ensure schema
    migrate(args.db)

    # Determine which sports to run
    sports = list(SPORT_SCRAPERS.keys()) if args.sport == "ALL" else [args.sport.upper()]

    # Run scrapers
    results = {}
    for sport in sports:
        ok = run_scraper(sport, args)
        results[sport] = ok
        if not ok:
            log.error(f"{sport} scraper failed")

    # Run load/rebuild pass
    if not args.skip_load:
        log.info("--- Rebuilding categories and memberships ---")
        conn = get_conn(args.db)
        derive_fields(conn)
        rebuild_categories(conn, "ALL")
        record_load_run(conn, args.sport, f"orchestrated run sports={sports}")
        conn.close()

    # Print vintage summary
    conn = get_conn(args.db)
    vintage = get_data_vintage(conn, "ALL")
    conn.close()

    log.info("\n=== Data vintage after ETL ===")
    for row in vintage:
        log.info(f"  {row['sport']:6s}  {row['run_at'][:10]}  ({row['total']} players)")

    # Summary
    log.info("\n=== Summary ===")
    for sport, ok in results.items():
        log.info(f"  {sport}: {'OK' if ok else 'FAILED'}")

    if not args.skip_load:
        log.info("\nNext steps:")
        log.info("  python generate_puzzles.py --days 7 --force")
        log.info("  (restart the Flask API to pick up new DB)")


if __name__ == "__main__":
    main()

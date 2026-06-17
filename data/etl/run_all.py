#!/usr/bin/env python3
"""
data/etl/run_all.py
-------------------
Catch-all ETL runner. Scrapes and loads every supported sport in one command.

This is the script you run for a full data refresh. It handles the
dual-gender NCAAB edge case internally so you don't have to remember
to run scrape_ncaab.py twice.

Usage:
    # Full refresh (all sports, walk away for ~90 minutes)
    python data/etl/run_all.py

    # Test run -- 20 players per sport (~5 minutes)
    python data/etl/run_all.py --limit 20

    # Re-run from cached data only (no HTTP, instant)
    python data/etl/run_all.py --from-cache

    # Specific sports only
    python data/etl/run_all.py --sports NFL NBA WNBA

    # Force re-scrape (refresh cache files)
    python data/etl/run_all.py --refresh

    # Be extra polite to the servers
    python data/etl/run_all.py --delay 2.0

After completion:
    - data/nfl.db updated with fresh player data and timestamps
    - data/cache/*_raw.json files saved for future --from-cache runs
    - etl_runs table has new rows for each sport
    - Categories and memberships fully rebuilt
    - Run: python generate_puzzles.py --days 7 --force

Run from your machine (residential IP). Sports-Reference blocks cloud/datacenter IPs.
Typical full-refresh time: ~90 minutes at 1 req/sec across all sports.

Sport                 Site                              ~Players  ~Minutes
----                  ----                              --------  --------
NFL                   pro-football-reference.com          500       10
NBA                   basketball-reference.com            400        7
MLB                   baseball-reference.com              600       12
NHL                   hockey-reference.com                500       10
WNBA                  basketball-reference.com/wnba/      200        4
NCAAF                 sports-reference.com/cfb/           400        8
NCAAB (mens)          sports-reference.com/cbb/           300        6
NCAAW (womens)        sports-reference.com/cbb/           200        4
----                  ----                              --------  --------
TOTAL                                                    3100       61
(plus category rebuild ~1 min, plus safety sleep between sports)
"""

import argparse
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, BASE_DIR)

from data.etl.schema import get_conn, migrate, get_data_vintage
from data.etl.load import derive_fields, rebuild_categories, record_load_run

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "nfl.db")

# Every sport, in run order. Each entry:
#   (sport_label, script_path, extra_args)
# extra_args is a list of additional CLI flags to pass to that scraper.
# (sport, script, extra_args, delay_override)
# sports-reference.com enforces a 20 req/min limit -- use 3.5s for those scrapers.
# basketball-reference and baseball-reference are more lenient -- 1s is fine.
# Sports-Reference rate limit: 20 requests/minute across ALL their domains.
# 4.0s per request = 15 req/min -- safely under the limit.
# If you get 403s, your session is jailed for up to 24h. Just wait and retry.
ALL_SPORTS = [
    ("NFL",   "data/etl/scrape_nfl.py",   [],                      4.0),
    ("NBA",   "data/etl/scrape_nba.py",   [],                      4.0),
    ("MLB",   "data/etl/scrape_mlb.py",   [],                      4.0),
    ("NHL",   "data/etl/scrape_nhl.py",   [],                      4.0),
    ("WNBA",  "data/etl/scrape_wnba.py",  [],                      4.0),
    ("NCAAF", "data/etl/scrape_ncaaf.py", [],                      4.0),
    ("NCAAB", "data/etl/scrape_ncaab.py", ["--gender", "mens"],   4.0),
    ("NCAAW", "data/etl/scrape_ncaab.py", ["--gender", "womens"], 4.0),
]

# Map sport label -> script for single-sport lookups
SPORT_SCRIPT_MAP = {label: (script, extra, delay) for label, script, extra, delay in ALL_SPORTS}


def run_scraper(
    sport: str,
    script: str,
    extra_args: list,
    args: argparse.Namespace,
    between_delay: float = 5.0,
    delay_override: float = None,
) -> bool:
    """Run a single sport scraper as a subprocess. Returns True on success."""
    script_path = os.path.join(BASE_DIR, script)
    if not os.path.exists(script_path):
        log.error(f"Script not found: {script_path}")
        return False

    # Use per-sport delay override unless user explicitly set --delay
    effective_delay = delay_override if (delay_override and args.delay == 4.0) else args.delay

    cmd = [sys.executable, script_path, "--db", args.db] + extra_args

    if args.from_cache:
        cmd.append("--from-cache")
    if args.refresh:
        cmd.append("--refresh")
    cmd.extend(["--delay", str(effective_delay)])
    if args.limit:
        cmd.extend(["--limit", str(args.limit)])

    log.info(f"{'='*56}")
    log.info(f"  {sport}")
    log.info(f"{'='*56}")

    result = subprocess.run(cmd, cwd=BASE_DIR)
    ok = result.returncode == 0

    if not ok:
        log.error(f"{sport} scraper exited with code {result.returncode}")
    else:
        log.info(f"{sport} complete")

    # Breathe between scrapers -- polite to the servers, avoids
    # triggering rate limits across domain boundaries
    if between_delay > 0:
        log.info(f"Sleeping {between_delay}s between scrapers...")
        time.sleep(between_delay)

    return ok


def print_summary(results: dict, start_time: float):
    elapsed = time.time() - start_time
    mins, secs = divmod(int(elapsed), 60)

    log.info("")
    log.info("=" * 56)
    log.info("  ETL COMPLETE")
    log.info("=" * 56)
    log.info(f"  Total time: {mins}m {secs}s")
    log.info("")
    for sport, ok in results.items():
        status = "OK   " if ok else "FAIL "
        log.info(f"  {status}  {sport}")
    log.info("")


def print_vintage(db_path: str):
    conn = get_conn(db_path)
    rows = get_data_vintage(conn, "ALL")
    conn.close()
    if rows:
        log.info("Data vintage:")
        for row in rows:
            log.info(f"  {row['sport']:8s}  {row['run_at'][:10]}  ({row['total']} players)")


def main():
    parser = argparse.ArgumentParser(
        description="Full ETL refresh for all 20/20 sports",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Run from")[0].strip(),
    )
    parser.add_argument(
        "--sports", nargs="+",
        metavar="SPORT",
        default=None,
        help=(
            "Sports to run (default: all). "
            "Options: NFL NBA MLB NHL WNBA NCAAF NCAAB NCAAW. "
            "Example: --sports NFL NBA WNBA"
        ),
    )
    parser.add_argument(
        "--from-cache", action="store_true",
        help="Skip all HTTP requests, load from existing cache/*.json files",
    )
    parser.add_argument(
        "--refresh", action="store_true",
        help="Force re-scrape even if cache files already exist",
    )
    parser.add_argument(
        "--delay", type=float, default=4.0,
        help="Seconds between HTTP requests per scraper (default: 4.0, safe under 20 req/min)",
    )
    parser.add_argument(
        "--between-delay", type=float, default=5.0,
        help="Extra seconds to sleep between sport scrapers (default: 5.0)",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Max players per sport -- use for testing (e.g. --limit 20)",
    )
    parser.add_argument(
        "--db", default=DB_PATH,
        help="Database path",
    )
    parser.add_argument(
        "--skip-load", action="store_true",
        help="Skip the category rebuild step after scraping",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would run without executing anything",
    )
    args = parser.parse_args()

    # Resolve sport list
    if args.sports:
        requested = [s.upper() for s in args.sports]
        invalid = [s for s in requested if s not in SPORT_SCRIPT_MAP]
        if invalid:
            log.error(f"Unknown sports: {invalid}. Valid: {list(SPORT_SCRIPT_MAP.keys())}")
            sys.exit(1)
        run_list = [(label, script, extra, delay) for label, script, extra, delay in ALL_SPORTS
                    if label in requested]
    else:
        run_list = ALL_SPORTS  # 4-tuples: (label, script, extra, delay)

    # Header
    log.info("=" * 56)
    log.info("  20/20 FULL ETL RUN")
    log.info("=" * 56)
    log.info(f"  Sports:       {[s[0] for s in run_list]}")
    log.info(f"  DB:           {args.db}")
    log.info(f"  Delay:        {args.delay}s per request")
    log.info(f"  Between:      {args.between_delay}s between sports")
    if args.limit:
        log.info(f"  TEST MODE:    {args.limit} players per sport")
    if args.from_cache:
        log.info("  MODE:         from-cache (no HTTP requests)")
    if args.dry_run:
        log.info("  DRY RUN:      showing plan only")
    log.info("")

    if args.dry_run:
        log.info("Would run:")
        for label, script, extra, delay in run_list:
            log.info(f"  {label:8s}  {script}  {extra}  (delay={delay}s)")
        log.info("\nWould then rebuild categories and memberships.")
        return

    # Ensure schema is current
    migrate(args.db)

    start = time.time()
    results = {}

    for label, script, extra, delay in run_list:
        ok = run_scraper(label, script, extra, args, args.between_delay, delay_override=delay)
        results[label] = ok

    # Category rebuild
    if not args.skip_load:
        log.info("=" * 56)
        log.info("  REBUILDING CATEGORIES + MEMBERSHIPS")
        log.info("=" * 56)
        conn = get_conn(args.db)
        derive_fields(conn)
        rebuild_categories(conn, "ALL")
        sport_list_str = ",".join(s[0] for s in run_list)
        record_load_run(conn, "ALL", f"run_all sports={sport_list_str}")
        conn.close()

    print_summary(results, start)
    print_vintage(args.db)

    failed = [s for s, ok in results.items() if not ok]
    if failed:
        log.warning(f"Failed sports: {failed}")
        log.info("Tip: re-run failed sports with --from-cache to reload from disk if cache was saved.")
    else:
        log.info("All sports OK.")

    log.info("")
    log.info("Next steps:")
    log.info("  python generate_puzzles.py --days 7 --force")
    log.info("  (restart Flask API to pick up new DB)")


if __name__ == "__main__":
    main()
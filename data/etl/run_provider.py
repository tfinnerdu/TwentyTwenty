"""
data/etl/run_provider.py
========================
Run a sanctioned-source provider end to end (fetch -> upsert -> categories).

This is the new, no-bot-block ingestion path that replaces scraping for the
sports where a clean openly-licensed source exists. Pick a provider by name;
each one maps its source into the shared `players` schema.

Usage:
    python -m data.etl.run_provider --provider lahman_mlb
    python -m data.etl.run_provider --provider lahman_mlb --source-dir ./csvs
    python -m data.etl.run_provider --provider lahman_mlb --limit 50 --skip-load

Afterwards:
    python generate_puzzles.py --sport MLB --days 7 --force
"""

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from data.etl.providers.base import run_pipeline
from data.etl.providers.lahman_mlb import LahmanMLBProvider

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "nfl.db")


def _make_lahman(args):
    return LahmanMLBProvider(data_dir=args.source_dir, refresh=args.refresh)


# name -> factory(args) -> Provider
PROVIDERS = {
    "lahman_mlb": _make_lahman,
}


def main():
    parser = argparse.ArgumentParser(description="Run a sanctioned-source data provider")
    parser.add_argument("--provider", required=True, choices=sorted(PROVIDERS),
                        help="Which provider to run")
    parser.add_argument("--db", default=DB_PATH, help="SQLite DB path")
    parser.add_argument("--source-dir", default=None,
                        help="Read source files from this dir instead of downloading")
    parser.add_argument("--refresh", action="store_true",
                        help="Re-download cached source files")
    parser.add_argument("--limit", type=int, default=None,
                        help="Cap players ingested (testing)")
    parser.add_argument("--skip-load", action="store_true",
                        help="Skip the derive/category rebuild step")
    args = parser.parse_args()

    provider = PROVIDERS[args.provider](args)
    log.info(f"=== 20/20 provider run: {args.provider} ({provider.sport}) ===")

    summary = run_pipeline(provider, args.db, skip_load=args.skip_load, limit=args.limit)

    log.info(f"Done: +{summary['added']} new, ~{summary['updated']} updated "
             f"({summary['sport']} via {summary['source']})")
    if not args.skip_load:
        log.info("Next: python generate_puzzles.py --sport %s --days 7 --force", provider.sport)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
build_demo_db.py
================
Build a ready-to-demo data/nfl.db with all seven sports, using the bundled
fixtures (MLB/NBA/NHL/WNBA/NFL) and curated datasets (NCAA F/B M+W). Runs
fully offline -- no network, no API keys -- so you can stand up the app and
demo it immediately.

    python build_demo_db.py
    python build_demo_db.py --real-mlb     # download the full Lahman MLB set
    FLASK_APP=api/app.py flask run -p 5902  # then start the app

For a real production data run, use the individual providers instead (see
data/etl/providers/README.md) -- this script just wires the bundled demo
data into one DB.
"""

import argparse
import logging
import os
import sys
import tempfile
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(__file__))
from data.etl.providers.base import run_pipeline
from data.etl.providers.lahman_mlb import LahmanMLBProvider
from data.etl.providers.csv_season import NBAProvider, WNBAProvider, NHLProvider, NFLProvider
from data.etl.providers.curated import CuratedProvider
from data.etl.providers._fixture_mlb import write_fixture as write_mlb
from data.etl.providers._fixture_nba import write_fixture as write_nba
from data.etl.providers._fixture_csv import write_fixture as write_csv
from data.etl.schema import get_conn
from generate_puzzles import generate_for_date

DEMO_SPORTS = ["ALL", "NFL", "NBA", "MLB", "NHL", "WNBA", "NCAAF", "NCAAB", "NCAAW"]

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("demo")

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "nfl.db")


def build(db_path: str, real_mlb: bool):
    tmp = tempfile.mkdtemp(prefix="2020_demo_")

    # MLB
    if real_mlb:
        mlb = LahmanMLBProvider()                       # downloads Lahman
    else:
        mlb_dir = os.path.join(tmp, "mlb"); write_mlb(mlb_dir)
        mlb = LahmanMLBProvider(data_dir=mlb_dir)       # 30-greats fixture

    # NBA / NHL / WNBA / NFL from bundled fixtures
    nba_dir = os.path.join(tmp, "nba"); write_nba(nba_dir)
    nhl_dir = os.path.join(tmp, "nhl"); write_csv("NHL", nhl_dir)
    wnba_dir = os.path.join(tmp, "wnba"); write_csv("WNBA", wnba_dir)
    nfl_dir = os.path.join(tmp, "nfl"); write_csv("NFL", nfl_dir)

    providers = [
        mlb,
        NBAProvider(source_dir=nba_dir),
        NHLProvider(source_dir=nhl_dir),
        WNBAProvider(source_dir=wnba_dir),
        NFLProvider(source_dir=nfl_dir),
        CuratedProvider("NCAAF"),
        CuratedProvider("NCAAB"),
        CuratedProvider("NCAAW"),
    ]
    for p in providers:
        run_pipeline(p, db_path)

    # summary
    conn = get_conn(db_path)
    c = conn.cursor()
    rows = c.execute("SELECT sport, COUNT(*) FROM players GROUP BY sport ORDER BY sport").fetchall()
    total = c.execute("SELECT COUNT(*) FROM players").fetchone()[0]
    cats = c.execute("SELECT COUNT(DISTINCT category_id) FROM player_categories").fetchone()[0]
    mem = c.execute("SELECT COUNT(*) FROM player_categories").fetchone()[0]
    conn.close()

    log.info("=" * 48)
    log.info(f"Demo DB ready: {db_path}")
    log.info(f"  {total} players  |  {cats} active categories  |  {mem} memberships")
    for s, n in rows:
        log.info(f"    {s:6s} {n}")
    log.info("=" * 48)
    return total


def gen_puzzles(days: int, length: int):
    """Pre-generate puzzles for every sport mode, at a length that fits the
    (small) demo data. difficulty=any keeps tight intersections in play."""
    log.info(f"Generating demo puzzles (length={length}, difficulty=any)...")
    for sport in DEMO_SPORTS:
        for i in range(days):
            d = date.today() + timedelta(days=i)
            generate_for_date(d, seed=int(d.strftime("%Y%m%d")) + len(sport), force=True,
                              sport_mode=sport, difficulty="any", chain_length=length)


def main():
    ap = argparse.ArgumentParser(description="Build a demo data/nfl.db (all 7 sports, offline)")
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--real-mlb", action="store_true", help="Download full Lahman MLB (needs network)")
    ap.add_argument("--length", type=int, default=10, help="Puzzle chain length for the demo (default 10)")
    ap.add_argument("--days", type=int, default=3, help="Days of puzzles to pre-generate")
    ap.add_argument("--skip-puzzles", action="store_true")
    args = ap.parse_args()

    build(args.db, args.real_mlb)
    if not args.skip_puzzles:
        gen_puzzles(args.days, args.length)
    log.info("=" * 48)
    log.info("Demo ready. Start the app:")
    log.info(f"  PUZZLE_CHAIN_LENGTH={args.length} PUZZLE_DIFFICULTY=any \\")
    log.info("    FLASK_APP=api/app.py .venv/bin/flask run --host 0.0.0.0 --port 5902")
    log.info("  open http://localhost:5902")


if __name__ == "__main__":
    main()


if __name__ == "__main__":
    main()

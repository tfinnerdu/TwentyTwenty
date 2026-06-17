#!/usr/bin/env python3
"""
run_full.py
===========
One command for a full, real-data run across all sports, into data/nfl.db.

Per sport it uses the best available source:
  MLB              Lahman / Chadwick Baseball Databank   (auto-downloads)
  NCAAF            CollegeFootballData API                (needs CFBD_API_KEY)
                   -- falls back to the curated set if no key
  NCAAB / NCAAW    curated datasets (data/etl/curated/)
  NBA/NHL/WNBA/NFL your season-stats CSVs via --<sport>-dir
                   -- falls back to the bundled demo fixtures if not supplied

Your CFBD key is read from the environment OR a .env file in the repo root
(KEY=VALUE lines), so:

    echo 'CFBD_API_KEY=xxxxxxxx' >> .env
    python run_full.py
    FLASK_APP=api/app.py .venv/bin/flask run -p 5902

Flags:
    --mlb-fixture        use the bundled MLB fixture instead of downloading Lahman
    --ncaaf-curated      use the curated NCAAF set even if a CFBD key is present
    --cfbd-start / --cfbd-end   CFBD season range (default 2005..2023)
    --nba-dir / --nhl-dir / --wnba-dir / --nfl-dir   real season-stats CSVs
    --length N           puzzle length (default 20)   --days N  puzzle days (default 7)
    --skip-puzzles
"""

import argparse
import logging
import os
import sys
import tempfile
from datetime import date, timedelta

BASE_DIR = os.path.dirname(__file__)
sys.path.insert(0, BASE_DIR)

from data.etl.providers.base import run_pipeline
from data.etl.providers.lahman_mlb import LahmanMLBProvider
from data.etl.providers.csv_season import NBAProvider, WNBAProvider, NHLProvider, NFLProvider, NCAAFProvider
from data.etl.providers.curated import CuratedProvider
from data.etl.providers._fixture_mlb import write_fixture as write_mlb
from data.etl.providers._fixture_nba import write_fixture as write_nba
from data.etl.providers._fixture_csv import write_fixture as write_csv
from data.etl.schema import get_conn
from generate_puzzles import generate_for_date

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("run_full")

DB_PATH = os.path.join(BASE_DIR, "data", "nfl.db")
PUZZLE_SPORTS = ["ALL", "NFL", "NBA", "MLB", "NHL", "WNBA", "NCAAF", "NCAAB", "NCAAW"]


def load_dotenv(path: str):
    """Minimal .env loader (no dependency). Doesn't override existing env vars."""
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def csv_provider(cls, source_dir, sport_label, tmp):
    """Real CSVs if given, else a bundled fixture (logged clearly)."""
    if source_dir:
        return cls(source_dir=source_dir)
    d = os.path.join(tmp, sport_label.lower())
    write_csv(sport_label, d)
    log.warning(f"  {sport_label}: no --{sport_label.lower()}-dir given -> using bundled DEMO fixture")
    return cls(source_dir=d)


def ncaaf_provider(args, tmp):
    key = os.environ.get("CFBD_API_KEY")
    if args.ncaaf_curated or not key:
        if not key and not args.ncaaf_curated:
            log.warning("  NCAAF: no CFBD_API_KEY found -> using curated set")
        return CuratedProvider("NCAAF")
    # Real CFBD: export to CSVs, then ingest with NCAAFProvider
    from data.etl.providers.cfbd_export import export
    out = os.path.join(tmp, "ncaaf_csv")
    log.info(f"  NCAAF: exporting CollegeFootballData {args.cfbd_start}-{args.cfbd_end} ...")
    export(args.cfbd_start, args.cfbd_end, out, key)
    return NCAAFProvider(source_dir=out)


def main():
    ap = argparse.ArgumentParser(description="Full real-data run across all sports")
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--mlb-fixture", action="store_true")
    ap.add_argument("--ncaaf-curated", action="store_true")
    ap.add_argument("--cfbd-start", type=int, default=2005)
    ap.add_argument("--cfbd-end", type=int, default=2023)
    ap.add_argument("--nba-dir"); ap.add_argument("--nhl-dir")
    ap.add_argument("--wnba-dir"); ap.add_argument("--nfl-dir")
    ap.add_argument("--length", type=int, default=20)
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--skip-puzzles", action="store_true")
    args = ap.parse_args()

    load_dotenv(os.path.join(BASE_DIR, ".env"))
    tmp = tempfile.mkdtemp(prefix="2020_full_")

    if args.mlb_fixture:
        d = os.path.join(tmp, "mlb"); write_mlb(d); mlb = LahmanMLBProvider(data_dir=d)
    else:
        log.info("  MLB: downloading Lahman / Baseball Databank ...")
        mlb = LahmanMLBProvider()

    nba_dir = args.nba_dir
    if not nba_dir:
        nba_dir = os.path.join(tmp, "nba"); write_nba(nba_dir)
        log.warning("  NBA: no --nba-dir given -> using bundled DEMO fixture")
    nba = NBAProvider(source_dir=nba_dir)

    providers = [
        mlb,
        nba,
        csv_provider(NHLProvider, args.nhl_dir, "NHL", tmp),
        csv_provider(WNBAProvider, args.wnba_dir, "WNBA", tmp),
        csv_provider(NFLProvider, args.nfl_dir, "NFL", tmp),
        ncaaf_provider(args, tmp),
        CuratedProvider("NCAAB"),
        CuratedProvider("NCAAW"),
    ]
    for p in providers:
        run_pipeline(p, args.db)

    conn = get_conn(args.db)
    rows = conn.execute("SELECT sport, COUNT(*) FROM players GROUP BY sport ORDER BY sport").fetchall()
    total = conn.execute("SELECT COUNT(*) FROM players").fetchone()[0]
    conn.close()
    log.info("=" * 48)
    log.info(f"Full run complete: {total} players -> {args.db}")
    for s, n in rows:
        log.info(f"    {s:6s} {n}")

    if not args.skip_puzzles:
        log.info(f"Generating puzzles (length={args.length}, {args.days} day(s))...")
        for sport in PUZZLE_SPORTS:
            for i in range(args.days):
                dt = date.today() + timedelta(days=i)
                try:
                    generate_for_date(dt, seed=int(dt.strftime("%Y%m%d")) + len(sport), force=True,
                                      sport_mode=sport, difficulty="any", chain_length=args.length)
                except Exception as e:
                    log.warning(f"  {sport} {dt}: {e}")
    log.info("=" * 48)
    log.info("Start the app:  FLASK_APP=api/app.py .venv/bin/flask run --host 0.0.0.0 --port 5902")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
run_full.py
===========
One command for a full, real-data run across all sports, into data/nfl.db.

Per sport it uses the best available source, and falls back to the bundled
demo fixture if a real pull fails (so a run always completes):

  MLB              Lahman / Chadwick Baseball Databank   (auto-downloads)
  NFL              nflverse / nfl_data_py
  NBA              stats.nba.com via nba_api
  NHL              official api-web.nhle.com
  WNBA             stats.wnba.com  (experimental)
  NCAAF            CollegeFootballData API  (needs CFBD_API_KEY; else curated)
  NCAAB / NCAAW    curated datasets

The CFBD key is read from the environment OR a .env file in the repo root.

    pip install -r requirements.txt -r requirements-etl.txt
    echo 'CFBD_API_KEY=xxxx' >> .env
    python run_full.py
    FLASK_APP=api/app.py .venv/bin/flask run -p 5902

Flags:
    --fixtures           use bundled fixtures for NBA/NHL/WNBA/NFL (skip real pulls)
    --mlb-fixture        use the MLB fixture instead of downloading Lahman
    --ncaaf-curated      use the curated NCAAF set even if a CFBD key is present
    --cfbd-start/-end    CFBD season range (default 2005..2023)
    --{nba,nhl,wnba,nfl}-dir   pre-exported season-stats CSVs to ingest directly
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
from data.etl.providers.nfl_export import export as export_nfl
from data.etl.providers.nba_export import export as export_nba
from data.etl.providers.nhl_export import export as export_nhl
from data.etl.providers.wnba_export import export as export_wnba
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


def resolve(sport, provider_cls, source_dir, exporter, fixture_fn, tmp, use_real, refresh=False):
    """--source-dir -> cached export -> live export -> bundled fixture, in order.
    Real exports are cached under data/cache/exports/<sport>/ and reused so the
    slow pulls (NHL especially) only happen once. --refresh forces a re-pull."""
    if source_dir:
        log.info(f"  {sport}: ingesting --source-dir {source_dir}")
        return provider_cls(source_dir=source_dir)

    cache = os.path.join(BASE_DIR, "data", "cache", "exports", sport.lower())
    cached_csv = os.path.join(cache, f"{sport.lower()}_players.csv")
    if use_real:
        if os.path.exists(cached_csv) and not refresh:
            log.info(f"  {sport}: reusing cached export ({cache}) -- use --refresh to re-pull")
            return provider_cls(source_dir=cache)
        try:
            log.info(f"  {sport}: pulling real data (this can take a while)...")
            np, ns = exporter(cache)
            log.info(f"  {sport}: exported {np} players / {ns} player-seasons")
            return provider_cls(source_dir=cache)
        except Exception as e:
            hint = "  [install: pip install -r requirements-etl.txt]" if isinstance(e, ImportError) else ""
            log.warning(f"  {sport}: real pull failed ({type(e).__name__}: {e}) -> demo fixture{hint}")
    out = os.path.join(tmp, sport.lower())
    fixture_fn(out)
    log.warning(f"  {sport}: using bundled DEMO fixture")
    return provider_cls(source_dir=out)


def ncaaf_provider(args, tmp):
    key = os.environ.get("CFBD_API_KEY")
    if args.ncaaf_curated or not key:
        if not key and not args.ncaaf_curated:
            log.warning("  NCAAF: no CFBD_API_KEY found -> curated set")
        return CuratedProvider("NCAAF")
    from data.etl.providers.cfbd_export import export as export_cfbd
    cache = os.path.join(BASE_DIR, "data", "cache", "exports", "ncaaf")
    if os.path.exists(os.path.join(cache, "ncaaf_players.csv")) and not args.refresh:
        log.info(f"  NCAAF: reusing cached CFBD export ({cache})")
        return NCAAFProvider(source_dir=cache)
    try:
        log.info(f"  NCAAF: exporting CollegeFootballData {args.cfbd_start}-{args.cfbd_end}...")
        export_cfbd(args.cfbd_start, args.cfbd_end, cache, key)
        return NCAAFProvider(source_dir=cache)
    except Exception as e:
        log.warning(f"  NCAAF: CFBD export failed ({e}) -> curated set")
        return CuratedProvider("NCAAF")


def generate_all_puzzles(db, length, days):
    """Generate puzzles for every mode, sizing each sport's chain to how many
    players it actually has (so a fixture-backed sport doesn't try -- and hang
    on -- a 20-link chain it can't fill)."""
    conn = get_conn(db)
    counts = {s: n for s, n in conn.execute("SELECT sport, COUNT(*) FROM players GROUP BY sport")}
    conn.close()
    total = sum(counts.values())
    log.info(f"Generating puzzles (up to length {length}, {days} day(s))...")
    for sport in PUZZLE_SPORTS:
        avail = total if sport == "ALL" else counts.get(sport, 0)
        L = min(length, max(6, avail * 2 // 3))   # leave the DFS some slack
        if avail < 8:
            log.warning(f"  {sport}: only {avail} players -> skipping puzzles")
            continue
        if L < length:
            log.info(f"  {sport}: {avail} players -> length {L}")
        for i in range(days):
            dt = date.today() + timedelta(days=i)
            try:
                generate_for_date(dt, seed=int(dt.strftime("%Y%m%d")) + len(sport), force=True,
                                  sport_mode=sport, difficulty="any", chain_length=L)
            except Exception as e:
                log.warning(f"  {sport} {dt}: {e}")


def main():
    ap = argparse.ArgumentParser(description="Full real-data run across all sports")
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--fixtures", action="store_true", help="Fixtures for NBA/NHL/WNBA/NFL")
    ap.add_argument("--mlb-fixture", action="store_true")
    ap.add_argument("--ncaaf-curated", action="store_true")
    ap.add_argument("--cfbd-start", type=int, default=2005)
    ap.add_argument("--cfbd-end", type=int, default=2023)
    ap.add_argument("--nba-dir"); ap.add_argument("--nhl-dir")
    ap.add_argument("--wnba-dir"); ap.add_argument("--nfl-dir")
    ap.add_argument("--length", type=int, default=20)
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--skip-puzzles", action="store_true")
    ap.add_argument("--keep-db", action="store_true",
                    help="Append to an existing DB instead of a clean rebuild")
    ap.add_argument("--refresh", action="store_true",
                    help="Force re-pull of cached exports (data/cache/exports/)")
    ap.add_argument("--puzzles-only", action="store_true",
                    help="Skip the data load; just (re)generate puzzles from the existing DB")
    args = ap.parse_args()

    load_dotenv(os.path.join(BASE_DIR, ".env"))
    if args.puzzles_only:
        generate_all_puzzles(args.db, args.length, args.days)
        log.info("Done. Start the app to play.")
        return
    if not args.keep_db:
        for f in (args.db, args.db + "-wal", args.db + "-shm"):
            if os.path.exists(f):
                os.remove(f)
        log.info("Cleared existing database for a clean rebuild")
    tmp = tempfile.mkdtemp(prefix="2020_full_")
    real = not args.fixtures

    if args.mlb_fixture:
        d = os.path.join(tmp, "mlb"); write_mlb(d); mlb = LahmanMLBProvider(data_dir=d)
    else:
        log.info("  MLB: downloading Lahman / Baseball Databank...")
        mlb = LahmanMLBProvider()

    providers = [
        mlb,
        resolve("NFL", NFLProvider, args.nfl_dir, lambda o: export_nfl(o),
                lambda o: write_csv("NFL", o), tmp, real, args.refresh),
        resolve("NBA", NBAProvider, args.nba_dir, lambda o: export_nba(o),
                lambda o: write_nba(o), tmp, real, args.refresh),
        resolve("NHL", NHLProvider, args.nhl_dir, lambda o: export_nhl(o),
                lambda o: write_csv("NHL", o), tmp, real, args.refresh),
        resolve("WNBA", WNBAProvider, args.wnba_dir, lambda o: export_wnba(o),
                lambda o: write_csv("WNBA", o), tmp, real, args.refresh),
        ncaaf_provider(args, tmp),
        CuratedProvider("NCAAB"),
        CuratedProvider("NCAAW"),
    ]
    for p in providers:
        try:
            run_pipeline(p, args.db)
        except Exception as e:
            log.error(f"  {p.sport}: load failed ({type(e).__name__}: {e})")
            if isinstance(p, LahmanMLBProvider):
                try:
                    fb = os.path.join(tmp, "mlb_fb"); write_mlb(fb)
                    run_pipeline(LahmanMLBProvider(data_dir=fb), args.db)
                    log.warning("  MLB: used bundled DEMO fixture after download failure")
                except Exception as e2:
                    log.error(f"  MLB fixture fallback failed: {e2}")

    conn = get_conn(args.db)
    rows = conn.execute("SELECT sport, COUNT(*) FROM players GROUP BY sport ORDER BY sport").fetchall()
    total = conn.execute("SELECT COUNT(*) FROM players").fetchone()[0]
    conn.close()
    log.info("=" * 48)
    log.info(f"Full run complete: {total} players -> {args.db}")
    for s, n in rows:
        log.info(f"    {s:6s} {n}")

    if not args.skip_puzzles:
        generate_all_puzzles(args.db, args.length, args.days)
    log.info("=" * 48)
    log.info("Start the app:  FLASK_APP=api/app.py .venv/bin/flask run --host 0.0.0.0 --port 5902")


if __name__ == "__main__":
    main()

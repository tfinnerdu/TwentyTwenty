# Sanctioned-source data providers

A swappable ingestion layer that replaces Sports-Reference scraping for
sports where a clean, openly-licensed (or officially sanctioned) source
exists. No bot-blocking, no datacenter-IP 403s, no rate-limit jail.

Each provider maps its source into the shared `players` schema; the generic
upsert + load pipeline lives in `base.py`. Add a source = add a `Provider`
subclass and register it in `run_provider.py`.

## Run it

```bash
# MLB from the Lahman / Chadwick Baseball Databank (downloads ~50MB of CSVs
# once into data/cache/lahman/, then reuses them)
python -m data.etl.run_provider --provider lahman_mlb

# NBA / WNBA / NHL / NFL from per-player season-stats CSVs
# (see csv_season.py for the canonical schema). Honors are applied
# automatically from the curated overlay.
python -m data.etl.run_provider --provider nba_csv  --source-dir /path/to/nba_csv
python -m data.etl.run_provider --provider wnba_csv --source-dir /path/to/wnba_csv
python -m data.etl.run_provider --provider nhl_api  --source-dir /path/to/nhl_csv
python -m data.etl.run_provider --provider nflverse --source-dir /path/to/nfl_csv

# NCAA: basketball (M/W) is hand-curated; football is curated by default but
# can be upgraded to real College Football Data with your API key.
python -m data.etl.run_provider --provider curated_ncaab
python -m data.etl.run_provider --provider curated_ncaaw
python -m data.etl.run_provider --provider curated_ncaaf
# ...or real NCAAF via CFBD:
CFBD_API_KEY=... python -m data.etl.providers.cfbd_export --start 2010 --end 2023 --out ./ncaaf_csv
python -m data.etl.run_provider --provider ncaaf_csv --source-dir ./ncaaf_csv

# Offline / firewalled: point --source-dir at a folder that already holds
# the source files (works for any provider).
python -m data.etl.run_provider --provider lahman_mlb --source-dir /path/to/csvs

# Re-apply curated honors after editing data/etl/awards/<sport>.json
python -m data.etl.apply_awards --sport NBA

# Then regenerate puzzles for the new data
python generate_puzzles.py --sport MLB --days 7 --force
```

## Full real run (all sports, one command)

`run_full.py` (repo root) pulls real data for every sport and writes the DB +
puzzles. Each pro sport has an **export helper** that fetches from its
sanctioned source into the canonical CSVs, and `run_full` calls them
automatically (falling back to the bundled fixture if a pull fails, so a run
always finishes):

| Sport | Export helper | Source |
|-------|---------------|--------|
| NFL  | `nfl_export.py`  | nflverse / `nfl_data_py` |
| NBA  | `nba_export.py`  | stats.nba.com via `nba_api` |
| NHL  | `nhl_export.py`  | official `api-web.nhle.com` (`requests`) |
| WNBA | `wnba_export.py` | stats.wnba.com (experimental) |
| NCAAF| `cfbd_export.py` | CollegeFootballData API (`CFBD_API_KEY`) |

```bash
pip install -r requirements.txt -r requirements-etl.txt   # adds nba_api, nfl_data_py
echo 'CFBD_API_KEY=xxxx' >> .env
python run_full.py                 # MLB+NCAA auto; NBA/NHL/WNBA/NFL via the helpers
python run_full.py --fixtures      # offline: bundled fixtures for the four pro sports
```

The helpers can also be run standalone (`python -m data.etl.providers.nba_export
--out ./nba_csv`) and ingested with `--source-dir`. They're written to each
library's documented API with defensive parsing — verify on first run; column
quirks are easy to adjust and don't affect the providers.

## Prove it without network

```bash
python -m data.etl.providers.smoke_test       # MLB (Lahman)
python -m data.etl.providers.smoke_test_nba   # NBA + awards overlay
python -m data.etl.providers.smoke_test_csv   # NHL, WNBA, NFL + awards overlay
python -m data.etl.providers.smoke_test_ncaa  # NCAA F / B(M) / B(W), curated
python -m data.etl.providers.smoke_test_all   # cross-sport ALL-mode chain
```

Each builds a schema-faithful fixture, runs the real provider against it,
applies the awards overlay, and generates a chain — the exact production code
path, just smaller data.

## Curated awards overlay

Most stat sources ship stat lines but not honors. `data/etl/apply_awards.py`
overlays a small hand-maintained award layer (`data/etl/awards/<sport>.json`),
matching players by normalized name within a sport, and runs automatically in
the pipeline after a provider loads. MLB is the exception — Lahman ships its
honors, so no `mlb.json` is needed.

## Status / notes

| Sport | Provider | Recommended source | Honors |
|-------|----------|--------------------|--------|
| MLB   | `lahman_mlb` | Chadwick Baseball Databank (CC-BY-SA) | from data (Lahman) |
| NBA   | `nba_csv`    | Kaggle season-stats / nba_api export | `awards/nba.json` |
| WNBA  | `wnba_csv`   | wehoop data releases | `awards/wnba.json` |
| NHL   | `nhl_api`    | api-web.nhle.com export | `awards/nhl.json` |
| NFL   | `nflverse`   | nflverse / nfl_data_py | `awards/nfl.json` |
| NCAAF | `curated_ncaaf` / `ncaaf_csv` | curated, or CollegeFootballData API | inline / `awards/ncaaf.json` |
| NCAAB | `curated_ncaab` | curated (`curated/ncaab.json`) | inline |
| NCAAW | `curated_ncaaw` | curated (`curated/ncaaw.json`) | inline |

NBA, WNBA, NHL, NFL and NCAAF share `csv_season.py` — each is a ~20-line
subclass declaring its team map, the season columns to sum, and how those map
onto the stat columns. NCAA basketball is hand-curated via `CuratedProvider`,
which *creates* full player rows from `curated/<sport>.json` (honors inline).

- The wyattowalsh **nbadb** SQLite is game-level (team box + play-by-play) — no
  per-player career averages — so `nba_csv` reads a season-stats table instead.
- **NCAA:** no clean free historical source, so basketball is curated and
  football defaults to curated; `cfbd_export.py` upgrades NCAAF to real
  CollegeFootballData with your key. A henrygd/ncaa-api instance is great for
  *live* scores but isn't a historical player catalog, so it's not wired in here.
- **Known gaps:** Lahman has no amateur-draft data or WAR; the non-MLB honors
  come from curated data (box scores carry no MVP/ring/All-NBA/Heisman).
- **Cross-sport** `college` / birth / decade categories connect every sport in
  ALL mode — see `smoke_test_all.py`.

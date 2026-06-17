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

# Offline / firewalled: point --source-dir at a folder that already holds
# the source files (works for any provider).
python -m data.etl.run_provider --provider lahman_mlb --source-dir /path/to/csvs

# Re-apply curated honors after editing data/etl/awards/<sport>.json
python -m data.etl.apply_awards --sport NBA

# Then regenerate puzzles for the new data
python generate_puzzles.py --sport MLB --days 7 --force
```

## Prove it without network

```bash
python -m data.etl.providers.smoke_test       # MLB (Lahman)
python -m data.etl.providers.smoke_test_nba   # NBA + awards overlay
python -m data.etl.providers.smoke_test_csv   # NHL, WNBA, NFL + awards overlay
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

NBA, WNBA, NHL and NFL share `csv_season.py` — each is a ~20-line subclass
declaring its team map, the season columns to sum, and how those map onto the
stat columns. Point `--source-dir` at the two canonical CSVs (see the module
docstring) produced from that sport's source.

- The wyattowalsh **nbadb** SQLite is game-level (team box + play-by-play) — no
  per-player career averages — so `nba_csv` reads a season-stats table instead.
- **Known gaps:** Lahman has no amateur-draft data or WAR; the non-MLB honors
  come from the curated overlay (box scores carry no MVP/ring/All-NBA/Heisman).
- **Remaining:** NCAA football + basketball (M/W) — the hard one: free
  historical college data is thin, so expect a mostly curated dataset.

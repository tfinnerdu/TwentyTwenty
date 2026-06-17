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

# NBA from per-player season-stats CSVs (see nba_csv.py for the schema).
# Honors come from the curated overlay automatically.
python -m data.etl.run_provider --provider nba_csv --source-dir /path/to/nba_csv

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
python -m data.etl.providers.smoke_test       # MLB
python -m data.etl.providers.smoke_test_nba   # NBA + awards overlay
```

Each builds a schema-faithful fixture (`_fixture_mlb.py` / `_fixture_nba.py`),
runs the real provider against it, applies the awards overlay, and generates a
chain — the exact production code path, just smaller data.

## Curated awards overlay

Most stat sources ship stat lines but not honors. `data/etl/apply_awards.py`
overlays a small hand-maintained award layer (`data/etl/awards/<sport>.json`),
matching players by normalized name within a sport, and runs automatically in
the pipeline after a provider loads. MLB is the exception — Lahman ships its
honors, so no `mlb.json` is needed.

## Status / notes

| Sport | Provider | Source | Honors |
|-------|----------|--------|--------|
| MLB   | `lahman_mlb` | Chadwick Baseball Databank (CC-BY-SA) | from data (Lahman) |
| NBA   | `nba_csv`    | per-player season-stats CSV (Kaggle / nba_api export) | curated overlay (`awards/nba.json`) |

- The wyattowalsh **nbadb** SQLite you may have seen is game-level (team box +
  play-by-play) — it has no per-player career averages, so `nba_csv` reads a
  season-stats table instead and rolls it up to career PPG/RPG/APG/games.
- **Known gaps:** Lahman has no amateur-draft data or WAR; NBA honors are the
  curated layer (box-score data carries no MVP/ring/All-NBA info).
- **Next providers**, same pattern: `nhl_api` (api-web.nhle.com), `wnba_wehoop`
  (wehoop data releases), nflverse for NFL — each with a curated `awards/<sport>.json`.

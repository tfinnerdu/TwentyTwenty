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

# Offline / firewalled: point at a folder that already holds the Lahman CSVs
python -m data.etl.run_provider --provider lahman_mlb --source-dir /path/to/csvs

# Then regenerate puzzles for the new data
python generate_puzzles.py --sport MLB --days 7 --force
```

## Prove it without network

```bash
python -m data.etl.providers.smoke_test
```

Builds a 30-player Lahman-schema fixture (`_fixture_mlb.py`), runs the real
provider against it, and generates an MLB chain — the exact production code
path, just smaller data.

## Status / notes

| Sport | Provider | Source | License |
|-------|----------|--------|---------|
| MLB   | `lahman_mlb` | Chadwick Baseball Databank | CC-BY-SA (own + redistribute) |

- **Awards included** for MLB: MVP, Cy Young, Gold Glove, All-Star, and World
  Series titles (derived from `SeriesPost` × `Appearances`). Lahman is the one
  source that ships honors for free.
- **Two known data gaps** (Lahman core): no amateur-draft data (MLB players
  are left un-tagged for draft categories rather than falsely "Undrafted"),
  and no WAR (`mlb_war` stays 0 until supplemented).
- **Next providers** to add the same way: `nba_kaggle` (Kaggle nbadb),
  `nhl_api` (api-web.nhle.com), `wnba_wehoop` (wehoop data releases),
  nflverse for NFL. Awards for those sports will need a small curated layer.

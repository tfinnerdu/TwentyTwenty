"""
data/etl/providers/nba_csv.py
=============================
NBA provider. The implementation now lives in csv_season.py (shared by NBA,
WNBA, NHL and NFL); this module re-exports it under the original name for
backwards compatibility.

See csv_season.NBAProvider for the canonical CSV schema. Reminder: the
wyattowalsh "nbadb" SQLite is game-level and has no per-player career
averages -- this provider reads a per-player season-stats table instead.
"""

from data.etl.providers.csv_season import NBAProvider as NBACsvProvider  # noqa: F401

__all__ = ["NBACsvProvider"]

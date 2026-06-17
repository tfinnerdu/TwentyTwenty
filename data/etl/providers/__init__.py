"""
data/etl/providers
==================
Sanctioned-source data providers for the 20/20 game.

Each provider knows how to pull one sport's data from an openly-licensed
or officially-sanctioned source (Lahman, nflverse, the NHL API, ...) and
yield player payload dicts shaped like the `players` table. The generic
upsert + pipeline driver lives in base.py, so adding a new source is just
a new Provider subclass -- the provider choice is swappable per sport and
you never get bot-blocked the way the Sports-Reference scrapers do.
"""

from data.etl.providers.base import Provider, run_pipeline, upsert_players  # noqa: F401

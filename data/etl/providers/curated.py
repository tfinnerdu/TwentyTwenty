"""
data/etl/providers/curated.py
=============================
Provider for hand-curated player datasets (data/etl/curated/<sport>.json).

Unlike the awards overlay (which only *updates* honor columns on players a
stat source already loaded), this *creates* full player rows from JSON. It's
the path for sports with no clean free source -- NCAA basketball especially,
where historical player data has to be hand-built.

Each record is a players-row dict (any subset of columns), e.g.:
    {"name": "Christian Laettner", "college": "Duke",
     "active_decades": ["1980s","1990s"], "ncaab_points": 16.6,
     "ncaab_championships": 2, "ncaab_all_american": 2,
     "position": "F", "position_group": "F", "birth_state": "NY"}

`sport` and a stable `sr_id` are filled in automatically if omitted.

    python -m data.etl.run_provider --provider curated_ncaab
"""

import json
import os
import re
import sys
from typing import Iterable

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))
from data.etl.providers.base import Provider

CURATED_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "curated")


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


class CuratedProvider(Provider):
    source_name = "curated"

    def __init__(self, sport: str, **_):
        self.sport = sport

    def _path(self) -> str:
        return os.path.join(CURATED_DIR, f"{self.sport.lower()}.json")

    def fetch(self) -> Iterable[dict]:
        path = self._path()
        if not os.path.exists(path):
            raise FileNotFoundError(f"No curated dataset at {path}")
        with open(path, encoding="utf-8") as fh:
            records = json.load(fh)
        for rec in records:
            rec.setdefault("sport", self.sport)
            # stable sr_id so re-runs upsert instead of duplicating
            rec.setdefault("sr_id", f"{self.sport.lower()}_{_slug(rec.get('name', ''))}")
            yield rec

"""
data/etl/providers/base.py
==========================
The provider-adapter backbone.

A Provider yields *player payload dicts* whose keys are columns of the
`players` table (plus the list-valued `teams` / `active_decades`, which we
JSON-encode for you). Everything sport-specific lives in the subclass; the
schema-aware upsert and the load pipeline are shared here.

This is the piece that makes data sources swappable: balldontlie, Lahman,
the NHL API, the Kaggle NBA db -- each is just a Provider, mapping its rows
into the same `players` shape. We cache into our own SQLite either way, so
the source is only ever touched at ingestion time.
"""

import json
import logging
import os
import sys
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Iterable

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))
from data.etl.schema import get_conn, migrate
from data.etl.load import derive_fields, rebuild_categories

log = logging.getLogger(__name__)

# Columns we store as JSON arrays in the DB but hand around as Python lists.
LIST_COLUMNS = ("teams", "active_decades")


class Provider(ABC):
    """Base class for a single-sport data source."""

    #: e.g. 'MLB' -- drives etl_runs.sport and which categories get rebuilt
    sport: str = ""
    #: e.g. 'lahman' -- stored in players.data_source / etl_runs.source
    source_name: str = ""

    @abstractmethod
    def fetch(self) -> Iterable[dict]:
        """Yield player payload dicts keyed by `players` columns."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Schema-aware upsert
# ---------------------------------------------------------------------------

def table_columns(conn, table: str = "players") -> set:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def upsert_players(conn, players: list, source: str, etl_run_id: int) -> tuple:
    """
    Upsert payloads by sr_id. Unknown keys are ignored, list columns are
    JSON-encoded, and data_source / last_updated / etl_run_id are stamped
    on every touched row. Returns (added, updated).
    """
    valid = table_columns(conn)
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.cursor()
    added = updated = 0

    for payload in players:
        rec = {k: v for k, v in payload.items() if k in valid}
        for col in LIST_COLUMNS:
            if isinstance(rec.get(col), (list, tuple)):
                rec[col] = json.dumps(list(rec[col]))
        rec["data_source"]  = source
        rec["last_updated"] = now
        rec["etl_run_id"]   = etl_run_id

        sr_id = rec.get("sr_id")
        existing = (
            cur.execute("SELECT id FROM players WHERE sr_id=?", (sr_id,)).fetchone()
            if sr_id else None
        )

        if existing:
            fields = [k for k in rec if k != "sr_id"]
            assignments = ", ".join(f"{k}=?" for k in fields)
            cur.execute(
                f"UPDATE players SET {assignments} WHERE sr_id=?",
                [rec[k] for k in fields] + [sr_id],
            )
            updated += 1
        else:
            keys = list(rec.keys())
            placeholders = ", ".join("?" * len(keys))
            cur.execute(
                f"INSERT INTO players ({', '.join(keys)}) VALUES ({placeholders})",
                [rec[k] for k in keys],
            )
            added += 1

    conn.commit()
    return added, updated


# ---------------------------------------------------------------------------
# Pipeline driver
# ---------------------------------------------------------------------------

def run_pipeline(provider: Provider, db_path: str, skip_load: bool = False,
                 limit: int | None = None, apply_award_overlay: bool = True) -> dict:
    """
    Full ingestion for one provider: register an etl_run, fetch + upsert,
    overlay curated awards (rings/MVP/etc. the stat source lacks), then
    (unless skip_load) derive fields, rebuild that sport's categories, and
    record the load. Returns a small summary dict.
    """
    migrate(db_path)
    conn = get_conn(db_path)
    now = datetime.now(timezone.utc).isoformat()

    cur = conn.cursor()
    cur.execute(
        "INSERT INTO etl_runs (sport, source, run_at, status, notes) "
        "VALUES (?, ?, ?, 'running', ?)",
        (provider.sport, provider.source_name, now, f"provider={provider.source_name}"),
    )
    conn.commit()
    run_id = cur.lastrowid

    log.info(f"[{provider.source_name}] fetching {provider.sport} players...")
    players = list(provider.fetch())
    if limit:
        players = players[:limit]
    log.info(f"[{provider.source_name}] fetched {len(players)} players")

    try:
        added, updated = upsert_players(conn, players, provider.source_name, run_id)
    except Exception as e:
        cur.execute("UPDATE etl_runs SET status='error', notes=? WHERE id=?",
                    (f"upsert failed: {e}", run_id))
        conn.commit()
        conn.close()
        raise

    cur.execute(
        "UPDATE etl_runs SET players_added=?, players_updated=?, status='ok' WHERE id=?",
        (added, updated, run_id),
    )
    conn.commit()
    log.info(f"[{provider.source_name}] upserted: {added} added, {updated} updated")

    if apply_award_overlay:
        from data.etl.apply_awards import apply_awards
        matched, total, unmatched = apply_awards(conn, provider.sport)
        if total:
            log.info(f"[{provider.source_name}] awards overlay: {matched}/{total} matched"
                     + (f" ({len(unmatched)} not in DB)" if unmatched else ""))

    if not skip_load:
        derive_fields(conn)
        rebuild_categories(conn, provider.sport)
        # NB: the provider's own etl_runs row (inserted above, updated with the
        # real added/updated counts) is the load record. Don't also call
        # record_load_run here -- that wrote a second, zero-count 'load' row per
        # sport that then masked the real one in the "Stats as of" footer.

    conn.close()
    return {"sport": provider.sport, "source": provider.source_name,
            "added": added, "updated": updated}

#!/usr/bin/env python3
"""
load_from_postgres.py
=====================
The inverse of load_to_postgres.py: copy the LIVE Postgres database (e.g. the
Render instance the deployed app reads from) DOWN into the local SQLite file
(data/nfl.db). Use it to pull production data -- including any /admin edits made
against the live site -- onto your machine to inspect, audit, or run locally.

    # pull live -> local  (URL from POSTGRES_DB_URL / DATABASE_URL in .env)
    python load_from_postgres.py
    # or pass the Render EXTERNAL connection string explicitly
    python load_from_postgres.py "postgresql://USER:PW@HOST/DB?sslmode=require"
    # write somewhere other than data/nfl.db (e.g. a dated snapshot)
    python load_from_postgres.py --sqlite data/pulled_2026-06-25.db

How it works: it first builds the canonical SQLite schema (schema.migrate -- the
right column types, primary keys, indexes, UNIQUE/AUTOINCREMENT), then REFRESHES
the data table-by-table from Postgres (each table's local rows are deleted and
re-inserted, ids preserved). Foreign-key enforcement is off during the load so
table order can't trip it. The result is a proper, fully-indexed local DB the app
/ generator / ETL can use as-is -- not a constraint-stripped mirror. Any Postgres
table without a canonical counterpart is still copied, verbatim, so nothing is
lost.

WARNING: this REPLACES the data in the destination SQLite file (default
data/nfl.db, which is a gitignored, regenerable artifact -- so overwriting it is
normal here). Pass --sqlite to write a snapshot elsewhere instead.
"""

import argparse
import os
import sqlite3
import sys
from decimal import Decimal

import psycopg2

sys.path.insert(0, os.path.dirname(__file__))
from data.etl.schema import migrate

DEFAULT_SQLITE = os.path.join(os.path.dirname(__file__), "data", "nfl.db")


def _sqlite_affinity(pg_type: str) -> str:
    """Postgres data_type -> SQLite column affinity (only used for the rare
    non-canonical table copied verbatim)."""
    t = (pg_type or "").lower()
    if t in ("integer", "bigint", "smallint", "boolean"):
        return "INTEGER"
    if t in ("real", "double precision", "numeric", "decimal"):
        return "REAL"
    return "TEXT"


def _coerce(v):
    """Make a psycopg2 value sqlite3-bindable. Only NUMERIC -> Decimal needs help;
    ints, floats, str, None pass straight through."""
    return float(v) if isinstance(v, Decimal) else v


def list_pg_tables(pcur) -> list:
    pcur.execute("SELECT table_name FROM information_schema.tables "
                 "WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name")
    return [r[0] for r in pcur.fetchall()]


def pg_columns(pcur, table) -> list:
    pcur.execute(f'SELECT * FROM "{table}" LIMIT 0')
    return [d[0] for d in pcur.description]


def sqlite_tables(scur) -> set:
    return {r[0] for r in scur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


def sqlite_columns(scur, table) -> list:
    return [r[1] for r in scur.execute(f"PRAGMA table_info('{table}')")]


def _create_table_from_pg(pcur, scur, table) -> list:
    """Fallback for a Postgres table with no canonical SQLite counterpart: recreate
    it from information_schema with sqlite affinities, no constraints. Returns its
    column names."""
    pcur.execute("SELECT column_name, data_type FROM information_schema.columns "
                 "WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position",
                 (table,))
    cols = pcur.fetchall()
    defs = ", ".join(f'"{name}" {_sqlite_affinity(dt)}' for name, dt in cols)
    scur.execute(f'DROP TABLE IF EXISTS "{table}"')
    scur.execute(f'CREATE TABLE "{table}" ({defs})')
    return [name for name, _dt in cols]


def copy_table(pcon, scon, table, canonical_cols) -> int:
    """Refresh one table in the dest from Postgres. canonical_cols=set means the
    table exists in the SQLite schema (delete + reinsert the shared columns);
    None means it doesn't (create it verbatim from Postgres, then insert)."""
    pcur = pcon.cursor()
    scur = scon.cursor()
    if canonical_cols is None:
        cols = _create_table_from_pg(pcur, scur, table)
    else:
        cols = [c for c in pg_columns(pcur, table) if c in canonical_cols]  # shared, PG order
        scur.execute(f'DELETE FROM "{table}"')
    if not cols:
        return 0
    col_sql = ", ".join(f'"{c}"' for c in cols)
    pcur.execute(f'SELECT {col_sql} FROM "{table}"')
    rows = [tuple(_coerce(v) for v in r) for r in pcur.fetchall()]
    if rows:
        ph = ", ".join("?" * len(cols))
        scur.executemany(f'INSERT INTO "{table}" ({col_sql}) VALUES ({ph})', rows)
    return len(rows)


def main():
    ap = argparse.ArgumentParser(description="Copy live Postgres down into local SQLite nfl.db")
    ap.add_argument("db_url", nargs="?",
                    help="Postgres URL (else POSTGRES_DB_URL / DATABASE_URL from .env)")
    ap.add_argument("--sqlite", default=DEFAULT_SQLITE, help="destination SQLite file (REPLACED)")
    args = ap.parse_args()

    # Fall back to .env so you don't have to paste the URL each run.
    if not args.db_url:
        from data.etl.dbconfig import load_env, postgres_url
        load_env()
        args.db_url = postgres_url()
    if not args.db_url:
        sys.exit("error: pass a Postgres URL, or set POSTGRES_DB_URL (or DATABASE_URL) in .env. "
                 "Use the Render EXTERNAL connection string with ?sslmode=require.")

    print(f"Building canonical schema in {args.sqlite}")
    migrate(args.sqlite)

    pcon = psycopg2.connect(args.db_url)
    scon = sqlite3.connect(args.sqlite)
    scon.execute("PRAGMA foreign_keys=OFF")     # bulk import: trust the source, ignore load order
    try:
        pcur = pcon.cursor()
        canon = sqlite_tables(scon.cursor())
        canon_cols = {t: set(sqlite_columns(scon.cursor(), t)) for t in canon}
        tables = list_pg_tables(pcur)
        print(f"Copying {len(tables)} tables from Postgres -> {args.sqlite}")
        total = 0
        for t in tables:
            n = copy_table(pcon, scon, t, canon_cols.get(t))   # None -> non-canonical, verbatim
            total += n
            tag = "" if t in canon else "   (non-canonical, copied verbatim)"
            print(f"  {t:20s} {n:>7d} rows{tag}")
        scon.commit()
        print(f"Done. {total} rows pulled into {args.sqlite}.")
    except Exception:
        scon.rollback()
        raise
    finally:
        pcon.close()
        scon.close()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
load_to_postgres.py
===================
Copy the locally-built SQLite database (data/nfl.db) into a Postgres database --
e.g. the Render Postgres instance the deployed app reads from.

The app/ETL stay SQLite: you build the DB locally with the real data the usual
way, then push it up here. The deployed app talks to Postgres whenever
DATABASE_URL is set (see api/app.py); locally it stays on SQLite.

    # 1. build the real data locally (schema + populate), as before
    pip install -r requirements.txt -r requirements-etl.txt
    python run_full.py --skip-puzzles

    # 2. push it into your Render Postgres (use the EXTERNAL connection string,
    #    which usually needs ?sslmode=require)
    python load_to_postgres.py "postgresql://USER:PW@HOST/DB?sslmode=require"
    #    (DATABASE_URL in the environment is used if no URL arg is given)

It introspects the SQLite schema (so it tracks new columns automatically),
recreates each table in Postgres (primary keys preserved; foreign-key
constraints omitted so a plain bulk copy can't trip over load order), and
bulk-copies every row. Re-running it refreshes the data (each table is dropped
and recreated), so it's safe to run after every local rebuild.
"""

import argparse
import os
import sqlite3
import sys

import psycopg2
import psycopg2.extras

DEFAULT_SQLITE = os.path.join(os.path.dirname(__file__), "data", "nfl.db")


def pg_type(sqlite_decl: str) -> str:
    """Map a SQLite declared type to a Postgres column type."""
    t = (sqlite_decl or "").upper()
    if "INT" in t:
        return "BIGINT"
    if any(k in t for k in ("REAL", "FLOA", "DOUB", "NUM", "DEC")):
        return "DOUBLE PRECISION"
    return "TEXT"   # TEXT / BLOB / dateless -- copy verbatim (JSON arrays live here)


def list_tables(scur) -> list:
    return [r[0] for r in scur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE 'sqlite_%' ORDER BY name")]


def build_ddl(table: str, cols: list) -> str:
    """cols: PRAGMA table_info rows (cid, name, type, notnull, dflt, pk). PK is
    preserved (so puzzle_meta's composite key still backs register_slug's
    ON CONFLICT); FK constraints and NOT NULL/DEFAULT are intentionally dropped so
    the verbatim copy always lands."""
    defs = [f'"{name}" {pg_type(ctype)}' for (_cid, name, ctype, _nn, _df, _pk) in cols]
    pk = sorted((c for c in cols if c[5]), key=lambda c: c[5])
    if pk:
        defs.append("PRIMARY KEY (" + ", ".join(f'"{c[1]}"' for c in pk) + ")")
    return f'CREATE TABLE "{table}" (\n  ' + ",\n  ".join(defs) + "\n)"


def copy_table(scon, pcur, table: str) -> int:
    scur = scon.cursor()
    cols = scur.execute(f"PRAGMA table_info('{table}')").fetchall()
    pcur.execute(f'DROP TABLE IF EXISTS "{table}" CASCADE')
    pcur.execute(build_ddl(table, cols))

    scur.execute(f'SELECT * FROM "{table}"')
    col_names = [d[0] for d in scur.description]
    rows = scur.fetchall()
    if rows:
        cols_sql = ", ".join(f'"{c}"' for c in col_names)
        psycopg2.extras.execute_values(
            pcur, f'INSERT INTO "{table}" ({cols_sql}) VALUES %s',
            [tuple(r) for r in rows], page_size=500)
    return len(rows)


def main():
    ap = argparse.ArgumentParser(description="Copy SQLite nfl.db into Postgres")
    ap.add_argument("db_url", nargs="?", default=os.environ.get("DATABASE_URL"),
                    help="Postgres connection string (or set DATABASE_URL)")
    ap.add_argument("--sqlite", default=DEFAULT_SQLITE, help="source SQLite file")
    args = ap.parse_args()

    if not args.db_url:
        sys.exit("error: pass a Postgres URL (or set DATABASE_URL). "
                 "Use the Render EXTERNAL connection string with ?sslmode=require.")
    if not os.path.exists(args.sqlite):
        sys.exit(f"error: SQLite DB not found: {args.sqlite} "
                 f"(build it first, e.g. `python run_full.py --skip-puzzles`)")

    scon = sqlite3.connect(args.sqlite)          # default tuple rows -> direct copy
    pcon = psycopg2.connect(args.db_url)
    try:
        pcur = pcon.cursor()
        tables = list_tables(scon.cursor())
        print(f"Copying {len(tables)} tables from {args.sqlite} -> Postgres")
        for t in tables:
            n = copy_table(scon, pcur, t)
            print(f"  {t:20s} {n:>7d} rows")
        pcon.commit()
        print("Done. Set DATABASE_URL on the web service to this database and redeploy.")
    except Exception:
        pcon.rollback()
        raise
    finally:
        scon.close()
        pcon.close()


if __name__ == "__main__":
    main()

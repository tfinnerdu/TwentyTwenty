"""
data/etl/dbconfig.py
====================
One place for local tooling to learn which database(s) to write to, read from
.env so you don't paste URLs or flip flags by hand:

    POSTGRES_DB_URL=postgresql://user:pw@host/db?sslmode=require   # (DATABASE_URL also accepted)
    USE_SQLITE=true       # maintain data/nfl.db          (default: true)
    USE_POSTGRES=true     # also mirror updates to Postgres (default: true when a URL is set)

targets() returns the resolved destinations so an update/refresh script can hit
SQLite and/or Postgres in one pass:

    from data.etl.dbconfig import targets
    t = targets()                 # {'sqlite': '.../data/nfl.db', 'postgres': 'postgres://...'}
"""

import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
DEFAULT_SQLITE = os.path.join(ROOT, "data", "nfl.db")


def load_env(path=None):
    """Populate os.environ from .env (never overrides an already-set var)."""
    path = path or os.path.join(ROOT, ".env")
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _flag(name, default):
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


def postgres_url():
    """The Postgres URL from POSTGRES_DB_URL (preferred) or DATABASE_URL, or None."""
    return (os.environ.get("POSTGRES_DB_URL") or os.environ.get("DATABASE_URL") or "").strip() or None


def use_sqlite():
    return _flag("USE_SQLITE", True)


def use_postgres():
    return _flag("USE_POSTGRES", postgres_url() is not None)


def targets(sqlite_path=None):
    """Resolved write destinations from .env:
    {'sqlite': path|None, 'postgres': url|None}. Loads .env first."""
    load_env()
    return {
        "sqlite":   (sqlite_path or DEFAULT_SQLITE) if use_sqlite() else None,
        "postgres": postgres_url() if use_postgres() else None,
    }


if __name__ == "__main__":
    load_env()
    t = targets()
    print("USE_SQLITE   =", use_sqlite(), "->", t["sqlite"])
    print("USE_POSTGRES =", use_postgres(), "->", (t["postgres"][:40] + "…") if t["postgres"] else None)

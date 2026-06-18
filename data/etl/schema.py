"""
20/20 Game - Multi-Sport Database Schema
----------------------------------------
Run this to create or migrate the database in place.
Safe to run on an existing DB - uses ALTER TABLE IF NOT EXISTS patterns.

Tables:
  etl_runs        - one row per ETL execution, drives the "Stats as of" UI footer
  players         - one row per player, sport-aware
  player_aliases  - alternate name spellings for autocomplete fuzzy matching
  categories      - filterable dimensions, tagged by sport scope
  player_categories - membership (player x category)
"""

import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "nfl.db")


def get_conn(path=None):
    conn = sqlite3.connect(path or DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def migrate(path=None):
    """Create or upgrade the schema in place. Idempotent."""
    conn = get_conn(path)
    c = conn.cursor()

    # ------------------------------------------------------------------
    # etl_runs: audit log for every scrape/load operation
    # ------------------------------------------------------------------
    c.execute("""
        CREATE TABLE IF NOT EXISTS etl_runs (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            sport         TEXT NOT NULL,        -- 'NFL', 'NBA', 'MLB', 'NHL', 'ALL'
            source        TEXT NOT NULL,        -- 'pfr', 'bbref', 'bref', 'hockeyref', 'manual'
            run_at        TEXT NOT NULL,        -- ISO8601 UTC timestamp
            players_added INTEGER DEFAULT 0,
            players_updated INTEGER DEFAULT 0,
            status        TEXT DEFAULT 'ok',    -- 'ok', 'partial', 'error'
            notes         TEXT
        )
    """)

    # ------------------------------------------------------------------
    # players: one row per player, all sports share this table
    # ------------------------------------------------------------------
    c.execute("""
        CREATE TABLE IF NOT EXISTS players (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            sport           TEXT NOT NULL DEFAULT 'NFL',
            sr_id           TEXT UNIQUE,        -- Sports-Reference player ID (e.g. BradTo00)
            name            TEXT NOT NULL,
            position        TEXT,
            position_group  TEXT,               -- QB/RB/WR/TE/DEF/OL or G/F/C or SP/RP/1B etc.

            -- Bio fields (cross-sport connectors)
            birth_date      TEXT,               -- YYYY-MM-DD
            birth_year      INTEGER,
            birth_city      TEXT,
            birth_state     TEXT,               -- 2-letter for US, country name for international
            birth_country   TEXT DEFAULT 'USA',
            college         TEXT,               -- last college attended
            high_school     TEXT,
            height_inches   INTEGER,            -- stored as inches for comparisons
            weight_lbs      INTEGER,

            -- Draft
            draft_year      INTEGER,
            draft_round     INTEGER DEFAULT 0,  -- 0 = undrafted
            draft_pick      INTEGER DEFAULT 0,  -- 0 = undrafted
            draft_team      TEXT,

            -- Career span
            debut_year      INTEGER,
            final_year      INTEGER,
            active_decades  TEXT,               -- JSON array e.g. ["1990s","2000s"]

            -- Teams
            teams           TEXT,               -- JSON array of franchise names

            -- ---- NFL stats ----
            pass_yds        INTEGER DEFAULT 0,
            rush_yds        INTEGER DEFAULT 0,
            rec_yds         INTEGER DEFAULT 0,
            pass_tds        INTEGER DEFAULT 0,
            rush_tds        INTEGER DEFAULT 0,
            rec_tds         INTEGER DEFAULT 0,
            total_tds       INTEGER DEFAULT 0,
            pro_bowls       INTEGER DEFAULT 0,
            super_bowls     INTEGER DEFAULT 0,
            nfl_mvps        INTEGER DEFAULT 0,
            ap_all_pro      INTEGER DEFAULT 0,  -- number of 1st-team All-Pro selections
            sacks           REAL DEFAULT 0,
            interceptions   INTEGER DEFAULT 0,

            -- ---- NBA stats ----
            nba_points      REAL DEFAULT 0,     -- career PPG
            nba_rebounds    REAL DEFAULT 0,     -- career RPG
            nba_assists     REAL DEFAULT 0,     -- career APG
            nba_games       INTEGER DEFAULT 0,
            nba_championships INTEGER DEFAULT 0,
            nba_mvps        INTEGER DEFAULT 0,
            nba_finals_mvps INTEGER DEFAULT 0,
            nba_all_star    INTEGER DEFAULT 0,
            nba_all_nba     INTEGER DEFAULT 0,  -- All-NBA team selections

            -- ---- MLB stats ----
            mlb_batting_avg REAL DEFAULT 0,
            mlb_hr          INTEGER DEFAULT 0,
            mlb_rbi         INTEGER DEFAULT 0,
            mlb_hits        INTEGER DEFAULT 0,
            mlb_era         REAL DEFAULT 0,     -- for pitchers
            mlb_wins        INTEGER DEFAULT 0,  -- for pitchers
            mlb_strikeouts  INTEGER DEFAULT 0,
            mlb_saves       INTEGER DEFAULT 0,
            mlb_war         REAL DEFAULT 0,
            mlb_all_star    INTEGER DEFAULT 0,
            mlb_championships INTEGER DEFAULT 0,
            mlb_mvps        INTEGER DEFAULT 0,
            mlb_cy_young    INTEGER DEFAULT 0,
            mlb_gold_gloves INTEGER DEFAULT 0,

            -- ---- NHL stats ----
            nhl_goals       INTEGER DEFAULT 0,
            nhl_assists     INTEGER DEFAULT 0,
            nhl_points      INTEGER DEFAULT 0,
            nhl_games       INTEGER DEFAULT 0,
            nhl_championships INTEGER DEFAULT 0,  -- Stanley Cups
            nhl_mvps        INTEGER DEFAULT 0,     -- Hart Trophy wins
            nhl_all_star    INTEGER DEFAULT 0,

            -- ---- WNBA stats ----
            wnba_points       REAL DEFAULT 0,
            wnba_rebounds     REAL DEFAULT 0,
            wnba_assists      REAL DEFAULT 0,
            wnba_games        INTEGER DEFAULT 0,
            wnba_championships INTEGER DEFAULT 0,
            wnba_mvps         INTEGER DEFAULT 0,
            wnba_finals_mvps  INTEGER DEFAULT 0,
            wnba_all_star     INTEGER DEFAULT 0,

            -- ---- NCAAF stats ----
            ncaaf_pass_yds    INTEGER DEFAULT 0,
            ncaaf_rush_yds    INTEGER DEFAULT 0,
            ncaaf_rec_yds     INTEGER DEFAULT 0,
            ncaaf_tds         INTEGER DEFAULT 0,
            ncaaf_sacks       REAL DEFAULT 0,
            ncaaf_all_american INTEGER DEFAULT 0,
            ncaaf_heisman     INTEGER DEFAULT 0,

            -- ---- NCAAB / NCAAW stats (shared columns) ----
            ncaab_points      REAL DEFAULT 0,
            ncaab_rebounds    REAL DEFAULT 0,
            ncaab_assists     REAL DEFAULT 0,
            ncaab_games       INTEGER DEFAULT 0,
            ncaab_championships INTEGER DEFAULT 0,
            ncaab_all_american  INTEGER DEFAULT 0,
            ncaab_player_of_year INTEGER DEFAULT 0,
            ncaab_all_conference INTEGER DEFAULT 0,   -- all-conference / minor-award keeper (M+W)

            -- ETL bookkeeping
            data_source     TEXT,               -- which scraper populated this row
            last_updated    TEXT,               -- ISO8601 UTC of last ETL touch
            etl_run_id      INTEGER REFERENCES etl_runs(id)
        )
    """)

    # Additive migration: CREATE TABLE IF NOT EXISTS won't add columns to an
    # already-existing players table, so ALTER in any a newer schema introduced.
    existing = {r[1] for r in c.execute("PRAGMA table_info(players)")}
    for col, decl in (("ncaab_all_conference", "INTEGER DEFAULT 0"),):
        if col not in existing:
            c.execute(f"ALTER TABLE players ADD COLUMN {col} {decl}")

    # ------------------------------------------------------------------
    # player_aliases: catches "LeBron" -> "LeBron James", misspellings, etc.
    # ------------------------------------------------------------------
    c.execute("""
        CREATE TABLE IF NOT EXISTS player_aliases (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            player_id   INTEGER NOT NULL REFERENCES players(id),
            alias       TEXT NOT NULL,
            alias_norm  TEXT NOT NULL            -- lowercased, punctuation-stripped
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_alias_norm ON player_aliases(alias_norm)")

    # ------------------------------------------------------------------
    # categories: filterable dimensions, sport-scoped
    # ------------------------------------------------------------------
    c.execute("""
        CREATE TABLE IF NOT EXISTS categories (
            id          TEXT PRIMARY KEY,
            label       TEXT NOT NULL,
            description TEXT,
            sport_scope TEXT DEFAULT 'ALL',     -- 'NFL','NBA','MLB','NHL','ALL'
            field       TEXT,                   -- player column name
            op          TEXT,                   -- gte, lte, eq, has, in, between
            value       TEXT,                   -- comparison value
            value2      TEXT,                   -- for 'between' op
            difficulty  INTEGER DEFAULT 2       -- 1=easy, 2=medium, 3=hard
        )
    """)

    # ------------------------------------------------------------------
    # player_categories: many-to-many membership
    # ------------------------------------------------------------------
    c.execute("""
        CREATE TABLE IF NOT EXISTS player_categories (
            player_id   INTEGER REFERENCES players(id),
            category_id TEXT REFERENCES categories(id),
            PRIMARY KEY (player_id, category_id)
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_pc_cat ON player_categories(category_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_pc_player ON player_categories(player_id)")

    # ------------------------------------------------------------------
    # puzzles: track generated puzzles with their data vintage
    # ------------------------------------------------------------------
    c.execute("""
        CREATE TABLE IF NOT EXISTS puzzle_meta (
            puzzle_date   TEXT,                 -- YYYY-MM-DD
            sport_mode    TEXT DEFAULT 'NFL',   -- 'NFL','NBA','ALL', etc.
            generated_at  TEXT NOT NULL,
            etl_run_id    INTEGER REFERENCES etl_runs(id),
            chain_length  INTEGER DEFAULT 20,
            url_slug      TEXT UNIQUE,          -- 8-char base64url, e.g. 'u81rd6rT'
            PRIMARY KEY (puzzle_date, sport_mode)
        )
    """)

    conn.commit()
    conn.close()
    print("Schema migrated successfully.")


def get_data_vintage(conn, sport='ALL'):
    """
    Return the most recent successful ETL run info for a sport.
    Used by the API to populate the 'Stats as of' footer.
    """
    c = conn.cursor()
    if sport == 'ALL':
        # One row per sport: its most recent successful load that actually
        # ingested players. Filtering out zero-count rows (legacy 'load'
        # markers carry no counts) means MAX(run_at) lands on the real load,
        # and dropping the old LIMIT lets every sport show in the footer.
        c.execute("""
            SELECT sport, MAX(run_at) AS run_at,
                   COALESCE(players_added,0) + COALESCE(players_updated,0) AS total
            FROM etl_runs
            WHERE status = 'ok'
              AND COALESCE(players_added,0) + COALESCE(players_updated,0) > 0
            GROUP BY sport
            ORDER BY run_at DESC
        """)
    else:
        c.execute("""
            SELECT sport, run_at,
                   COALESCE(players_added,0) + COALESCE(players_updated,0) AS total
            FROM etl_runs
            WHERE status = 'ok' AND (sport = ? OR sport = 'ALL')
              AND COALESCE(players_added,0) + COALESCE(players_updated,0) > 0
            ORDER BY run_at DESC
            LIMIT 1
        """, (sport,))
    return c.fetchall()


if __name__ == "__main__":
    migrate()
    conn = get_conn()
    rows = get_data_vintage(conn)
    if rows:
        print("\nData vintage:")
        for r in rows:
            print(f"  {r['sport']} — {r['run_at']} ({r['total']} players)")
    else:
        print("\nNo ETL runs recorded yet.")
    conn.close()

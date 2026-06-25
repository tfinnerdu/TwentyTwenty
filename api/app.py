"""
20/20 Game - Flask API

Endpoints:
  GET  /api/puzzle/today        -> today's chain (no answers exposed)
  GET  /api/puzzle/<date>       -> specific date's chain (YYYY-MM-DD)
  POST /api/validate            -> validate a player answer for a question
  POST /api/generate            -> admin: generate + store a new chain
  GET  /health                  -> health check
"""

import json
import os
import sys
import hmac
import hashlib
import sqlite3
from functools import wraps
from datetime import date, datetime, timedelta
from flask import (Flask, jsonify, request, send_from_directory,
                   session, redirect, abort, Response)
from flask_cors import CORS
from markupsafe import escape

# Allow imports from parent dir
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from engine.generator import generate_chain
try:
    from data.etl.schema import get_data_vintage as _get_vintage
    HAS_ETL_SCHEMA = True
except ImportError:
    HAS_ETL_SCHEMA = False
try:
    # pure (no-DB) category rule fns, reused to re-derive one player's categories
    # after an admin edit -- get_categories()/player_matches() touch no database.
    from data.etl.load import get_categories as _get_categories, player_matches as _player_matches
    HAS_CATEGORY_RULES = True
except Exception:
    HAS_CATEGORY_RULES = False

app = Flask(__name__, static_folder="../static")
CORS(app)

# --- Admin auth ------------------------------------------------------------
# /admin* is gated by a single shared password in ADMIN_PW (set it in Render).
# If ADMIN_PW is unset the whole admin surface is disabled (no default password).
# The session secret is derived from ADMIN_PW so logins survive restarts without
# a second env var; override with SECRET_KEY if you prefer.
ADMIN_PW = os.environ.get("ADMIN_PW")
app.secret_key = os.environ.get("SECRET_KEY") or (
    hashlib.sha256(("2020-admin|" + ADMIN_PW).encode()).hexdigest()
    if ADMIN_PW else "dev-insecure-key-admin-disabled")


def require_admin(fn):
    """Gate a route behind the ADMIN_PW session login."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not ADMIN_PW:
            return Response("Admin is disabled (set the ADMIN_PW env var).", 503)
        if not session.get("admin_ok"):
            return redirect("/admin/login")
        return fn(*args, **kwargs)
    return wrapper

BASE_DIR   = os.path.dirname(os.path.dirname(__file__))
DB_PATH    = os.path.join(BASE_DIR, "data", "nfl.db")
PUZZLE_DIR = os.path.join(BASE_DIR, "puzzles")

# Database backend: Postgres when DATABASE_URL is set (production / Render), else
# the local SQLite file (offline dev + the ETL build). The thin _PG* adapter below
# lets the sqlite-style call sites (conn.execute / conn.cursor, '?' placeholders,
# Row-style index+name access) run unchanged against Postgres.
DATABASE_URL = os.environ.get("DATABASE_URL")
try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    psycopg2 = None
USE_PG = bool(DATABASE_URL) and psycopg2 is not None

START_DATE = date(2025, 1, 1)   # day 1 of the game


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pg_sql(sql: str) -> str:
    """Translate the SQLite dialect used in this file to Postgres: '?' params ->
    '%s', and datetime('now') -> now()::text. Our queries contain no literal '%'
    or '?', so the straight replace is safe."""
    return sql.replace("?", "%s").replace("datetime('now')", "now()::text")


class _PGCursor:
    """sqlite3.Cursor-shaped wrapper: translates SQL on execute and yields
    psycopg2 DictRows (which support both row[0] and row['col'], like sqlite3.Row)."""
    def __init__(self, cur):
        self._cur = cur

    def execute(self, sql, params=()):
        self._cur.execute(_pg_sql(sql), params)
        return self

    # Proxy the DB-API cursor attributes the call sites use (sqlite3.Cursor exposes
    # these directly; the admin routes read .description for column names).
    @property
    def description(self):
        return self._cur.description

    @property
    def rowcount(self):
        return self._cur.rowcount

    def fetchone(self):  return self._cur.fetchone()
    def fetchall(self):  return self._cur.fetchall()
    def __iter__(self):  return iter(self._cur)
    def close(self):     self._cur.close()


class _PGConn:
    """Thin adapter so this file's sqlite-style calls (conn.execute / conn.cursor)
    work against Postgres unchanged. Cursors use DictCursor for Row-like access."""
    def __init__(self, raw):
        self._raw = raw

    def execute(self, sql, params=()):
        return self.cursor().execute(sql, params)

    def cursor(self):
        return _PGCursor(self._raw.cursor(cursor_factory=psycopg2.extras.DictCursor))

    def commit(self): self._raw.commit()
    def close(self):  self._raw.close()


def get_conn():
    if USE_PG:
        return _PGConn(psycopg2.connect(DATABASE_URL))
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def puzzle_path(d: date, sport: str = None) -> str:
    """
    Puzzle files are namespaced by sport mode so NFL and NBA puzzles
    for the same date coexist without collision.
    e.g. puzzles/2026-06-17_nfl.json
         puzzles/2026-06-17_nfl-nba.json
    Defaults to server SPORT_MODE when sport is not provided.
    """
    slug = sport_slug(sport or default_sport_mode())
    return os.path.join(PUZZLE_DIR, f"{d.isoformat()}_{slug}.json")


def load_puzzle(d: date, sport: str = None):
    path = puzzle_path(d, sport)
    if not os.path.exists(path):
        # Fallback: try the legacy un-namespaced filename (pre-sport-mode era)
        legacy = os.path.join(PUZZLE_DIR, f"{d.isoformat()}.json")
        if os.path.exists(legacy):
            with open(legacy) as f:
                return json.load(f)
        return None
    with open(path) as f:
        return json.load(f)


def save_puzzle(d: date, chain: list, sport: str = None):
    os.makedirs(PUZZLE_DIR, exist_ok=True)
    with open(puzzle_path(d, sport), "w") as f:
        json.dump(chain, f, indent=2)
    register_slug(d, sport)


def strip_answers(chain: list) -> list:
    """
    Remove valid_players from chain before sending to client.
    Client only gets question labels + answer_count.
    """
    return [
        {
            "left_label":   node["left_label"],
            "right_label":  node["right_label"],
            "answer_count": node["answer_count"],
        }
        for node in chain
    ]


def puzzle_response(d: date, chain: list, sport: str, difficulty: str) -> dict:
    """Build the standard puzzle response dict, including the URL slug."""
    slug = url_slug(d.isoformat(), sport or default_sport_mode())
    return {
        "puzzle_number": puzzle_number(d),
        "date":          d.isoformat(),
        "sport_mode":    sport or default_sport_mode(),
        "difficulty":    difficulty or default_difficulty(),
        "slug":          slug,
        "url":           f"{site_url()}/p/{slug}",
        "coverage":      coverage_string(sport or default_sport_mode()),
        "questions":     strip_answers(chain),
    }


def puzzle_number(d: date) -> int:
    return (d - START_DATE).days + 1


def sport_slug(sport_mode: str) -> str:
    """Convert sport mode string to a safe filename slug."""
    return sport_mode.lower().replace(',', '-').replace(' ', '_')


def url_slug(date_str: str, sport_mode: str) -> str:
    """
    Deterministic 8-char URL slug for a date + sport mode combo.
    SHA-256 of 'date|sport' -> first 6 bytes -> base64url (8 chars, no padding).
    Same inputs always produce the same slug, so links survive regeneration.
    """
    import hashlib, base64
    key = f'{date_str}|{sport_mode.lower()}'.encode()
    digest = hashlib.sha256(key).digest()[:6]
    return base64.urlsafe_b64encode(digest).decode().rstrip('=')


def _resolve_slug_from_files(slug: str):
    """Match a share-link slug to (date, sport_mode) by recomputing url_slug for
    each puzzle file -- the slug is a one-way hash of date|sport_mode, so we match
    rather than reverse it. Filenames are '<date>_<sport-slug>.json'; the sport
    codes carry no '-'/'_', so the slugging is reversible (','->'-', ' '->'_')."""
    try:
        names = os.listdir(PUZZLE_DIR)
    except OSError:
        return None
    for fn in names:
        if not fn.endswith(".json"):
            continue
        stem = fn[:-5]
        if "_" in stem:                      # dates have no '_', so split is clean
            date_str, sslug = stem.split("_", 1)
            sport_mode = sslug.replace("-", ",").replace("_", " ").upper()
        else:                                # legacy un-namespaced file == default sport
            date_str, sport_mode = stem, default_sport_mode()
        if url_slug(date_str, sport_mode) == slug:
            return date_str, sport_mode
    return None


def register_slug(d: date, sport: str):
    """Record (date, sport_mode) -> slug in puzzle_meta so /p/<slug> can resolve it."""
    try:
        conn = get_conn()
        c = conn.cursor()
        sport_key = sport or default_sport_mode()
        slug = url_slug(d.isoformat(), sport_key)
        c.execute("""
            INSERT INTO puzzle_meta (puzzle_date, sport_mode, generated_at, chain_length, url_slug)
            VALUES (?, ?, datetime('now'), 20, ?)
            ON CONFLICT(puzzle_date, sport_mode) DO UPDATE SET
                url_slug = excluded.url_slug
        """, (d.isoformat(), sport_key, slug))
        conn.commit()
        conn.close()
    except Exception as e:
        app.logger.warning(f"register_slug failed: {e}")


def default_sport_mode():
    return os.environ.get("SPORT_MODE", "NFL")

def default_difficulty():
    return os.environ.get("PUZZLE_DIFFICULTY", "medium")

def default_chain_length():
    """Puzzle length. 20 in production; lower it (e.g. PUZZLE_CHAIN_LENGTH=10)
    for an offline demo whose dataset is too small to fill a 20-link chain."""
    try:
        return int(os.environ.get("PUZZLE_CHAIN_LENGTH", "20"))
    except ValueError:
        return 20

def _sport_floor(conn, sport):
    """Earliest year of data we have for a sport (from debut_year, falling back
    to the earliest active decade)."""
    row = conn.execute(
        "SELECT MIN(debut_year) FROM players WHERE sport=? AND debut_year IS NOT NULL AND debut_year>0",
        (sport,)).fetchone()
    yr = row[0] if row and row[0] else None
    if not yr:
        decades = []
        for (ad,) in conn.execute("SELECT active_decades FROM players WHERE sport=?", (sport,)):
            try:
                decades += [int(d[:4]) for d in json.loads(ad or "[]") if d[:4].isdigit()]
            except Exception:
                pass
        yr = min(decades) if decades else None
    return yr


def coverage_string(sport_mode):
    """Short, data-derived coverage caption for the UI ('since 1996',
    'all eras', or 'eras vary by league'). Reflects whatever year range was
    actually loaded, so it stays honest as the data grows."""
    try:
        conn = get_conn()
        if sport_mode == "ALL":
            sports = [r[0] for r in conn.execute("SELECT DISTINCT sport FROM players")]
        else:
            sports = [s.strip().upper() for s in sport_mode.split(",")]
        labels = set()
        for sp in sports:
            yr = _sport_floor(conn, sp)
            if yr:
                labels.add("all eras" if yr <= 1955 else f"since {yr}")
        conn.close()
        if not labels:
            return None
        return labels.pop() if len(labels) == 1 else "eras vary by league"
    except Exception:
        return None


def site_url():
    """Base URL for share links. Set SITE_URL env var in production."""
    return os.environ.get("SITE_URL", "http://localhost:5902").rstrip("/")

def ensure_today_exists(sport=None, difficulty=None):
    """Generate today's puzzle if it doesn't exist yet (for this sport mode)."""
    today = date.today()
    if not os.path.exists(puzzle_path(today, sport)):
        try:
            chain = generate_chain(
                chain_length=default_chain_length(),
                sport_mode=sport or default_sport_mode(),
                difficulty=difficulty or default_difficulty(),
            )
            save_puzzle(today, chain, sport)
            app.logger.info(f"Generated puzzle for {today} sport={sport} difficulty={difficulty}")
        except Exception as e:
            app.logger.error(f"Failed to generate puzzle for {today}: {e}")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/health")
def health():
    return jsonify({
        "status":  "ok",
        "service": "2020-game-api",
        "version": "1.0.0",
    })


@app.route("/api/vintage")
def data_vintage():
    """
    Returns the most recent ETL run timestamps per sport.
    Used by the frontend to display 'Stats as of MM/DD/YYYY'.
    """
    if not HAS_ETL_SCHEMA:
        return jsonify({"vintages": [], "note": "ETL schema not available"})

    try:
        conn = get_conn()
        rows = _get_vintage(conn, "ALL")
        conn.close()
    except Exception as e:
        app.logger.warning(f"vintage query failed: {e}")
        return jsonify({"vintages": [], "note": "vintage unavailable"})

    vintages = []
    for row in rows:
        run_at = row["run_at"]
        # Format as MM/DD/YYYY for display
        try:
            dt = datetime.fromisoformat(run_at.replace("Z", "+00:00"))
            display = dt.strftime("%-m/%-d/%Y")
        except Exception:
            display = run_at[:10]
        vintages.append({
            "sport":      row["sport"],
            "run_at_iso": run_at,
            "display":    display,
            "total":      row["total"],
        })

    # Also derive an overall "as of" date (oldest ETL run across all sports)
    as_of = None
    if vintages:
        oldest = min(v["run_at_iso"] for v in vintages)
        try:
            dt = datetime.fromisoformat(oldest.replace("Z", "+00:00"))
            as_of = dt.strftime("%-m/%-d/%Y")
        except Exception:
            as_of = oldest[:10]

    return jsonify({"vintages": vintages, "as_of": as_of})


@app.route("/api/puzzle/today")
def puzzle_today():
    # ?sport= and ?difficulty= let the frontend request a specific mode.
    # Falls back to server env vars if not provided.
    sport      = request.args.get("sport",      default_sport_mode())
    difficulty = request.args.get("difficulty", default_difficulty())

    ensure_today_exists(sport=sport, difficulty=difficulty)
    today = date.today()
    chain = load_puzzle(today, sport)
    if not chain:
        return jsonify({"error": "Puzzle not available", "code": "NO_PUZZLE"}), 503

    return jsonify(puzzle_response(today, chain, sport, difficulty))


@app.route("/api/puzzle/<string:date_str>")
def puzzle_by_date(date_str):
    sport      = request.args.get("sport",      default_sport_mode())
    difficulty = request.args.get("difficulty", default_difficulty())

    try:
        d = date.fromisoformat(date_str)
    except ValueError:
        return jsonify({"error": "Invalid date format. Use YYYY-MM-DD", "code": "BAD_DATE"}), 400

    if d > date.today():
        return jsonify({"error": "Future puzzles not available", "code": "FUTURE_DATE"}), 403

    chain = load_puzzle(d, sport)
    if not chain:
        # Try to generate if it's recent enough
        if (date.today() - d).days <= 7:
            try:
                chain = generate_chain(
                    chain_length=default_chain_length(),
                    sport_mode=sport,
                    difficulty=difficulty,
                )
                save_puzzle(d, chain, sport)
            except Exception as e:
                return jsonify({"error": "Puzzle not available", "code": "NO_PUZZLE"}), 503
        else:
            return jsonify({"error": "Puzzle not found", "code": "NOT_FOUND"}), 404

    return jsonify(puzzle_response(d, chain, sport, difficulty))


@app.route("/api/validate", methods=["POST"])
def validate():
    """
    Body: { "date": "2025-01-15", "question_index": 3, "player": "Tom Brady" }
    Returns: { "correct": true/false, "used_in_chain": true/false }
    """
    body = request.get_json(silent=True) or {}
    date_str = body.get("date")
    q_index  = body.get("question_index")
    player   = (body.get("player") or "").strip()

    # Input validation
    if not date_str or q_index is None or not player:
        return jsonify({"error": "Missing required fields: date, question_index, player", "code": "BAD_REQUEST"}), 400

    try:
        d = date.fromisoformat(date_str)
    except ValueError:
        return jsonify({"error": "Invalid date", "code": "BAD_DATE"}), 400

    if not isinstance(q_index, int) or q_index < 0 or q_index >= 20:
        return jsonify({"error": "question_index must be 0-19", "code": "BAD_INDEX"}), 400

    # Load chain (with answers)
    sport = body.get("sport", default_sport_mode())
    chain = load_puzzle(d, sport)
    if not chain:
        return jsonify({"error": "Puzzle not found", "code": "NOT_FOUND"}), 404

    node = chain[q_index]

    # Normalize comparison (case-insensitive, strip punctuation)
    def norm(s):
        import re
        return re.sub(r"[^a-z0-9]", "", s.lower())

    valid_normalized = {norm(p): p for p in node["valid_players"]}
    canonical = valid_normalized.get(norm(player))

    correct = canonical is not None

    # Also tell the client the canonical spelling so they can display it cleanly
    return jsonify({
        "correct":          correct,
        "canonical_name":   canonical if correct else None,
        "question_index":   q_index,
    })


def _safe_json_list(s):
    """Parse a JSON array column (active_decades / teams) into a list, safely."""
    try:
        v = json.loads(s or "[]")
        return v if isinstance(v, list) else []
    except Exception:
        return []


def _player_hint_facts(row) -> list:
    """
    Build an ordered list of attribute 'hints' about a player, vague -> specific.
    Each fact: {"label": ..., "text": ...}. Only facts backed by real data are
    included. Deliberately never includes the player's name.
    """
    facts = []

    # 1. Era -- broad. Prefer the decade span, fall back to debut year.
    decades = _safe_json_list(row["active_decades"])
    if decades:
        if len(decades) == 1:
            facts.append({"label": "Era", "text": f"Active in the {decades[0]}"})
        else:
            facts.append({"label": "Era", "text": f"Active from the {decades[0]} to the {decades[-1]}"})
    elif row["debut_year"]:
        facts.append({"label": "Era", "text": f"Debuted in {row['debut_year']}"})

    # 2. Position
    pos = row["position"] or row["position_group"]
    if pos:
        facts.append({"label": "Position", "text": f"Played {pos}"})

    # 3. Origin -- mild. State for USA players, otherwise country.
    bp = None
    if row["birth_state"] and (row["birth_country"] in (None, "", "USA")):
        bp = row["birth_state"]
    elif row["birth_country"] and row["birth_country"] != "USA":
        bp = row["birth_country"]
    if bp:
        facts.append({"label": "Origin", "text": f"Born in {bp}"})

    # 4. College
    if row["college"]:
        facts.append({"label": "College", "text": f"Played college at {row['college']}"})

    # 5. Teams
    teams = _safe_json_list(row["teams"])
    if teams:
        if len(teams) == 1:
            t = f"the {teams[0]}"
        elif len(teams) == 2:
            t = f"the {teams[0]} and {teams[1]}"
        else:
            t = f"the {teams[0]}, {teams[1]} and others"
        facts.append({"label": "Team", "text": f"Suited up for {t}"})

    # 6. Draft -- most specific / most identifying, so it comes last.
    if row["draft_year"]:
        bits = f"Drafted in {row['draft_year']}"
        rp = []
        if row["draft_round"]:
            rp.append(f"Round {row['draft_round']}")
        if row["draft_pick"]:
            rp.append(f"Pick {row['draft_pick']}")
        if rp:
            bits += f" ({', '.join(rp)})"
        if row["draft_team"]:
            bits += f" by the {row['draft_team']}"
        facts.append({"label": "Draft", "text": bits})

    return facts


@app.route("/api/hint", methods=["POST"])
def hint():
    """
    Progressive 'attribute hint' help. Reveals ONE biographical attribute at a
    time about a single valid answer for a question -- never the player's name.

    Body: { "date": "2025-01-15", "question_index": 3, "sport": "NFL", "revealed": 1 }
      revealed = how many hints the client already holds (default 0); the server
      returns the next one (index == revealed).
    Returns: { available, exhausted, hint:{label,text}, index, total, remaining }
    """
    body = request.get_json(silent=True) or {}
    date_str = body.get("date")
    q_index  = body.get("question_index")
    revealed = body.get("revealed", 0)

    if not date_str or q_index is None:
        return jsonify({"error": "Missing required fields: date, question_index", "code": "BAD_REQUEST"}), 400
    try:
        d = date.fromisoformat(date_str)
    except ValueError:
        return jsonify({"error": "Invalid date", "code": "BAD_DATE"}), 400
    if not isinstance(q_index, int) or q_index < 0 or q_index >= 20:
        return jsonify({"error": "question_index must be 0-19", "code": "BAD_INDEX"}), 400
    try:
        revealed = max(0, int(revealed))
    except (TypeError, ValueError):
        revealed = 0

    sport = body.get("sport", default_sport_mode())
    chain = load_puzzle(d, sport)
    if not chain:
        return jsonify({"error": "Puzzle not found", "code": "NOT_FOUND"}), 404

    node  = chain[q_index]
    valid = node.get("valid_players", [])
    if not valid:
        return jsonify({"available": False, "reason": "no_answers", "question_index": q_index})

    # Resolve valid answers to DB rows so we can describe their attributes.
    # valid_players are stored as canonical DB names, so an IN-match is exact.
    conn = get_conn()
    placeholders = ",".join("?" * len(valid))
    rows = conn.execute(
        f"SELECT * FROM players WHERE name IN ({placeholders})", valid
    ).fetchall()
    conn.close()

    # One fact-set per name; if a name maps to several rows (cross-sport), keep
    # the richest. Drop anyone we can't say anything about.
    best = {}
    for r in rows:
        f = _player_hint_facts(r)
        if f and (r["name"] not in best or len(f) > len(best[r["name"]])):
            best[r["name"]] = f
    if not best:
        return jsonify({"available": False, "reason": "no_data", "question_index": q_index})

    # Pick a single representative answer, deterministically and stably so the
    # progressive hints always describe the SAME player. Prefer the richest
    # fact-set (most help), breaking ties with a stable hash of the question.
    import hashlib
    max_facts = max(len(f) for f in best.values())
    pool = sorted(name for name, f in best.items() if len(f) == max_facts)
    key  = f"{date_str}|{sport.lower()}|{q_index}".encode()
    pick = int(hashlib.sha256(key).hexdigest(), 16) % len(pool)
    facts = best[pool[pick]]

    total = len(facts)
    if revealed >= total:
        return jsonify({
            "available": True, "exhausted": True,
            "question_index": q_index, "total": total,
        })

    return jsonify({
        "available":      True,
        "exhausted":      False,
        "question_index": q_index,
        "hint":           facts[revealed],
        "index":          revealed,
        "total":          total,
        "remaining":      total - revealed - 1,
    })


@app.route("/api/validate/player", methods=["POST"])
def validate_player_exists():
    """
    Check if a player name exists in the DB at all (for autocomplete validation).
    Body: { "player": "Tom Brady" }
    Does NOT reveal which questions they're valid for.
    """
    body = request.get_json(silent=True) or {}
    player = (body.get("player") or "").strip()
    if not player:
        return jsonify({"error": "Missing player", "code": "BAD_REQUEST"}), 400

    conn = get_conn()
    c = conn.cursor()

    import re
    def norm(s):
        return re.sub(r"[^a-z0-9]", "", s.lower())

    # Fetch all player names and do normalized comparison
    c.execute("SELECT name FROM players")
    rows = c.fetchall()
    conn.close()

    match = next((r[0] for r in rows if norm(r[0]) == norm(player)), None)
    return jsonify({
        "exists":         match is not None,
        "canonical_name": match,
    })


@app.route("/api/players/search")
def search_players():
    """
    Autocomplete endpoint. Query param: q=tom
    Returns list of matching player names (no stat info).
    """
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify({"players": []})

    # Scope suggestions to the current sport mode so an NFL puzzle doesn't
    # suggest, say, an NHL player. DISTINCT also dedupes a name that exists
    # in two sports (e.g. a college + pro row).
    sport = request.args.get("sport", "")
    conn = get_conn()
    c = conn.cursor()
    if sport and sport.upper() != "ALL":
        sports = [s.strip().upper() for s in sport.split(",")]
        ph = ",".join("?" * len(sports))
        c.execute(
            f"SELECT DISTINCT name FROM players WHERE sport IN ({ph}) "
            f"AND LOWER(name) LIKE ? ORDER BY name LIMIT 10",
            (*sports, f"%{q.lower()}%"))
    else:
        c.execute("SELECT DISTINCT name FROM players WHERE LOWER(name) LIKE ? ORDER BY name LIMIT 10",
                  (f"%{q.lower()}%",))
    names = [r[0] for r in c.fetchall()]
    conn.close()
    return jsonify({"players": names})


@app.route("/api/generate", methods=["POST"])
def generate():
    """
    Admin endpoint: generate a puzzle for a given date.
    Body: { "date": "2025-02-01", "secret": "admin_secret" }
    """
    body = request.get_json(silent=True) or {}
    secret = body.get("secret")
    if secret != os.environ.get("ADMIN_SECRET", "dev_secret"):
        return jsonify({"error": "Unauthorized", "code": "UNAUTHORIZED"}), 401

    date_str = body.get("date", date.today().isoformat())
    try:
        d = date.fromisoformat(date_str)
    except ValueError:
        return jsonify({"error": "Invalid date", "code": "BAD_DATE"}), 400

    sport_mode = body.get("sport", default_sport_mode())
    difficulty  = body.get("difficulty", default_difficulty())
    chain = generate_chain(chain_length=default_chain_length(), sport_mode=sport_mode, difficulty=difficulty)
    save_puzzle(d, chain, sport_mode)

    return jsonify({
        "generated":  True,
        "date":       d.isoformat(),
        "sport_mode": sport_mode,
        "difficulty":  difficulty,
        "questions":  strip_answers(chain),
    })


@app.route("/api/generate/batch", methods=["POST"])
def generate_batch():
    """
    Pre-generate puzzles for the next N days.
    Body: { "days": 7, "secret": "admin_secret" }
    """
    body = request.get_json(silent=True) or {}
    secret = body.get("secret")
    if secret != os.environ.get("ADMIN_SECRET", "dev_secret"):
        return jsonify({"error": "Unauthorized", "code": "UNAUTHORIZED"}), 401

    days = min(int(body.get("days", 7)), 30)
    results = []

    for i in range(days):
        d = date.today() + timedelta(days=i)
        sport_mode = body.get("sport", default_sport_mode())
        difficulty  = body.get("difficulty", default_difficulty())
        if os.path.exists(puzzle_path(d, sport_mode)):
            results.append({"date": d.isoformat(), "status": "already_exists"})
            continue
        try:
            chain = generate_chain(chain_length=default_chain_length(), sport_mode=sport_mode, difficulty=difficulty)
            save_puzzle(d, chain, sport_mode)
            results.append({"date": d.isoformat(), "status": "generated",
                            "sport_mode": sport_mode, "difficulty": difficulty})
        except Exception as e:
            results.append({"date": d.isoformat(), "status": "error", "error": str(e)})

    return jsonify({"results": results})


@app.route("/api/config")
def config():
    """
    Public client configuration. Exposes non-secret env vars the frontend needs.
    Called once on boot; responses can be cached aggressively.
    """
    return jsonify({
        "site_url":   site_url(),
        "sport_mode": default_sport_mode(),
        "difficulty": default_difficulty(),
    })


@app.route("/p/<string:slug>")
def puzzle_by_slug(slug):
    """
    Resolve a short slug to a puzzle.
    GET /p/u81rd6rT  ->  same response as /api/puzzle/2026-06-17?sport=NFL

    Two resolution paths: puzzle_meta (for app-registered slugs) first, then a
    match against the puzzle files -- offline-generated puzzles never populate
    puzzle_meta, so the file fallback is what makes their share links resolve.
    """
    puzzle_date = sport_mode = None

    # 1) app-registered slugs (puzzle_meta) -- fast path, kept for back-compat
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT puzzle_date, sport_mode FROM puzzle_meta WHERE url_slug = ?", (slug,))
        row = c.fetchone()
        conn.close()
        if row:
            puzzle_date, sport_mode = row["puzzle_date"], row["sport_mode"]
    except Exception as e:
        app.logger.warning(f"puzzle_meta slug lookup failed (falling back to files): {e}")

    # 2) fall back to matching the slug against the puzzle files
    if not puzzle_date:
        resolved = _resolve_slug_from_files(slug)
        if resolved:
            puzzle_date, sport_mode = resolved

    if not puzzle_date:
        return jsonify({"error": "Puzzle not found", "code": "NOT_FOUND"}), 404

    try:
        d = date.fromisoformat(puzzle_date)
    except ValueError:
        return jsonify({"error": "Invalid stored date", "code": "INTERNAL"}), 500

    chain = load_puzzle(d, sport_mode)
    if not chain:
        return jsonify({"error": "Puzzle file missing", "code": "NOT_FOUND"}), 404

    return jsonify(puzzle_response(d, chain, sport_mode, default_difficulty()))


# ---------------------------------------------------------------------------
# Admin: category/answer audit + editable player records (gated by ADMIN_PW)
# ---------------------------------------------------------------------------
SCOPE_ORDER = ["NFL", "NBA", "MLB", "NHL", "WNBA", "NCAAF", "NCAAB", "NCAAW", "ALL"]
ADMIN_EDIT_EXCLUDE = {"id", "sr_id", "data_source", "last_updated", "etl_run_id"}
ADMIN_JSON_LIST = {"teams", "active_decades"}      # stored as JSON arrays, edited as comma lists
# Audit-relevant fields first (college transfers etc.), then everything else.
ADMIN_FIELD_PRIORITY = [
    "name", "sport", "position", "position_group", "college", "high_school",
    "birth_year", "birth_date", "birth_city", "birth_state", "birth_country",
    "height_inches", "weight_lbs", "draft_year", "draft_round", "draft_pick",
    "draft_team", "debut_year", "final_year", "active_decades", "teams",
]

_ADMIN_NUMERIC = None       # (int_cols, real_cols), computed once per process


def _admin_numeric_cols(conn):
    """Which players columns are int vs real, so admin edits write the right Python
    type (Postgres won't implicitly cast a text param into a numeric column)."""
    global _ADMIN_NUMERIC
    if _ADMIN_NUMERIC is not None:
        return _ADMIN_NUMERIC
    ints, reals = set(), set()
    if USE_PG:
        cur = conn.execute("SELECT column_name, data_type FROM information_schema.columns "
                           "WHERE table_name='players'")
        for r in cur.fetchall():
            name, t = r[0], r[1]
            if t in ("integer", "bigint", "smallint"):
                ints.add(name)
            elif t in ("real", "double precision", "numeric"):
                reals.add(name)
    else:
        cur = conn.execute("PRAGMA table_info(players)")
        for r in cur.fetchall():
            name, t = r[1], (r[2] or "").upper()
            if "INT" in t:
                ints.add(name)
            elif any(k in t for k in ("REAL", "FLOA", "DOUB", "NUM")):
                reals.add(name)
    _ADMIN_NUMERIC = (ints, reals)
    return _ADMIN_NUMERIC


def rederive_player_categories(conn, pid):
    """Recompute one player's category memberships after an edit, using the pure
    rule fns from load.py. DELETE-then-INSERT (no upsert) so it's portable across
    SQLite and Postgres."""
    cur = conn.execute("SELECT * FROM players WHERE id=?", (pid,))
    r = cur.fetchone()
    if not r:
        return
    cols = [d[0] for d in cur.description]
    player = {c: r[c] for c in cols}
    for f in ("teams", "active_decades"):
        try:
            player[f] = json.loads(player[f]) if player[f] else []
        except Exception:
            player[f] = []
    sport = player.get("sport")
    conn.execute("DELETE FROM player_categories WHERE player_id=?", (pid,))
    for cat in _get_categories():
        scope = cat["scope"]
        if scope != "ALL" and scope != sport:
            continue
        if _player_matches(player, cat):
            conn.execute("INSERT INTO player_categories (player_id, category_id) VALUES (?,?)",
                         (pid, cat["id"]))
    conn.commit()


def _scope_rank(s):
    return SCOPE_ORDER.index(s) if s in SCOPE_ORDER else len(SCOPE_ORDER)


def _lastname_key(name):
    """Sort key: last name ascending, then full name as a tiebreak."""
    parts = (name or "").split()
    return (parts[-1].lower() if parts else (name or "").lower(), (name or "").lower())


_ADMIN_CSS = """
 body{font:14px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;margin:0;background:#0f1115;color:#e6e6e6}
 header{background:#171a21;padding:12px 20px;border-bottom:1px solid #2a2f3a;display:flex;align-items:center;gap:16px;position:sticky;top:0;z-index:5}
 header h1{font-size:16px;margin:0;font-weight:600}
 a{color:#7aa2ff;text-decoration:none}
 .wrap{max-width:1100px;margin:0 auto;padding:20px}
 .scope{margin:26px 0 8px;font-size:13px;letter-spacing:.08em;text-transform:uppercase;color:#8b93a7;border-bottom:1px solid #2a2f3a;padding-bottom:6px}
 details{background:#171a21;border:1px solid #2a2f3a;border-radius:8px;margin:8px 0;padding:6px 12px}
 summary{cursor:pointer;font-weight:600;outline:none}
 summary .meta{color:#8b93a7;font-weight:400}
 .sport-grp{margin:8px 0 4px}
 .sport-grp b{display:inline-block;min-width:64px;color:#cbb26b}
 .ans a{display:inline-block;margin:2px 10px 2px 0;white-space:nowrap}
 .empty{color:#6b7280;font-style:italic}
 input[type=text],input[type=password]{width:100%;box-sizing:border-box;background:#0f1115;border:1px solid #2a2f3a;color:#e6e6e6;border-radius:6px;padding:7px 9px;font:inherit}
 label.f{display:block;margin:10px 0 2px;color:#8b93a7;font-size:12px}
 .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:0 18px}
 .save{background:#2a6df4;color:#fff;border:0;border-radius:6px;padding:10px 18px;font:inherit;font-weight:600;cursor:pointer;margin-top:18px}
 .banner{background:#16361f;border:1px solid #2e6b3e;color:#9fe0b0;padding:8px 12px;border-radius:6px;margin-bottom:14px}
 .hint{color:#6b7280;font-size:11px}
"""

_FILTER_SCRIPT = """
<script>
var f=document.getElementById('filter');
if(f){f.addEventListener('input',function(){
  var q=this.value.toLowerCase();
  document.querySelectorAll('details[data-label]').forEach(function(d){
    d.style.display=d.getAttribute('data-label').indexOf(q)>-1?'':'none';});
  document.querySelectorAll('.scope').forEach(function(s){s.style.display=q?'none':'';});
});}
</script>
"""


def _admin_shell(title, body, nav=True):
    header = ('<header><h1>20/20 Admin</h1>'
              '<a href="/admin">Categories</a><a href="/admin/logout">Log out</a></header>'
              if nav else '<header><h1>20/20 Admin</h1></header>')
    return (f'<!doctype html><html><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{escape(title)}</title><style>{_ADMIN_CSS}</style></head><body>'
            f'{header}<div class="wrap">{body}</div></body></html>')


def render_admin_login(error):
    err = (f'<div class="banner" style="background:#3a1620;border-color:#6b2e3a;color:#e0a0ac">'
           f'{escape(error)}</div>') if error else ""
    body = (f'<h2>Admin Login</h2>{err}'
            f'<form method="post" action="/admin/login" style="max-width:320px">'
            f'<label class="f" for="password">Password</label>'
            f'<input type="password" id="password" name="password" autofocus>'
            f'<button class="save" type="submit">Enter</button></form>')
    return _admin_shell("Admin Login", body, nav=False)


def render_admin_index(by_scope, members):
    total = sum(len(v) for v in by_scope.values())
    parts = [f'<p class="hint">{total} categories. Click one to expand its answers '
             f'(grouped by sport, last name A→Z). Each player links to an editable record.</p>',
             '<input id="filter" type="text" placeholder="Filter categories…">']
    for scope in [s for s in SCOPE_ORDER if s in by_scope] + \
                 [s for s in by_scope if s not in SCOPE_ORDER]:
        parts.append(f'<div class="scope">{escape(scope)}</div>')
        for cat in by_scope[scope]:
            mem = members.get(cat["id"], [])
            data_label = escape((cat["label"] + " " + scope).lower())
            parts.append(f'<details data-label="{data_label}">'
                         f'<summary>{escape(cat["label"])} '
                         f'<span class="meta">· {escape(scope)} · {len(mem)} answers</span></summary>')
            if not mem:
                parts.append('<div class="empty">(no answers)</div>')
            else:
                bysport = {}
                for name, sp, pid in mem:
                    bysport.setdefault(sp, []).append((name, pid))
                for sp in sorted(bysport, key=_scope_rank):
                    links = sorted(bysport[sp], key=lambda t: _lastname_key(t[0]))
                    anchors = " ".join(f'<a href="/admin/player/{pid}">{escape(name)}</a>'
                                       for name, pid in links)
                    parts.append(f'<div class="sport-grp"><b>{escape(sp or "?")}</b> '
                                 f'<span class="ans">{anchors}</span></div>')
            parts.append('</details>')
    return "".join(parts) + _FILTER_SCRIPT


def render_player_form(pid, cols, valmap, saved):
    editable = [c for c in cols if c not in ADMIN_EDIT_EXCLUDE]
    ordered = [c for c in ADMIN_FIELD_PRIORITY if c in editable] + \
              [c for c in editable if c not in ADMIN_FIELD_PRIORITY]
    name = valmap.get("name") or f"player {pid}"
    parts = ['<p><a href="/admin">← all categories</a></p>']
    if saved:
        parts.append('<div class="banner">Saved ✓ — categories re-derived for this player.</div>')
    parts.append(f'<h2 style="margin:6px 0">{escape(name)} '
                 f'<span class="hint">#{pid} · {escape(valmap.get("sport") or "")} · '
                 f'sr_id {escape(valmap.get("sr_id") or "—")}</span></h2>')
    parts.append(f'<form method="post" action="/admin/player/{pid}"><div class="grid">')
    for col in ordered:
        v = valmap.get(col)
        if col in ADMIN_JSON_LIST:
            try:
                lst = json.loads(v) if v else []
            except Exception:
                lst = []
            disp, hint = ", ".join(str(x) for x in lst), ' <span class="hint">(comma-separated)</span>'
        else:
            disp, hint = ("" if v is None else str(v)), ""
        parts.append(f'<div><label class="f" for="{escape(col)}">{escape(col)}{hint}</label>'
                     f'<input type="text" id="{escape(col)}" name="{escape(col)}" '
                     f'value="{escape(disp)}"></div>')
    parts.append('</div><button class="save" type="submit">Save changes</button></form>')
    return _admin_shell(f"Edit · {name}", "".join(parts))


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if not ADMIN_PW:
        return Response("Admin is disabled (set the ADMIN_PW env var).", 503)
    error = ""
    if request.method == "POST":
        if hmac.compare_digest((request.form.get("password") or ""), ADMIN_PW):
            session["admin_ok"] = True
            return redirect("/admin")
        error = "Incorrect password."
    return Response(render_admin_login(error), mimetype="text/html")


@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_ok", None)
    return redirect("/admin/login")


@app.route("/admin")
@require_admin
def admin_index():
    conn = get_conn()
    cats = conn.execute(
        "SELECT id, label, sport_scope FROM categories ORDER BY sport_scope, label").fetchall()
    rows = conn.execute(
        "SELECT pc.category_id AS cid, p.id AS pid, p.name AS name, p.sport AS sport "
        "FROM player_categories pc JOIN players p ON p.id = pc.player_id").fetchall()
    conn.close()

    members = {}
    for r in rows:
        members.setdefault(r["cid"], []).append((r["name"], r["sport"], r["pid"]))
    by_scope = {}
    for cat in cats:
        by_scope.setdefault(cat["sport_scope"], []).append(cat)

    return Response(_admin_shell("Categories", render_admin_index(by_scope, members)),
                    mimetype="text/html")


@app.route("/admin/player/<int:pid>", methods=["GET", "POST"])
@require_admin
def admin_player(pid):
    conn = get_conn()
    cur = conn.execute("SELECT * FROM players WHERE id=?", (pid,))
    row = cur.fetchone()
    if not row:
        conn.close()
        abort(404)
    cols = [d[0] for d in cur.description]

    if request.method == "POST":
        int_cols, real_cols = _admin_numeric_cols(conn)
        updates = {}
        for col in cols:
            if col in ADMIN_EDIT_EXCLUDE or col not in request.form:
                continue
            raw = (request.form.get(col) or "").strip()
            if col in ADMIN_JSON_LIST:
                updates[col] = json.dumps([x.strip() for x in raw.split(",") if x.strip()])
            elif raw == "":
                updates[col] = None
            elif col in int_cols:
                try:
                    updates[col] = int(float(raw))
                except ValueError:
                    updates[col] = None
            elif col in real_cols:
                try:
                    updates[col] = float(raw)
                except ValueError:
                    updates[col] = None
            else:
                updates[col] = raw
        if updates:
            sets = ", ".join(f"{c}=?" for c in updates)
            conn.execute(f"UPDATE players SET {sets} WHERE id=?", [*updates.values(), pid])
            conn.commit()
            if HAS_CATEGORY_RULES:
                try:
                    rederive_player_categories(conn, pid)
                except Exception as e:
                    app.logger.warning(f"category rederive failed for player {pid}: {e}")
        conn.close()
        return redirect(f"/admin/player/{pid}?saved=1")

    valmap = {c: row[c] for c in cols}
    conn.close()
    return Response(render_player_form(pid, cols, valmap, saved=bool(request.args.get("saved"))),
                    mimetype="text/html")


# Serve the frontend for any non-API route
@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_frontend(path):
    static_dir = os.path.join(BASE_DIR, "static")
    if path and os.path.exists(os.path.join(static_dir, path)):
        return send_from_directory(static_dir, path)
    return send_from_directory(static_dir, "index.html")


if __name__ == "__main__":
    ensure_today_exists()
    app.run(host="0.0.0.0", port=5902, debug=True, use_reloader=False)

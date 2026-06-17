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
import sqlite3
from datetime import date, datetime, timedelta
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

# Allow imports from parent dir
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from engine.generator import generate_chain
try:
    from data.etl.schema import get_data_vintage as _get_vintage
    HAS_ETL_SCHEMA = True
except ImportError:
    HAS_ETL_SCHEMA = False

app = Flask(__name__, static_folder="../static")
CORS(app)

BASE_DIR   = os.path.dirname(os.path.dirname(__file__))
DB_PATH    = os.path.join(BASE_DIR, "data", "nfl.db")
PUZZLE_DIR = os.path.join(BASE_DIR, "puzzles")

START_DATE = date(2025, 1, 1)   # day 1 of the game


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_conn():
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

def site_url():
    """Base URL for share links. Set SITE_URL env var in production."""
    return os.environ.get("SITE_URL", "http://localhost:5902").rstrip("/")

def ensure_today_exists(sport=None, difficulty=None):
    """Generate today's puzzle if it doesn't exist yet (for this sport mode)."""
    today = date.today()
    if not os.path.exists(puzzle_path(today, sport)):
        try:
            chain = generate_chain(
                chain_length=20,
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

    conn = get_conn()
    rows = _get_vintage(conn, "ALL")
    conn.close()

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
                    chain_length=20,
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

    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT name FROM players WHERE LOWER(name) LIKE ? ORDER BY name LIMIT 10",
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
    chain = generate_chain(chain_length=20, sport_mode=sport_mode, difficulty=difficulty)
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
            chain = generate_chain(chain_length=20, sport_mode=sport_mode, difficulty=difficulty)
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
    Redirects to the standard puzzle JSON so the frontend can boot with it.
    """
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute(
            "SELECT puzzle_date, sport_mode FROM puzzle_meta WHERE url_slug = ?",
            (slug,)
        )
        row = c.fetchone()
        conn.close()
    except Exception as e:
        return jsonify({"error": "Database error", "code": "DB_ERROR"}), 500

    if not row:
        return jsonify({"error": "Puzzle not found", "code": "NOT_FOUND"}), 404

    puzzle_date, sport_mode = row["puzzle_date"], row["sport_mode"]

    try:
        d = date.fromisoformat(puzzle_date)
    except ValueError:
        return jsonify({"error": "Invalid stored date", "code": "INTERNAL"}), 500

    chain = load_puzzle(d, sport_mode)
    if not chain:
        return jsonify({"error": "Puzzle file missing", "code": "NOT_FOUND"}), 404

    difficulty = default_difficulty()
    return jsonify(puzzle_response(d, chain, sport_mode, difficulty))


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

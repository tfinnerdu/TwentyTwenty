#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# refresh_data.sh -- full LOCAL, cache-only rebuild of the game DB, then ship.
# Re-ingests every sport from data/cache, applies the team/school standardizers
# and the data fixes, rebuilds all puzzles, and pushes. This is the heavier
# "after a data fix" job; refresh_puzzles.sh is the lighter daily puzzles-only one.
#
# All DATA steps are offline (no network) -- they read data/cache. The only
# networked step is the optional Postgres push at the very end (commented out).
#
#   ./refresh_data.sh                 # rebuild data/nfl.db + puzzles, push puzzles
#   ./refresh_data.sh data/nfl.db     # same, explicit DB
# ---------------------------------------------------------------------------
set -euo pipefail
cd "$(dirname "$0")"
[ -f .venv/bin/activate ] && source .venv/bin/activate
DB="${1:-data/nfl.db}"

git pull --quiet || true

# 1. Re-ingest the game DB from the cached exports. The franchise/school maps in
#    the providers apply here, so previously-dropped teams come back and schools
#    converge on one spelling. Watch the log for any "UNMAPPED team code(s)".
python run_full.py --skip-puzzles --db "$DB"

# 2. NFL bio + standardized (franchise-href) teams from the cached SR pages.
python -m data.etl.backfill_from_cache --sport NFL --rebuild-teams --db "$DB"

# 3. Correct bulk-boundary debut years from the cached alpha index (the Malone/
#    Iverson 1999 fix). Rides along right after the re-ingest, cache-only.
python -m data.etl.reconcile_debut --sport ALL --db "$DB"

# 4. Audit -- prints per-sport cleanliness across all 8 entities. Informational
#    (never fails the run); eyeball it for anything non-canonical.
python -m data.etl.teams --audit --sport ALL --db "$DB" || true

# 5. Nuke + rebuild every puzzle from the corrected DB.
python rebuild_puzzles.py --db "$DB"

# 6. Ship puzzles via git (Render serves the committed JSON). The SQLite DB is
#    gitignored on purpose -- prod reads Postgres (step 7), not the file.
git add puzzles
if git diff --cached --quiet; then
    echo "No puzzle changes to push."
else
    git commit -m "data: cache refresh + puzzle rebuild $(date +%F)"
    git push
fi

# 7. (optional deploy) push the corrected player DB to Postgres. Uses
#    POSTGRES_DB_URL / DATABASE_URL from .env. Uncomment to deploy player data:
# python load_to_postgres.py --sqlite "$DB"

# db_all gets the same debut fix on its own crawl:
#   python -m data.etl.reconcile_debut --sport ALL --db data/db_all.db

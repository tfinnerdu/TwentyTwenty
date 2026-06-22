#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# refresh_puzzles.sh -- generate the next N days of puzzles locally and push
# them so Render redeploys with them. macOS / Linux / WSL counterpart to
# refresh_puzzles.bat. Schedule with cron, e.g.:
#
#   0 3 * * * /path/to/TwentyTwenty/refresh_puzzles.sh >> /tmp/tt_puzzles.log 2>&1
#
# Delivery is git: the deployed app serves the committed puzzle JSON, so a push
# triggers a Render deploy. (Remove the puzzles/ disk from render.yaml first -- a
# mounted disk hides the committed files. Puzzles are regenerable, so nothing is
# lost.)
# ---------------------------------------------------------------------------
set -euo pipefail
cd "$(dirname "$0")"
[ -f .venv/bin/activate ] && source .venv/bin/activate

git pull --quiet
# --puzzles-only: regenerate from the existing local DB (no data re-pull).
# Swap for `python generate_puzzles.py --sport NFL --days 60` if you only serve NFL.
python run_full.py --puzzles-only --days 60

git add puzzles
if git diff --cached --quiet; then
    echo "No new puzzles to push."
else
    git commit -m "puzzles: auto-refresh $(date +%F)"
    git push
fi

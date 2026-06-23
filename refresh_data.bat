@echo off
REM ---------------------------------------------------------------------------
REM refresh_data.bat -- full LOCAL, cache-only rebuild of the game DB, then ship.
REM Re-ingests every sport from data\cache, applies the team/school standardizers
REM and the data fixes, rebuilds all puzzles, and pushes. The heavier "after a
REM data fix" job; refresh_puzzles.bat is the lighter daily puzzles-only one.
REM
REM All DATA steps are offline (read data\cache). The only networked step is the
REM optional Postgres push at the end (commented out). Schedule example:
REM
REM   schtasks /Create /TN "TwentyTwenty Data" ^
REM     /TR "C:\doane\Personal\TwentyTwenty\refresh_data.bat" /SC WEEKLY /D SUN /ST 02:00
REM ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"
call .venv\Scripts\activate.bat 2>nul
set DB=data\nfl.db
if not "%~1"=="" set DB=%~1

git pull --quiet

REM 1. Re-ingest the game DB from cached exports (franchise/school maps apply).
python run_full.py --skip-puzzles --db "%DB%" || exit /b 1
REM 2. NFL bio + standardized teams from the cached SR pages.
python -m data.etl.backfill_from_cache --sport NFL --rebuild-teams --db "%DB%" || exit /b 1
REM 3. Correct bulk-boundary debut years from the cached index (Malone/Iverson 1999).
python -m data.etl.reconcile_debut --sport ALL --db "%DB%" || exit /b 1
REM 4. Audit -- per-sport cleanliness; informational, never fatal.
python -m data.etl.teams --audit --sport ALL --db "%DB%"
REM 5. Nuke + rebuild every puzzle from the corrected DB.
python rebuild_puzzles.py --db "%DB%" || exit /b 1

REM 6. Ship puzzles via git (Render serves the committed JSON). The SQLite DB is
REM    gitignored -- prod reads Postgres (step 7), not the file.
git add puzzles
git diff --cached --quiet
if %errorlevel%==0 (
    echo No puzzle changes to push.
) else (
    git commit -m "data: cache refresh + puzzle rebuild %date%"
    git pull --no-edit
    git push
)

REM 7. (optional deploy) push the corrected player DB to Postgres
REM    (POSTGRES_DB_URL / DATABASE_URL from .env). Uncomment to deploy player data:
REM python load_to_postgres.py --sqlite "%DB%"
endlocal

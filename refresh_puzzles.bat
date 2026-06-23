@echo off
REM ---------------------------------------------------------------------------
REM refresh_puzzles.bat -- generate the next N days of puzzles locally and push
REM them so Render redeploys with them. Runs from its own folder (no hard-coded
REM path), so just point Task Scheduler at this file. Schedule example:
REM
REM   schtasks /Create /TN "TwentyTwenty Puzzles" ^
REM     /TR "C:\doane\Personal\TwentyTwenty\refresh_puzzles.bat" /SC DAILY /ST 03:00
REM
REM Delivery is git: the deployed app serves the committed puzzle JSON, so a push
REM triggers a Render deploy. (Remove the puzzles/ disk from render.yaml first --
REM a mounted disk hides the committed files. Puzzles are regenerable, so nothing
REM is lost.)
REM ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"
call .venv\Scripts\activate.bat 2>nul

git pull --quiet
REM --puzzles-only: regenerate from the existing local DB (no data re-pull).
REM Swap for `python generate_puzzles.py --sport NFL --days 60` if you only serve NFL.
python run_full.py --puzzles-only --days 60 || exit /b 1

git add puzzles
git diff --cached --quiet
if %errorlevel%==0 (
    echo No new puzzles to push.
) else (
    git commit -m "puzzles: auto-refresh %date%"
    git pull --no-edit
    git push
)
endlocal

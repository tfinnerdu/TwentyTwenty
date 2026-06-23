#!/usr/bin/env python3
"""
rebuild_puzzles.py
==================
Nuke every pre-generated puzzle and rebuild them from the current game DB. Local
only -- reads data/nfl.db, no network. Run it after a data fix so stale puzzles
(built from the old, wrong data) are replaced with correct ones, then commit +
push the puzzles/ change to deploy.

    python rebuild_puzzles.py                  # all modes, 60 days, difficulty=any
    python rebuild_puzzles.py --days 30
    python rebuild_puzzles.py --dry-run        # show what it'd nuke/build, write nothing
    python rebuild_puzzles.py --keep           # rebuild (force-overwrite) without deleting first

Chain length is sized to each mode's available players so a thin sport doesn't
hang on a 20-link chain it can't fill (same rule as run_full).
"""

import argparse
import glob
import os
import sqlite3
import sys
from datetime import date, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
import generate_puzzles as gp          # generate_for_date + its PUZZLE_DIR

DB_PATH = os.path.join(BASE_DIR, "data", "nfl.db")
SPORTS = ["ALL", "NFL", "NBA", "MLB", "NHL", "WNBA", "NCAAF", "NCAAB", "NCAAW"]


def player_counts(db):
    conn = sqlite3.connect(db)
    try:
        counts = {s: n for s, n in conn.execute("SELECT sport, COUNT(*) FROM players GROUP BY sport")}
    finally:
        conn.close()
    return counts


def main():
    ap = argparse.ArgumentParser(description="Nuke + rebuild all puzzles from the local DB (no network)")
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--length", type=int, default=20)
    ap.add_argument("--difficulty", default="any", choices=["easy", "medium", "hard", "any"])
    ap.add_argument("--puzzle-dir", help="override the puzzles directory (default puzzles/)")
    ap.add_argument("--keep", action="store_true", help="rebuild without deleting existing puzzles first")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.puzzle_dir:                  # redirect generate_for_date's output too
        gp.PUZZLE_DIR = os.path.abspath(args.puzzle_dir)
    puzzle_dir = gp.PUZZLE_DIR
    os.makedirs(puzzle_dir, exist_ok=True)

    existing = sorted(glob.glob(os.path.join(puzzle_dir, "*.json")))
    if not args.keep:
        print(f"Nuke: {len(existing)} existing puzzle file(s) in {puzzle_dir}"
              + (" (dry run -- kept)" if args.dry_run else ""))
        if not args.dry_run:
            for f in existing:
                os.remove(f)

    counts = player_counts(args.db)
    total = sum(counts.values())
    print(f"Rebuild: {args.days} day(s) x {len(SPORTS)} modes from {args.db} ({total} players, "
          f"difficulty={args.difficulty})")

    made = 0
    for sport in SPORTS:
        avail = total if sport == "ALL" else counts.get(sport, 0)
        if avail < 8:
            print(f"  {sport:6s} only {avail} players -> skip")
            continue
        length = min(args.length, max(6, avail * 2 // 3))   # leave the DFS slack
        note = f" (length {length})" if length < args.length else ""
        if args.dry_run:
            print(f"  {sport:6s} {args.days} day(s){note}")
            continue
        for i in range(args.days):
            d = date.today() + timedelta(days=i)
            try:
                if gp.generate_for_date(d, seed=int(d.strftime("%Y%m%d")) + len(sport), force=True,
                                        sport_mode=sport, difficulty=args.difficulty, chain_length=length):
                    made += 1
            except Exception as e:
                print(f"  {sport} {d.isoformat()}: {e}")
    print(f"Done -- {made} puzzle file(s) written" + (" (dry run -- nothing written)" if args.dry_run else ""))


if __name__ == "__main__":
    main()

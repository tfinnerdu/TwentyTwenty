#!/usr/bin/env python3
"""
generate_puzzles.py
-------------------
Run nightly to pre-generate the next N days of puzzles.

Usage:
    python generate_puzzles.py --days 7
    python generate_puzzles.py --sport NFL --difficulty medium
    python generate_puzzles.py --sport ALL --difficulty easy
    python generate_puzzles.py --date 2026-07-04 --force
    python generate_puzzles.py --days 7 --seed 12345

Cron / GitHub Actions:
    0 3 * * * python /path/to/generate_puzzles.py --days 7
"""

import argparse
import json
import os
import sys
import logging
from datetime import date, timedelta

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

BASE_DIR   = os.path.dirname(__file__)
PUZZLE_DIR = os.path.join(BASE_DIR, "puzzles")
sys.path.insert(0, BASE_DIR)

from engine.generator import generate_chain


def sport_slug(sport_mode: str) -> str:
    return sport_mode.lower().replace(',', '-').replace(' ', '_')


def puzzle_path(d: date, sport_mode: str = 'nfl') -> str:
    slug = sport_slug(sport_mode)
    return os.path.join(PUZZLE_DIR, f"{d.isoformat()}_{slug}.json")


def generate_for_date(
    d: date,
    seed: int = None,
    force: bool = False,
    sport_mode: str = "NFL",
    difficulty: str = "medium",
) -> bool:
    path = puzzle_path(d, sport_mode)
    if os.path.exists(path) and not force:
        log.info(f"  {d.isoformat()} -- already exists, skipping")
        return False

    log.info(f"  {d.isoformat()} -- generating (sport={sport_mode}, difficulty={difficulty})...")
    try:
        chain = generate_chain(
            chain_length=20,
            seed=seed,
            sport_mode=sport_mode,
            difficulty=difficulty,
        )
        os.makedirs(PUZZLE_DIR, exist_ok=True)
        with open(path, "w") as f:
            json.dump(chain, f, indent=2)
        avg = sum(n["answer_count"] for n in chain) / len(chain)
        log.info(f"  {d.isoformat()} -- done ({len(chain)} questions, avg answers={avg:.1f})")
        return True
    except Exception as e:
        log.error(f"  {d.isoformat()} -- FAILED: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Pre-generate 20/20 daily puzzles")
    parser.add_argument("--days",       type=int,   default=7,        help="Days ahead to generate (default: 7)")
    parser.add_argument("--seed",       type=int,   default=None,     help="Random seed; default is date-derived")
    parser.add_argument("--force",      action="store_true",          help="Overwrite existing puzzles")
    parser.add_argument("--date",       type=str,   default=None,     help="Generate a specific date (YYYY-MM-DD)")
    parser.add_argument("--sport",      type=str,   default="NFL",    help="Sport mode: NFL / NBA / ALL / NFL,NBA / ...")
    parser.add_argument("--difficulty", type=str,   default="medium", choices=["easy","medium","hard","any"],
                        help="Target answer-count difficulty (default: medium)")
    args = parser.parse_args()

    log.info(f"20/20 puzzle generator -- sport={args.sport}  difficulty={args.difficulty}")

    if args.date:
        try:
            d = date.fromisoformat(args.date)
        except ValueError:
            log.error(f"Invalid date: {args.date}")
            sys.exit(1)
        generate_for_date(d, seed=args.seed, force=args.force,
                          sport_mode=args.sport, difficulty=args.difficulty)
    else:
        log.info(f"Generating {args.days} day(s) starting from today...")
        generated = 0
        for i in range(args.days):
            d    = date.today() + timedelta(days=i)
            seed = args.seed if args.seed is not None else int(d.strftime("%Y%m%d"))
            if generate_for_date(d, seed=seed, force=args.force,
                                 sport_mode=args.sport, difficulty=args.difficulty):
                generated += 1

        log.info(f"Done -- {generated} new puzzle(s) generated")


if __name__ == "__main__":
    main()

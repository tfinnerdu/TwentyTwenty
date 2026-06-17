"""
data/etl/providers/smoke_test_csv.py
====================================
End-to-end proof for the CSV providers (NHL / WNBA / NFL) + awards overlay.

For each sport: build the fixture, run the provider, apply the curated awards
overlay, rebuild categories, and generate a chain. Exercises the production
path offline.

    python -m data.etl.providers.smoke_test_csv            # all three
    python -m data.etl.providers.smoke_test_csv NHL        # one
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))
from data.etl.providers._fixture_csv import write_fixture
from data.etl.providers.csv_season import NHLProvider, WNBAProvider, NFLProvider
from data.etl.providers.base import run_pipeline
from data.etl.schema import get_conn
from engine.generator import generate_chain, print_chain

PROVIDERS = {"NHL": NHLProvider, "WNBA": WNBAProvider, "NFL": NFLProvider}

# spot checks: category_id -> a player who must be a member
CHECKS = {
    "NHL": {"nhl_500g": "Wayne Gretzky", "nhl_1000pts": "Mario Lemieux",
            "nhl_cup": "Mark Messier", "nhl_defense": "Bobby Orr",
            "nhl_goalie": "Patrick Roy", "nhl_hart": "Sidney Crosby"},
    "WNBA": {"wnba_ring": "Sue Bird", "wnba_mvp": "A'ja Wilson",
             "wnba_5apg": "Courtney Vandersloot", "wnba_storm": "Breanna Stewart",
             "wnba_center": "Lisa Leslie"},
    "NFL": {"pass_50k": "Dan Marino", "rush_10k": "Barry Sanders",
            "rec_10k": "Jerry Rice", "sacks_100": "Bruce Smith",
            "sb_3": "Tom Brady", "pos_qb": "Peyton Manning", "int_20": "Ed Reed"},
}


def run_one(sport: str) -> bool:
    tmp = tempfile.mkdtemp(prefix=f"2020_{sport.lower()}_")
    csv_dir = os.path.join(tmp, "csvs")
    db_path = os.path.join(tmp, "test.db")
    n = write_fixture(sport, csv_dir)
    summary = run_pipeline(PROVIDERS[sport](source_dir=csv_dir), db_path)
    print(f"\n=== {sport}: {n} players, +{summary['added']} added ===")

    conn = get_conn(db_path)
    c = conn.cursor()

    def members(cat_id):
        return {r[0] for r in c.execute(
            "SELECT p.name FROM players p JOIN player_categories pc ON p.id=pc.player_id "
            "WHERE pc.category_id=?", (cat_id,))}

    ok = True
    for cat, who in CHECKS[sport].items():
        present = who in members(cat)
        ok = ok and present
        print(f"  [{'OK' if present else 'FAIL'}] {who} in {cat}")

    chain = generate_chain(chain_length=10, sport_mode=sport, difficulty="any",
                           db_path=db_path, max_attempts=6000, seed=5)
    conn.close()
    assert ok, f"{sport} spot-checks failed"
    assert len(chain) == 10, f"{sport} chain wrong length"
    print(f"  chain[0]: [{chain[0]['left_label']}] + [{chain[0]['right_label']}]"
          f" ({chain[0]['answer_count']} answers)")
    print(f"  chain[9]: [{chain[9]['left_label']}] + [{chain[9]['right_label']}]"
          f" ({chain[9]['answer_count']} answers)")
    return True


def main():
    sports = [sys.argv[1].upper()] if len(sys.argv) > 1 else ["NHL", "WNBA", "NFL"]
    for sport in sports:
        run_one(sport)
    print("\nSMOKE TEST PASSED")


if __name__ == "__main__":
    main()

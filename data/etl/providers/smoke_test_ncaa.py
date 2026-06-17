"""
data/etl/providers/smoke_test_ncaa.py
=====================================
End-to-end proof for the curated NCAA datasets (football + men's/women's
basketball). Runs CuratedProvider for each, rebuilds categories, and
generates a chain -- including the cross-sport `college` connectors that let
NCAA players link to the pro sports in ALL mode.

    python -m data.etl.providers.smoke_test_ncaa
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))
from data.etl.providers.curated import CuratedProvider
from data.etl.providers.base import run_pipeline
from data.etl.schema import get_conn
from engine.generator import generate_chain, print_chain

CHECKS = {
    "NCAAF": {"ncaaf_heisman": "Tim Tebow", "ncaaf_alabama": "Derrick Henry",
              "ncaaf_pass5k": "Baker Mayfield", "ncaaf_rush3k": "Herschel Walker",
              "college_usc": "Reggie Bush"},
    "NCAAB": {"ncaab_duke": "Christian Laettner", "ncaab_champ": "Bill Walton",
              "ncaab_poy": "Pete Maravich", "ncaab_25ppg": "Pete Maravich",
              "college_kentucky": "Anthony Davis"},
    "NCAAW": {"ncaaw_uconn": "Breanna Stewart", "ncaaw_champ": "Maya Moore",
              "ncaaw_poy": "Cheryl Miller", "ncaaw_20ppg": "Caitlin Clark"},
}


def run_one(sport: str):
    tmp = tempfile.mkdtemp(prefix=f"2020_{sport.lower()}_")
    db_path = os.path.join(tmp, "test.db")
    summary = run_pipeline(CuratedProvider(sport), db_path)
    print(f"\n=== {sport}: +{summary['added']} added ===")

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
                           db_path=db_path, max_attempts=8000, seed=3)
    conn.close()
    assert ok, f"{sport} spot-checks failed"
    assert len(chain) == 10, f"{sport} chain wrong length"
    print(f"  chain[0]: [{chain[0]['left_label']}] + [{chain[0]['right_label']}]"
          f" ({chain[0]['answer_count']} answers)")


def main():
    for sport in ("NCAAF", "NCAAB", "NCAAW"):
        run_one(sport)
    print("\nSMOKE TEST PASSED")


if __name__ == "__main__":
    main()

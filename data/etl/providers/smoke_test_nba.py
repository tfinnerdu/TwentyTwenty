"""
data/etl/providers/smoke_test_nba.py
====================================
End-to-end proof for the NBA provider + curated awards overlay, offline.

Builds the NBA season-stats fixture, runs NBACsvProvider against it, applies
the awards overlay (data/etl/awards/nba.json), rebuilds categories, and
generates an NBA chain. Asserts that honors landed (e.g. Jordan = 6 rings)
and that the stat categories work.

    python -m data.etl.providers.smoke_test_nba
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))
from data.etl.providers._fixture_nba import write_fixture
from data.etl.providers.nba_csv import NBACsvProvider
from data.etl.providers.base import run_pipeline
from data.etl.schema import get_conn
from engine.generator import generate_chain, print_chain


def main():
    tmp = tempfile.mkdtemp(prefix="2020_nba_")
    csv_dir = os.path.join(tmp, "csvs")
    db_path = os.path.join(tmp, "test.db")
    n = write_fixture(csv_dir)
    print(f"\nFixture: {n} players -> {csv_dir}")

    summary = run_pipeline(NBACsvProvider(source_dir=csv_dir), db_path)
    print(f"Pipeline: +{summary['added']} added, ~{summary['updated']} updated\n")

    conn = get_conn(db_path)
    c = conn.cursor()
    total = c.execute("SELECT COUNT(*) FROM players WHERE sport='NBA'").fetchone()[0]
    print(f"players(NBA)={total}")

    # awards overlay must have populated honor columns from awards/nba.json
    jordan = c.execute("SELECT nba_championships, nba_mvps, nba_finals_mvps "
                       "FROM players WHERE name='Michael Jordan'").fetchone()
    print(f"Michael Jordan honors: rings={jordan[0]} mvps={jordan[1]} fmvp={jordan[2]}")
    assert jordan[0] == 6 and jordan[1] == 5, "awards overlay did not apply"

    print("\nSample category memberships:")
    for r in c.execute("""
        SELECT cat.label, COUNT(pc.player_id) n
        FROM categories cat JOIN player_categories pc ON cat.id=pc.category_id
        WHERE cat.sport_scope IN ('NBA','ALL')
        GROUP BY cat.id HAVING n > 0 ORDER BY n DESC LIMIT 16""").fetchall():
        print(f"  {r[1]:3d}  {r[0]}")

    def members(cat_id):
        return {r[0] for r in c.execute(
            "SELECT p.name FROM players p JOIN player_categories pc ON p.id=pc.player_id "
            "WHERE pc.category_id=?", (cat_id,))}

    checks = {
        "nba_ring":   "Michael Jordan",     # from awards overlay
        "nba_rings2": "Tim Duncan",          # 5 rings
        "nba_mvp":    "Stephen Curry",       # from awards overlay (2 MVPs)
        "nba_7apg":   "John Stockton",       # from stats
        "nba_10rpg":  "Bill Russell",        # from stats
        "nba_lakers": "Kobe Bryant",         # from teams
        "nba_center": "Hakeem Olajuwon",     # from position
    }
    print("\nSpot checks:")
    ok = True
    for cat, who in checks.items():
        present = who in members(cat)
        ok = ok and present
        print(f"  [{'OK' if present else 'FAIL'}] {who} in {cat}")
    conn.close()
    assert ok, "membership spot-checks failed"

    print("\nGenerating NBA chain (length 10)...")
    chain = generate_chain(chain_length=10, sport_mode="NBA", difficulty="any",
                           db_path=db_path, max_attempts=5000, seed=11)
    print_chain(chain)
    assert len(chain) == 10

    print("\nSMOKE TEST PASSED")


if __name__ == "__main__":
    main()

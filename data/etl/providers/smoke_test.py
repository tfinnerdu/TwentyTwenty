"""
data/etl/providers/smoke_test.py
================================
End-to-end proof for the provider pipeline, offline.

Builds the Lahman fixture, runs the real LahmanMLBProvider against it
(parse -> aggregate -> upsert -> derive -> rebuild categories), then
generates an MLB chain from the result. Exercises the exact production
code path; only the data source size differs.

    python -m data.etl.providers.smoke_test
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))
from data.etl.providers._fixture_mlb import write_fixture
from data.etl.providers.lahman_mlb import LahmanMLBProvider
from data.etl.providers.base import run_pipeline
from data.etl.schema import get_conn
from engine.generator import generate_chain, print_chain


def main():
    tmp = tempfile.mkdtemp(prefix="2020_lahman_")
    csv_dir = os.path.join(tmp, "csvs")
    db_path = os.path.join(tmp, "test.db")
    n = write_fixture(csv_dir)
    print(f"\nFixture: {n} players -> {csv_dir}")

    summary = run_pipeline(LahmanMLBProvider(data_dir=csv_dir), db_path)
    print(f"Pipeline: +{summary['added']} added, ~{summary['updated']} updated\n")

    conn = get_conn(db_path)
    c = conn.cursor()

    total = c.execute("SELECT COUNT(*) FROM players WHERE sport='MLB'").fetchone()[0]
    memberships = c.execute("SELECT COUNT(*) FROM player_categories").fetchone()[0]
    print(f"players(MLB)={total}  category_memberships={memberships}")

    print("\nSample category memberships:")
    rows = c.execute("""
        SELECT cat.label, COUNT(pc.player_id) n
        FROM categories cat JOIN player_categories pc ON cat.id=pc.category_id
        WHERE cat.sport_scope IN ('MLB','ALL')
        GROUP BY cat.id HAVING n > 0 ORDER BY n DESC LIMIT 18
    """).fetchall()
    for r in rows:
        print(f"  {r[1]:3d}  {r[0]}")

    # spot-check a few memberships we know must be true
    def members(cat_id):
        return {r[0] for r in c.execute("""
            SELECT p.name FROM players p JOIN player_categories pc ON p.id=pc.player_id
            WHERE pc.category_id=?""", (cat_id,))}

    checks = {
        "mlb_500hr":  "Babe Ruth",
        "mlb_3000hits": "Pete Rose",
        "mlb_300w":   "Nolan Ryan",
        "mlb_300saves": "Mariano Rivera",
        "mlb_cy":     "Roger Clemens",
        "mlb_yankees": "Derek Jeter",
        "mlb_pitcher": "Randy Johnson",
        "mlb_ws":     "Yogi Berra",
    }
    print("\nSpot checks:")
    ok = True
    for cat, who in checks.items():
        present = who in members(cat)
        ok = ok and present
        print(f"  [{'OK' if present else 'FAIL'}] {who} in {cat}")
    conn.close()
    assert ok, "membership spot-checks failed"

    print("\nGenerating MLB chain (length 10)...")
    chain = generate_chain(chain_length=10, sport_mode="MLB", difficulty="any",
                           db_path=db_path, max_attempts=5000, seed=7)
    print_chain(chain)
    assert len(chain) == 10, f"expected 10 links, got {len(chain)}"

    print("\nSMOKE TEST PASSED")


if __name__ == "__main__":
    main()

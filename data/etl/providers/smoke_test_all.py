"""
data/etl/providers/smoke_test_all.py
====================================
Capstone: load several sports into one DB from the providers and generate an
ALL-mode chain that crosses sports via the shared `college` / birth / decade
connectors -- the payoff of the whole pipeline.

    python -m data.etl.providers.smoke_test_all
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))
from data.etl.providers._fixture_nba import write_fixture as write_nba
from data.etl.providers._fixture_csv import write_fixture as write_csv
from data.etl.providers.csv_season import NBAProvider, NFLProvider
from data.etl.providers.curated import CuratedProvider
from data.etl.providers.base import run_pipeline
from data.etl.schema import get_conn
from engine.generator import generate_chain, print_chain


def main():
    tmp = tempfile.mkdtemp(prefix="2020_all_")
    db = os.path.join(tmp, "test.db")
    nba_dir, nfl_dir = os.path.join(tmp, "nba"), os.path.join(tmp, "nfl")
    write_nba(nba_dir)
    write_csv("NFL", nfl_dir)

    for provider in (NBAProvider(source_dir=nba_dir), NFLProvider(source_dir=nfl_dir),
                     CuratedProvider("NCAAB"), CuratedProvider("NCAAF")):
        run_pipeline(provider, db)

    conn = get_conn(db)
    c = conn.cursor()
    total = c.execute("SELECT COUNT(*) FROM players").fetchone()[0]
    by_sport = c.execute("SELECT sport, COUNT(*) FROM players GROUP BY sport").fetchall()
    print(f"\nLoaded {total} players: " + ", ".join(f"{s}={n}" for s, n in by_sport))

    chain = generate_chain(chain_length=12, sport_mode="ALL", difficulty="any",
                           db_path=db, max_attempts=10000, seed=1)
    print_chain(chain)

    # how many distinct sports show up among the answers? (cross-sport proof)
    names = {n for node in chain for n in node["valid_players"]}
    qmarks = ",".join("?" * len(names))
    sports = {r[0] for r in c.execute(
        f"SELECT DISTINCT sport FROM players WHERE name IN ({qmarks})", list(names))}
    conn.close()
    print(f"\nSports represented in the chain: {sorted(sports)}")
    assert len(sports) >= 2, "expected a cross-sport chain"
    assert len(chain) == 12
    print("SMOKE TEST PASSED")


if __name__ == "__main__":
    main()

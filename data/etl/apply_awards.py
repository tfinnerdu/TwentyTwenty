"""
data/etl/apply_awards.py
========================
Curated awards/honors overlay.

Most sanctioned stat sources ship stat lines but NOT honors -- MVPs, rings,
All-Star/All-NBA selections, Heisman, etc. (Lahman/MLB is the lucky exception.)
This applies a small, hand-maintained award layer on top of whatever a
provider loaded, by matching players on normalized name within a sport.

Award data lives in data/etl/awards/<sport>.json as a list of records:
    [{"name": "Michael Jordan", "nba_championships": 6, "nba_mvps": 5,
      "nba_finals_mvps": 6, "nba_all_nba": 11, "nba_all_star": 14}, ...]

Any key that is a real `players` column is written; `name` is only used to
match. Run after ingesting a provider, or standalone after editing the JSON:

    python -m data.etl.apply_awards --sport NBA
"""

import argparse
import json
import logging
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from data.etl.schema import get_conn, migrate
from data.etl.load import rebuild_categories, record_load_run

log = logging.getLogger(__name__)

AWARDS_DIR = os.path.join(os.path.dirname(__file__), "awards")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def award_file(sport: str) -> str:
    return os.path.join(AWARDS_DIR, f"{sport.lower()}.json")


def load_award_records(sport: str) -> list:
    path = award_file(sport)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def apply_awards(conn, sport: str) -> tuple:
    """
    Overlay curated honors for one sport. Returns (matched, total, unmatched).
    Only columns that exist on the players table are written.
    """
    records = load_award_records(sport)
    if not records:
        return (0, 0, [])

    valid = {row[1] for row in conn.execute("PRAGMA table_info(players)")}
    c = conn.cursor()
    by_norm = {}
    for row in c.execute("SELECT id, name FROM players WHERE sport=?", (sport,)):
        by_norm.setdefault(_norm(row["name"]), row["id"])

    matched, unmatched = 0, []
    for rec in records:
        pid = by_norm.get(_norm(rec.get("name", "")))
        if not pid:
            unmatched.append(rec.get("name", "?"))
            continue
        updates = {k: v for k, v in rec.items() if k != "name" and k in valid}
        if updates:
            assignments = ", ".join(f"{k}=?" for k in updates)
            c.execute(f"UPDATE players SET {assignments} WHERE id=?",
                      [*updates.values(), pid])
            matched += 1
    conn.commit()
    return matched, len(records), unmatched


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S")
    parser = argparse.ArgumentParser(description="Apply curated awards overlay")
    parser.add_argument("--sport", required=True, help="NBA, NFL, NHL, ...")
    parser.add_argument("--db", default=os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "data", "nfl.db"))
    parser.add_argument("--skip-rebuild", action="store_true",
                        help="Don't rebuild categories afterward")
    args = parser.parse_args()

    migrate(args.db)
    conn = get_conn(args.db)
    matched, total, unmatched = apply_awards(conn, args.sport)
    log.info(f"Awards [{args.sport}]: {matched}/{total} matched")
    if unmatched:
        log.warning(f"  {len(unmatched)} unmatched (not in DB): "
                    f"{', '.join(unmatched[:12])}" + ("..." if len(unmatched) > 12 else ""))
    if not args.skip_rebuild and matched:
        rebuild_categories(conn, args.sport)
        record_load_run(conn, args.sport, f"awards overlay matched={matched}")
    conn.close()


if __name__ == "__main__":
    main()

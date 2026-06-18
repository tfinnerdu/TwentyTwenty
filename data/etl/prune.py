"""
data/etl/prune.py
=================
Trim the bulk college / WNBA pools down to recognizable players, leaving every
other source (curated legends, sr_history, sr_college) untouched.

Keep a bulk player if ANY of:
  - they carry an honor  (college: appeared in an SR award list, flagged by
    sr_college; WNBA: a curated championship/MVP/All-Star)
  - WNBA only: career points >= --career OR a single season >= --season

Everything else loaded from the bulk CSV is deleted. College is awards-only by
design ("prune anyone who didn't make an award list"), so RUN sr_college FIRST
-- otherwise nothing is flagged and there'd be nothing to keep (guarded below).

    python -m data.etl.prune --sport NCAAB              # dry run: shows the cut
    python -m data.etl.prune --sport NCAAB --apply      # actually delete
    python -m data.etl.prune --sport WNBA --apply --career 4000 --season 500
    python -m data.etl.prune --sport ALL --apply
"""

import argparse
import csv
import logging
import os
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, ROOT)

from data.etl.load import rebuild_categories
from data.etl.schema import get_conn

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("prune")

PRUNE = {
    "NCAAB": {"source": "ncaab_csv",
              "honors": ["ncaab_all_american", "ncaab_player_of_year", "ncaab_championships"],
              "career": None, "season": None},
    "NCAAW": {"source": "ncaaw_csv",
              "honors": ["ncaab_all_american", "ncaab_player_of_year", "ncaab_championships"],
              "career": None, "season": None},
    "WNBA":  {"source": "wnba_csv",
              "honors": ["wnba_championships", "wnba_mvps", "wnba_finals_mvps", "wnba_all_star"],
              "career": 4000, "season": 500},
}


def _points_keep(conn, sport, career, season, seasons_csv):
    """DB ids of bulk players whose career or best season clears the threshold."""
    if not (career or season) or not os.path.exists(seasons_csv):
        if (career or season):
            log.warning(f"  no cached seasons CSV at {seasons_csv} -- skipping points criteria")
        return set()
    car, best = defaultdict(int), defaultdict(int)
    with open(seasons_csv, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            pid, pts = r["player_id"], int(float(r.get("pts") or 0))
            car[pid] += pts
            best[pid] = max(best[pid], pts)
    srids = {f"{sport.lower()}_{pid}" for pid in car
             if (career and car[pid] >= career) or (season and best[pid] >= season)}
    id_by_srid = {r[1]: r[0] for r in conn.execute(
        "SELECT id, sr_id FROM players WHERE sport=?", (sport,))}
    return {id_by_srid[s] for s in srids if s in id_by_srid}


def prune(sport, db, apply, career=None, season=None, force=False):
    cfg = PRUNE[sport]
    career = cfg["career"] if career is None else career
    season = cfg["season"] if season is None else season
    conn = get_conn(db)

    honor_clause = " OR ".join(f"COALESCE({h},0)>0" for h in cfg["honors"])
    keep = {r[0] for r in conn.execute(
        f"SELECT id FROM players WHERE sport=? AND ({honor_clause})", (sport,))}
    n_honored = len(keep)
    seasons_csv = os.path.join(ROOT, "data", "cache", "exports", sport.lower(),
                               f"{sport.lower()}_seasons.csv")
    keep |= _points_keep(conn, sport, career, season, seasons_csv)

    bulk = [r[0] for r in conn.execute(
        "SELECT id FROM players WHERE sport=? AND data_source=?", (sport, cfg["source"]))]
    to_delete = [pid for pid in bulk if pid not in keep]
    kept = len(bulk) - len(to_delete)
    log.info(f"[{sport}] bulk={len(bulk)}  honored={n_honored}"
             f"{f'  +points-notable' if (career or season) else ''}"
             f"  -> keep {kept}, prune {len(to_delete)}")

    # Safety: awards-only sport with nothing flagged almost certainly means
    # sr_college hasn't run -- don't nuke the whole pool by accident.
    if not apply:
        log.info("  dry run -- pass --apply to delete")
        conn.close()
        return
    if kept == 0 and not force:
        log.error(f"[{sport}] would delete the ENTIRE bulk pool (nothing kept). "
                  f"Did you run sr_college first? Re-run with --force to override.")
        conn.close()
        return

    conn.executemany("DELETE FROM player_categories WHERE player_id=?", [(i,) for i in to_delete])
    conn.executemany("DELETE FROM players WHERE id=?", [(i,) for i in to_delete])
    conn.commit()
    rebuild_categories(conn, sport)
    conn.close()
    log.info(f"[{sport}] pruned {len(to_delete)} players; {kept} kept")


def main():
    ap = argparse.ArgumentParser(description="Prune bulk pools to notable players")
    ap.add_argument("--sport", required=True, help="NCAAB / NCAAW / WNBA / ALL")
    ap.add_argument("--db", default=os.path.join(ROOT, "data", "nfl.db"))
    ap.add_argument("--apply", action="store_true", help="Actually delete (default is a dry run)")
    ap.add_argument("--career", type=int, default=None, help="Override career-points keep threshold")
    ap.add_argument("--season", type=int, default=None, help="Override best-season-points keep threshold")
    ap.add_argument("--force", action="store_true", help="Allow pruning even if it empties the pool")
    args = ap.parse_args()

    sports = list(PRUNE) if args.sport.upper() == "ALL" else [args.sport.upper()]
    for sp in sports:
        if sp not in PRUNE:
            log.error(f"Unknown sport {sp!r}; known: {', '.join(PRUNE)} (or ALL)")
            continue
        prune(sp, args.db, args.apply, args.career, args.season, args.force)


if __name__ == "__main__":
    main()

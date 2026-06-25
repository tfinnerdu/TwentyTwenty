"""
data/etl/prune.py
=================
Trim the bulk pools to notable players, leaving the curated / sr_college / (for
most sports) sr_history rows untouched. NHL is the exception: it also sweeps the
retired sr_history alpha-crawl source, so any stale rows it left are held to the
same bar instead of being stuck (sr_history players are otherwise prune-immune).

Keep a bulk player if ANY of:
  - they carry an honor (college: an SR award flagged by sr_college; WNBA/NHL:
    a curated/awards honor)
  - their best single season clears --min-games  (WNBA 25, NHL 50)

College is awards-only by design ("prune anyone not on an award list"), so RUN
sr_college FIRST -- otherwise nothing is flagged and there'd be nothing to keep
(guarded below). NHL keeps award-winners OR a 50-game season, so the NHL awards
flag (sr_college --sport NHL) should run first too to keep the early-era legends.

    python -m data.etl.prune --sport WNBA              # dry run: shows the cut
    python -m data.etl.prune --sport WNBA --apply
    python -m data.etl.prune --sport NHL --apply --min-games 50
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
              "honors": ["ncaab_all_american", "ncaab_player_of_year", "ncaab_championships",
                         "ncaab_all_conference"],
              "min_season_games": None,
              # College bulk carries no award flags (no overlay; sr_college writes
              # separate rows), so awards-only would delete ~everything. Keep the
              # statistically notable instead: a real career average in ANY of pts/
              # reb/ast. Tune these against the dry-run counts.
              "min_career": [("ncaab_points", 12.0), ("ncaab_rebounds", 6.0),
                             ("ncaab_assists", 4.0)]},
    "NCAAW": {"source": "ncaaw_csv",
              "honors": ["ncaab_all_american", "ncaab_player_of_year", "ncaab_championships",
                         "ncaab_all_conference"],
              "min_season_games": None,
              "min_career": [("ncaab_points", 12.0), ("ncaab_rebounds", 6.0),
                             ("ncaab_assists", 4.0)]},
    "NCAAF": {"source": "cfbd",
              "honors": ["ncaaf_all_american", "ncaaf_heisman", "ncaaf_all_conference"],
              "min_season_games": None,
              # cfbd hardcodes games=12, so a games threshold is meaningless here --
              # keep by career production instead (a multi-year starter / star in ANY
              # of pass/rush/rec yards or TDs).
              "min_career": [("ncaaf_pass_yds", 4000), ("ncaaf_rush_yds", 2000),
                             ("ncaaf_rec_yds", 1500), ("ncaaf_tds", 20)]},
    "WNBA":  {"source": "wnba_csv",
              "honors": ["wnba_championships", "wnba_mvps", "wnba_finals_mvps", "wnba_all_star"],
              "min_season_games": 25},
    # nhl_api is the modern bulk; sr_history is the retired alpha-crawl source --
    # sweep any stale rows it left so they meet the same bar. sr_college award
    # legends and curated rows are deliberately omitted (= protected): every
    # sr_college NHL row is honored by construction, so it'd survive anyway.
    "NHL":   {"source": ["nhl_api", "sr_history"],
              "honors": ["nhl_championships", "nhl_mvps", "nhl_all_star"],
              "min_season_games": 50},                        # award OR best season >= 50 G
}


def _games_keep(conn, sport, min_games, seasons_csv):
    """DB ids of bulk players whose best single season clears min_games."""
    if not min_games:
        return set()
    if not os.path.exists(seasons_csv):
        log.warning(f"  no cached seasons CSV at {seasons_csv} -- skipping games criteria")
        return set()
    best = defaultdict(int)
    with open(seasons_csv, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            pid, g = r["player_id"], int(float(r.get("g") or 0))
            best[pid] = max(best[pid], g)
    srids = {f"{sport.lower()}_{pid}" for pid in best if best[pid] >= min_games}
    id_by_srid = {r[1]: r[0] for r in conn.execute(
        "SELECT id, sr_id FROM players WHERE sport=?", (sport,))}
    return {id_by_srid[s] for s in srids if s in id_by_srid}


def _career_keep(conn, sport, specs):
    """DB ids of players whose CAREER stat clears ANY (column >= threshold). For the
    college bulk, which carries no award flags -- keeps the statistically notable
    instead of pruning to nothing."""
    if not specs:
        return set()
    clause = " OR ".join(f"COALESCE({col},0) >= ?" for col, _ in specs)
    params = [thr for _, thr in specs]
    return {r[0] for r in conn.execute(
        f"SELECT id FROM players WHERE sport=? AND ({clause})", (sport, *params))}


def prune(sport, db, apply, min_games=None, force=False):
    cfg = PRUNE[sport]
    min_games = cfg["min_season_games"] if min_games is None else min_games
    conn = get_conn(db)

    honor_clause = " OR ".join(f"COALESCE({h},0)>0" for h in cfg["honors"])
    keep = {r[0] for r in conn.execute(
        f"SELECT id FROM players WHERE sport=? AND ({honor_clause})", (sport,))}
    n_honored = len(keep)
    seasons_csv = os.path.join(ROOT, "data", "cache", "exports", sport.lower(),
                               f"{sport.lower()}_seasons.csv")
    keep |= _games_keep(conn, sport, min_games, seasons_csv)
    n_career = 0
    if cfg.get("min_career"):
        career_ids = _career_keep(conn, sport, cfg["min_career"])
        n_career = len(career_ids)
        keep |= career_ids

    sources = cfg["source"] if isinstance(cfg["source"], (list, tuple)) else [cfg["source"]]
    qs = ",".join("?" for _ in sources)
    bulk = [r[0] for r in conn.execute(
        f"SELECT id FROM players WHERE sport=? AND data_source IN ({qs})", (sport, *sources))]
    to_delete = [pid for pid in bulk if pid not in keep]
    kept = len(bulk) - len(to_delete)
    crit_bits = []
    if min_games:
        crit_bits.append(f"best-season>={min_games}G")
    if cfg.get("min_career"):
        crit_bits.append("career[" + "/".join(f"{c}>={t}" for c, t in cfg["min_career"]) + f"]={n_career}")
    crit = ("  " + "  ".join(crit_bits)) if crit_bits else ""
    log.info(f"[{sport}] bulk={len(bulk)}  honored={n_honored}{crit}"
             f"  -> keep {kept}, prune {len(to_delete)}")

    if not apply:
        log.info("  dry run -- pass --apply to delete")
        conn.close()
        return
    if kept == 0 and not force:
        log.error(f"[{sport}] would delete the ENTIRE bulk pool (nothing kept). "
                  f"Did the awards crawl run first? Re-run with --force to override.")
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
    ap.add_argument("--sport", required=True, help="NCAAB / NCAAW / WNBA / NHL / ALL")
    ap.add_argument("--db", default=os.path.join(ROOT, "data", "nfl.db"))
    ap.add_argument("--apply", action="store_true", help="Actually delete (default is a dry run)")
    ap.add_argument("--min-games", type=int, default=None,
                    help="Override the best-single-season games keep threshold")
    ap.add_argument("--force", action="store_true", help="Allow pruning even if it empties the pool")
    args = ap.parse_args()

    sports = list(PRUNE) if args.sport.upper() == "ALL" else [args.sport.upper()]
    for sp in sports:
        if sp not in PRUNE:
            log.error(f"Unknown sport {sp!r}; known: {', '.join(PRUNE)} (or ALL)")
            continue
        prune(sp, args.db, args.apply, args.min_games, args.force)


if __name__ == "__main__":
    main()

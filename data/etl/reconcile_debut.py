#!/usr/bin/env python3
"""
data/etl/reconcile_debut.py
===========================
Fix bulk-boundary debut years from the CACHED alphabetical index -- no network,
no player-page fetches.

The problem
-----------
The windowed bulk sources cap a pre-window star's debut at the window start:
  * NBA / WNBA GitHub bulk -> 1999-2020 only
  * nflverse                -> 1999+ only
Karl Malone (true debut 1985) lands as debut_year 1999 because only his
1999-2004 seasons are in the bulk -- and the history crawl then DEDUPS him by
name (enumerate_targets: `if _slug(name) in have_slugs: continue`) and skips him,
so the earlier seasons never merge. He wrongly misses "Played in the 1980s," and
his per-game averages are just the late-career tail. Players NOT in the thin bulk
get created fresh by the crawl with the right debut (Charles Barkley -> 1984), so
the error is confined to the bulk's name overlap.

The fix (cheap + isolated)
--------------------------
Each sport's SR alpha index -- already cached under data/cache/sr/ from the crawl
-- carries every player's true year_min / year_max. For any player whose stored
debut is later than that true start, lower debut_year, raise final_year, and union
in the decades the bulk was missing. Career STATS are left alone (those need the
player page); this is only the decade/debut correction, which is the part that
drives "Played in the 19x0s" and the era hint.

Strictly cache-only: it reads the index pages already on disk and skips (warns)
any letter that wasn't cached. Nothing is fetched.

    python -m data.etl.reconcile_debut --sport NBA --db data/nfl.db --dry-run
    python -m data.etl.reconcile_debut --sport NBA --db data/nfl.db
    python -m data.etl.reconcile_debut --sport ALL --db data/db_all.db
"""

import argparse
import json
import logging
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, ROOT)

from data.etl.sr_history import parse_alpha_index, _slug, LETTERS
from data.etl.backfill_sr import _cache_path, _soup
from data.etl.load import rebuild_categories
from data.etl.schema import get_conn, migrate

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("reconcile_debut")

DB_PATH = os.path.join(ROOT, "data", "nfl.db")

# Per-sport alpha index (same URLs the crawl cached). Only the windowed-bulk
# sports have the boundary problem; NHL is included for completeness (harmless
# where the data is already right). upper_letters: PFR uses /players/A/ not /a/.
ALPHA = {
    "NBA":  ("https://www.basketball-reference.com/players/{letter}/", False),
    "WNBA": ("https://www.basketball-reference.com/wnba/players/{letter}/", False),
    "NFL":  ("https://www.pro-football-reference.com/players/{letter}/", True),
    "NHL":  ("https://www.hockey-reference.com/players/{letter}/", False),
}
# ALL = the sports whose bulk has a 1999-window boundary.
BULK_BOUNDARY = ["NBA", "WNBA", "NFL"]


def _decades(lo, hi):
    """Every decade tag a continuous career from lo..hi touches."""
    if not lo or not hi or hi < lo:
        return set()
    return {f"{(y // 10) * 10}s" for y in range(lo, hi + 1)}


def build_index_from_cache(sport):
    """{name_slug: (year_min, year_max)} from the sport's CACHED alpha index pages.
    Returns (index, n_letters_read, n_letters_missing). No network."""
    alpha, upper = ALPHA[sport]
    letters = LETTERS.upper() if upper else LETTERS
    index, read, missing = {}, 0, 0
    for letter in letters:
        cp = _cache_path(alpha.format(letter=letter))
        if not os.path.exists(cp):
            missing += 1
            continue
        read += 1
        with open(cp, encoding="utf-8", errors="ignore") as f:
            soup = _soup(f.read())
        for name, _href, ymin, ymax in parse_alpha_index(soup):
            if ymin is None:
                continue
            ymax = ymax or ymin
            s = _slug(name)
            cur = index.get(s)
            index[s] = (min(ymin, cur[0]), max(ymax, cur[1])) if cur else (ymin, ymax)
    return index, read, missing


def reconcile(conn, sport, index, dry_run=False):
    """Lower debut_year / raise final_year / union decades for any player the index
    shows starting earlier (or ending later) than the bulk recorded. Returns counts."""
    rows = conn.execute(
        "SELECT id, name, debut_year, final_year, active_decades FROM players WHERE sport=?",
        (sport,)).fetchall()
    changed = examples = 0
    for row in rows:
        info = index.get(_slug(row["name"]))
        if not info:
            continue
        ymin, ymax = info
        debut, final = row["debut_year"], row["final_year"]
        new_debut = ymin if (not debut or ymin < debut) else debut
        new_final = ymax if (not final or ymax > final) else final
        try:
            have = set(json.loads(row["active_decades"] or "[]"))
        except Exception:
            have = set()
        new_decades = have | _decades(new_debut, new_final)

        if new_debut == debut and new_final == final and new_decades == have:
            continue
        changed += 1
        if examples < 12:
            examples += 1
            log.info(f"  {row['name']}: debut {debut}->{new_debut}, final {final}->{new_final}, "
                     f"+decades {sorted(new_decades - have)}")
        if not dry_run:
            conn.execute(
                "UPDATE players SET debut_year=?, final_year=?, active_decades=? WHERE id=?",
                (new_debut, new_final, json.dumps(sorted(new_decades)), row["id"]))
    return {"rows": len(rows), "matched_index": sum(1 for r in rows if _slug(r["name"]) in index),
            "changed": changed}


def run(sport, db, dry_run=False):
    if sport not in ALPHA:
        log.error(f"unknown sport {sport!r}; known: {', '.join(ALPHA)}")
        return
    index, read, missing = build_index_from_cache(sport)
    if not index:
        log.warning(f"[{sport}] no cached index pages found ({missing} letters missing) -- "
                    f"run the crawl first so data/cache/sr/ has the index. Skipping.")
        return
    log.info(f"[{sport}] index: {len(index)} players from {read} cached letter page(s)"
             + (f" ({missing} not cached)" if missing else ""))
    migrate(db)
    conn = get_conn(db)
    res = reconcile(conn, sport, index, dry_run)
    if not dry_run and res["changed"]:
        conn.commit()
        rebuild_categories(conn, sport)
        log.info(f"[{sport}] categories rebuilt")
    conn.close()
    log.info(f"[{sport}] {res['matched_index']}/{res['rows']} players in index; "
             f"{res['changed']} corrected" + (" (dry run -- nothing written)" if dry_run else ""))


def main():
    ap = argparse.ArgumentParser(description="Correct bulk-boundary debut years from the "
                                             "cached SR alpha index (no network)")
    ap.add_argument("--sport", default="NBA",
                    help="NBA / WNBA / NFL / NHL / ALL  (ALL = " + "+".join(BULK_BOUNDARY) + ")")
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    sports = BULK_BOUNDARY if args.sport.upper() == "ALL" else [args.sport.upper()]
    for sp in sports:
        run(sp, args.db, args.dry_run)


if __name__ == "__main__":
    main()

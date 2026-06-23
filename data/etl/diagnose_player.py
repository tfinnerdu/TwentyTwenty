#!/usr/bin/env python3
"""
data/etl/diagnose_player.py
===========================
"Why isn't <player> matching <category>?"  Dump everything the game DB knows
about one or more players so a miss can be pinned to its cause WITHOUT guessing.
Read-only, local, no network.

For each name it shows every record across ALL sports (the cross-sport model
keeps an NFL stat record and an NCAAF college record as SEPARATE rows), the bio/
stat fields that drive categories, the categories the record IS in, and -- the
useful part -- categories it WOULD match by value but doesn't, split into:
  * stale memberships  (value + scope both ok, not a member -> re-run load)
  * scope-blocked      (value ok but the category is X-scoped and this record is
                        a different sport -> you need a record in sport X)

That last bucket is usually the answer for "NFL player didn't count as going to
<college>": the college categories are NCAAF/NCAAB-scoped, so the NFL record can't
satisfy them -- the player needs a college-sport record too.

    python -m data.etl.diagnose_player "Baker Mayfield" "Marquise Brown"
    python -m data.etl.diagnose_player "Baker Mayfield" --db data/db_all.db
"""

import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, ROOT)

from data.etl.load import get_categories, player_matches
from data.etl.schema import get_conn

DB_PATH = os.path.join(ROOT, "data", "nfl.db")

# Fields worth printing when set (skip the noise). total_tds etc. are the ones
# categories key off; teams/active_decades/college are the membership drivers.
SHOW = ["position", "position_group", "college", "draft_year", "draft_round",
        "draft_pick", "debut_year", "final_year", "total_tds", "pass_yds",
        "rush_yds", "rec_yds", "nba_points", "mlb_hr", "nhl_goals",
        "teams", "active_decades"]


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def diagnose(conn, query):
    cats = get_categories()
    labels = {c["id"]: c for c in cats}
    rows = conn.execute("SELECT * FROM players WHERE name LIKE ? COLLATE NOCASE",
                        (f"%{query}%",)).fetchall()
    close = [r for r in rows if _norm(query) in _norm(r["name"]) or _norm(r["name"]) in _norm(query)]
    rows = close or rows

    print(f"\n{'='*70}\n{query!r}  ->  {len(rows)} record(s)")
    if not rows:
        print("  NO record by that name. Either a typo, or the player isn't in this DB\n"
              "  at all (e.g. no NCAAF/college record was ever crawled for them).")
        return
    sports = sorted({r["sport"] for r in rows})
    print(f"  sports present: {sports}")

    for r in rows:
        d = dict(r)
        for f in ("teams", "active_decades"):
            try:
                d[f] = json.loads(d[f]) if d[f] else []
            except Exception:
                d[f] = []
        print(f"\n  [{d['sport']}] {d['name']}   id={d['id']}  sr_id={d.get('sr_id')}  source={d.get('data_source','?')}")
        shown = [f"{f}={d[f]}" for f in SHOW if d.get(f) not in (None, "", 0, [])]
        print("    " + ("; ".join(shown) if shown else "(no notable fields set)"))
        if not d.get("college"):
            print("    !! college is EMPTY for this record")

        memids = {m[0] for m in conn.execute(
            "SELECT category_id FROM player_categories WHERE player_id=?", (d["id"],))}
        print(f"    member of {len(memids)} categor(ies): {', '.join(sorted(memids)) or '(none)'}")

        stale, blocked = [], []
        for c in cats:
            try:
                hit = player_matches(d, c)
            except Exception:
                hit = False
            if not hit:
                continue
            scope_ok = c["scope"] == "ALL" or c["scope"] == d["sport"]
            if scope_ok and c["id"] not in memids:
                stale.append(c)
            elif not scope_ok:
                blocked.append(c)
        for c in stale:
            print(f"    ?? satisfies {c['id']} ({c['label']}) but is NOT a member "
                  f"-> stale memberships; re-run `python -m data.etl.load`")
        for c in blocked:
            print(f"    -- would match {c['id']} ({c['label']}) by value, but it's "
                  f"{c['scope']}-scoped and this record is {d['sport']} "
                  f"-> needs a {c['scope']} record for {d['name']}")

    # Cross-record hint: a college category needs a college-sport record.
    college = next((dict(r).get("college") for r in rows if dict(r).get("college")), None)
    have_college_sport = any(r["sport"] in ("NCAAF", "NCAAB", "NCAAW") for r in rows)
    if college and not have_college_sport:
        print(f"\n  NOTE: a record lists college={college!r} but there is NO NCAAF/NCAAB/NCAAW\n"
              f"  record for this name. The 'Played at <school>' categories are college-scoped,\n"
              f"  so this player can't match them until a college record exists.")


def cache_report(names):
    """Scan the cached Pro-Football-Reference pages and show what each player's page
    would actually contribute -- the franchises off its /teams/<code>/ hrefs (incl.
    mid-season trades), the season span, the college -- i.e. whether 2025 or a
    traded team is even ON DISK for `backfill_from_cache --rebuild-teams` to use."""
    import glob
    import re as _re
    from data.etl.backfill_sr import _soup, parse_meta, CACHE_DIR
    from data.etl.teams import nfl_teams_from_page

    if not os.path.isdir(CACHE_DIR):
        print(f"\n  no SR page cache at {CACHE_DIR} -- nothing was crawled locally.")
        return
    targets = {_norm(n): n for n in names}
    found = {}
    files = glob.glob(os.path.join(CACHE_DIR, "*.html"))
    print(f"\n  scanning {len(files)} cached SR pages for {list(targets.values())} "
          f"(stops early once all are found)...")
    for fn in files:
        if len(found) == len(targets):
            break
        try:
            soup = _soup(open(fn, encoding="utf-8", errors="ignore").read())
        except Exception:
            continue
        h = soup.select_one("div#meta h1")
        if not h:
            continue
        key = _norm(h.get_text(strip=True))
        if key not in targets or key in found:
            continue
        years = set()
        for row in soup.select("table tbody tr"):
            cell = (row.select_one("[data-stat='year_id']")
                    or row.select_one("[data-stat='season']") or row.select_one("th"))
            m = _re.search(r"(19|20)\d{2}", cell.get_text(strip=True)) if cell else None
            if m:
                years.add(int(m.group(0)))
        found[key] = (h.get_text(strip=True), nfl_teams_from_page(soup),
                      parse_meta(soup).get("college", ""), (min(years), max(years)) if years else None)

    for key, orig in targets.items():
        if key in found:
            nm, teams, college, span = found[key]
            print(f"\n  [cache] {nm}: page FOUND")
            print(f"    seasons on page : {span[0]}-{span[1]}" if span else "    seasons on page : (none parsed)")
            print(f"    teams from page : {teams}   <- what --rebuild-teams would set")
            print(f"    college on page : {college!r}")
        else:
            print(f"\n  [cache] {orig}: NO cached page -> not crawled; backfill can't fill from cache")


def main():
    ap = argparse.ArgumentParser(description="Dump a player's DB records + category fit (read-only)")
    ap.add_argument("names", nargs="+", help="player name(s), e.g. \"Baker Mayfield\"")
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--cache", action="store_true",
                    help="also scan data/cache/sr/ and report what each player's cached "
                         "PFR page would contribute (teams/season-span/college)")
    args = ap.parse_args()
    conn = get_conn(args.db)
    for name in args.names:
        diagnose(conn, name)
    conn.close()
    if args.cache:
        cache_report(args.names)


if __name__ == "__main__":
    main()

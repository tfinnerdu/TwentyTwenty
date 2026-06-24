"""
data/etl/cache_index.py
=======================
A PERSISTENT, incrementally-updated index of the Sports-Reference page cache
(data/cache/sr/). Parsing thousands of cached HTML pages with BeautifulSoup is
the slow part of every cache-based step -- and the runbook does it repeatedly
(backfill_from_cache for NFL, then again for NBA, then db_all --from-cache). This
caches the *parsed* result so only NEW or CHANGED pages are ever re-parsed.

How it stays correct:
  - keyed by cache filename (an md5(url) hash); each entry stores the file's
    mtime + size. A page re-fetched with fresher data gets a new mtime -> it's
    re-parsed. A deleted page drops out. Everything else is reused verbatim.
  - a format VERSION guards the parse logic itself: bump it when the parsers below
    change and the whole index rebuilds (so a code change can't serve stale parses).

What it stores per player page (granular, sport-neutral, so every consumer can
derive exactly what it needs without touching the HTML again):
  sport / sr_id / name / norm_name / birth_year, the bio meta dict, the page's
  teams, active decades + debut/final year, and raw career stat components
  (NFL keeps pass_td and rush+rec TD separate so a QB's total isn't undercounted).

Consumers:
  refresh()                       -> {fname: entry}   (updates the store first)
  by_name(index, sport, ...)      -> {norm_name: [(meta, birth_year, teams, stats)]}
                                     (the shape backfill_from_cache expects)
  players(index, sports)          -> {sport: [upsert-ready rec, ...]}
                                     (what db_all --from-cache ingests)

The index file lives under data/cache/ (already gitignored) -- it's a pure
derived artifact, safe to delete; it just rebuilds on the next run.
"""

import glob
import json
import logging
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, ROOT)

from data.etl.backfill_sr import _soup, parse_meta, CACHE_DIR
from data.etl.sr_history import (HISTORY, parse_player_history, _sr_id_from_href,
                                 _find_table)
from data.etl.providers.csv_season import NBAProvider, NFLProvider, NHLProvider

log = logging.getLogger("cache_index")

INDEX_PATH = os.path.join(CACHE_DIR, "sr_index.json")
# Bump whenever the parse logic below (or the stored shape) changes, to force a
# full re-parse instead of serving stale entries from an older code version.
VERSION = 1

# Granular NFL stat cfg: keep passing TDs (pass_td) separate from rush+rec TDs
# (rr_td) so each consumer can sum its own total. franchise:'nfl' makes
# parse_player_history standardize teams off the /teams/<code>/ href (registry).
NFL_INDEX_CFG = {
    "franchise": "nfl", "team_map": NFLProvider.TEAM_MAP,
    "stat_tables": [
        ("passing", {"pass_yds": "pass_yds", "pass_td": "pass_td"}),
        ("rushing_and_receiving", {"rush_yds": "rush_yds", "rec_yds": "rec_yds",
                                   "rush_receive_td": "rr_td"}),
    ],
}
# Mirrors db_all.FULL['NHL'] (kept here so cache_index imports nothing from db_all
# -- db_all imports US). parse_player_history only needs team_map + stat_*.
NHL_INDEX_CFG = {
    "team_map": NHLProvider.TEAM_MAP, "stat_table": "player_stats", "stat_league": "NHL",
    "stat_cols": {"goals": "nhl_goals", "assists": "nhl_assists",
                  "points": "nhl_points", "games": "nhl_games"},
}
# The four pro sports that share parse_player_history. NBA/WNBA reuse the game's
# HISTORY cfg; NFL/NHL get the configs above.
INDEX_CFG = {"NBA": HISTORY["NBA"], "WNBA": HISTORY["WNBA"],
             "NHL": NHL_INDEX_CFG, "NFL": NFL_INDEX_CFG}

# Every stat column any sport's cfg can emit -- split out of the parsed record
# into stat_raw so the bio meta stays clean.
STAT_KEYS = ("pass_yds", "pass_td", "rush_yds", "rec_yds", "rr_td",
             "nba_points", "nba_rebounds", "nba_assists", "nba_games",
             "wnba_points", "wnba_rebounds", "wnba_assists", "wnba_games",
             "nhl_goals", "nhl_assists", "nhl_points", "nhl_games")


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _page_url(soup):
    """The player's canonical SR URL off the cached page. The cache filename is an
    md5(url) hash, so sport + slug have to come from the page itself: canonical
    link first, og:url second."""
    link = soup.find("link", rel="canonical")
    if link and link.get("href"):
        return link["href"]
    og = soup.find("meta", attrs={"property": "og:url"})
    if og and og.get("content"):
        return og["content"]
    return ""


def _sport_from_url(url):
    """Map an SR player URL to one of the four pro alpha sports that share
    parse_player_history. College (cbb/cfb) and MLB pages also live in the cache
    but parse via sr_college / Lahman, so they return None -- still indexed (for
    name matching) but never placed into db_all."""
    u = (url or "").lower()
    if "basketball-reference.com" in u:
        return "WNBA" if "/wnba/" in u else "NBA"
    if "hockey-reference.com" in u:
        return "NHL"
    if "pro-football-reference.com" in u:
        return "NFL"
    return None


def _nba_teams_from_page(soup):
    """Ordered, de-duped NBA franchises off the bbref per_game_stats rows
    (team_name_abbr -> NBAProvider.TEAM_MAP). Skips the multi-team TOT/2TM rows
    and unmapped codes (so a stray abbr can't leak in as a 'team')."""
    tmap = NBAProvider.TEAM_MAP
    table = _find_table(soup, "per_game_stats")
    teams = []
    if not table:
        return teams
    for row in table.select("tbody tr"):
        cell = (row.select_one("[data-stat='team_name_abbr']")
                or row.select_one("[data-stat='team_id']") or row.select_one("[data-stat='team']"))
        abbr = cell.get_text(strip=True) if cell else ""
        if not abbr or abbr.upper() in ("TOT", "2TM", "3TM", "4TM", ""):
            continue
        nick = tmap.get(abbr.upper())
        if nick and nick not in teams:
            teams.append(nick)
    return teams


def _parse_file(path):
    """Parse one cached page into a stored entry. Non-player pages (search/index)
    and unreadable files are recorded as is_player:False so they're not re-parsed."""
    try:
        soup = _soup(open(path, encoding="utf-8", errors="ignore").read())
    except Exception:
        return {"is_player": False}
    if not soup.select_one("div#meta"):
        return {"is_player": False}
    h1 = soup.select_one("div#meta h1")
    name = h1.get_text(strip=True) if h1 else ""
    url = _page_url(soup)
    srid = _sr_id_from_href(url)
    sport = _sport_from_url(url)

    if sport in INDEX_CFG and name:
        cfg = INDEX_CFG[sport]
        rec = parse_player_history(soup, cfg["team_map"], cfg)  # meta + teams + decades + stats
        if sport == "NBA":                                      # match backfill: per_game_stats only, no unmapped junk
            rec["teams"] = _nba_teams_from_page(soup)
        stat_raw = {k: rec.pop(k) for k in STAT_KEYS if k in rec}
        teams = rec.pop("teams", [])
        decades = rec.pop("active_decades", None)
        debut = rec.pop("debut_year", None)
        final = rec.pop("final_year", None)
        meta = rec                                              # whatever's left is bio
        return {"is_player": True, "sport": sport, "url": url,
                "sr_id": (f"{sport.lower()}_{srid}" if srid else None),
                "name": name, "norm_name": _norm(name), "birth_year": meta.get("birth_year"),
                "meta": meta, "teams": teams, "stat_raw": stat_raw,
                "active_decades": decades, "debut_year": debut, "final_year": final}

    # college / MLB / undetected: index the bio for name-matching, but no pro
    # teams/stats and no sr_id (it's not one of the four pro sports we place).
    meta = parse_meta(soup)
    return {"is_player": bool(name), "sport": sport, "url": url, "sr_id": None,
            "name": name, "norm_name": _norm(name), "birth_year": meta.get("birth_year"),
            "meta": meta, "teams": [], "stat_raw": {},
            "active_decades": None, "debut_year": None, "final_year": None}


def _load():
    if not os.path.exists(INDEX_PATH):
        return {}
    try:
        return json.load(open(INDEX_PATH, encoding="utf-8"))
    except Exception as e:
        log.warning(f"cache index unreadable ({e}); rebuilding from scratch")
        return {}


def _save(store):
    os.makedirs(CACHE_DIR, exist_ok=True)
    tmp = INDEX_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(store, fh)
    os.replace(tmp, INDEX_PATH)            # atomic: a crash mid-write can't corrupt the index


def refresh(verbose=True):
    """Bring the persistent index in line with the cache on disk and return it as
    {fname: entry}. Only new/changed pages (by mtime+size) are parsed; everything
    else is reused. A VERSION mismatch forces a full re-parse."""
    files = {os.path.basename(f): f for f in glob.glob(os.path.join(CACHE_DIR, "*.html"))}
    store = _load()
    pages = store.get("pages", {}) if store.get("_meta", {}).get("version") == VERSION else {}

    for fn in list(pages):                 # drop entries whose file is gone
        if fn not in files:
            del pages[fn]

    to_parse = []
    for fn, path in files.items():
        try:
            st = os.stat(path)
        except OSError:
            continue
        e = pages.get(fn)
        if e and e.get("mtime") == st.st_mtime and e.get("size") == st.st_size:
            continue                       # unchanged -> reuse the stored parse
        to_parse.append((fn, path, st.st_mtime, st.st_size))

    if verbose:
        log.info(f"cache index: {len(files)} pages on disk, {len(to_parse)} new/changed "
                 f"to parse, {len(files) - len(to_parse)} reused")
    for i, (fn, path, mtime, size) in enumerate(to_parse, 1):
        if verbose and (i % 5000 == 0 or i == len(to_parse)):
            log.info(f"  parsed {i}/{len(to_parse)}")
        entry = _parse_file(path)
        entry["mtime"], entry["size"] = mtime, size
        pages[fn] = entry

    if to_parse:                           # persist only when something actually changed
        _save({"_meta": {"version": VERSION}, "pages": pages})
    if verbose:
        npl = sum(1 for e in pages.values() if e.get("is_player"))
        log.info(f"cache index ready: {npl} player pages "
                 f"({len(to_parse)} parsed this run, rest from index)")
    return pages


def _derive_stats(entry, sport):
    """Build a sport's stat dict from the granular stat_raw, matching what the
    old per-sport parsers returned. NFL sums pass + rush + rec TDs into total_tds."""
    sr = entry.get("stat_raw") or {}
    if sport == "NFL":
        out = {c: int(sr[c]) for c in ("pass_yds", "rush_yds", "rec_yds") if c in sr}
        if "pass_td" in sr or "rr_td" in sr:
            out["total_tds"] = int(sr.get("pass_td", 0)) + int(sr.get("rr_td", 0))
        return out
    cols = {"NBA": ("nba_points", "nba_rebounds", "nba_assists", "nba_games"),
            "WNBA": ("wnba_points", "wnba_rebounds", "wnba_assists", "wnba_games"),
            "NHL": ("nhl_goals", "nhl_assists", "nhl_points", "nhl_games")}.get(sport, ())
    return {k: sr[k] for k in cols if k in sr}


def by_name(index, sport, with_teams=False, with_stats=False):
    """{norm_name: [(meta, birth_year, teams, stats), ...]} -- the structure
    backfill_from_cache matches DB players against. teams/stats are filled only for
    pages whose own sport matches `sport` (and only when requested), so a same-name
    player from another sport can't donate the wrong franchises."""
    out = {}
    for e in index.values():
        if not e.get("is_player"):
            continue
        meta = e.get("meta") or {}
        same = e.get("sport") == sport
        teams = (e.get("teams") or []) if (with_teams and same) else []
        stats = _derive_stats(e, sport) if (with_stats and same) else {}
        if not meta and not teams and not stats:
            continue
        out.setdefault(e["norm_name"], []).append((meta, e.get("birth_year"), teams, stats))
    return out


def players(index, sports):
    """{sport: [upsert-ready rec, ...]} for db_all --from-cache: every indexed pro
    player page, grouped by sport, as a payload parse_player_history would have
    produced (bio + teams + decades + stats + sport/sr_id/name)."""
    by_sport = {sp: [] for sp in sports}
    for e in index.values():
        sp = e.get("sport")
        if not e.get("is_player") or sp not in by_sport or not e.get("name") or not e.get("sr_id"):
            continue
        rec = dict(e.get("meta") or {})
        if e.get("teams"):
            rec["teams"] = e["teams"]
        if e.get("active_decades"):
            rec["active_decades"] = e["active_decades"]
        if e.get("debut_year"):
            rec["debut_year"], rec["final_year"] = e["debut_year"], e.get("final_year")
        rec.update(_derive_stats(e, sp))
        rec.update({"sport": sp, "sr_id": e["sr_id"], "name": e["name"]})
        by_sport[sp].append(rec)
    return by_sport


def main():
    """CLI: refresh the index (and report what's in it). Run it standalone to warm
    the cache index before the backfill steps, or just let those steps call it."""
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                        datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser(description="Refresh the persistent SR page-cache index")
    ap.add_argument("--rebuild", action="store_true", help="ignore the stored index; re-parse every page")
    args = ap.parse_args()
    if args.rebuild and os.path.exists(INDEX_PATH):
        os.remove(INDEX_PATH)
        log.info("removed existing index -- full rebuild")
    idx = refresh()
    by_sport = {}
    for e in idx.values():
        if e.get("is_player"):
            by_sport[e.get("sport")] = by_sport.get(e.get("sport"), 0) + 1
    for sp, n in sorted(by_sport.items(), key=lambda kv: (kv[0] is None, kv[0] or "")):
        log.info(f"  {sp or '(other/college/MLB)':24} {n}")


if __name__ == "__main__":
    main()

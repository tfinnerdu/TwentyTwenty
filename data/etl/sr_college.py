"""
data/etl/sr_college.py
======================
College honors crawler -- pulls NOTABLE historical college players from
Sports-Reference's award pages (the recognizable names a trivia game wants),
rather than every walk-on via the alphabetical index.

    NCAAF   sports-reference.com/cfb/awards/   (All-America, Heisman, nat'l/conf awards)
    NCAAB   sports-reference.com/cbb/awards/men/   (All-America, AP/Naismith/Wooden POY, ...)
    NCAAW   sports-reference.com/cbb/awards/women/ (All-America, award winners, ...)

sports-reference.com isn't Cloudflare-gated (no cookie needed). Flow:
  1. fetch the award hub(s), auto-discover the individual award pages (one level
     deep; per-decade All-America pages included, noisy per-year pages skipped).
  2. pull every player link off those pages, tagging the honor each page confers
     (All-America -> *_all_american, Heisman -> ncaaf_heisman, POY -> *_player_of_year).
  3. for a player ALREADY in the DB (e.g. a post-2003 All-American from the bulk
     hoopR/wehoop pull) just set the honor flag -- no page fetch. For one not yet
     loaded (the pre-bulk legends) fetch the page, parse school/years/position,
     and create the row.

    python -m data.etl.sr_college --sport NCAAB --dry-run    # what it'd pull/flag
    python -m data.etl.sr_college --sport NCAAB --limit 50   # small live check
    python -m data.etl.sr_college --sport ALL

Honors come straight from which page a player appears on, so they're reliable.
SR is blocked from CI, so dry-run + a small --limit shakedown before a full run.
"""

import argparse
import logging
import os
import re
import sys
from urllib.parse import urljoin

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, ROOT)

from data.etl.backfill_sr import make_session, get_page, parse_meta, Jailed, _load_env_file
from data.etl.providers.base import upsert_players
from data.etl.load import derive_fields, rebuild_categories
from data.etl.schema import get_conn, migrate

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("sr_college")

DB_PATH = os.path.join(ROOT, "data", "nfl.db")
CHECKPOINT = 50

HONORS = {
    "NCAAF": {
        "hub": "https://www.sports-reference.com/cfb/awards/",
        "domain": "https://www.sports-reference.com",
        "awards": "/cfb/awards/", "players": "/cfb/players/",
        "honor_map": [("heisman", "ncaaf_heisman"), ("all-america", "ncaaf_all_american")],
    },
    "NCAAB": {
        "hub": "https://www.sports-reference.com/cbb/awards/",
        "domain": "https://www.sports-reference.com",
        "awards": "/cbb/awards/men/", "players": "/cbb/players/",
        "honor_map": [("all-america", "ncaab_all_american"),
                      ("poy", "ncaab_player_of_year"), ("naismith", "ncaab_player_of_year"),
                      ("wooden", "ncaab_player_of_year")],
    },
    "NCAAW": {
        "hub": "https://www.sports-reference.com/cbb/awards/",
        "domain": "https://www.sports-reference.com",
        "awards": "/cbb/awards/women/", "players": "/cbb/players/",
        "honor_map": [("all-america", "ncaab_all_american"),
                      ("poy", "ncaab_player_of_year"), ("mop", "ncaab_player_of_year")],
    },
}


def _slug(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _is_year_page(url):
    """True for noisy per-year pages (heisman-2024.html) but not decade ranges
    (all-america-1980-1989.html)."""
    return bool(re.search(r"-\d{4}\.html$", url)) and not re.search(r"\d{4}-\d{4}", url)


def _honor_cols(award_url, honor_map):
    low = award_url.lower()
    return {col for key, col in honor_map if key in low}


def _award_links(soup, base, awards):
    """Absolute award-page URLs on a page (resolves relative hrefs)."""
    for a in soup.find_all("a", href=True):
        full = urljoin(base, a["href"]).split("#")[0]
        if awards in full and full.endswith(".html"):
            yield full


def discover_award_pages(session, cfg, delay):
    """Hub -> the individual award pages (one level deep, skipping per-year noise)."""
    pages, seen = [], set()
    hub = get_page(session, cfg["hub"], delay)
    frontier = []
    if hub is not None:
        for full in _award_links(hub, cfg["hub"], cfg["awards"]):
            if full not in seen and not _is_year_page(full):
                seen.add(full)
                frontier.append(full)
    # follow each award page one level (to reach the per-decade All-America lists)
    for url in list(frontier):
        pages.append(url)
        soup = get_page(session, url, delay)
        if soup is None:
            continue
        for full in _award_links(soup, url, cfg["awards"]):
            if full not in seen and not _is_year_page(full):
                seen.add(full)
                pages.append(full)
    log.info(f"  discovered {len(pages)} award pages")
    return pages


def collect_players(session, pages, cfg, delay):
    """award pages -> {player_url: (name, {honor_cols})}."""
    found = {}
    for url in pages:
        soup = get_page(session, url, delay)
        if soup is None:
            continue
        cols = _honor_cols(url, cfg["honor_map"])
        for a in soup.find_all("a", href=True):
            full = urljoin(url, a["href"]).split("#")[0]
            if cfg["players"] not in full or not full.endswith(".html"):
                continue
            name = a.get_text(strip=True)
            if not name:
                continue
            nm, hc = found.get(full, (name, set()))
            found[full] = (nm or name, hc | cols)
    return found


def parse_college_player(soup, sport):
    """Bio + schools + active decades from an SR college player page."""
    out = dict(parse_meta(soup))           # position / birth / draft / ht / wt (no /colleges/ link here)
    schools, years = [], set()
    for row in soup.select("table tbody tr"):
        scell = row.select_one("[data-stat='school_name'] a, [data-stat='team_name'] a, "
                               "[data-stat='school_name'], [data-stat='team_name']")
        if not scell:
            continue
        school = scell.get_text(strip=True)
        if not school or school.upper() in ("TOT", "OVERALL"):
            continue
        if school not in schools:
            schools.append(school)
        szn = row.select_one("[data-stat='season'], [data-stat='year_id']")
        if szn:
            m = re.search(r"(\d{4})", szn.get_text())
            if m:
                years.add(int(m.group(1)))
    if schools:
        out["teams"] = schools
        out["college"] = schools[0]
    if years:
        out["active_decades"] = sorted({f"{(y // 10) * 10}s" for y in years})
        out["debut_year"], out["final_year"] = min(years), max(years)
    return out


def existing_by_name(conn, sport):
    return {_slug(r[1]): r[0] for r in conn.execute(
        "SELECT id, name FROM players WHERE sport=?", (sport,)) if r[1]}


def crawl(sport, db, limit, delay, dry_run, cf_clearance, user_agent):
    cfg = HONORS[sport]
    migrate(db)
    conn = get_conn(db)
    have = existing_by_name(conn, sport)
    session = make_session(sport, cf_clearance, user_agent)

    log.info(f"[{sport}] discovering award pages...")
    try:
        pages = discover_award_pages(session, cfg, delay)
        found = collect_players(session, pages, cfg, delay)
    except Jailed as e:
        log.error(f"[{sport}] {e}")
        conn.close()
        return

    # split: already in DB (honor-flag only) vs new (fetch + create)
    flag, fetch = [], []
    for url, (name, cols) in found.items():
        pid = have.get(_slug(name))
        if pid:
            flag.append((pid, name, cols))
        else:
            fetch.append((url, name, cols))
    log.info(f"[{sport}] {len(found)} honored players: {len(flag)} already loaded "
             f"(flag only), {len(fetch)} new to fetch"
             + (" (dry run)" if dry_run else ""))
    if dry_run:
        for url, name, cols in fetch[:30]:
            log.info(f"    new: {name}  {sorted(cols)}  ({url})")
        for pid, name, cols in flag[:10]:
            log.info(f"    flag existing: {name}  {sorted(cols)}")
        conn.close()
        return

    cur = conn.cursor()
    cur.execute("INSERT INTO etl_runs (sport, source, run_at, status, notes) "
                "VALUES (?, 'sr_college', datetime('now'), 'running', 'award-page honors')", (sport,))
    conn.commit()
    run_id = cur.lastrowid

    # 1) flag honors on players already in the DB -- no fetch needed
    flagged = 0
    for pid, name, cols in flag:
        if not cols:
            continue
        sets = ", ".join(f"{c}=1" for c in cols)
        cur.execute(f"UPDATE players SET {sets} WHERE id=?", (pid,))
        flagged += 1
    conn.commit()

    # 2) fetch + create the pre-bulk honored players
    if limit:
        fetch = fetch[:limit]
    batch, added, updated, parsed, stopped = [], 0, 0, 0, None

    def flush():
        nonlocal added, updated, batch
        if batch:
            a, u = upsert_players(conn, batch, "sr_college", run_id)
            added += a
            updated += u
            batch = []

    try:
        for url, name, cols in fetch:
            soup = get_page(session, url, delay)
            if soup is None:
                continue
            rec = parse_college_player(soup, sport)
            rec.update({"sport": sport, "sr_id": f"{sport.lower()}_{_sr_id(url)}", "name": name})
            for c in cols:
                rec[c] = 1
            batch.append(rec)
            parsed += 1
            if len(batch) >= CHECKPOINT:
                flush()
                log.info(f"  {sport}: checkpoint -- {parsed} parsed, {added} saved")
    except Jailed as e:
        stopped = f"jailed after {parsed}"
        log.error(str(e))
    except KeyboardInterrupt:
        stopped = f"interrupted after {parsed}"
        log.warning(f"  {sport}: interrupted -- saving...")

    flush()
    derive_fields(conn)
    rebuild_categories(conn, sport)
    cur.execute("UPDATE etl_runs SET players_added=?, players_updated=?, status='ok', notes=? WHERE id=?",
                (added, flagged + updated, stopped or f"{flagged} flagged, {added} created", run_id))
    conn.commit()
    conn.close()
    log.info(f"[{sport}] college honors: {flagged} flagged on existing, +{added} new created"
             + (f" -- STOPPED: {stopped} (saved; re-run resumes from cache)" if stopped else ""))


def _sr_id(url):
    m = re.search(r"/([a-z0-9-]+)\.html", url or "")
    return m.group(1) if m else _slug(url)


def main():
    ap = argparse.ArgumentParser(description="College honors crawler (SR award pages)")
    ap.add_argument("--sport", required=True, help="NCAAF / NCAAB / NCAAW / ALL")
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--limit", type=int, default=None, help="Cap NEW players fetched (validation)")
    ap.add_argument("--delay", type=float, default=5.0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--cf-clearance", help="overrides SR_CF_CLEARANCE from .env")
    ap.add_argument("--user-agent")
    args = ap.parse_args()

    _load_env_file(os.path.join(ROOT, ".env"))
    cf = args.cf_clearance or os.environ.get("SR_CF_CLEARANCE")
    ua = args.user_agent or os.environ.get("SR_CF_UA")

    sports = list(HONORS) if args.sport.upper() == "ALL" else [args.sport.upper()]
    for sp in sports:
        if sp not in HONORS:
            log.error(f"Unknown sport {sp!r}; known: {', '.join(HONORS)} (or ALL)")
            continue
        crawl(sp, args.db, args.limit, args.delay, args.dry_run, cf, ua)


if __name__ == "__main__":
    main()

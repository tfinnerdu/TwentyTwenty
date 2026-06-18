"""
data/etl/sr_history.py
======================
Targeted Sports-Reference HISTORICAL backfill -- pulls the pre-coverage eras the
bulk/GitHub sources miss, as full new player rows (not just gap-filling existing
ones, which is backfill_sr.py's job).

Coverage gaps it targets (everything earlier than each bulk source's start):
    NBA   pre-1999   (jacobbaruch GitHub set starts 1999)  <- the marquee gap
    WNBA  pre-2003   (wehoop starts 2003)
    NHL   pre-1918+  (fills older seasons the NHL API thins out)
    NFL   pre-1999   (nflverse modern era)
MLB is already complete back to 1871 via Lahman, so it's intentionally absent.
NCAAF/NCAAB live on sports-reference.com with a different index layout and far
larger volume -- configured but flagged; validate those separately.

How it works (reusing backfill_sr's polite, cookie-aware, cached session):
  1. enumerate every player from SR's alphabetical index (/players/a/ ...),
     reading each one's first/last season.
  2. keep only those whose career reaches into the gap (year_min < cutoff) AND
     who aren't already in the DB (matched by normalized name) -> no duplicates.
  3. fetch each target page once, parse bio (the tested parse_meta) + teams +
     active decades, and upsert. Honors come from the curated awards overlay.

    python -m data.etl.sr_history --sport NBA --dry-run     # list who'd be pulled
    python -m data.etl.sr_history --sport NBA --limit 50    # small live validation
    python -m data.etl.sr_history --sport ALL               # every configured gap

Politeness/safety are inherited from backfill_sr: 5s delay, cookie + curl_cffi,
page cache (so stops/restarts resume), and the first-403 abort in cookie mode.
SR is blocked from CI sandboxes, so do a --dry-run then a small --limit run to
confirm parsing before a full crawl.
"""

import argparse
import logging
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, ROOT)

from data.etl.backfill_sr import (make_session, get_page, _soup, parse_meta, Jailed,
                                  _load_env_file, _cell)
from data.etl.providers.base import upsert_players
from data.etl.providers.csv_season import (NBAProvider, WNBAProvider, NHLProvider, NFLProvider)
from data.etl.load import derive_fields, rebuild_categories
from data.etl.schema import get_conn, migrate

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("sr_history")

DB_PATH = os.path.join(ROOT, "data", "nfl.db")
LETTERS = "abcdefghijklmnopqrstuvwxyz"
CHECKPOINT = 50        # upsert+commit every N parsed players (durable progress)

# Per-sport crawl config. `alpha` is the per-letter index; `cutoff` is the first
# year the bulk source covers (we keep players whose career starts before it);
# `team_map` reuses the provider's abbrev->nickname table (fallback: the abbrev).
HISTORY = {
    "NBA": {
        "domain": "https://www.basketball-reference.com",
        "alpha":  "https://www.basketball-reference.com/players/{letter}/",
        "cutoff": 1999, "team_map": NBAProvider.TEAM_MAP,
    },
    "WNBA": {
        "domain": "https://www.basketball-reference.com",
        "alpha":  "https://www.basketball-reference.com/wnba/players/{letter}/",
        "cutoff": 2003, "team_map": WNBAProvider.TEAM_MAP,
    },
    "NHL": {
        "domain": "https://www.hockey-reference.com",
        "alpha":  "https://www.hockey-reference.com/players/{letter}/",
        # NHL is small (~7.5k all-time); pull everything the API missed and let
        # the name-dedup drop the ~4.2k already loaded. Tune with --cutoff.
        "cutoff": 2026, "team_map": NHLProvider.TEAM_MAP,
    },
    "NFL": {
        "domain": "https://www.pro-football-reference.com",
        "alpha":  "https://www.pro-football-reference.com/players/{letter}/",
        "cutoff": 1999, "team_map": NFLProvider.TEAM_MAP,
        "upper_letters": True,     # PFR's index is /players/A/ ... (uppercase)
    },
}


def _slug(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _sr_id_from_href(href):
    """/players/j/jordami01.html -> jordami01 ; /players/A/AbduKa00.html -> AbduKa00
    (PFR player ids are mixed-case)."""
    m = re.search(r"/([A-Za-z0-9.-]+)\.html$", href or "")
    return m.group(1) if m else ""


# player page: lowercase ids end .html; PFR ids are mixed-case and end .htm
PLAYER_HREF = re.compile(r"/players/[A-Za-z]/[A-Za-z0-9.'-]+\.html?$")


def parse_alpha_index(soup):
    """Yield (name, href, year_min, year_max) for every player link on an SR
    alphabetical index page. basketball-reference lays it out as a <table> with
    year_min/year_max cells; hockey-/wnba-/pro-football-reference use <p> tags
    with the years as trailing text -- handle both."""
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not PLAYER_HREF.search(href) or href in seen:
            continue
        name = a.get_text(strip=True)
        if not name:
            continue
        seen.add(href)
        ymin = ymax = None
        row = a.find_parent("tr")
        if row:                                  # table style (basketball-reference)
            mn = re.search(r"(\d{4})", _cell(row, "year_min"))
            mx = re.search(r"(\d{4})", _cell(row, "year_max"))
            ymin = int(mn.group(1)) if mn else None
            ymax = int(mx.group(1)) if mx else None
        if ymin is None:                         # <p> style: years in trailing text
            p = a.find_parent(["p", "li", "div"])
            yrs = re.findall(r"(?:19|20)\d{2}", p.get_text()) if p else []
            if yrs:
                ymin, ymax = int(min(yrs)), int(max(yrs))
        yield name, href, ymin, ymax


def parse_player_history(soup, team_map):
    """Bio (tested parse_meta) + teams + active decades pulled from the player's
    season table. Returns a payload dict (sans sport/sr_id/name)."""
    out = dict(parse_meta(soup))   # college / position / birth / draft / ht / wt

    teams, years = [], set()
    for row in soup.select("table tbody tr"):
        # Only count a row with a real pro team, so its season-year is one the
        # player actually played (a college/other table on the page would
        # otherwise leak years -- e.g. an LSU 1988 row adding a bogus 1980s).
        # Team data-stat varies: team_name_abbr (NHL/NFL), team (WNBA), team_id.
        abbr = _cell(row, "team_name_abbr", "team_id", "team").upper()
        if not abbr or abbr in ("TOT", "2TM", "3TM", "4TM", "TM"):
            continue
        nick = team_map.get(abbr, abbr)            # fallback: the abbreviation
        if nick and nick not in teams:
            teams.append(nick)
        m = re.search(r"(\d{4})", _cell(row, "season", "year_id", "year"))  # season varies too
        if m:
            years.add(int(m.group(1)))
    if teams:
        out["teams"] = teams
    if years:
        out["active_decades"] = sorted({f"{(y // 10) * 10}s" for y in years})
        out["debut_year"], out["final_year"] = min(years), max(years)
    return out


def existing_name_slugs(conn, sport):
    return {_slug(r[0]) for r in conn.execute(
        "SELECT name FROM players WHERE sport=?", (sport,)) if r[0]}


def enumerate_targets(session, sport, cfg, delay, have_slugs, cutoff):
    """All players whose career reaches into the gap and aren't already loaded."""
    targets, seen = [], set()
    letters = LETTERS.upper() if cfg.get("upper_letters") else LETTERS
    for letter in letters:
        url = cfg["alpha"].format(letter=letter)
        soup = get_page(session, url, delay)
        if soup is None:
            log.warning(f"  {sport}: index /{letter}/ unavailable -- skipping")
            continue
        n_letter = 0
        for name, href, ymin, ymax in parse_alpha_index(soup):
            srid = _sr_id_from_href(href)
            if not srid or srid in seen:
                continue
            # in the gap = career began before the bulk source's coverage
            if ymin is not None and ymin >= cutoff:
                continue
            if _slug(name) in have_slugs:        # already in DB from a bulk source
                continue
            seen.add(srid)
            full = href if href.startswith("http") else cfg["domain"] + href
            targets.append((srid, name, full))
            n_letter += 1
        log.info(f"  {sport}: /{letter}/ -> {n_letter} gap players (running {len(targets)})")
    return targets


def crawl(sport, db, limit, delay, dry_run, cf_clearance, user_agent, cutoff=None):
    cfg = HISTORY[sport]
    cutoff = cutoff or cfg["cutoff"]
    migrate(db)
    conn = get_conn(db)
    have = existing_name_slugs(conn, sport)
    session = make_session(sport, cf_clearance, user_agent)

    log.info(f"[{sport}] enumerating SR alphabetical index (gap = started before {cutoff})...")
    try:
        targets = enumerate_targets(session, sport, cfg, delay, have, cutoff)
    except Jailed as e:
        log.error(f"[{sport}] {e}")
        log.error(f"[{sport}] can't enumerate without SR access -- set SR_CF_CLEARANCE "
                  f"in .env (see backfill_sr) and retry.")
        conn.close()
        return
    if limit:
        targets = targets[:limit]
    log.info(f"[{sport}] {len(targets)} historical players to pull"
             + (" (dry run)" if dry_run else ""))
    if dry_run:
        for srid, name, url in targets[:40]:
            log.info(f"    would pull: {name}  ({url})")
        conn.close()
        return

    cur = conn.cursor()
    cur.execute("INSERT INTO etl_runs (sport, source, run_at, status, notes) "
                "VALUES (?, 'sr_history', datetime('now'), 'running', ?)",
                (sport, f"history pre-{cfg['cutoff']}"))
    conn.commit()
    run_id = cur.lastrowid

    # Persist in checkpoints so progress survives a jail / Ctrl-C / crash. Each
    # flush commits (upsert_players commits internally) and the page cache means
    # a re-run resumes from where the network left off, re-parsing cached pages.
    batch, added, updated, parsed, stopped = [], 0, 0, 0, None

    def flush():
        nonlocal added, updated, batch
        if batch:
            a, u = upsert_players(conn, batch, "sr_history", run_id)
            added += a
            updated += u
            batch = []

    try:
        for i, (srid, name, url) in enumerate(targets, 1):
            soup = get_page(session, url, delay)
            if soup is None:
                continue
            rec = parse_player_history(soup, cfg["team_map"])
            rec.update({"sport": sport, "sr_id": f"{sport.lower()}_{srid}", "name": name})
            batch.append(rec)
            if len(batch) >= CHECKPOINT:
                flush()
                log.info(f"  {sport}: checkpoint -- {parsed + 1} parsed, {added} saved")
            parsed += 1
    except Jailed as e:
        stopped = f"jailed after {parsed} ({e})"
        log.error(str(e))
    except KeyboardInterrupt:
        stopped = f"interrupted by user after {parsed}"
        log.warning(f"  {sport}: interrupted -- saving what's parsed so far...")

    flush()                                   # persist the final partial batch
    if added or updated:
        derive_fields(conn)
        rebuild_categories(conn, sport)       # so the partial data is usable now
    cur.execute("UPDATE etl_runs SET players_added=?, players_updated=?, status='ok', notes=? WHERE id=?",
                (added, updated, stopped or f"history pre-{cutoff} ({parsed} parsed)", run_id))
    conn.commit()
    conn.close()
    log.info(f"[{sport}] history: +{added} new, {updated} updated"
             + (f" -- STOPPED: {stopped} (saved; re-run to resume from cache)" if stopped else ""))


def main():
    ap = argparse.ArgumentParser(description="Targeted Sports-Reference historical backfill")
    ap.add_argument("--sport", required=True, help="NBA / WNBA / NHL / NFL / ALL")
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--limit", type=int, default=None, help="Cap players per sport (validation)")
    ap.add_argument("--delay", type=float, default=5.0)
    ap.add_argument("--dry-run", action="store_true", help="List who'd be pulled; fetch nothing extra")
    ap.add_argument("--cf-clearance", help="overrides SR_CF_CLEARANCE from .env")
    ap.add_argument("--user-agent")
    ap.add_argument("--cutoff", type=int, default=None,
                    help="Override the per-sport cutoff: pull players who started before "
                         "this year (and aren't already loaded). Higher = more history.")
    args = ap.parse_args()

    _load_env_file(os.path.join(ROOT, ".env"))
    cf = args.cf_clearance or os.environ.get("SR_CF_CLEARANCE")
    ua = args.user_agent or os.environ.get("SR_CF_UA")

    sports = list(HISTORY) if args.sport.upper() == "ALL" else [args.sport.upper()]
    for sp in sports:
        if sp not in HISTORY:
            log.error(f"Unknown/unsupported sport {sp!r}; known: {', '.join(HISTORY)} (or ALL)")
            continue
        crawl(sp, args.db, args.limit, args.delay, args.dry_run, cf, ua, args.cutoff)


if __name__ == "__main__":
    main()

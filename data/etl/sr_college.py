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

from data.etl.backfill_sr import make_session, get_page, parse_meta, Jailed, _load_env_file, _cell
from data.etl.providers.base import upsert_players
from data.etl.providers.csv_season import NFLProvider, NHLProvider
from data.etl.load import derive_fields, rebuild_categories
from data.etl.schema import get_conn, migrate

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("sr_college")

DB_PATH = os.path.join(ROOT, "data", "nfl.db")
CHECKPOINT = 50

# create=True: award winners not already loaded are fetched + created (the
#   college/NHL/NFL legends that pre-date each bulk pull). create=False: only
#   flag existing rows by name.
# parser="pro": player pages carry pro teams, not schools -- parse with
#   sr_history.parse_player_history + a tricode->nickname team_map (NHL, NFL).
# honor_map:   substring match on the award URL (loose award families).
# slug_honors: EXACT award-slug -> column (precise -- separates national awards
#   from same-suffixed conference ones, e.g. 'ap-poy' vs 'acc-poy').
# default_honor: column to flag when nothing above matches (so every award page
#   still keeps its players; college uses the all-conference keeper here).
HONORS = {
    "NCAAF": {
        "hub": "https://www.sports-reference.com/cfb/awards/",
        "domain": "https://www.sports-reference.com",
        "awards": "/cfb/awards/", "players": "/cfb/players/", "create": True,
        "honor_map": [("heisman", "ncaaf_heisman"), ("all-america", "ncaaf_all_american")],
        "default_honor": "ncaaf_all_american",   # keep every award-list player (incl. conference)
    },
    "NCAAB": {
        "hub": "https://www.sports-reference.com/cbb/awards/",
        "domain": "https://www.sports-reference.com",
        "awards": "/cbb/awards/men/", "players": "/cbb/players/", "create": True,
        # National All-America TEAMS + national PLAYER-OF-THE-YEAR awards are
        # matched by EXACT award slug, so conference pages (acc-poy, all-acc,
        # acc-all-frosh, ...) can't masquerade as national honors. Everyone else
        # on an award list falls to the all-conference keeper (kept, separate
        # category). 'parade-all-america' is intentionally excluded -- it's a
        # high-school team, not a college honor.
        "honor_map": [],
        "slug_honors": {
            "consensus-all-america": "ncaab_all_american", "ap-all-america": "ncaab_all_american",
            "usbwa-all-america": "ncaab_all_american", "nabc-all-america": "ncaab_all_american",
            "sport-news-all-america": "ncaab_all_american", "wooden-all-america": "ncaab_all_american",
            "chuck-all-america": "ncaab_all_american",
            "naismith": "ncaab_player_of_year", "wooden": "ncaab_player_of_year",
            "ap-poy": "ncaab_player_of_year", "usbwa-poy": "ncaab_player_of_year",
            "nabc-poy": "ncaab_player_of_year", "tsn-poy": "ncaab_player_of_year",
            "upi-poy": "ncaab_player_of_year", "helms-poy": "ncaab_player_of_year",
            "rupp": "ncaab_player_of_year",
        },
        "default_honor": "ncaab_all_conference",   # keep every other award-list player
    },
    "NCAAW": {
        "hub": "https://www.sports-reference.com/cbb/awards/",
        "domain": "https://www.sports-reference.com",
        "awards": "/cbb/awards/women/", "players": "/cbb/players/", "create": True,
        "honor_map": [],
        "slug_honors": {
            "ap-all-america": "ncaab_all_american", "usbwa-all-america": "ncaab_all_american",
            "wbca-all-america": "ncaab_all_american", "wooden-all-america": "ncaab_all_american",
            "naismith": "ncaab_player_of_year", "wooden": "ncaab_player_of_year",
            "ap-poy": "ncaab_player_of_year", "usbwa-poy": "ncaab_player_of_year",
            "wbca-poy": "ncaab_player_of_year", "wade": "ncaab_player_of_year",
            "honda": "ncaab_player_of_year",
        },
        "default_honor": "ncaab_all_conference",   # keep every other award-list player
    },
    "NHL": {
        # pre-bulk history = award winners only. The nhl_api bulk crawls
        # present-day rosters (~2000+), so pre-2000 legends on relocated/defunct
        # teams are missing -- create=True with the PRO parser (teams, tricode
        # ->nickname) builds them from the award lists. Modern players already
        # loaded are flag-only.
        "hub": "https://www.hockey-reference.com/awards/",
        "domain": "https://www.hockey-reference.com",
        "awards": "/awards/", "players": "/players/", "create": True,
        "parser": "pro", "team_map": NHLProvider.TEAM_MAP,
        "honor_map": [("hart", "nhl_mvps"), ("stanley", "nhl_championships")],
        "default_honor": "nhl_all_star",   # Norris/Vezina/HHOF/Top-100/... -> notable
        # weekly/monthly "3 stars" nods would flag thousands as All-Stars -- skip
        "skip": ("weekly", "monthly", "3star"),
    },
    "NFL": {
        # pre-1999 history = award winners only (Pro Bowl / All-Pro / MVP / HOF,
        # All-Pro back to 1922). create=True with the PRO parser (teams, .htm,
        # mixed-case ids). Modern NFL keeps its full nflverse heft separately.
        "hub": "https://www.pro-football-reference.com/awards/",
        "domain": "https://www.pro-football-reference.com",
        # named awards live in /awards/; All-Pro teams (back to 1922) at
        # /years/YYYY/allpro.htm -- match both. The hub links every leaf page, so
        # don't follow, and keep the per-year pages (they ARE the lists).
        "awards": ("/awards/", "/allpro.htm"), "players": "/players/",
        "create": True, "parser": "pro", "team_map": NFLProvider.TEAM_MAP,
        "follow": False, "skip_years": False,
        # players/rookies of the week/month would flag thousands -- skip
        "skip": ("of-the-week", "of-the-month"),
        "honor_map": [("mvp", "nfl_mvps"), ("all-pro", "ap_all_pro"),
                      ("allpro", "ap_all_pro"), ("super-bowl", "super_bowls")],
        "default_honor": None,             # include all awards; flag specific ones
    },
}


_SUFFIX = re.compile(r"\s+(?:jr|sr|ii|iii|iv|v)\.?$", re.I)


def _slug(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _base_slug(s):
    """Slug with a trailing generational suffix dropped. SR award pages render
    'Greg Oden Jr' where the bulk has 'Greg Oden'; matching on the base too stops
    us creating a duplicate. The exact slug is always tried first, so genuine
    father/son pairs (Gary Payton vs Gary Payton II) still resolve separately."""
    return _slug(_SUFFIX.sub("", (s or "").strip()))


def _is_year_page(url):
    """True for noisy per-year pages (heisman-2024.html) but not decade ranges
    (all-america-1980-1989.html). .htm? covers PFR's .htm."""
    return bool(re.search(r"-\d{4}\.html?$", url)) and not re.search(r"\d{4}-\d{4}", url)


def _award_slug(url):
    """Award page's slug: filename minus extension and any trailing year/decade.
    .../consensus-all-america-2020-2029.html -> consensus-all-america ;
    .../ap-poy.html -> ap-poy ; .../naismith.html -> naismith."""
    m = re.search(r"/([a-z0-9-]+)\.html?$", (url or "").lower())
    slug = m.group(1) if m else ""
    return re.sub(r"-\d{4}(?:-\d{4})?$", "", slug)


def _honor_cols(url, cfg):
    """Honor columns a given award page confers (unioned):
      honor_map   -- substring match on the URL (loose award families, e.g. NHL 'hart')
      slug_honors -- EXACT award-slug match (precise: national 'ap-poy' vs conf 'acc-poy')
    Falls back to default_honor so every award page still keeps its players."""
    low = (url or "").lower()
    cols = {col for key, col in cfg.get("honor_map", []) if key in low}
    col = (cfg.get("slug_honors") or {}).get(_award_slug(low))
    if col:
        cols.add(col)
    if not cols and cfg.get("default_honor"):
        cols = {cfg["default_honor"]}      # any award page still marks its players notable
    return cols


def _award_links(soup, base, awards):
    """Absolute award-page URLs on a page (resolves relative hrefs). `awards` is
    a path substring or tuple of them; match any. Allows .htm (PFR)."""
    aw = (awards,) if isinstance(awards, str) else awards
    for a in soup.find_all("a", href=True):
        full = urljoin(base, a["href"]).split("#")[0]
        if full.endswith((".htm", ".html")) and any(s in full for s in aw):
            yield full


def discover_award_pages(session, cfg, delay):
    """Hub -> the individual award pages. `follow` (default True) chases one
    level to reach college decade lists; `skip_years` (default True) drops the
    redundant per-year pages -- both off for NFL, whose per-year /allpro/ pages
    ARE the lists."""
    pages, seen = [], set()
    skip = cfg.get("skip", ())
    skip_years = cfg.get("skip_years", True)
    awards = cfg["awards"]

    def _ok(u):
        if u in seen or any(s in u for s in skip):
            return False
        return not (skip_years and _is_year_page(u))

    hub = get_page(session, cfg["hub"], delay)
    frontier = []
    if hub is not None:
        for full in _award_links(hub, cfg["hub"], awards):
            if _ok(full):
                seen.add(full)
                frontier.append(full)
    pages.extend(frontier)
    if cfg.get("follow", True):
        for url in frontier:
            soup = get_page(session, url, delay)
            if soup is None:
                continue
            for full in _award_links(soup, url, awards):
                if _ok(full):
                    seen.add(full)
                    pages.append(full)
    log.info(f"  discovered {len(pages)} award pages")
    return pages


def collect_players(session, pages, cfg, delay):
    """award pages -> {player_url: (name, {honor_cols})}. Only links inside the
    stat TABLES count: SR pages carry sidebar 'trending / recent searches' widgets
    that link to arbitrary players (incl. women on a men's award page, or NBA
    pros) -- those sit outside any table, so scoping to tables drops them."""
    found = {}
    for url in pages:
        soup = get_page(session, url, delay)
        if soup is None:
            continue
        cols = _honor_cols(url, cfg)
        for table in soup.find_all("table"):
            for a in table.find_all("a", href=True):
                full = urljoin(url, a["href"]).split("#")[0]
                if cfg["players"] not in full or not full.endswith((".htm", ".html")):
                    continue
                name = a.get_text(strip=True)
                if not name:
                    continue
                nm, hc = found.get(full, (name, set()))
                found[full] = (nm or name, hc | cols)
    return found


def parse_college_player(soup, sport):
    """Bio + schools + active decades from an SR college player page."""
    out = dict(parse_meta(soup))           # position / birth / ht / wt (no /colleges/ link here)
    schools, years = [], set()
    for row in soup.select("table tbody tr"):
        # college player pages put the SCHOOL in team_name_abbr ("Iowa", "Florida")
        school = _cell(row, "team_name_abbr", "school_name", "team_name")
        if not school or school.upper() in ("TOT", "OVERALL", "CAREER"):
            continue
        if school not in schools:
            schools.append(school)
        m = re.search(r"(\d{4})", _cell(row, "year_id", "season", "year"))
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
    have = {}
    for pid, name in conn.execute("SELECT id, name FROM players WHERE sport=?", (sport,)):
        if not name:
            continue
        have.setdefault(_slug(name), pid)        # exact name wins
        have.setdefault(_base_slug(name), pid)   # suffix-stripped fallback (won't clobber exact)
    return have


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
        pid = have.get(_slug(name)) or have.get(_base_slug(name))
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

    # 2) fetch + create the pre-bulk honored players. A flag-only sport
    #    (create=False) skips this -- an award winner not already loaded just
    #    stays unflagged rather than being created.
    if not cfg.get("create", True):
        fetch = []
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
            if cfg.get("parser") == "pro":     # NFL: teams (not schools) via the history parser
                from data.etl.sr_history import parse_player_history
                rec = parse_player_history(soup, cfg.get("team_map", {}))
            else:
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
    # cbb/cfb: lowercase-slug.html ; PFR: mixed-case .htm (MahoPa00.htm)
    m = re.search(r"/([A-Za-z0-9.-]+)\.html?$", url or "")
    return m.group(1) if m else _slug(url)


def main():
    ap = argparse.ArgumentParser(description="College honors crawler (SR award pages)")
    ap.add_argument("--sport", required=True, help="NCAAF / NCAAB / NCAAW / NHL / ALL")
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

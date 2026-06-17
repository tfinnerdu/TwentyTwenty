"""
data/etl/scrape_nfl.py
----------------------
Scrapes NFL player data from pro-football-reference.com.

Usage:
    python data/etl/scrape_nfl.py                  # scrape top players, save cache
    python data/etl/scrape_nfl.py --from-cache     # rebuild DB from existing cache only
    python data/etl/scrape_nfl.py --limit 50       # quick test run

Output:
    data/cache/nfl_raw.json   -- raw scraped data, never overwritten unless --refresh
    data/nfl.db               -- updated player rows with last_updated timestamps

Rate limiting:
    1 request/second by default. PFR allows 20/min; we stay well under.
    --delay 2.0 to be extra polite if you're pulling a large batch.

Run this from YOUR MACHINE (residential IP), not from a hosted server.
PFR blocks datacenter IPs.
"""

import argparse
import json
import os
import sys
import time
import logging
from datetime import datetime, timezone
from typing import Optional

import requests
from bs4 import BeautifulSoup

# Allow imports from project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from data.etl.schema import get_conn, migrate

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

BASE_URL   = "https://www.pro-football-reference.com"
CACHE_DIR  = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cache")
CACHE_FILE = os.path.join(CACHE_DIR, "nfl_raw.json")
DB_PATH    = os.path.join(os.path.dirname(os.path.dirname(__file__)), "nfl.db")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# -------------------------------------------------------------------------
# Leader pages we scrape to discover player IDs
# Each entry: (url_path, stat_column_hint, min_value)
# -------------------------------------------------------------------------
LEADER_PAGES = [
    # Passing leaders
    ("/leaders/pass_yds_career.htm",   "pass_yds",   10000),
    # Rushing leaders
    ("/leaders/rush_yds_career.htm",   "rush_yds",   5000),
    # Receiving leaders
    ("/leaders/rec_yds_career.htm",    "rec_yds",    5000),
    # Touchdown leaders (all players)
    ("/leaders/all_td_career.htm",     "total_tds",  50),
    # Sack leaders (defense)
    ("/leaders/sacks_career.htm",      "sacks",      50),
    # Interception leaders (defense)
    ("/leaders/def_int_career.htm",    "interceptions", 20),
    # Pro Bowl selections (covers all positions)
    ("/leaders/probowl_career.htm",    "pro_bowls",  3),
]

# NFL team name normalization - PFR abbreviation -> full name
TEAM_MAP = {
    "crd": "Cardinals", "atl": "Falcons", "rav": "Ravens", "buf": "Bills",
    "car": "Panthers", "chi": "Bears", "cin": "Bengals", "cle": "Browns",
    "dal": "Cowboys", "den": "Broncos", "det": "Lions", "gnb": "Packers",
    "htx": "Texans", "clt": "Colts", "jax": "Jaguars", "kan": "Chiefs",
    "rai": "Raiders", "sdg": "Chargers", "lac": "Chargers", "ram": "Rams",
    "mia": "Dolphins", "min": "Vikings", "nwe": "Patriots", "nor": "Saints",
    "nyg": "Giants", "nyj": "Jets", "phi": "Eagles", "pit": "Steelers",
    "sfo": "49ers", "sea": "Seahawks", "tam": "Buccaneers", "oti": "Titans",
    "was": "Redskins", "lvr": "Raiders", "lar": "Rams",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


# Tracks consecutive 403s so we can bail out of a jailed session fast
_consecutive_403s = 0
_MAX_403s = 3  # abort sport after this many in a row

def fetch(url: str, delay: float = 1.0) -> Optional[BeautifulSoup]:
    global _consecutive_403s
    time.sleep(delay)
    try:
        r = SESSION.get(url, timeout=15)

        if r.status_code == 403:
            _consecutive_403s += 1
            log.warning(
                f"HTTP 403 for {url} "
                f"({_consecutive_403s}/{_MAX_403s} consecutive -- "
                f"session may be jailed, see sports-reference.com bot policy)"
            )
            if _consecutive_403s >= _MAX_403s:
                log.error(
                    "Too many 403s in a row. Session is likely jailed (up to 24h). "
                    "Wait and retry, or try a different network/IP."
                )
            return None

        if r.status_code == 429:
            _consecutive_403s = 0
            log.warning("Rate limited (429) -- sleeping 60s then retrying")
            time.sleep(60)
            r = SESSION.get(url, timeout=15)

        if r.status_code != 200:
            log.warning(f"HTTP {r.status_code} for {url}")
            return None

        _consecutive_403s = 0  # reset on success
        return BeautifulSoup(r.text, "html.parser")
    except Exception as e:
        log.error(f"Fetch error for {url}: {e}")
        return None


# -------------------------------------------------------------------------
# Step 1: Discover player IDs from leaderboard pages
# -------------------------------------------------------------------------

def discover_player_ids(delay: float = 1.0) -> set:
    """
    Scrape the career leaderboard pages to collect a set of PFR player IDs.
    Returns set of strings like 'BradTo00'.
    """
    player_ids = set()

    for path, stat, min_val in LEADER_PAGES:
        url = BASE_URL + path
        log.info(f"  Discovering from {path}")
        soup = fetch(url, delay)
        if not soup:
            continue

        # PFR leaderboard tables have player links like /players/B/BradTo00.htm
        for a in soup.select("table#leaders tbody tr td a[href*='/players/']"):
            href = a.get("href", "")
            parts = href.strip("/").split("/")
            # parts: ['players', 'B', 'BradTo00.htm']
            if len(parts) == 3 and parts[0] == "players":
                pid = parts[2].replace(".htm", "")
                player_ids.add(pid)

    log.info(f"Discovered {len(player_ids)} unique player IDs")
    return player_ids


# -------------------------------------------------------------------------
# Step 2: Scrape individual player pages
# -------------------------------------------------------------------------

def parse_player_page(soup: BeautifulSoup, sr_id: str) -> Optional[dict]:
    """Extract all fields from a PFR player page into a flat dict."""

    # --- Name ---
    h1 = soup.select_one("div#meta h1 span")
    if not h1:
        return None
    name = h1.get_text(strip=True)

    # --- Bio block ---
    meta = soup.select_one("div#meta")
    bio = {}
    if meta:
        for p in meta.select("p"):
            text = p.get_text(" ", strip=True)
            # Position
            if "Position:" in text:
                bio["position"] = text.split("Position:")[-1].split("▸")[0].strip().split()[0]
            # College
            if "College:" in text or "Colleges:" in text:
                college_link = p.select_one("a[href*='/friv/colleges']")
                if college_link:
                    bio["college"] = college_link.get_text(strip=True)
            # High school
            if "High School:" in text:
                bio["high_school"] = text.split("High School:")[-1].strip().split("\n")[0]
            # Birth date / birthplace
            birth_span = p.select_one("span#necro-birth[data-birth]")
            if birth_span:
                bio["birth_date"] = birth_span.get("data-birth", "")
                bio["birth_year"] = int(bio["birth_date"][:4]) if bio.get("birth_date") else None
            birth_place = p.select_one("span.f-i")
            if birth_place:
                place_text = birth_place.get_text(" ", strip=True)
                parts = [x.strip() for x in place_text.split(",")]
                if len(parts) >= 2:
                    bio["birth_city"] = parts[0]
                    bio["birth_state"] = parts[1][:2] if len(parts[1]) >= 2 else parts[1]
            # Height/weight
            if "lb" in text and "," in text:
                hw = text.strip().split(",")
                for seg in hw:
                    seg = seg.strip()
                    if "-" in seg and len(seg) < 8:
                        try:
                            ft, inch = seg.split("-")
                            bio["height_inches"] = int(ft) * 12 + int(inch)
                        except Exception:
                            pass
                    if "lb" in seg:
                        try:
                            bio["weight_lbs"] = int(seg.replace("lb", "").strip())
                        except Exception:
                            pass

    # --- Teams played for ---
    teams = []
    team_links = soup.select("div#meta p a[href*='/teams/']")
    for a in team_links:
        abbr = a.get("href", "").strip("/").split("/")[-2].lower()
        team_name = TEAM_MAP.get(abbr)
        if team_name and team_name not in teams:
            teams.append(team_name)

    # --- Career stats (totals row in stat tables) ---
    stats = {
        "pass_yds": 0, "rush_yds": 0, "rec_yds": 0,
        "pass_tds": 0, "rush_tds": 0, "rec_tds": 0,
        "total_tds": 0, "pro_bowls": 0, "sacks": 0.0, "interceptions": 0,
    }

    # Passing
    passing_table = soup.select_one("table#passing")
    if passing_table:
        career_row = passing_table.select_one("tfoot tr")
        if career_row:
            _td = lambda s: _int(career_row.select_one(f"td[data-stat='{s}']"))
            stats["pass_yds"] = _td("pass_yds")
            stats["pass_tds"] = _td("pass_td")

    # Rushing
    rush_table = soup.select_one("table#rushing")
    if rush_table:
        career_row = rush_table.select_one("tfoot tr")
        if career_row:
            stats["rush_yds"] = _int(career_row.select_one("td[data-stat='rush_yds']"))
            stats["rush_tds"] = _int(career_row.select_one("td[data-stat='rush_td']"))

    # Receiving
    rec_table = soup.select_one("table#receiving")
    if rec_table:
        career_row = rec_table.select_one("tfoot tr")
        if career_row:
            stats["rec_yds"] = _int(career_row.select_one("td[data-stat='rec_yds']"))
            stats["rec_tds"] = _int(career_row.select_one("td[data-stat='rec_td']"))

    # Defense (sacks, interceptions)
    def_table = soup.select_one("table#defense")
    if def_table:
        career_row = def_table.select_one("tfoot tr")
        if career_row:
            sack_cell = career_row.select_one("td[data-stat='sacks']")
            if sack_cell and sack_cell.get_text(strip=True):
                try:
                    stats["sacks"] = float(sack_cell.get_text(strip=True))
                except Exception:
                    pass
            stats["interceptions"] = _int(career_row.select_one("td[data-stat='def_int']"))

    stats["total_tds"] = stats["pass_tds"] + stats["rush_tds"] + stats["rec_tds"]

    # --- Pro Bowls and Draft ---
    leaderboard = soup.select_one("div#leaderboard_pro_bowls")
    if leaderboard:
        val = leaderboard.select_one("p.leaderboard_number")
        if val:
            try:
                stats["pro_bowls"] = int(val.get_text(strip=True))
            except Exception:
                pass

    draft_info = {}
    draft_p = soup.find("p", string=lambda t: t and "Draft:" in t)
    if not draft_p:
        draft_p = soup.find("p", text=lambda t: t and "Draft:" in t)
    if draft_p:
        text = draft_p.get_text(" ", strip=True)
        # e.g. "Draft: New England Patriots in the 6th round, 199th pick"
        try:
            import re
            round_m = re.search(r"(\d+)(st|nd|rd|th) round", text)
            pick_m  = re.search(r"(\d+)(st|nd|rd|th) pick", text)
            year_m  = re.search(r"\b(19|20)\d{2}\b", text)
            if round_m: draft_info["draft_round"] = int(round_m.group(1))
            if pick_m:  draft_info["draft_pick"]  = int(pick_m.group(1))
            if year_m:  draft_info["draft_year"]  = int(year_m.group(0))
        except Exception:
            pass

    # --- Active decades from season rows ---
    decades = set()
    for row in soup.select("table tbody tr[id]"):
        th = row.select_one("th[data-stat='year_id']")
        if th:
            year_text = th.get_text(strip=True).replace("*", "").replace("+", "")
            try:
                year = int(year_text)
                decade = f"{(year // 10) * 10}s"
                decades.add(decade)
            except ValueError:
                pass

    debut = min((int(d[:4]) for d in decades if d[:4].isdigit()), default=None)
    final = max((int(d[:4]) + 9 for d in decades if d[:4].isdigit()), default=None)

    return {
        "sport":       "NFL",
        "sr_id":       sr_id,
        "name":        name,
        "position":    bio.get("position", ""),
        "college":     bio.get("college", ""),
        "high_school": bio.get("high_school", ""),
        "birth_date":  bio.get("birth_date", ""),
        "birth_year":  bio.get("birth_year"),
        "birth_city":  bio.get("birth_city", ""),
        "birth_state": bio.get("birth_state", ""),
        "birth_country": "USA",
        "height_inches": bio.get("height_inches"),
        "weight_lbs":    bio.get("weight_lbs"),
        "teams":         teams,
        "active_decades": sorted(decades),
        "debut_year":    debut,
        "final_year":    final,
        "draft_year":    draft_info.get("draft_year"),
        "draft_round":   draft_info.get("draft_round", 0),
        "draft_pick":    draft_info.get("draft_pick", 0),
        **stats,
    }


def _int(cell) -> int:
    if cell is None:
        return 0
    text = cell.get_text(strip=True).replace(",", "")
    try:
        return int(text)
    except (ValueError, TypeError):
        return 0


def scrape_player(sr_id: str, delay: float = 1.0) -> Optional[dict]:
    letter = sr_id[0].upper()
    url = f"{BASE_URL}/players/{letter}/{sr_id}.htm"
    soup = fetch(url, delay)
    if not soup:
        return None
    return parse_player_page(soup, sr_id)


# -------------------------------------------------------------------------
# Step 3: Load scraped data into the DB
# -------------------------------------------------------------------------

def load_into_db(players: list, conn, etl_run_id: int) -> tuple:
    """
    Upsert players into the DB. Returns (added, updated) counts.
    Sets last_updated on every touched row.
    """
    c = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    added = updated = 0

    for p in players:
        c.execute("SELECT id FROM players WHERE sr_id = ?", (p.get("sr_id"),))
        existing = c.fetchone()

        if existing:
            c.execute("""
                UPDATE players SET
                    name=?, position=?, college=?, high_school=?,
                    birth_date=?, birth_year=?, birth_city=?, birth_state=?, birth_country=?,
                    height_inches=?, weight_lbs=?,
                    teams=?, active_decades=?, debut_year=?, final_year=?,
                    draft_year=?, draft_round=?, draft_pick=?,
                    pass_yds=?, rush_yds=?, rec_yds=?,
                    pass_tds=?, rush_tds=?, rec_tds=?, total_tds=?,
                    pro_bowls=?, sacks=?, interceptions=?,
                    data_source=?, last_updated=?, etl_run_id=?
                WHERE sr_id=?
            """, (
                p["name"], p.get("position"), p.get("college"), p.get("high_school"),
                p.get("birth_date"), p.get("birth_year"), p.get("birth_city"),
                p.get("birth_state"), p.get("birth_country", "USA"),
                p.get("height_inches"), p.get("weight_lbs"),
                json.dumps(p.get("teams", [])),
                json.dumps(p.get("active_decades", [])),
                p.get("debut_year"), p.get("final_year"),
                p.get("draft_year"), p.get("draft_round", 0), p.get("draft_pick", 0),
                p.get("pass_yds", 0), p.get("rush_yds", 0), p.get("rec_yds", 0),
                p.get("pass_tds", 0), p.get("rush_tds", 0), p.get("rec_tds", 0),
                p.get("total_tds", 0), p.get("pro_bowls", 0),
                p.get("sacks", 0.0), p.get("interceptions", 0),
                "pfr", now, etl_run_id, p["sr_id"],
            ))
            updated += 1
        else:
            c.execute("""
                INSERT INTO players (
                    sport, sr_id, name, position, college, high_school,
                    birth_date, birth_year, birth_city, birth_state, birth_country,
                    height_inches, weight_lbs,
                    teams, active_decades, debut_year, final_year,
                    draft_year, draft_round, draft_pick,
                    pass_yds, rush_yds, rec_yds,
                    pass_tds, rush_tds, rec_tds, total_tds,
                    pro_bowls, sacks, interceptions,
                    data_source, last_updated, etl_run_id
                ) VALUES (
                    'NFL',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
                )
            """, (
                p.get("sr_id"), p["name"], p.get("position"), p.get("college"),
                p.get("high_school"), p.get("birth_date"), p.get("birth_year"),
                p.get("birth_city"), p.get("birth_state"), p.get("birth_country", "USA"),
                p.get("height_inches"), p.get("weight_lbs"),
                json.dumps(p.get("teams", [])),
                json.dumps(p.get("active_decades", [])),
                p.get("debut_year"), p.get("final_year"),
                p.get("draft_year"), p.get("draft_round", 0), p.get("draft_pick", 0),
                p.get("pass_yds", 0), p.get("rush_yds", 0), p.get("rec_yds", 0),
                p.get("pass_tds", 0), p.get("rush_tds", 0), p.get("rec_tds", 0),
                p.get("total_tds", 0), p.get("pro_bowls", 0),
                p.get("sacks", 0.0), p.get("interceptions", 0),
                "pfr", now, etl_run_id,
            ))
            added += 1

    conn.commit()
    return added, updated


# -------------------------------------------------------------------------
# Main
# -------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Scrape NFL players from pro-football-reference.com")
    parser.add_argument("--from-cache",  action="store_true", help="Skip scraping, load from existing cache")
    parser.add_argument("--refresh",     action="store_true", help="Re-scrape even if cache exists")
    parser.add_argument("--delay",       type=float, default=1.0, help="Seconds between requests (default: 1.0)")
    parser.add_argument("--limit",       type=int, default=None, help="Max players to scrape (for testing)")
    parser.add_argument("--db",          type=str, default=DB_PATH, help="Database path")
    args = parser.parse_args()

    os.makedirs(CACHE_DIR, exist_ok=True)

    log.info("=== 20/20 NFL ETL ===")

    # --- Ensure schema exists ---
    migrate(args.db)
    conn = get_conn(args.db)

    # --- Register ETL run ---
    now = datetime.now(timezone.utc).isoformat()
    c = conn.cursor()
    c.execute("""
        INSERT INTO etl_runs (sport, source, run_at, status, notes)
        VALUES ('NFL', 'pfr', ?, 'running', ?)
    """, (now, f"delay={args.delay}s limit={args.limit}"))
    conn.commit()
    etl_run_id = c.lastrowid
    log.info(f"ETL run ID: {etl_run_id}")

    # --- Load or scrape raw data ---
    raw_players = []

    if not args.from_cache and (args.refresh or not os.path.exists(CACHE_FILE)):
        log.info("Phase 1: Discovering player IDs from leaderboard pages...")
        player_ids = discover_player_ids(args.delay)

        if args.limit:
            player_ids = set(list(player_ids)[:args.limit])
            log.info(f"Limited to {args.limit} players for this run")

        log.info(f"Phase 2: Scraping {len(player_ids)} player pages...")
        failed = 0
        for i, pid in enumerate(sorted(player_ids), 1):
            log.info(f"  [{i}/{len(player_ids)}] {pid}")
            data = scrape_player(pid, args.delay)
            if data:
                raw_players.append(data)
            else:
                failed += 1
                log.warning(f"  FAILED: {pid}")

        log.info(f"Scraping complete: {len(raw_players)} ok, {failed} failed")

        # Save cache
        with open(CACHE_FILE, "w") as f:
            json.dump({
                "scraped_at": now,
                "player_count": len(raw_players),
                "players": raw_players,
            }, f, indent=2)
        log.info(f"Cache saved: {CACHE_FILE}")

    else:
        if not os.path.exists(CACHE_FILE):
            log.error(f"No cache found at {CACHE_FILE}. Run without --from-cache first.")
            sys.exit(1)
        log.info(f"Loading from cache: {CACHE_FILE}")
        with open(CACHE_FILE) as f:
            cache = json.load(f)
        raw_players = cache["players"]
        log.info(f"Loaded {len(raw_players)} players from cache (scraped {cache.get('scraped_at', 'unknown')})")

    # --- Load into DB ---
    log.info(f"Phase 3: Loading {len(raw_players)} players into DB...")
    added, updated = load_into_db(raw_players, conn, etl_run_id)

    # --- Finalize ETL run record ---
    c.execute("""
        UPDATE etl_runs SET players_added=?, players_updated=?, status='ok'
        WHERE id=?
    """, (added, updated, etl_run_id))
    conn.commit()

    log.info(f"Done: {added} added, {updated} updated")
    log.info(f"DB: {args.db}")

    conn.close()


if __name__ == "__main__":
    main()
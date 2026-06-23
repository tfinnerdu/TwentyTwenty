"""
data/etl/scrape_ncaaf.py
------------------------
Scrapes NCAAF player data from sports-reference.com/cfb/.

Key differences from pro scrapers:
  - URL: sports-reference.com/cfb/players/[name-based-id].html
  - ID format: firstname-lastname-01.html  (name-based, not opaque code)
  - Discovery: Heisman winners + career leaders + All-American lists
  - Stats: college career only (passing, rushing, receiving, defense)
  - Cross-sport angle: same players appear in NFL DB — "college" field links them

Usage:
    python data/etl/scrape_ncaaf.py
    python data/etl/scrape_ncaaf.py --from-cache
    python data/etl/scrape_ncaaf.py --limit 30

Run from your machine (residential IP).
"""

import argparse, json, os, re, sys, time, logging
from datetime import datetime, timezone
from typing import Optional

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from data.etl.schema import get_conn, migrate
from data.etl.teams import canonical_school

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger(__name__)

BASE_URL   = "https://www.sports-reference.com"
CACHE_DIR  = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cache")
CACHE_FILE = os.path.join(CACHE_DIR, "ncaaf_raw.json")
DB_PATH    = os.path.join(os.path.dirname(os.path.dirname(__file__)), "nfl.db")

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0.0.0 Safari/537.36"),
    "Accept-Language": "en-US,en;q=0.9",
}

# Discovery sources -- award pages and career leaderboards
# These give us the most notable players worth including
# sports-reference.com blocks award/leader pages aggressively.
# Use the static alphabetical player index instead -- one page per letter,
# plain HTML, lists every player with their school inline.
# CFB player index: /cfb/players/a-index.htm  (note: .htm not .html)
PLAYER_INDEX_LETTERS = "abcdefghijklmnopqrstuvwxyz"

# School-name normalization is shared with every other college path via
# data.etl.teams.canonical_school ("Ohio St." -> "Ohio State", "Miami (FL)" ->
# "Miami"), so all sources converge on one spelling per school.

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


def discover_player_ids(delay: float = 1.0) -> set:
    """
    Use static alphabetical player index pages.
    sports-reference.com blocks award/leader pages with bot detection.
    The index pages (/cfb/players/a-index.htm) are plain HTML and
    list every player with basic info. We filter by games played to
    focus on players with meaningful careers.
    """
    player_ids = set()
    for letter in PLAYER_INDEX_LETTERS:
        path = f"/cfb/players/{letter}-index.htm"
        log.info(f"  Discovering from {path}")
        soup = fetch(BASE_URL + path, delay)
        if not soup:
            continue
        for a in soup.select("ul.index_list li a, div#div_players li a, li a[href*='/cfb/players/']"):
            href = a.get("href", "")
            m = re.search(r"/cfb/players/([\w\-]+\.html)", href)
            if m:
                player_ids.add(m.group(1).replace(".html", ""))
        if not player_ids and letter == "a":
            # Fallback selector for any layout variant
            for a in soup.select("a[href*='/cfb/players/']"):
                href = a.get("href", "")
                m = re.search(r"/cfb/players/([\w\-]+)\.html", href)
                if m and "-" in m.group(1):  # name-based IDs always have hyphens
                    player_ids.add(m.group(1))
    log.info(f"Discovered {len(player_ids)} unique NCAAF player IDs")
    return player_ids


def _int_cell(row, stat: str) -> int:
    cell = row.select_one(f"td[data-stat='{stat}']")
    if not cell: return 0
    try: return int(cell.get_text(strip=True).replace(",", "") or 0)
    except: return 0


def parse_player_page(soup: BeautifulSoup, sr_id: str) -> Optional[dict]:
    h1 = soup.select_one("div#meta h1 span") or soup.select_one("h1[itemprop='name'] span")
    if not h1:
        h1 = soup.select_one("h1")
    if not h1:
        return None
    name = h1.get_text(strip=True)

    meta = soup.select_one("div#meta")
    bio = {}
    if meta:
        for p in meta.select("p"):
            text = p.get_text(" ", strip=True)
            if "Position:" in text:
                pos_raw = text.split("Position:")[-1].strip().split("\n")[0].strip()
                pos_map = {
                    "quarterback": "QB", "running back": "RB", "halfback": "RB",
                    "fullback": "FB", "wide receiver": "WR", "tight end": "TE",
                    "offensive lineman": "OL", "offensive tackle": "OT",
                    "offensive guard": "OG", "center": "C",
                    "defensive end": "DE", "defensive tackle": "DT",
                    "linebacker": "LB", "cornerback": "CB", "safety": "S",
                    "kicker": "K", "punter": "P",
                }
                bio["position"] = pos_map.get(pos_raw.lower().split("/")[0].strip(), pos_raw[:2].upper())
            if "School:" in text or "College:" in text:
                school_link = p.select_one("a[href*='/cfb/schools/']")
                if school_link:
                    bio["college"] = canonical_school(school_link.get_text(strip=True))
            if "Hometown:" in text:
                hometown = text.split("Hometown:")[-1].strip()
                parts = hometown.split(",")
                if len(parts) >= 2:
                    bio["birth_city"]  = parts[0].strip()
                    bio["birth_state"] = parts[1].strip()[:2]
            birth_span = p.select_one("span#necro-birth[data-birth]")
            if birth_span:
                bio["birth_date"] = birth_span.get("data-birth","")
                try: bio["birth_year"] = int(bio["birth_date"][:4])
                except: pass

    # Active decades from season rows
    decades = set()
    for row in soup.select("table tbody tr[id]"):
        th = row.select_one("th[data-stat='year_id']") or row.select_one("th[data-stat='year']")
        if th:
            y = th.get_text(strip=True)[:4]
            try: decades.add(f"{(int(y) // 10) * 10}s")
            except: pass

    # Career stats -- passing
    stats = {
        "ncaaf_pass_yds": 0, "ncaaf_rush_yds": 0, "ncaaf_rec_yds": 0,
        "ncaaf_tds": 0, "ncaaf_sacks": 0.0, "ncaaf_all_american": 0,
        "ncaaf_heisman": 0,
    }

    passing = soup.select_one("table#passing")
    if passing:
        cr = passing.select_one("tfoot tr")
        if cr:
            stats["ncaaf_pass_yds"] = _int_cell(cr, "pass_yds")
            stats["ncaaf_tds"]      += _int_cell(cr, "pass_td")

    rushing = soup.select_one("table#rushing")
    if rushing:
        cr = rushing.select_one("tfoot tr")
        if cr:
            stats["ncaaf_rush_yds"] = _int_cell(cr, "rush_yds")
            stats["ncaaf_tds"]      += _int_cell(cr, "rush_td")

    receiving = soup.select_one("table#receiving")
    if receiving:
        cr = receiving.select_one("tfoot tr")
        if cr:
            stats["ncaaf_rec_yds"] = _int_cell(cr, "rec_yds")
            stats["ncaaf_tds"]     += _int_cell(cr, "rec_td")

    defense = soup.select_one("table#defense")
    if defense:
        cr = defense.select_one("tfoot tr")
        if cr:
            sack_cell = cr.select_one("td[data-stat='sacks']")
            if sack_cell:
                try: stats["ncaaf_sacks"] = float(sack_cell.get_text(strip=True) or 0)
                except: pass

    # Awards from sidebar
    for li in soup.select("ul#bling li"):
        text = li.get_text(" ", strip=True).lower()
        if "heisman" in text:
            stats["ncaaf_heisman"] = 1
        elif "all-american" in text or "all american" in text:
            m = re.search(r"(\d+)x", text)
            stats["ncaaf_all_american"] = int(m.group(1)) if m else 1

    pos = bio.get("position", "")
    pos_group_map = {
        "QB": "QB", "RB": "RB", "FB": "RB", "WR": "WR", "TE": "TE",
        "OL": "OL", "OT": "OL", "OG": "OL", "C": "OL",
        "DE": "DEF", "DT": "DEF", "LB": "DEF", "CB": "DEF", "S": "DEF",
        "K": "ST", "P": "ST",
    }

    return {
        "sport": "NCAAF", "sr_id": sr_id, "name": name,
        "position": pos, "position_group": pos_group_map.get(pos, ""),
        "college": bio.get("college",""),
        "birth_date": bio.get("birth_date",""), "birth_year": bio.get("birth_year"),
        "birth_city": bio.get("birth_city",""), "birth_state": bio.get("birth_state",""),
        "birth_country": "USA",
        "teams": [bio["college"]] if bio.get("college") else [],
        "active_decades": sorted(decades),
        **stats,
    }


def scrape_player(sr_id: str, delay: float = 1.0) -> Optional[dict]:
    url = f"{BASE_URL}/cfb/players/{sr_id}.html"
    soup = fetch(url, delay)
    return parse_player_page(soup, sr_id) if soup else None


def load_into_db(players: list, conn, etl_run_id: int) -> tuple:
    c = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    added = updated = 0
    for p in players:
        c.execute("SELECT id FROM players WHERE sr_id=?", (p.get("sr_id"),))
        existing = c.fetchone()
        common = (
            p["name"], p.get("position"), p.get("position_group"),
            p.get("college",""),
            p.get("birth_date",""), p.get("birth_year"),
            p.get("birth_city",""), p.get("birth_state",""), "USA",
            json.dumps(p.get("teams",[])), json.dumps(p.get("active_decades",[])),
            p.get("ncaaf_pass_yds",0), p.get("ncaaf_rush_yds",0),
            p.get("ncaaf_rec_yds",0), p.get("ncaaf_tds",0),
            p.get("ncaaf_sacks",0), p.get("ncaaf_all_american",0),
            p.get("ncaaf_heisman",0),
            "cfb", now, etl_run_id,
        )
        if existing:
            c.execute("""UPDATE players SET name=?,position=?,position_group=?,
                college=?,birth_date=?,birth_year=?,
                birth_city=?,birth_state=?,birth_country=?,
                teams=?,active_decades=?,
                ncaaf_pass_yds=?,ncaaf_rush_yds=?,ncaaf_rec_yds=?,ncaaf_tds=?,
                ncaaf_sacks=?,ncaaf_all_american=?,ncaaf_heisman=?,
                data_source=?,last_updated=?,etl_run_id=? WHERE sr_id=?""",
                      (*common, p["sr_id"]))
            updated += 1
        else:
            c.execute("""INSERT INTO players
                (sport,sr_id,name,position,position_group,college,
                 birth_date,birth_year,birth_city,birth_state,birth_country,
                 teams,active_decades,
                 ncaaf_pass_yds,ncaaf_rush_yds,ncaaf_rec_yds,ncaaf_tds,
                 ncaaf_sacks,ncaaf_all_american,ncaaf_heisman,
                 data_source,last_updated,etl_run_id)
                VALUES ('NCAAF',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (p.get("sr_id"), *common))
            added += 1
    conn.commit()
    return added, updated


def main():
    parser = argparse.ArgumentParser(description="Scrape NCAAF players from sports-reference.com/cfb/")
    parser.add_argument("--from-cache", action="store_true")
    parser.add_argument("--refresh",    action="store_true")
    parser.add_argument("--delay",      type=float, default=1.0)
    parser.add_argument("--limit",      type=int, default=None)
    parser.add_argument("--db",         type=str, default=DB_PATH)
    args = parser.parse_args()

    os.makedirs(CACHE_DIR, exist_ok=True)
    log.info("=== 20/20 NCAAF ETL ===")
    migrate(args.db)
    conn = get_conn(args.db)
    now = datetime.now(timezone.utc).isoformat()
    c = conn.cursor()
    c.execute("INSERT INTO etl_runs (sport,source,run_at,status) VALUES ('NCAAF','cfb',?,'running')", (now,))
    conn.commit()
    etl_run_id = c.lastrowid

    raw_players = []
    if not args.from_cache and (args.refresh or not os.path.exists(CACHE_FILE)):
        player_ids = discover_player_ids(args.delay)
        if args.limit:
            player_ids = set(list(player_ids)[:args.limit])
        log.info(f"Scraping {len(player_ids)} players...")
        failed = 0
        for i, pid in enumerate(sorted(player_ids), 1):
            log.info(f"  [{i}/{len(player_ids)}] {pid}")
            data = scrape_player(pid, args.delay)
            if data: raw_players.append(data)
            else: failed += 1
        log.info(f"Done: {len(raw_players)} ok, {failed} failed")
        with open(CACHE_FILE, "w") as f:
            json.dump({"scraped_at": now, "players": raw_players}, f, indent=2)
    else:
        if not os.path.exists(CACHE_FILE):
            log.error(f"No cache at {CACHE_FILE}")
            sys.exit(1)
        with open(CACHE_FILE) as f:
            cache = json.load(f)
        raw_players = cache["players"]
        log.info(f"Loaded {len(raw_players)} from cache")

    added, updated = load_into_db(raw_players, conn, etl_run_id)
    c.execute("UPDATE etl_runs SET players_added=?,players_updated=?,status='ok' WHERE id=?",
              (added, updated, etl_run_id))
    conn.commit()
    log.info(f"Done: {added} added, {updated} updated")
    conn.close()


if __name__ == "__main__":
    main()
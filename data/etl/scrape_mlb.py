"""
data/etl/scrape_mlb.py
----------------------
Scrapes MLB player data from baseball-reference.com.

Handles both position players (batting stats) and pitchers (pitching stats)
by detecting which stat tables are present on the page.

Usage:
    python data/etl/scrape_mlb.py
    python data/etl/scrape_mlb.py --from-cache
    python data/etl/scrape_mlb.py --limit 30

Output:
    data/cache/mlb_raw.json
    data/nfl.db (players table, sport='MLB')

Run from your machine (residential IP). baseball-reference.com blocks
datacenter IPs. Rate limit: stay under 20 req/min; default 1 req/sec.
"""

import argparse
import json
import os
import re
import sys
import time
import logging
from datetime import datetime, timezone
from typing import Optional

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from data.etl.schema import get_conn, migrate

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

BASE_URL   = "https://www.baseball-reference.com"
CACHE_DIR  = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cache")
CACHE_FILE = os.path.join(CACHE_DIR, "mlb_raw.json")
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

# Leader pages across both hitting and pitching to get broad coverage
LEADER_PAGES = [
    "/leaders/H_career.shtml",             # hits leaders (position players)
    "/leaders/HR_career.shtml",            # home run leaders
    "/leaders/RBI_career.shtml",           # RBI leaders
    "/leaders/batting_avg_career.shtml",   # batting average (correct URL)
    "/leaders/W_career.shtml",             # win leaders (pitchers)
    "/leaders/SO_career.shtml",            # strikeout leaders (pitchers)
    "/leaders/SV_career.shtml",            # save leaders (closers)
    "/leaders/WAR_career.shtml",           # WAR (covers all positions)
]

MLB_TEAM_MAP = {
    "ARI": "Diamondbacks", "ATL": "Braves", "BAL": "Orioles", "BOS": "Red Sox",
    "CHC": "Cubs", "CHW": "White Sox", "CIN": "Reds", "CLE": "Guardians",
    "COL": "Rockies", "DET": "Tigers", "HOU": "Astros", "KCR": "Royals",
    "LAA": "Angels", "LAD": "Dodgers", "MIA": "Marlins", "MIL": "Brewers",
    "MIN": "Twins", "NYM": "Mets", "NYY": "Yankees", "OAK": "Athletics",
    "PHI": "Phillies", "PIT": "Pirates", "SDP": "Padres", "SEA": "Mariners",
    "SFG": "Giants", "STL": "Cardinals", "TBR": "Rays", "TEX": "Rangers",
    "TOR": "Blue Jays", "WSN": "Nationals",
    # Historical
    "MON": "Expos", "FLA": "Marlins", "ANA": "Angels", "TBD": "Rays",
    "CAL": "Angels", "MLN": "Braves", "SLB": "Browns",
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


def discover_player_ids(delay: float = 1.0) -> set:
    player_ids = set()
    for path in LEADER_PAGES:
        log.info(f"  Discovering from {path}")
        soup = fetch(BASE_URL + path, delay)
        if not soup:
            continue
        # BBRef leader links: /players/b/bondsba01.shtml
        for a in soup.select("table#leaders tbody tr td a[href*='/players/']"):
            href = a.get("href", "")
            m = re.search(r"/players/[a-z]/(\w+)\.shtml", href)
            if m:
                player_ids.add(m.group(1))
    log.info(f"Discovered {len(player_ids)} unique MLB player IDs")
    return player_ids


def _float_cell(row, stat: str) -> float:
    cell = row.select_one(f"td[data-stat='{stat}']")
    if not cell:
        return 0.0
    try:
        return float(cell.get_text(strip=True).replace(",", "") or 0)
    except (ValueError, TypeError):
        return 0.0


def _int_cell(row, stat: str) -> int:
    cell = row.select_one(f"td[data-stat='{stat}']")
    if not cell:
        return 0
    try:
        return int(cell.get_text(strip=True).replace(",", "") or 0)
    except (ValueError, TypeError):
        return 0


def parse_player_page(soup: BeautifulSoup, sr_id: str) -> Optional[dict]:
    # Name
    h1 = soup.select_one("div#meta h1 span")
    if not h1:
        return None
    name = h1.get_text(strip=True)

    # Bio
    meta = soup.select_one("div#meta")
    bio = {}
    if meta:
        for p in meta.select("p"):
            text = p.get_text(" ", strip=True)

            # Position
            if "Position:" in text:
                pos_raw = text.split("Position:")[-1].strip().split("▸")[0]
                # MLB positions: Pitcher, Catcher, First Baseman, etc.
                pos_map = {
                    "pitcher": "P", "catcher": "C", "first baseman": "1B",
                    "second baseman": "2B", "third baseman": "3B",
                    "shortstop": "SS", "left fielder": "LF",
                    "center fielder": "CF", "right fielder": "RF",
                    "outfielder": "OF", "designated hitter": "DH",
                }
                bio["position"] = pos_map.get(pos_raw.lower().strip(), pos_raw[:3])

            # College / High School
            if "High School:" in text:
                bio["high_school"] = text.split("High School:")[-1].strip().split(",")[0]
            if "College:" in text:
                c = p.select_one("a[href*='college']")
                if c:
                    bio["college"] = c.get_text(strip=True)

            # Birth
            birth_span = p.select_one("span#necro-birth[data-birth]")
            if birth_span:
                bio["birth_date"] = birth_span.get("data-birth", "")
                try:
                    bio["birth_year"] = int(bio["birth_date"][:4])
                except Exception:
                    pass

            # Birthplace
            born_m = re.search(
                r"Born:.*?in\s+([\w\s\.]+),\s*([A-Z]{2}|[\w\s]+)", text
            )
            if born_m:
                bio["birth_city"]    = born_m.group(1).strip()
                raw_state            = born_m.group(2).strip()
                bio["birth_state"]   = raw_state
                bio["birth_country"] = "USA" if len(raw_state) == 2 else raw_state

            # Height / weight
            hw_m = re.search(r"(\d)-(\d+),\s*(\d+)lb", text)
            if hw_m:
                bio["height_inches"] = int(hw_m.group(1)) * 12 + int(hw_m.group(2))
                bio["weight_lbs"]    = int(hw_m.group(3))

    # Teams
    teams = []
    for a in soup.select("div#meta a[href*='/teams/']"):
        abbr = a.get("href", "").strip("/").split("/")[-2].upper()
        team = MLB_TEAM_MAP.get(abbr)
        if team and team not in teams:
            teams.append(team)

    # Active decades from season rows
    decades = set()
    for tbl_id in ("batting_standard", "pitching_standard", "standard_fielding"):
        for row in soup.select(f"table#{tbl_id} tbody tr[id]"):
            th = row.select_one("th[data-stat='year_ID']")
            if not th:
                th = row.select_one("th[data-stat='year_id']")
            if th:
                y = th.get_text(strip=True)[:4]
                try:
                    decades.add(f"{(int(y) // 10) * 10}s")
                except ValueError:
                    pass

    # --- Stats: detect pitcher vs position player ---
    stats = {
        "mlb_batting_avg": 0.0, "mlb_hr": 0, "mlb_rbi": 0, "mlb_hits": 0,
        "mlb_era": 0.0, "mlb_wins": 0, "mlb_strikeouts": 0, "mlb_saves": 0,
        "mlb_war": 0.0, "mlb_all_star": 0, "mlb_championships": 0,
        "mlb_mvps": 0, "mlb_cy_young": 0, "mlb_gold_gloves": 0,
    }

    # Position player batting totals
    bat_table = soup.select_one("table#batting_standard")
    if bat_table:
        career_row = bat_table.select_one("tfoot tr")
        if career_row:
            stats["mlb_hr"]          = _int_cell(career_row, "HR")
            stats["mlb_rbi"]         = _int_cell(career_row, "RBI")
            stats["mlb_hits"]        = _int_cell(career_row, "H")
            ba = _float_cell(career_row, "batting_avg")
            stats["mlb_batting_avg"] = round(ba, 3)

    # Pitcher totals
    pit_table = soup.select_one("table#pitching_standard")
    if pit_table:
        career_row = pit_table.select_one("tfoot tr")
        if career_row:
            stats["mlb_wins"]        = _int_cell(career_row, "W")
            stats["mlb_strikeouts"]  = _int_cell(career_row, "SO")
            stats["mlb_saves"]       = _int_cell(career_row, "SV")
            era = _float_cell(career_row, "earned_run_avg")
            stats["mlb_era"]         = round(era, 2)

    # WAR (covers everyone)
    war_table = soup.select_one("table#players_value_batting") or \
                soup.select_one("table#players_value_pitching")
    if war_table:
        career_row = war_table.select_one("tfoot tr")
        if career_row:
            war = _float_cell(career_row, "WAR")
            if not war:
                war = _float_cell(career_row, "war_total")
            stats["mlb_war"] = round(war, 1)

    # Awards from sidebar
    for li in soup.select("ul#bling li"):
        text = li.get_text(" ", strip=True).lower()
        if "world series" in text:
            m = re.search(r"(\d+)x", text)
            stats["mlb_championships"] = int(m.group(1)) if m else 1
        elif "al mvp" in text or "nl mvp" in text or "most valuable" in text:
            m = re.search(r"(\d+)x", text)
            stats["mlb_mvps"] = int(m.group(1)) if m else 1
        elif "cy young" in text:
            m = re.search(r"(\d+)x", text)
            stats["mlb_cy_young"] = int(m.group(1)) if m else 1
        elif "all-star" in text:
            m = re.search(r"(\d+)x", text)
            stats["mlb_all_star"] = int(m.group(1)) if m else 1
        elif "gold glove" in text:
            m = re.search(r"(\d+)x", text)
            stats["mlb_gold_gloves"] = int(m.group(1)) if m else 1

    # Determine position group
    pos = bio.get("position", "")
    if pos == "P":
        pos_group = "SP" if stats["mlb_saves"] < 10 else "RP"
    elif pos in ("C",):
        pos_group = "C"
    elif pos in ("1B", "2B", "3B", "SS"):
        pos_group = "IF"
    elif pos in ("LF", "CF", "RF", "OF"):
        pos_group = "OF"
    elif pos == "DH":
        pos_group = "DH"
    else:
        pos_group = pos

    return {
        "sport":         "MLB",
        "sr_id":         sr_id,
        "name":          name,
        "position":      bio.get("position", ""),
        "position_group": pos_group,
        "college":       bio.get("college", ""),
        "high_school":   bio.get("high_school", ""),
        "birth_date":    bio.get("birth_date", ""),
        "birth_year":    bio.get("birth_year"),
        "birth_city":    bio.get("birth_city", ""),
        "birth_state":   bio.get("birth_state", ""),
        "birth_country": bio.get("birth_country", "USA"),
        "height_inches": bio.get("height_inches"),
        "weight_lbs":    bio.get("weight_lbs"),
        "teams":         teams,
        "active_decades": sorted(decades),
        **stats,
    }


def scrape_player(sr_id: str, delay: float = 1.0) -> Optional[dict]:
    letter = sr_id[0].lower()
    url = f"{BASE_URL}/players/{letter}/{sr_id}.shtml"  # .shtml for baseball
    soup = fetch(url, delay)
    if not soup:
        return None
    return parse_player_page(soup, sr_id)


def load_into_db(players: list, conn, etl_run_id: int) -> tuple:
    c = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    added = updated = 0

    for p in players:
        c.execute("SELECT id FROM players WHERE sr_id = ?", (p.get("sr_id"),))
        existing = c.fetchone()

        common = (
            p["name"], p.get("position"), p.get("position_group"),
            p.get("college"), p.get("high_school"),
            p.get("birth_date"), p.get("birth_year"),
            p.get("birth_city"), p.get("birth_state"), p.get("birth_country", "USA"),
            p.get("height_inches"), p.get("weight_lbs"),
            json.dumps(p.get("teams", [])),
            json.dumps(p.get("active_decades", [])),
            p.get("mlb_batting_avg", 0), p.get("mlb_hr", 0),
            p.get("mlb_rbi", 0), p.get("mlb_hits", 0),
            p.get("mlb_era", 0), p.get("mlb_wins", 0),
            p.get("mlb_strikeouts", 0), p.get("mlb_saves", 0),
            p.get("mlb_war", 0), p.get("mlb_all_star", 0),
            p.get("mlb_championships", 0), p.get("mlb_mvps", 0),
            p.get("mlb_cy_young", 0), p.get("mlb_gold_gloves", 0),
            "bref", now, etl_run_id,
        )

        if existing:
            c.execute("""
                UPDATE players SET
                    name=?, position=?, position_group=?, college=?, high_school=?,
                    birth_date=?, birth_year=?, birth_city=?, birth_state=?, birth_country=?,
                    height_inches=?, weight_lbs=?,
                    teams=?, active_decades=?,
                    mlb_batting_avg=?, mlb_hr=?, mlb_rbi=?, mlb_hits=?,
                    mlb_era=?, mlb_wins=?, mlb_strikeouts=?, mlb_saves=?,
                    mlb_war=?, mlb_all_star=?, mlb_championships=?,
                    mlb_mvps=?, mlb_cy_young=?, mlb_gold_gloves=?,
                    data_source=?, last_updated=?, etl_run_id=?
                WHERE sr_id=?
            """, (*common, p["sr_id"]))
            updated += 1
        else:
            c.execute("""
                INSERT INTO players (
                    sport, sr_id, name, position, position_group, college, high_school,
                    birth_date, birth_year, birth_city, birth_state, birth_country,
                    height_inches, weight_lbs, teams, active_decades,
                    mlb_batting_avg, mlb_hr, mlb_rbi, mlb_hits,
                    mlb_era, mlb_wins, mlb_strikeouts, mlb_saves,
                    mlb_war, mlb_all_star, mlb_championships,
                    mlb_mvps, mlb_cy_young, mlb_gold_gloves,
                    data_source, last_updated, etl_run_id
                ) VALUES ('MLB',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (p.get("sr_id"), *common))
            added += 1

    conn.commit()
    return added, updated


def main():
    parser = argparse.ArgumentParser(description="Scrape MLB players from baseball-reference.com")
    parser.add_argument("--from-cache", action="store_true")
    parser.add_argument("--refresh",    action="store_true")
    parser.add_argument("--delay",      type=float, default=1.0)
    parser.add_argument("--limit",      type=int, default=None)
    parser.add_argument("--db",         type=str, default=DB_PATH)
    args = parser.parse_args()

    os.makedirs(CACHE_DIR, exist_ok=True)
    log.info("=== 20/20 MLB ETL ===")

    migrate(args.db)
    conn = get_conn(args.db)
    now = datetime.now(timezone.utc).isoformat()

    c = conn.cursor()
    c.execute("INSERT INTO etl_runs (sport, source, run_at, status) VALUES ('MLB','bref',?,'running')", (now,))
    conn.commit()
    etl_run_id = c.lastrowid

    raw_players = []

    if not args.from_cache and (args.refresh or not os.path.exists(CACHE_FILE)):
        log.info("Discovering MLB player IDs from leaderboard pages...")
        player_ids = discover_player_ids(args.delay)
        if args.limit:
            player_ids = set(list(player_ids)[:args.limit])
        log.info(f"Scraping {len(player_ids)} players...")
        failed = 0
        for i, pid in enumerate(sorted(player_ids), 1):
            log.info(f"  [{i}/{len(player_ids)}] {pid}")
            data = scrape_player(pid, args.delay)
            if data:
                raw_players.append(data)
            else:
                failed += 1
        log.info(f"Scraping complete: {len(raw_players)} ok, {failed} failed")
        with open(CACHE_FILE, "w") as f:
            json.dump({"scraped_at": now, "players": raw_players}, f, indent=2)
        log.info(f"Cache saved: {CACHE_FILE}")
    else:
        if not os.path.exists(CACHE_FILE):
            log.error(f"No cache at {CACHE_FILE}. Run without --from-cache first.")
            sys.exit(1)
        with open(CACHE_FILE) as f:
            cache = json.load(f)
        raw_players = cache["players"]
        log.info(f"Loaded {len(raw_players)} players from cache (scraped {cache.get('scraped_at','?')[:10]})")

    added, updated = load_into_db(raw_players, conn, etl_run_id)
    c.execute("UPDATE etl_runs SET players_added=?, players_updated=?, status='ok' WHERE id=?",
              (added, updated, etl_run_id))
    conn.commit()
    log.info(f"Done: {added} added, {updated} updated")
    conn.close()


if __name__ == "__main__":
    main()
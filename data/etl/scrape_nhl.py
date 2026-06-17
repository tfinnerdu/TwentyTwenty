"""
data/etl/scrape_nhl.py
----------------------
Scrapes NHL player data from hockey-reference.com.

Note: hockey-reference.com is a separate subdomain from the other
Sports-Reference sites. Same rate-limit rules apply (20 req/min).

Usage:
    python data/etl/scrape_nhl.py
    python data/etl/scrape_nhl.py --from-cache
    python data/etl/scrape_nhl.py --limit 30

Output:
    data/cache/nhl_raw.json
    data/nfl.db (players table, sport='NHL')

Run from your machine (residential IP).
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

BASE_URL   = "https://www.hockey-reference.com"
CACHE_DIR  = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cache")
CACHE_FILE = os.path.join(CACHE_DIR, "nhl_raw.json")
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

LEADER_PAGES = [
    "/leaders/goals_career.html",     # goal scorers
    "/leaders/assists_career.html",   # assist leaders
    "/leaders/points_career.html",    # points leaders
    "/leaders/games_played_career.html",  # ironmen (longevity)
    "/leaders/save_pct_career.html",  # goalie leaders
    "/leaders/wins_career.html",      # goalie wins
]

NHL_TEAM_MAP = {
    "ANA": "Ducks", "ARI": "Coyotes", "ATL": "Thrashers", "BOS": "Bruins",
    "BUF": "Sabres", "CGY": "Flames", "CAR": "Hurricanes", "CHI": "Blackhawks",
    "COL": "Avalanche", "CBJ": "Blue Jackets", "DAL": "Stars", "DET": "Red Wings",
    "EDM": "Oilers", "FLA": "Panthers", "LAK": "Kings", "MIN": "Wild",
    "MTL": "Canadiens", "NSH": "Predators", "NJD": "Devils", "NYI": "Islanders",
    "NYR": "Rangers", "OTT": "Senators", "PHI": "Flyers", "PIT": "Penguins",
    "SJS": "Sharks", "SEA": "Kraken", "STL": "Blues", "TBL": "Lightning",
    "TOR": "Maple Leafs", "VAN": "Canucks", "VGK": "Golden Knights",
    "WSH": "Capitals", "WPG": "Jets",
    # Historical
    "QUE": "Nordiques", "HFD": "Whalers", "ATL2": "Flames",
    "MNS": "North Stars", "WIN": "Jets",
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
        # hockey-reference player links: /players/g/gretzzo01.html
        for a in soup.select("table#leaders tbody tr td a[href*='/players/']"):
            href = a.get("href", "")
            m = re.search(r"/players/[a-z]/(\w+)\.html", href)
            if m:
                player_ids.add(m.group(1))
    log.info(f"Discovered {len(player_ids)} unique NHL player IDs")
    return player_ids


def _int_cell(row, stat: str) -> int:
    cell = row.select_one(f"td[data-stat='{stat}']")
    if not cell:
        return 0
    try:
        return int(cell.get_text(strip=True).replace(",", "") or 0)
    except (ValueError, TypeError):
        return 0


def _float_cell(row, stat: str) -> float:
    cell = row.select_one(f"td[data-stat='{stat}']")
    if not cell:
        return 0.0
    try:
        return float(cell.get_text(strip=True) or 0)
    except (ValueError, TypeError):
        return 0.0


def parse_player_page(soup: BeautifulSoup, sr_id: str) -> Optional[dict]:
    h1 = soup.select_one("div#meta h1 span")
    if not h1:
        return None
    name = h1.get_text(strip=True)

    meta = soup.select_one("div#meta")
    bio = {}
    if meta:
        for p in meta.select("p"):
            text = p.get_text(" ", strip=True)

            # Position
            if "Position:" in text:
                pos_raw = text.split("Position:")[-1].strip().split("\n")[0].strip()
                pos_map = {
                    "center": "C", "left wing": "LW", "right wing": "RW",
                    "defense": "D", "defenseman": "D", "goalie": "G",
                    "goaltender": "G", "forward": "F",
                }
                bio["position"] = pos_map.get(pos_raw.lower().split("▸")[0].strip(), pos_raw[:2])

            # Birth
            birth_span = p.select_one("span#necro-birth[data-birth]")
            if birth_span:
                bio["birth_date"] = birth_span.get("data-birth", "")
                try:
                    bio["birth_year"] = int(bio["birth_date"][:4])
                except Exception:
                    pass

            # Birthplace -- NHL has lots of international players
            # Format: "Born: City, Province/State, Country"
            born_m = re.search(
                r"Born:.*?in\s+([\w\s\-\.]+),\s*([\w\s]+),\s*([\w\s]+)", text
            )
            if born_m:
                bio["birth_city"]    = born_m.group(1).strip()
                bio["birth_state"]   = born_m.group(2).strip()
                bio["birth_country"] = born_m.group(3).strip()
            else:
                # Two-part: City, Country (no province)
                born_m2 = re.search(r"Born:.*?in\s+([\w\s\-\.]+),\s*([\w\s]+)", text)
                if born_m2:
                    bio["birth_city"]    = born_m2.group(1).strip()
                    bio["birth_country"] = born_m2.group(2).strip()
                    # Infer state if it's a 2-letter US state abbreviation
                    country = bio["birth_country"]
                    if len(country) == 2 and country.isupper():
                        bio["birth_state"]   = country
                        bio["birth_country"] = "USA"

            # Height/weight
            hw_m = re.search(r"(\d)-(\d+),\s*(\d+)lb", text)
            if hw_m:
                bio["height_inches"] = int(hw_m.group(1)) * 12 + int(hw_m.group(2))
                bio["weight_lbs"]    = int(hw_m.group(3))

    # Teams
    teams = []
    for a in soup.select("div#meta a[href*='/teams/']"):
        abbr = a.get("href", "").strip("/").split("/")[-2].upper()
        team = NHL_TEAM_MAP.get(abbr)
        if team and team not in teams:
            teams.append(team)

    # Stats
    stats = {
        "nhl_goals": 0, "nhl_assists": 0, "nhl_points": 0,
        "nhl_games": 0, "nhl_championships": 0,
        "nhl_mvps": 0, "nhl_all_star": 0,
    }

    # Skater stats
    skater_table = soup.select_one("table#stats_basic_plus_nhl")
    if not skater_table:
        skater_table = soup.select_one("table#stats_basic_nhl")
    if skater_table:
        career_row = skater_table.select_one("tfoot tr")
        if career_row:
            stats["nhl_goals"]   = _int_cell(career_row, "goals")
            stats["nhl_assists"] = _int_cell(career_row, "assists")
            stats["nhl_points"]  = _int_cell(career_row, "points")
            stats["nhl_games"]   = _int_cell(career_row, "games_played")

    # Goalie stats (if present, override games)
    goalie_table = soup.select_one("table#stats_basic_goalies_nhl")
    if goalie_table:
        career_row = goalie_table.select_one("tfoot tr")
        if career_row:
            stats["nhl_games"] = _int_cell(career_row, "games_goalie")

    # Awards from bling list
    for li in soup.select("ul#bling li"):
        text = li.get_text(" ", strip=True).lower()
        if "stanley cup" in text:
            m = re.search(r"(\d+)x", text)
            stats["nhl_championships"] = int(m.group(1)) if m else 1
        elif "hart" in text:  # Hart Trophy = NHL MVP
            m = re.search(r"(\d+)x", text)
            stats["nhl_mvps"] = int(m.group(1)) if m else 1
        elif "all-star" in text:
            m = re.search(r"(\d+)x", text)
            stats["nhl_all_star"] = int(m.group(1)) if m else 1

    # Active decades
    decades = set()
    for tbl_id in ("stats_basic_plus_nhl", "stats_basic_nhl", "stats_basic_goalies_nhl"):
        for row in soup.select(f"table#{tbl_id} tbody tr[id]"):
            th = row.select_one("th[data-stat='season']")
            if th:
                y = th.get_text(strip=True)[:4]
                try:
                    decades.add(f"{(int(y) // 10) * 10}s")
                except ValueError:
                    pass

    pos = bio.get("position", "")
    pos_group = "G" if pos == "G" else ("D" if pos == "D" else "F")

    return {
        "sport":          "NHL",
        "sr_id":          sr_id,
        "name":           name,
        "position":       pos,
        "position_group": pos_group,
        "birth_date":     bio.get("birth_date", ""),
        "birth_year":     bio.get("birth_year"),
        "birth_city":     bio.get("birth_city", ""),
        "birth_state":    bio.get("birth_state", ""),
        "birth_country":  bio.get("birth_country", ""),
        "height_inches":  bio.get("height_inches"),
        "weight_lbs":     bio.get("weight_lbs"),
        "teams":          teams,
        "active_decades": sorted(decades),
        **stats,
    }


def scrape_player(sr_id: str, delay: float = 1.0) -> Optional[dict]:
    letter = sr_id[0].lower()
    url = f"{BASE_URL}/players/{letter}/{sr_id}.html"
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
            p.get("birth_date"), p.get("birth_year"),
            p.get("birth_city"), p.get("birth_state"), p.get("birth_country", ""),
            p.get("height_inches"), p.get("weight_lbs"),
            json.dumps(p.get("teams", [])),
            json.dumps(p.get("active_decades", [])),
            p.get("nhl_goals", 0), p.get("nhl_assists", 0),
            p.get("nhl_points", 0), p.get("nhl_games", 0),
            p.get("nhl_championships", 0), p.get("nhl_mvps", 0),
            p.get("nhl_all_star", 0),
            "hockeyref", now, etl_run_id,
        )

        if existing:
            c.execute("""
                UPDATE players SET
                    name=?, position=?, position_group=?,
                    birth_date=?, birth_year=?, birth_city=?, birth_state=?, birth_country=?,
                    height_inches=?, weight_lbs=?,
                    teams=?, active_decades=?,
                    nhl_goals=?, nhl_assists=?, nhl_points=?, nhl_games=?,
                    nhl_championships=?, nhl_mvps=?, nhl_all_star=?,
                    data_source=?, last_updated=?, etl_run_id=?
                WHERE sr_id=?
            """, (*common, p["sr_id"]))
            updated += 1
        else:
            c.execute("""
                INSERT INTO players (
                    sport, sr_id, name, position, position_group,
                    birth_date, birth_year, birth_city, birth_state, birth_country,
                    height_inches, weight_lbs, teams, active_decades,
                    nhl_goals, nhl_assists, nhl_points, nhl_games,
                    nhl_championships, nhl_mvps, nhl_all_star,
                    data_source, last_updated, etl_run_id
                ) VALUES ('NHL',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (p.get("sr_id"), *common))
            added += 1

    conn.commit()
    return added, updated


def main():
    parser = argparse.ArgumentParser(description="Scrape NHL players from hockey-reference.com")
    parser.add_argument("--from-cache", action="store_true")
    parser.add_argument("--refresh",    action="store_true")
    parser.add_argument("--delay",      type=float, default=1.0)
    parser.add_argument("--limit",      type=int, default=None)
    parser.add_argument("--db",         type=str, default=DB_PATH)
    args = parser.parse_args()

    os.makedirs(CACHE_DIR, exist_ok=True)
    log.info("=== 20/20 NHL ETL ===")

    migrate(args.db)
    conn = get_conn(args.db)
    now = datetime.now(timezone.utc).isoformat()

    c = conn.cursor()
    c.execute("INSERT INTO etl_runs (sport, source, run_at, status) VALUES ('NHL','hockeyref',?,'running')", (now,))
    conn.commit()
    etl_run_id = c.lastrowid

    raw_players = []

    if not args.from_cache and (args.refresh or not os.path.exists(CACHE_FILE)):
        log.info("Discovering NHL player IDs from leaderboard pages...")
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
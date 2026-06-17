"""
data/etl/scrape_nba.py
----------------------
Scrapes NBA player data from basketball-reference.com.

Usage:
    python data/etl/scrape_nba.py
    python data/etl/scrape_nba.py --from-cache
    python data/etl/scrape_nba.py --limit 30

Output:
    data/cache/nba_raw.json
    data/nfl.db (players table, sport='NBA')
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

BASE_URL   = "https://www.basketball-reference.com"
CACHE_DIR  = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cache")
CACHE_FILE = os.path.join(CACHE_DIR, "nba_raw.json")
DB_PATH    = os.path.join(os.path.dirname(os.path.dirname(__file__)), "nfl.db")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# BBRef career leader pages are JS-rendered -- BeautifulSoup cannot parse them.
# Use the static alphabetical player index instead.
PLAYER_INDEX_LETTERS = list("abcdefghijklmnopqrstuvwxyz")

NBA_TEAM_MAP = {
    "ATL": "Hawks", "BOS": "Celtics", "NJN": "Nets", "BRK": "Nets",
    "CHA": "Hornets", "CHO": "Hornets", "CHI": "Bulls", "CLE": "Cavaliers",
    "DAL": "Mavericks", "DEN": "Nuggets", "DET": "Pistons", "GSW": "Warriors",
    "HOU": "Rockets", "IND": "Pacers", "LAC": "Clippers", "LAL": "Lakers",
    "MEM": "Grizzlies", "MIA": "Heat", "MIL": "Bucks", "MIN": "Timberwolves",
    "NOP": "Pelicans", "NOH": "Pelicans", "NYK": "Knicks", "OKC": "Thunder",
    "SEA": "SuperSonics", "ORL": "Magic", "PHI": "76ers", "PHO": "Suns",
    "POR": "Blazers", "SAC": "Kings", "SAS": "Spurs", "TOR": "Raptors",
    "UTA": "Jazz", "WAS": "Wizards", "WSB": "Wizards",
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
    """
    BBRef career leader pages are JS-rendered -- invisible to BeautifulSoup.
    Use the static alphabetical player index (/players/a/ etc.) which lists
    every player with career stats inline. Filter to players with 400+ games.
    """
    player_ids = set()
    for letter in PLAYER_INDEX_LETTERS:
        path = f"/players/{letter}/"
        log.info(f"  Discovering from {path}")
        soup = fetch(BASE_URL + path, delay)
        if not soup:
            continue
        for a in soup.select("table#players tbody tr td a"):
            href = a.get("href", "")
            m = re.search(r"/players/[a-z]/(\w+)\.html", href)
            if not m:
                continue
            tr = a.find_parent("tr")
            if tr:
                g_cell = tr.select_one("td[data-stat='g']")
                if g_cell:
                    try:
                        if int(g_cell.get_text(strip=True).replace(",", "") or 0) >= 400:
                            player_ids.add(m.group(1))
                        continue
                    except (ValueError, TypeError):
                        pass
            player_ids.add(m.group(1))
    log.info(f"Discovered {len(player_ids)} unique NBA player IDs")
    return player_ids


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
            if "Position:" in text:
                pos_text = text.split("Position:")[-1].strip()
                bio["position"] = pos_text.split("▸")[0].split("and")[0].strip()[:2]
            if "College:" in text or "Colleges:" in text:
                c_link = p.select_one("a[href*='college']")
                if c_link:
                    bio["college"] = c_link.get_text(strip=True)
            if "High School:" in text:
                bio["high_school"] = text.split("High School:")[-1].strip().split(",")[0]
            birth_span = p.select_one("span#necro-birth[data-birth]")
            if birth_span:
                bio["birth_date"] = birth_span.get("data-birth", "")
                bio["birth_year"] = int(bio["birth_date"][:4]) if bio.get("birth_date") else None
            # Born in...
            born_match = re.search(r"Born:.*?in\s+([\w\s]+),\s*([A-Z]{2}|[\w\s]+)", text)
            if born_match:
                bio["birth_city"]    = born_match.group(1).strip()
                bio["birth_state"]   = born_match.group(2).strip()
                bio["birth_country"] = "USA" if len(born_match.group(2)) == 2 else born_match.group(2).strip()

    # Teams
    teams = []
    for a in soup.select("div#meta a[href*='/teams/']"):
        abbr = a.get("href", "").strip("/").split("/")[-2].upper()
        team = NBA_TEAM_MAP.get(abbr)
        if team and team not in teams:
            teams.append(team)

    # Career totals
    stats = {
        "nba_points": 0.0, "nba_rebounds": 0.0, "nba_assists": 0.0,
        "nba_games": 0, "nba_championships": 0, "nba_mvps": 0,
        "nba_finals_mvps": 0, "nba_all_star": 0, "nba_all_nba": 0,
    }

    per_game = soup.select_one("table#per_game")
    if per_game:
        career_row = per_game.select_one("tfoot tr")
        if career_row:
            def _f(stat):
                cell = career_row.select_one(f"td[data-stat='{stat}']")
                if cell:
                    try: return float(cell.get_text(strip=True))
                    except: return 0.0
                return 0.0
            stats["nba_points"]   = _f("pts_per_g")
            stats["nba_rebounds"] = _f("trb_per_g")
            stats["nba_assists"]  = _f("ast_per_g")

    totals = soup.select_one("table#totals")
    if totals:
        career_row = totals.select_one("tfoot tr")
        if career_row:
            g_cell = career_row.select_one("td[data-stat='g']")
            if g_cell:
                try: stats["nba_games"] = int(g_cell.get_text(strip=True))
                except: pass

    # Awards from leaderboard sidebar
    for li in soup.select("ul#bling li"):
        text = li.get_text(" ", strip=True).lower()
        if "nba champion" in text:
            m = re.search(r"(\d+)x", text)
            stats["nba_championships"] = int(m.group(1)) if m else 1
        elif "mvp" in text and "finals" not in text:
            m = re.search(r"(\d+)x", text)
            stats["nba_mvps"] = int(m.group(1)) if m else 1
        elif "finals mvp" in text:
            m = re.search(r"(\d+)x", text)
            stats["nba_finals_mvps"] = int(m.group(1)) if m else 1
        elif "all-star" in text:
            m = re.search(r"(\d+)x", text)
            stats["nba_all_star"] = int(m.group(1)) if m else 1
        elif "all-nba" in text:
            m = re.search(r"(\d+)x", text)
            stats["nba_all_nba"] = int(m.group(1)) if m else 1

    # Decades
    decades = set()
    for row in soup.select("table#per_game tbody tr[id]"):
        th = row.select_one("th[data-stat='season']")
        if th:
            y = th.get_text(strip=True)[:4]
            try:
                yr = int(y)
                decades.add(f"{(yr // 10) * 10}s")
            except: pass

    return {
        "sport": "NBA", "sr_id": sr_id, "name": name,
        "position": bio.get("position", ""),
        "college": bio.get("college", ""),
        "high_school": bio.get("high_school", ""),
        "birth_date": bio.get("birth_date", ""),
        "birth_year": bio.get("birth_year"),
        "birth_city": bio.get("birth_city", ""),
        "birth_state": bio.get("birth_state", ""),
        "birth_country": bio.get("birth_country", "USA"),
        "teams": teams,
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

        fields = (
            p["name"], p.get("position"), p.get("college"), p.get("high_school"),
            p.get("birth_date"), p.get("birth_year"), p.get("birth_city"),
            p.get("birth_state"), p.get("birth_country", "USA"),
            json.dumps(p.get("teams", [])),
            json.dumps(p.get("active_decades", [])),
            p.get("nba_points", 0), p.get("nba_rebounds", 0), p.get("nba_assists", 0),
            p.get("nba_games", 0), p.get("nba_championships", 0),
            p.get("nba_mvps", 0), p.get("nba_finals_mvps", 0),
            p.get("nba_all_star", 0), p.get("nba_all_nba", 0),
            "bbref", now, etl_run_id,
        )

        if existing:
            c.execute("""
                UPDATE players SET
                    name=?, position=?, college=?, high_school=?,
                    birth_date=?, birth_year=?, birth_city=?, birth_state=?, birth_country=?,
                    teams=?, active_decades=?,
                    nba_points=?, nba_rebounds=?, nba_assists=?, nba_games=?,
                    nba_championships=?, nba_mvps=?, nba_finals_mvps=?,
                    nba_all_star=?, nba_all_nba=?,
                    data_source=?, last_updated=?, etl_run_id=?
                WHERE sr_id=?
            """, (*fields, p["sr_id"]))
            updated += 1
        else:
            c.execute("""
                INSERT INTO players (
                    sport, sr_id, name, position, college, high_school,
                    birth_date, birth_year, birth_city, birth_state, birth_country,
                    teams, active_decades,
                    nba_points, nba_rebounds, nba_assists, nba_games,
                    nba_championships, nba_mvps, nba_finals_mvps,
                    nba_all_star, nba_all_nba,
                    data_source, last_updated, etl_run_id
                ) VALUES ('NBA',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (p.get("sr_id"), *fields))
            added += 1

    conn.commit()
    return added, updated


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-cache", action="store_true")
    parser.add_argument("--refresh",    action="store_true")
    parser.add_argument("--delay",      type=float, default=1.0)
    parser.add_argument("--limit",      type=int, default=None)
    parser.add_argument("--db",         type=str, default=DB_PATH)
    args = parser.parse_args()

    os.makedirs(CACHE_DIR, exist_ok=True)
    log.info("=== 20/20 NBA ETL ===")

    migrate(args.db)
    conn = get_conn(args.db)
    now = datetime.now(timezone.utc).isoformat()

    c = conn.cursor()
    c.execute("INSERT INTO etl_runs (sport, source, run_at, status) VALUES ('NBA','bbref',?,'running')", (now,))
    conn.commit()
    etl_run_id = c.lastrowid

    raw_players = []

    if not args.from_cache and (args.refresh or not os.path.exists(CACHE_FILE)):
        log.info("Discovering NBA player IDs...")
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
    c.execute("UPDATE etl_runs SET players_added=?, players_updated=?, status='ok' WHERE id=?",
              (added, updated, etl_run_id))
    conn.commit()
    log.info(f"Done: {added} added, {updated} updated")
    conn.close()


if __name__ == "__main__":
    main()
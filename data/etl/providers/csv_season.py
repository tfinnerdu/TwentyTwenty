"""
data/etl/providers/csv_season.py
================================
Shared base for providers that ingest per-player *season* rows and roll them
up into career lines. NBA, WNBA, NHL and NFL all share this machinery; each
is a small subclass declaring its team map, the season columns to sum, and
how those sums map onto the players-table stat columns.

Honors (rings, MVP, All-Star, Heisman, ...) are not in box-score data and
come from the curated awards overlay (data/etl/awards/<sport>.json).

Canonical input files in --source-dir (produced from a Kaggle export, an
nba_api/NHL-API dump, nflverse, wehoop, etc.):

  <sport>_players.csv : player_id,name,position,college,birth_year,birth_city,
                        birth_state,birth_country,height_inches,weight_lbs,
                        draft_year,draft_round,draft_number
  <sport>_seasons.csv : player_id,season,team,<stat columns...>
"""

import csv
import logging
import os
import sys
from typing import Iterable

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))
from data.etl.providers.base import Provider
from data.etl.teams import ABBR2NICK as NFL_ABBR2NICK, canonical_school

log = logging.getLogger(__name__)


def _f(s) -> float:
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


def _i(s) -> int:
    return int(_f(s))


def _year(s) -> int:
    s = (s or "").strip()
    return int(s[:4]) if s[:4].isdigit() else 0


class CsvSeasonProvider(Provider):
    # --- subclass declares these ---
    TEAM_MAP: dict = {}        # team abbrev -> franchise nickname
    PASSTHROUGH_TEAMS = False  # if True, use the raw team value when not in TEAM_MAP
                               # (college: the school IS the team, no fixed map)
    IS_COLLEGE = False         # if True, the passthrough "team" is a school name
                               # -> run it through the shared school canonicalizer
    SUM_COLS: tuple = ()       # season numeric columns to accumulate
    GAMES_COL: str = "g"       # which summed column is games played

    def __init__(self, source_dir: str | None = None, **_):
        self.data_dir = source_dir or os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "cache", self.sport.lower())

    # subclass: map accumulated sums -> {player_column: value}
    def stat_fields(self, sums: dict) -> dict:
        raise NotImplementedError

    # subclass: return position_group, or None to let load.derive_fields set it
    def position_group_value(self, position: str):
        return None

    # -- io --
    def _file(self, suffix: str) -> str:
        return os.path.join(self.data_dir, f"{self.sport.lower()}_{suffix}.csv")

    def _rows(self, suffix: str):
        path = self._file(suffix)
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"{path} not found. Supply {self.sport.lower()}_players.csv and "
                f"{self.sport.lower()}_seasons.csv via --source-dir.")
        with open(path, newline="", encoding="utf-8-sig") as fh:
            yield from csv.DictReader(fh)

    def inspect(self):
        for suffix in ("players", "seasons"):
            path = self._file(suffix)
            if not os.path.exists(path):
                print(f"{os.path.basename(path)}: MISSING")
                continue
            with open(path, newline="", encoding="utf-8-sig") as fh:
                rdr = csv.reader(fh)
                header = next(rdr, [])
                n = sum(1 for _ in rdr)
            print(f"{os.path.basename(path)}: {n} rows, columns={header}")

    # -- parse + aggregate --
    def fetch(self) -> Iterable[dict]:
        agg: dict = {}
        dropped: dict = {}        # raw codes we couldn't map (would silently vanish)
        for r in self._rows("seasons"):
            pid = r["player_id"]
            a = agg.get(pid)
            if a is None:
                a = {"teams": [], "years": set()}
                a.update({col: 0.0 for col in self.SUM_COLS})
                agg[pid] = a
            for col in self.SUM_COLS:
                a[col] += _f(r.get(col))
            a["years"].add(_year(r.get("season")))
            raw_team = (r.get("team") or "").strip()
            nick = self.TEAM_MAP.get(raw_team.upper()) or (raw_team if self.PASSTHROUGH_TEAMS else None)
            if nick and self.IS_COLLEGE:       # passthrough school -> canonical form
                nick = canonical_school(nick)
            if nick and nick not in a["teams"]:
                a["teams"].append(nick)
            elif raw_team and not nick and not self.IS_COLLEGE:  # pro code that won't map
                dropped[raw_team] = dropped.get(raw_team, 0) + 1   # -> a team is dropped

        if dropped:                            # make "garbage in" loud, never silent
            top = dict(sorted(dropped.items(), key=lambda x: -x[1])[:20])
            log.warning("%s: %d season row(s) dropped for %d UNMAPPED team code(s) -- "
                        "add them to %s.TEAM_MAP: %s", self.sport, sum(dropped.values()),
                        len(dropped), type(self).__name__, top)

        for r in self._rows("players"):
            pid = r["player_id"]
            a = agg.get(pid)
            if not a or a.get(self.GAMES_COL, 0) == 0:
                continue
            years = {y for y in a["years"] if y}
            payload = {
                "sport": self.sport,
                "sr_id": f"{self.sport.lower()}_{pid}",
                "name": (r.get("name") or "").strip(),
                "position": (r.get("position") or "").strip(),
                # college is a school name for every sport -> canonicalize it too
                "college": canonical_school((r.get("college") or "").strip()),
                "birth_year": _i(r.get("birth_year")) or None,
                "birth_city": (r.get("birth_city") or "").strip(),
                "birth_state": (r.get("birth_state") or "").strip(),
                "birth_country": (r.get("birth_country") or "USA").strip() or "USA",
                "height_inches": _i(r.get("height_inches")) or None,
                "weight_lbs": _i(r.get("weight_lbs")) or None,
                "draft_year": _i(r.get("draft_year")) or None,
                "draft_round": _i(r.get("draft_round")) or None,
                "draft_pick": _i(r.get("draft_number")) or None,
                "teams": a["teams"],
                "active_decades": sorted({f"{(y // 10) * 10}s" for y in years}),
                "debut_year": min(years) if years else None,
                "final_year": max(years) if years else None,
            }
            payload.update(self.stat_fields(a))
            grp = self.position_group_value(payload["position"])
            if grp is not None:
                payload["position_group"] = grp
            yield payload


# ---------------------------------------------------------------------------
# Per-sport subclasses
# ---------------------------------------------------------------------------

class NBAProvider(CsvSeasonProvider):
    sport = "NBA"
    source_name = "nba_csv"
    SUM_COLS = ("g", "pts", "trb", "ast")
    # Era-accurate franchise map (basketball-reference codes). A code resolves to
    # the nickname the team ACTUALLY wore that season -- the same convention the
    # data already used (SEA->SuperSonics, not Thunder). Relocations keep the old
    # city's nickname under the old code; renamed-in-place collapses use the code.
    # Historical city codes never collide with a current code, so the extras below
    # only ever ADD coverage (a code the source spells differently just goes
    # unused -- run `python -m data.etl.teams --audit` to surface any real drop).
    TEAM_MAP = {
        # -- current 30 franchises --
        "ATL": "Hawks", "BOS": "Celtics", "BKN": "Nets", "CHA": "Hornets",
        "CHO": "Hornets", "CHI": "Bulls", "CLE": "Cavaliers", "DAL": "Mavericks",
        "DEN": "Nuggets", "DET": "Pistons", "GSW": "Warriors", "HOU": "Rockets",
        "IND": "Pacers", "LAC": "Clippers", "LAL": "Lakers", "MEM": "Grizzlies",
        "MIA": "Heat", "MIL": "Bucks", "MIN": "Timberwolves", "NOP": "Pelicans",
        "NYK": "Knicks", "OKC": "Thunder", "ORL": "Magic", "PHI": "76ers",
        "PHX": "Suns", "PHO": "Suns", "POR": "Trail Blazers", "SAC": "Kings",
        "SAS": "Spurs", "TOR": "Raptors", "UTA": "Jazz", "WAS": "Wizards",
        # -- relocated / renamed (era-accurate by the name worn then) --
        "SEA": "SuperSonics",                                  # -> Thunder (OKC)
        "NJN": "Nets", "NYN": "Nets", "BRK": "Nets",          # NJ/NY Nets; BRK = b-ref Brooklyn
        "CHH": "Hornets", "NOH": "Hornets", "NOK": "Hornets",  # Charlotte & New Orleans Hornets
        "VAN": "Grizzlies",                                    # Vancouver -> Memphis
        "SDR": "Rockets",                                      # San Diego -> Houston
        "BUF": "Braves", "SDC": "Clippers",                   # Buffalo Braves -> SD -> LA Clippers
        "NOJ": "Jazz",                                         # New Orleans -> Utah Jazz
        "ROC": "Royals", "CIN": "Royals", "KCO": "Kings", "KCK": "Kings",  # Royals -> Kings (SAC)
        "PHW": "Warriors", "SFW": "Warriors",                 # Philadelphia/SF -> Golden State
        "SYR": "Nationals",                                    # Syracuse Nationals -> 76ers
        "FTW": "Pistons",                                      # Fort Wayne -> Detroit
        "MNL": "Lakers",                                       # Minneapolis -> LA Lakers
        "TRI": "Blackhawks", "MLH": "Hawks", "STL": "Hawks",  # Tri-Cities/Milwaukee/St. Louis -> Atlanta
        "CHP": "Packers", "CHZ": "Zephyrs", "BAL": "Bullets",  # Chicago Packers/Zephyrs -> Baltimore
        "CAP": "Bullets", "WSB": "Bullets", "BLB": "Bullets",  # Capital/Washington Bullets -> Wizards
        # -- defunct BAA/early-NBA clubs (no modern successor) --
        "CHS": "Stags", "STB": "Bombers", "WSC": "Capitols",
        "PRO": "Steamrollers", "TRH": "Huskies", "AND": "Packers",
        "WAT": "Hawks", "DNN": "Nuggets", "INO": "Olympians",
    }

    def stat_fields(self, s):
        g = s["g"] or 1
        return {"nba_points": round(s["pts"] / g, 1), "nba_rebounds": round(s["trb"] / g, 1),
                "nba_assists": round(s["ast"] / g, 1), "nba_games": int(s["g"])}
        # position_group handled by load.derive_fields for NBA


class WNBAProvider(CsvSeasonProvider):
    sport = "WNBA"
    source_name = "wnba_csv"
    SUM_COLS = ("g", "pts", "reb", "ast")
    TEAM_MAP = {
        # abbreviations (bundled fixture)
        "PHX": "Mercury", "PHO": "Mercury", "SEA": "Storm", "LAS": "Sparks",
        "IND": "Fever", "MIN": "Lynx", "HOU": "Comets", "NYL": "Liberty",
        "LVA": "Aces", "CHI": "Sky", "WAS": "Mystics", "CON": "Sun",
        "ATL": "Dream", "DAL": "Wings", "TUL": "Shock",
        # franchise nicknames (wehoop GitHub export emits these directly)
        "MERCURY": "Mercury", "STORM": "Storm", "SPARKS": "Sparks",
        "FEVER": "Fever", "LYNX": "Lynx", "COMETS": "Comets", "LIBERTY": "Liberty",
        "ACES": "Aces", "SKY": "Sky", "MYSTICS": "Mystics", "SUN": "Sun",
        "DREAM": "Dream", "WINGS": "Wings", "SHOCK": "Shock", "STING": "Sting",
        "ROCKERS": "Rockers", "MONARCHS": "Monarchs", "STARS": "Stars",
        "VALKYRIES": "Valkyries",
    }
    _GROUP = {"G": "G", "PG": "G", "SG": "G", "F": "F", "SF": "F", "PF": "F", "C": "C"}

    def stat_fields(self, s):
        g = s["g"] or 1
        return {"wnba_points": round(s["pts"] / g, 1), "wnba_rebounds": round(s["reb"] / g, 1),
                "wnba_assists": round(s["ast"] / g, 1), "wnba_games": int(s["g"])}

    def position_group_value(self, position):
        return self._GROUP.get(position.upper(), "")


class NHLProvider(CsvSeasonProvider):
    sport = "NHL"
    source_name = "nhl_api"
    SUM_COLS = ("g", "goals", "assists", "points")
    # Era-accurate franchise map (hockey-reference codes). Same convention as NBA:
    # a code resolves to the nickname worn that season; relocations keep the old
    # code -> old nickname (QUE Nordiques, HFD Whalers), so they never collide with
    # a current code. Adding a historical/alt code only ever ADDS coverage.
    TEAM_MAP = {
        # -- current 32 franchises (+ common alt spellings) --
        "MTL": "Canadiens", "MON": "Canadiens", "TOR": "Maple Leafs", "BOS": "Bruins",
        "CHI": "Blackhawks", "NYR": "Rangers", "EDM": "Oilers", "PIT": "Penguins",
        "DET": "Red Wings", "PHI": "Flyers", "COL": "Avalanche",
        "NJD": "Devils", "NJ": "Devils", "WSH": "Capitals", "WAS": "Capitals",
        "DAL": "Stars", "STL": "Blues", "LAK": "Kings", "LA": "Kings",
        "BUF": "Sabres", "CGY": "Flames", "VAN": "Canucks", "OTT": "Senators",
        "TBL": "Lightning", "TB": "Lightning", "FLA": "Panthers",
        "NYI": "Islanders", "SJS": "Sharks", "SJ": "Sharks", "ANA": "Ducks",
        "NSH": "Predators", "CBJ": "Blue Jackets", "MIN": "Wild",
        "WPG": "Jets", "CAR": "Hurricanes",
        "VGK": "Golden Knights", "VEG": "Golden Knights",
        "SEA": "Kraken",                                       # 2021+ (was missing)
        "ARI": "Coyotes", "PHX": "Coyotes", "PHO": "Coyotes",  # Phoenix era was missing
        "UTA": "Mammoth", "UHC": "Mammoth",                    # Coyotes -> Utah (2024+)
        # -- relocated / defunct (era-accurate by the name worn then) --
        "QUE": "Nordiques",                  # Quebec -> Colorado Avalanche
        "HFD": "Whalers", "HAR": "Whalers",  # Hartford -> Carolina Hurricanes
        "WIN": "Jets",                       # original Winnipeg Jets -> Phoenix
        "MNS": "North Stars",                # Minnesota -> Dallas Stars
        "ATL": "Thrashers", "ATF": "Thrashers",  # Atlanta Thrashers -> Winnipeg
        "AFM": "Flames",                     # Atlanta Flames -> Calgary
        "KCS": "Scouts",                     # Kansas City Scouts -> Colorado -> NJ
        "CLR": "Rockies", "COR": "Rockies",  # Colorado Rockies (NHL) -> NJ Devils
        "CGS": "Golden Seals", "OAK": "Seals", "CSE": "Seals",  # Oakland/California Seals
        "CLE": "Barons",                     # Cleveland Barons -> merged into North Stars
    }
    _GROUP = {"C": "F", "LW": "F", "RW": "F", "W": "F", "F": "F", "D": "D", "G": "G"}

    def stat_fields(self, s):
        return {"nhl_goals": int(s["goals"]), "nhl_assists": int(s["assists"]),
                "nhl_points": int(s["points"]), "nhl_games": int(s["g"])}

    def position_group_value(self, position):
        return self._GROUP.get(position.upper(), "")


class NFLProvider(CsvSeasonProvider):
    sport = "NFL"
    source_name = "nflverse"
    SUM_COLS = ("g", "pass_yds", "rush_yds", "rec_yds",
                "pass_td", "rush_td", "rec_td", "sacks", "interceptions")
    # Single source of truth: the franchise registry (data/etl/teams.py). It maps
    # every nflverse city code + PFR franchise code to a canonical nickname, with
    # relocations collapsed and renames resolved. Unmapped codes return None (the
    # base class drops them) -- run `python -m data.etl.teams --audit` to catch any.
    TEAM_MAP = NFL_ABBR2NICK

    def stat_fields(self, s):
        return {"pass_yds": int(s["pass_yds"]), "rush_yds": int(s["rush_yds"]),
                "rec_yds": int(s["rec_yds"]),
                "total_tds": int(s["pass_td"] + s["rush_td"] + s["rec_td"]),
                "sacks": round(s["sacks"], 1), "interceptions": int(s["interceptions"])}
        # position_group handled by load.derive_fields for NFL


class NCAAFProvider(CsvSeasonProvider):
    """College football. School is the players.csv `college` column (NCAAF
    categories key off college, not pro-style teams). Honors (Heisman,
    All-American) come from the curated awards/ncaaf.json overlay -- a CFBD
    export carries stats but not those. Use cfbd_export.py to produce the
    CSVs from the CollegeFootballData API with your key."""
    sport = "NCAAF"
    source_name = "cfbd"
    IS_COLLEGE = True                 # school-based; categories key off `college`
    SUM_COLS = ("g", "pass_yds", "rush_yds", "rec_yds", "tds", "sacks")

    def stat_fields(self, s):
        return {"ncaaf_pass_yds": int(s["pass_yds"]), "ncaaf_rush_yds": int(s["rush_yds"]),
                "ncaaf_rec_yds": int(s["rec_yds"]), "ncaaf_tds": int(s["tds"]),
                "ncaaf_sacks": round(s["sacks"], 1)}


class NCAABProvider(CsvSeasonProvider):
    sport = "NCAAB"
    source_name = "ncaab_csv"
    SUM_COLS = ("g", "pts", "reb", "ast")
    PASSTHROUGH_TEAMS = True          # team == the school (Duke, Kansas, ...)
    IS_COLLEGE = True                 # -> canonicalize the school name
    _GROUP = {"G": "G", "PG": "G", "SG": "G", "F": "F", "SF": "F", "PF": "F", "C": "C"}

    def stat_fields(self, s):
        g = s["g"] or 1
        # men's & women's college share the ncaab_* columns (no ncaaw_* in schema)
        return {"ncaab_points": round(s["pts"] / g, 1), "ncaab_rebounds": round(s["reb"] / g, 1),
                "ncaab_assists": round(s["ast"] / g, 1), "ncaab_games": int(s["g"])}

    def position_group_value(self, position):
        return self._GROUP.get(position.upper(), "")


class NCAAWProvider(NCAABProvider):
    sport = "NCAAW"
    source_name = "ncaaw_csv"          # same ncaab_* stat columns, different sport

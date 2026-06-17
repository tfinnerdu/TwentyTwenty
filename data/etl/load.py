"""
data/etl/load.py
----------------
Run after any scrape step. Rebuilds categories and player_categories
based on what's currently in the players table. Also computes
position_group, active_decades, and other derived fields.

Usage:
    python data/etl/load.py
    python data/etl/load.py --sport NFL
    python data/etl/load.py --sport ALL
"""

import argparse
import json
import os
import re
import sys
import logging
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from data.etl.schema import get_conn, migrate

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "nfl.db")


# -------------------------------------------------------------------------
# Position group normalization
# -------------------------------------------------------------------------
NFL_POS_GROUPS = {
    "QB": "QB", "RB": "RB", "HB": "RB", "FB": "RB",
    "WR": "WR", "TE": "TE",
    "OT": "OL", "OG": "OL", "C": "OL", "OL": "OL", "G": "OL", "T": "OL",
    "DE": "DEF", "DT": "DEF", "NT": "DEF", "DL": "DEF",
    "LB": "DEF", "ILB": "DEF", "OLB": "DEF", "MLB": "DEF",
    "CB": "DEF", "S": "DEF", "FS": "DEF", "SS": "DEF", "DB": "DEF",
    "K": "ST", "P": "ST", "LS": "ST",
}

NBA_POS_GROUPS = {
    "PG": "G", "SG": "G", "G": "G",
    "SF": "F", "PF": "F", "F": "F",
    "C": "C",
}


def derive_fields(conn):
    """Fill in position_group and any other derived fields."""
    c = conn.cursor()
    c.execute("SELECT id, sport, position FROM players")
    rows = c.fetchall()
    for row in rows:
        pid, sport, pos = row["id"], row["sport"], row["position"] or ""
        pos = pos.upper().strip()
        if sport == "NFL":
            grp = NFL_POS_GROUPS.get(pos, "")
        elif sport == "NBA":
            grp = NBA_POS_GROUPS.get(pos, "")
        else:
            grp = ""
        c.execute("UPDATE players SET position_group=? WHERE id=?", (grp, pid))
    conn.commit()
    log.info(f"Derived position_group for {len(rows)} players")


# -------------------------------------------------------------------------
# Category definitions (multi-sport aware)
# -------------------------------------------------------------------------
def get_categories():
    """
    Returns the full category list. sport_scope controls which puzzle modes
    include this category.

    op values:
      gte   field >= value
      lte   0 < field <= value  (draft picks; excludes undrafted)
      eq    field == value
      has   value in JSON array field
      in    field in JSON list value
    """
    return [
        # =================================================================
        # NFL CATEGORIES  (sport_scope='NFL')
        # =================================================================

        # --- Passing ---
        {"id": "pass_10k",  "label": "10,000+ Passing Yds",  "scope": "NFL", "field": "pass_yds",  "op": "gte", "value": 10000},
        {"id": "pass_30k",  "label": "30,000+ Passing Yds",  "scope": "NFL", "field": "pass_yds",  "op": "gte", "value": 30000},
        {"id": "pass_50k",  "label": "50,000+ Passing Yds",  "scope": "NFL", "field": "pass_yds",  "op": "gte", "value": 50000},

        # --- Rushing ---
        {"id": "rush_5k",   "label": "5,000+ Rushing Yds",   "scope": "NFL", "field": "rush_yds",  "op": "gte", "value": 5000},
        {"id": "rush_10k",  "label": "10,000+ Rushing Yds",  "scope": "NFL", "field": "rush_yds",  "op": "gte", "value": 10000},

        # --- Receiving ---
        {"id": "rec_5k",    "label": "5,000+ Receiving Yds", "scope": "NFL", "field": "rec_yds",   "op": "gte", "value": 5000},
        {"id": "rec_10k",   "label": "10,000+ Receiving Yds","scope": "NFL", "field": "rec_yds",   "op": "gte", "value": 10000},

        # --- TDs ---
        {"id": "td_50",     "label": "50+ Career TDs",       "scope": "NFL", "field": "total_tds", "op": "gte", "value": 50},
        {"id": "td_100",    "label": "100+ Career TDs",      "scope": "NFL", "field": "total_tds", "op": "gte", "value": 100},
        {"id": "td_150",    "label": "150+ Career TDs",      "scope": "NFL", "field": "total_tds", "op": "gte", "value": 150},
        {"id": "td_200",    "label": "200+ Career TDs",      "scope": "NFL", "field": "total_tds", "op": "gte", "value": 200},
        {"id": "td_400",    "label": "400+ Career TDs",      "scope": "NFL", "field": "total_tds", "op": "gte", "value": 400},

        # --- Defense ---
        {"id": "sacks_50",  "label": "50+ Career Sacks",     "scope": "NFL", "field": "sacks",     "op": "gte", "value": 50},
        {"id": "sacks_100", "label": "100+ Career Sacks",    "scope": "NFL", "field": "sacks",     "op": "gte", "value": 100},
        {"id": "int_20",    "label": "20+ Career INTs",      "scope": "NFL", "field": "interceptions", "op": "gte", "value": 20},

        # --- Pro Bowls ---
        {"id": "pb_3",      "label": "3+ Pro Bowls",         "scope": "NFL", "field": "pro_bowls", "op": "gte", "value": 3},
        {"id": "pb_5",      "label": "5+ Pro Bowls",         "scope": "NFL", "field": "pro_bowls", "op": "gte", "value": 5},
        {"id": "pb_8",      "label": "8+ Pro Bowls",         "scope": "NFL", "field": "pro_bowls", "op": "gte", "value": 8},
        {"id": "pb_10",     "label": "10+ Pro Bowls",        "scope": "NFL", "field": "pro_bowls", "op": "gte", "value": 10},

        # --- Super Bowls ---
        {"id": "sb_1",      "label": "Super Bowl Champion",  "scope": "NFL", "field": "super_bowls","op": "gte", "value": 1},
        {"id": "sb_2",      "label": "Multi SB Champion",    "scope": "NFL", "field": "super_bowls","op": "gte", "value": 2},
        {"id": "sb_3",      "label": "3+ Super Bowl Rings",  "scope": "NFL", "field": "super_bowls","op": "gte", "value": 3},

        # --- NFL MVPs ---
        {"id": "nfl_mvp",   "label": "NFL MVP",              "scope": "NFL", "field": "nfl_mvps",  "op": "gte", "value": 1},

        # --- NFL Positions ---
        {"id": "pos_qb",    "label": "Quarterback",          "scope": "NFL", "field": "position_group", "op": "eq", "value": "QB"},
        {"id": "pos_rb",    "label": "Running Back",         "scope": "NFL", "field": "position_group", "op": "eq", "value": "RB"},
        {"id": "pos_wr",    "label": "Wide Receiver",        "scope": "NFL", "field": "position_group", "op": "eq", "value": "WR"},
        {"id": "pos_te",    "label": "Tight End",            "scope": "NFL", "field": "position_group", "op": "eq", "value": "TE"},
        {"id": "pos_nfl_def","label": "NFL Defensive Player","scope": "NFL", "field": "position_group", "op": "eq", "value": "DEF"},
        {"id": "pos_nfl_ol", "label": "NFL Offensive Lineman","scope":"NFL", "field": "position_group", "op": "eq", "value": "OL"},

        # --- NFL Teams ---
        {"id": "team_patriots",   "label": "Patriots",   "scope": "NFL", "field": "teams", "op": "has", "value": "Patriots"},
        {"id": "team_cowboys",    "label": "Cowboys",    "scope": "NFL", "field": "teams", "op": "has", "value": "Cowboys"},
        {"id": "team_49ers",      "label": "49ers",      "scope": "NFL", "field": "teams", "op": "has", "value": "49ers"},
        {"id": "team_broncos",    "label": "Broncos",    "scope": "NFL", "field": "teams", "op": "has", "value": "Broncos"},
        {"id": "team_steelers",   "label": "Steelers",   "scope": "NFL", "field": "teams", "op": "has", "value": "Steelers"},
        {"id": "team_packers",    "label": "Packers",    "scope": "NFL", "field": "teams", "op": "has", "value": "Packers"},
        {"id": "team_chiefs",     "label": "Chiefs",     "scope": "NFL", "field": "teams", "op": "has", "value": "Chiefs"},
        {"id": "team_colts",      "label": "Colts",      "scope": "NFL", "field": "teams", "op": "has", "value": "Colts"},
        {"id": "team_chargers",   "label": "Chargers",   "scope": "NFL", "field": "teams", "op": "has", "value": "Chargers"},
        {"id": "team_vikings",    "label": "Vikings",    "scope": "NFL", "field": "teams", "op": "has", "value": "Vikings"},
        {"id": "team_rams",       "label": "Rams",       "scope": "NFL", "field": "teams", "op": "has", "value": "Rams"},
        {"id": "team_ravens",     "label": "Ravens",     "scope": "NFL", "field": "teams", "op": "has", "value": "Ravens"},
        {"id": "team_giants",     "label": "Giants",     "scope": "NFL", "field": "teams", "op": "has", "value": "Giants"},
        {"id": "team_eagles",     "label": "Eagles",     "scope": "NFL", "field": "teams", "op": "has", "value": "Eagles"},
        {"id": "team_cardinals",  "label": "Cardinals",  "scope": "NFL", "field": "teams", "op": "has", "value": "Cardinals"},
        {"id": "team_bears",      "label": "Bears",      "scope": "NFL", "field": "teams", "op": "has", "value": "Bears"},
        {"id": "team_dolphins",   "label": "Dolphins",   "scope": "NFL", "field": "teams", "op": "has", "value": "Dolphins"},
        {"id": "team_lions",      "label": "Lions",      "scope": "NFL", "field": "teams", "op": "has", "value": "Lions"},
        {"id": "team_bills",      "label": "Bills",      "scope": "NFL", "field": "teams", "op": "has", "value": "Bills"},
        {"id": "team_falcons",    "label": "Falcons",    "scope": "NFL", "field": "teams", "op": "has", "value": "Falcons"},
        {"id": "team_saints",     "label": "Saints",     "scope": "NFL", "field": "teams", "op": "has", "value": "Saints"},
        {"id": "team_seahawks",   "label": "Seahawks",   "scope": "NFL", "field": "teams", "op": "has", "value": "Seahawks"},
        {"id": "team_raiders",    "label": "Raiders",    "scope": "NFL", "field": "teams", "op": "has", "value": "Raiders"},
        {"id": "team_texans",     "label": "Texans",     "scope": "NFL", "field": "teams", "op": "has", "value": "Texans"},
        {"id": "team_bengals",    "label": "Bengals",    "scope": "NFL", "field": "teams", "op": "has", "value": "Bengals"},
        {"id": "team_browns",     "label": "Browns",     "scope": "NFL", "field": "teams", "op": "has", "value": "Browns"},
        {"id": "team_jets",       "label": "Jets",       "scope": "NFL", "field": "teams", "op": "has", "value": "Jets"},
        {"id": "team_panthers",   "label": "Panthers",   "scope": "NFL", "field": "teams", "op": "has", "value": "Panthers"},
        {"id": "team_titans",     "label": "Titans",     "scope": "NFL", "field": "teams", "op": "has", "value": "Titans"},
        {"id": "team_redskins",   "label": "Redskins",   "scope": "NFL", "field": "teams", "op": "has", "value": "Redskins"},

        # =================================================================
        # NBA CATEGORIES  (sport_scope='NBA')
        # =================================================================
        {"id": "nba_20ppg",  "label": "20+ PPG Career",     "scope": "NBA", "field": "nba_points",       "op": "gte", "value": 20},
        {"id": "nba_25ppg",  "label": "25+ PPG Career",     "scope": "NBA", "field": "nba_points",       "op": "gte", "value": 25},
        {"id": "nba_10rpg",  "label": "10+ RPG Career",     "scope": "NBA", "field": "nba_rebounds",     "op": "gte", "value": 10},
        {"id": "nba_7apg",   "label": "7+ APG Career",      "scope": "NBA", "field": "nba_assists",      "op": "gte", "value": 7},
        {"id": "nba_1000g",  "label": "1,000+ NBA Games",   "scope": "NBA", "field": "nba_games",        "op": "gte", "value": 1000},
        {"id": "nba_ring",   "label": "NBA Champion",       "scope": "NBA", "field": "nba_championships","op": "gte", "value": 1},
        {"id": "nba_rings2", "label": "Multi NBA Champion", "scope": "NBA", "field": "nba_championships","op": "gte", "value": 2},
        {"id": "nba_mvp",    "label": "NBA MVP",            "scope": "NBA", "field": "nba_mvps",         "op": "gte", "value": 1},
        {"id": "nba_fmvp",   "label": "NBA Finals MVP",     "scope": "NBA", "field": "nba_finals_mvps",  "op": "gte", "value": 1},
        {"id": "nba_5star",  "label": "5+ All-Star Selections","scope":"NBA","field": "nba_all_star",     "op": "gte", "value": 5},
        {"id": "nba_10star", "label": "10+ All-Star Selections","scope":"NBA","field":"nba_all_star",      "op": "gte", "value": 10},
        {"id": "nba_allnba", "label": "All-NBA Selection",  "scope": "NBA", "field": "nba_all_nba",      "op": "gte", "value": 1},

        # NBA teams
        {"id": "nba_lakers",   "label": "Lakers",         "scope": "NBA", "field": "teams", "op": "has", "value": "Lakers"},
        {"id": "nba_celtics",  "label": "Celtics",        "scope": "NBA", "field": "teams", "op": "has", "value": "Celtics"},
        {"id": "nba_bulls",    "label": "Bulls",          "scope": "NBA", "field": "teams", "op": "has", "value": "Bulls"},
        {"id": "nba_spurs",    "label": "Spurs",          "scope": "NBA", "field": "teams", "op": "has", "value": "Spurs"},
        {"id": "nba_warriors", "label": "Warriors",       "scope": "NBA", "field": "teams", "op": "has", "value": "Warriors"},
        {"id": "nba_heat",     "label": "Heat",           "scope": "NBA", "field": "teams", "op": "has", "value": "Heat"},
        {"id": "nba_knicks",   "label": "Knicks",         "scope": "NBA", "field": "teams", "op": "has", "value": "Knicks"},
        {"id": "nba_pistons",  "label": "Pistons",        "scope": "NBA", "field": "teams", "op": "has", "value": "Pistons"},
        {"id": "nba_rockets",  "label": "Rockets",        "scope": "NBA", "field": "teams", "op": "has", "value": "Rockets"},
        {"id": "nba_nets",     "label": "Nets",           "scope": "NBA", "field": "teams", "op": "has", "value": "Nets"},

        # NBA positions
        {"id": "nba_guard",    "label": "NBA Guard",      "scope": "NBA", "field": "position_group", "op": "eq", "value": "G"},
        {"id": "nba_forward",  "label": "NBA Forward",    "scope": "NBA", "field": "position_group", "op": "eq", "value": "F"},
        {"id": "nba_center",   "label": "NBA Center",     "scope": "NBA", "field": "position_group", "op": "eq", "value": "C"},

        # =================================================================
        # MLB CATEGORIES  (sport_scope='MLB')
        # =================================================================

        # Batting
        {"id": "mlb_300ba",     "label": ".300+ Career Avg",         "scope": "MLB", "field": "mlb_batting_avg",   "op": "gte", "value": 0.3},
        {"id": "mlb_500hr",     "label": "500+ Career HRs",          "scope": "MLB", "field": "mlb_hr",            "op": "gte", "value": 500},
        {"id": "mlb_400hr",     "label": "400+ Career HRs",          "scope": "MLB", "field": "mlb_hr",            "op": "gte", "value": 400},
        {"id": "mlb_1500rbi",   "label": "1,500+ Career RBI",        "scope": "MLB", "field": "mlb_rbi",           "op": "gte", "value": 1500},
        {"id": "mlb_1000rbi",   "label": "1,000+ Career RBI",        "scope": "MLB", "field": "mlb_rbi",           "op": "gte", "value": 1000},
        {"id": "mlb_3000hits",  "label": "3,000+ Career Hits",       "scope": "MLB", "field": "mlb_hits",          "op": "gte", "value": 3000},
        {"id": "mlb_2000hits",  "label": "2,000+ Career Hits",       "scope": "MLB", "field": "mlb_hits",          "op": "gte", "value": 2000},

        # Pitching
        {"id": "mlb_300w",      "label": "300+ Career Wins",         "scope": "MLB", "field": "mlb_wins",          "op": "gte", "value": 300},
        {"id": "mlb_200w",      "label": "200+ Career Wins",         "scope": "MLB", "field": "mlb_wins",          "op": "gte", "value": 200},
        {"id": "mlb_3000k",     "label": "3,000+ Career Strikeouts", "scope": "MLB", "field": "mlb_strikeouts",    "op": "gte", "value": 3000},
        {"id": "mlb_300saves",  "label": "300+ Career Saves",        "scope": "MLB", "field": "mlb_saves",         "op": "gte", "value": 300},

        # Awards
        {"id": "mlb_ws",        "label": "World Series Champion",    "scope": "MLB", "field": "mlb_championships", "op": "gte", "value": 1},
        {"id": "mlb_mvp",       "label": "MLB MVP",                  "scope": "MLB", "field": "mlb_mvps",          "op": "gte", "value": 1},
        {"id": "mlb_cy",        "label": "Cy Young Award",           "scope": "MLB", "field": "mlb_cy_young",      "op": "gte", "value": 1},
        {"id": "mlb_gg",        "label": "Gold Glove Award",         "scope": "MLB", "field": "mlb_gold_gloves",   "op": "gte", "value": 1},
        {"id": "mlb_5gg",       "label": "5+ Gold Gloves",           "scope": "MLB", "field": "mlb_gold_gloves",   "op": "gte", "value": 5},
        {"id": "mlb_allstar5",  "label": "5+ All-Star Selections",   "scope": "MLB", "field": "mlb_all_star",      "op": "gte", "value": 5},
        {"id": "mlb_war50",     "label": "50+ Career WAR",           "scope": "MLB", "field": "mlb_war",           "op": "gte", "value": 50},
        {"id": "mlb_war70",     "label": "70+ Career WAR",           "scope": "MLB", "field": "mlb_war",           "op": "gte", "value": 70},

        # MLB positions
        {"id": "mlb_pitcher",   "label": "MLB Pitcher",              "scope": "MLB", "field": "position_group",    "op": "in",  "value": ["SP", "RP"]},
        {"id": "mlb_catcher",   "label": "MLB Catcher",              "scope": "MLB", "field": "position_group",    "op": "eq",  "value": "C"},
        {"id": "mlb_infield",   "label": "MLB Infielder",            "scope": "MLB", "field": "position_group",    "op": "eq",  "value": "IF"},
        {"id": "mlb_outfield",  "label": "MLB Outfielder",           "scope": "MLB", "field": "position_group",    "op": "eq",  "value": "OF"},

        # MLB teams
        {"id": "mlb_yankees",   "label": "Yankees",    "scope": "MLB", "field": "teams", "op": "has", "value": "Yankees"},
        {"id": "mlb_redsox",    "label": "Red Sox",    "scope": "MLB", "field": "teams", "op": "has", "value": "Red Sox"},
        {"id": "mlb_dodgers",   "label": "Dodgers",    "scope": "MLB", "field": "teams", "op": "has", "value": "Dodgers"},
        {"id": "mlb_cubs",      "label": "Cubs",       "scope": "MLB", "field": "teams", "op": "has", "value": "Cubs"},
        {"id": "mlb_giants_bb", "label": "SF Giants",  "scope": "MLB", "field": "teams", "op": "has", "value": "Giants"},
        {"id": "mlb_cardinals", "label": "Cardinals",  "scope": "MLB", "field": "teams", "op": "has", "value": "Cardinals"},
        {"id": "mlb_braves",    "label": "Braves",     "scope": "MLB", "field": "teams", "op": "has", "value": "Braves"},
        {"id": "mlb_astros",    "label": "Astros",     "scope": "MLB", "field": "teams", "op": "has", "value": "Astros"},

        # =================================================================
        # NHL CATEGORIES  (sport_scope='NHL')
        # =================================================================

        # Scoring
        {"id": "nhl_500g",      "label": "500+ Career Goals",        "scope": "NHL", "field": "nhl_goals",         "op": "gte", "value": 500},
        {"id": "nhl_400g",      "label": "400+ Career Goals",        "scope": "NHL", "field": "nhl_goals",         "op": "gte", "value": 400},
        {"id": "nhl_1000pts",   "label": "1,000+ Career Points",     "scope": "NHL", "field": "nhl_points",        "op": "gte", "value": 1000},
        {"id": "nhl_700pts",    "label": "700+ Career Points",       "scope": "NHL", "field": "nhl_points",        "op": "gte", "value": 700},
        {"id": "nhl_1000g",     "label": "1,000+ NHL Games",         "scope": "NHL", "field": "nhl_games",         "op": "gte", "value": 1000},

        # Awards
        {"id": "nhl_cup",       "label": "Stanley Cup Champion",     "scope": "NHL", "field": "nhl_championships", "op": "gte", "value": 1},
        {"id": "nhl_cups2",     "label": "2+ Stanley Cups",          "scope": "NHL", "field": "nhl_championships", "op": "gte", "value": 2},
        {"id": "nhl_hart",      "label": "Hart Trophy (NHL MVP)",    "scope": "NHL", "field": "nhl_mvps",          "op": "gte", "value": 1},
        {"id": "nhl_allstar5",  "label": "5+ All-Star Selections",   "scope": "NHL", "field": "nhl_all_star",      "op": "gte", "value": 5},

        # NHL positions
        {"id": "nhl_forward",   "label": "NHL Forward",              "scope": "NHL", "field": "position_group",    "op": "eq",  "value": "F"},
        {"id": "nhl_defense",   "label": "NHL Defenseman",           "scope": "NHL", "field": "position_group",    "op": "eq",  "value": "D"},
        {"id": "nhl_goalie",    "label": "NHL Goalie",               "scope": "NHL", "field": "position_group",    "op": "eq",  "value": "G"},

        # NHL teams
        {"id": "nhl_canadiens", "label": "Canadiens",   "scope": "NHL", "field": "teams", "op": "has", "value": "Canadiens"},
        {"id": "nhl_leafs",     "label": "Maple Leafs", "scope": "NHL", "field": "teams", "op": "has", "value": "Maple Leafs"},
        {"id": "nhl_bruins",    "label": "Bruins",      "scope": "NHL", "field": "teams", "op": "has", "value": "Bruins"},
        {"id": "nhl_hawks",     "label": "Blackhawks",  "scope": "NHL", "field": "teams", "op": "has", "value": "Blackhawks"},
        {"id": "nhl_rangers",   "label": "Rangers",     "scope": "NHL", "field": "teams", "op": "has", "value": "Rangers"},
        {"id": "nhl_oilers",    "label": "Oilers",      "scope": "NHL", "field": "teams", "op": "has", "value": "Oilers"},
        {"id": "nhl_penguins",  "label": "Penguins",    "scope": "NHL", "field": "teams", "op": "has", "value": "Penguins"},
        {"id": "nhl_wings",     "label": "Red Wings",   "scope": "NHL", "field": "teams", "op": "has", "value": "Red Wings"},
        {"id": "nhl_flyers",    "label": "Flyers",      "scope": "NHL", "field": "teams", "op": "has", "value": "Flyers"},

        # =================================================================
        # WNBA CATEGORIES  (sport_scope='WNBA')
        # =================================================================
        {"id": "wnba_20ppg",    "label": "20+ PPG Career (WNBA)",     "scope": "WNBA", "field": "wnba_points",       "op": "gte", "value": 20},
        {"id": "wnba_15ppg",    "label": "15+ PPG Career (WNBA)",     "scope": "WNBA", "field": "wnba_points",       "op": "gte", "value": 15},
        {"id": "wnba_8rpg",     "label": "8+ RPG Career (WNBA)",      "scope": "WNBA", "field": "wnba_rebounds",     "op": "gte", "value": 8},
        {"id": "wnba_5apg",     "label": "5+ APG Career (WNBA)",      "scope": "WNBA", "field": "wnba_assists",      "op": "gte", "value": 5},
        {"id": "wnba_300g",     "label": "300+ WNBA Games",           "scope": "WNBA", "field": "wnba_games",        "op": "gte", "value": 300},
        {"id": "wnba_ring",     "label": "WNBA Champion",             "scope": "WNBA", "field": "wnba_championships", "op": "gte", "value": 1},
        {"id": "wnba_mvp",      "label": "WNBA MVP",                  "scope": "WNBA", "field": "wnba_mvps",         "op": "gte", "value": 1},
        {"id": "wnba_fmvp",     "label": "WNBA Finals MVP",           "scope": "WNBA", "field": "wnba_finals_mvps",  "op": "gte", "value": 1},
        {"id": "wnba_allstar3",  "label": "3+ WNBA All-Star Selections","scope": "WNBA", "field": "wnba_all_star",    "op": "gte", "value": 3},
        # WNBA teams
        {"id": "wnba_storm",    "label": "Storm",      "scope": "WNBA", "field": "teams", "op": "has", "value": "Storm"},
        {"id": "wnba_lynx",     "label": "Lynx",       "scope": "WNBA", "field": "teams", "op": "has", "value": "Lynx"},
        {"id": "wnba_mercury",  "label": "Mercury",    "scope": "WNBA", "field": "teams", "op": "has", "value": "Mercury"},
        {"id": "wnba_aces",     "label": "Aces",       "scope": "WNBA", "field": "teams", "op": "has", "value": "Aces"},
        {"id": "wnba_sparks",   "label": "Sparks",     "scope": "WNBA", "field": "teams", "op": "has", "value": "Sparks"},
        {"id": "wnba_liberty",  "label": "Liberty",    "scope": "WNBA", "field": "teams", "op": "has", "value": "Liberty"},
        {"id": "wnba_fever",    "label": "Fever",      "scope": "WNBA", "field": "teams", "op": "has", "value": "Fever"},
        {"id": "wnba_sun",      "label": "Sun",        "scope": "WNBA", "field": "teams", "op": "has", "value": "Sun"},
        # WNBA positions
        {"id": "wnba_guard",    "label": "WNBA Guard",   "scope": "WNBA", "field": "position_group", "op": "eq", "value": "G"},
        {"id": "wnba_forward",  "label": "WNBA Forward", "scope": "WNBA", "field": "position_group", "op": "eq", "value": "F"},
        {"id": "wnba_center",   "label": "WNBA Center",  "scope": "WNBA", "field": "position_group", "op": "eq", "value": "C"},

        # =================================================================
        # NCAAF CATEGORIES  (sport_scope='NCAAF')
        # =================================================================
        {"id": "ncaaf_pass5k",  "label": "5,000+ College Pass Yds",  "scope": "NCAAF", "field": "ncaaf_pass_yds",    "op": "gte", "value": 5000},
        {"id": "ncaaf_rush3k",  "label": "3,000+ College Rush Yds",  "scope": "NCAAF", "field": "ncaaf_rush_yds",    "op": "gte", "value": 3000},
        {"id": "ncaaf_rec2k",   "label": "2,000+ College Rec Yds",   "scope": "NCAAF", "field": "ncaaf_rec_yds",     "op": "gte", "value": 2000},
        {"id": "ncaaf_td30",    "label": "30+ College TDs",          "scope": "NCAAF", "field": "ncaaf_tds",         "op": "gte", "value": 30},
        {"id": "ncaaf_heisman", "label": "Heisman Trophy Winner",    "scope": "NCAAF", "field": "ncaaf_heisman",     "op": "gte", "value": 1},
        {"id": "ncaaf_aa",      "label": "College All-American (Football)","scope": "NCAAF", "field": "ncaaf_all_american", "op": "gte", "value": 1},
        # NCAAF by school (these are the big cross-sport connectors)
        {"id": "ncaaf_alabama",  "label": "Played at Alabama (CFB)",   "scope": "NCAAF", "field": "college", "op": "eq", "value": "Alabama"},
        {"id": "ncaaf_ohio_st",  "label": "Played at Ohio State (CFB)","scope": "NCAAF", "field": "college", "op": "eq", "value": "Ohio State"},
        {"id": "ncaaf_usc",      "label": "Played at USC (CFB)",       "scope": "NCAAF", "field": "college", "op": "eq", "value": "USC"},
        {"id": "ncaaf_michigan", "label": "Played at Michigan (CFB)",  "scope": "NCAAF", "field": "college", "op": "eq", "value": "Michigan"},
        {"id": "ncaaf_nd",       "label": "Played at Notre Dame (CFB)","scope": "NCAAF", "field": "college", "op": "eq", "value": "Notre Dame"},
        {"id": "ncaaf_oklahoma", "label": "Played at Oklahoma (CFB)",  "scope": "NCAAF", "field": "college", "op": "eq", "value": "Oklahoma"},
        {"id": "ncaaf_florida",  "label": "Played at Florida (CFB)",   "scope": "NCAAF", "field": "college", "op": "eq", "value": "Florida"},

        # =================================================================
        # NCAAB CATEGORIES  (sport_scope='NCAAB')
        # =================================================================
        {"id": "ncaab_25ppg",   "label": "25+ PPG in College (M)",   "scope": "NCAAB", "field": "ncaab_points",      "op": "gte", "value": 25},
        {"id": "ncaab_20ppg",   "label": "20+ PPG in College (M)",   "scope": "NCAAB", "field": "ncaab_points",      "op": "gte", "value": 20},
        {"id": "ncaab_10rpg",   "label": "10+ RPG in College (M)",   "scope": "NCAAB", "field": "ncaab_rebounds",    "op": "gte", "value": 10},
        {"id": "ncaab_champ",   "label": "NCAA Champion (M)",         "scope": "NCAAB", "field": "ncaab_championships","op": "gte", "value": 1},
        {"id": "ncaab_poy",     "label": "College POY (M)",          "scope": "NCAAB", "field": "ncaab_player_of_year","op": "gte", "value": 1},
        {"id": "ncaab_aa",      "label": "College All-American (M)", "scope": "NCAAB", "field": "ncaab_all_american", "op": "gte", "value": 1},
        {"id": "ncaab_guard",   "label": "College Guard (M)",        "scope": "NCAAB", "field": "position_group",    "op": "eq",  "value": "G"},
        {"id": "ncaab_forward",  "label": "College Forward (M)",     "scope": "NCAAB", "field": "position_group",    "op": "eq",  "value": "F"},
        {"id": "ncaab_center",   "label": "College Center (M)",      "scope": "NCAAB", "field": "position_group",    "op": "eq",  "value": "C"},
        {"id": "ncaab_duke",    "label": "Played at Duke (M)",       "scope": "NCAAB", "field": "college", "op": "eq", "value": "Duke"},
        {"id": "ncaab_kentucky","label": "Played at Kentucky (M)",   "scope": "NCAAB", "field": "college", "op": "eq", "value": "Kentucky"},
        {"id": "ncaab_unc",     "label": "Played at UNC (M)",        "scope": "NCAAB", "field": "college", "op": "eq", "value": "North Carolina"},
        {"id": "ncaab_ucla",    "label": "Played at UCLA (M)",       "scope": "NCAAB", "field": "college", "op": "eq", "value": "UCLA"},
        {"id": "ncaab_indiana",  "label": "Played at Indiana (M)",   "scope": "NCAAB", "field": "college", "op": "eq", "value": "Indiana"},
        {"id": "ncaab_kansas",   "label": "Played at Kansas (M)",    "scope": "NCAAB", "field": "college", "op": "eq", "value": "Kansas"},

        # =================================================================
        # NCAAW CATEGORIES  (sport_scope='NCAAW')
        # =================================================================
        {"id": "ncaaw_20ppg",   "label": "20+ PPG in College (W)",   "scope": "NCAAW", "field": "ncaab_points",      "op": "gte", "value": 20},
        {"id": "ncaaw_champ",   "label": "NCAA Champion (W)",         "scope": "NCAAW", "field": "ncaab_championships","op": "gte", "value": 1},
        {"id": "ncaaw_poy",     "label": "College POY (W)",          "scope": "NCAAW", "field": "ncaab_player_of_year","op": "gte", "value": 1},
        {"id": "ncaaw_aa",      "label": "College All-American (W)", "scope": "NCAAW", "field": "ncaab_all_american", "op": "gte", "value": 1},
        {"id": "ncaaw_uconn",   "label": "Played at UConn (W)",      "scope": "NCAAW", "field": "college", "op": "eq", "value": "Connecticut"},
        {"id": "ncaaw_tennessee","label": "Played at Tennessee (W)", "scope": "NCAAW", "field": "college", "op": "eq", "value": "Tennessee"},
        {"id": "ncaaw_stanford", "label": "Played at Stanford (W)",  "scope": "NCAAW", "field": "college", "op": "eq", "value": "Stanford"},
        {"id": "ncaaw_lsu",      "label": "Played at LSU (W)",       "scope": "NCAAW", "field": "college", "op": "eq", "value": "LSU"},
        {"id": "ncaaw_iowa",     "label": "Played at Iowa (W)",      "scope": "NCAAW", "field": "college", "op": "eq", "value": "Iowa"},

        # =================================================================
        # CROSS-SPORT CATEGORIES  (sport_scope='ALL')
        # These are the fun ones -- connect NFL, NBA, MLB, NHL, WNBA, NCAAF, NCAAB, NCAAW
        # =================================================================
        # --- College ---
        {"id": "college_alabama",  "label": "Went to Alabama",   "scope": "ALL", "field": "college", "op": "eq", "value": "Alabama"},
        {"id": "college_usc",      "label": "Went to USC",       "scope": "ALL", "field": "college", "op": "eq", "value": "USC"},
        {"id": "college_ohio_st",  "label": "Went to Ohio State","scope": "ALL", "field": "college", "op": "eq", "value": "Ohio State"},
        {"id": "college_michigan", "label": "Went to Michigan",  "scope": "ALL", "field": "college", "op": "eq", "value": "Michigan"},
        {"id": "college_florida",  "label": "Went to Florida",   "scope": "ALL", "field": "college", "op": "eq", "value": "Florida"},
        {"id": "college_lsu",      "label": "Went to LSU",       "scope": "ALL", "field": "college", "op": "eq", "value": "LSU"},
        {"id": "college_nd",       "label": "Went to Notre Dame","scope": "ALL", "field": "college", "op": "eq", "value": "Notre Dame"},
        {"id": "college_texas",    "label": "Went to Texas",     "scope": "ALL", "field": "college", "op": "eq", "value": "Texas"},
        {"id": "college_penn_st",  "label": "Went to Penn State","scope": "ALL", "field": "college", "op": "eq", "value": "Penn State"},
        {"id": "college_miami",    "label": "Went to Miami",     "scope": "ALL", "field": "college", "op": "eq", "value": "Miami"},
        {"id": "college_oklahoma", "label": "Went to Oklahoma",  "scope": "ALL", "field": "college", "op": "eq", "value": "Oklahoma"},
        {"id": "college_duke",     "label": "Went to Duke",      "scope": "ALL", "field": "college", "op": "eq", "value": "Duke"},
        {"id": "college_kentucky", "label": "Went to Kentucky",  "scope": "ALL", "field": "college", "op": "eq", "value": "Kentucky"},
        {"id": "college_unc",      "label": "Went to UNC",       "scope": "ALL", "field": "college", "op": "eq", "value": "North Carolina"},

        # --- Birth state ---
        {"id": "born_tx",  "label": "Born in Texas",      "scope": "ALL", "field": "birth_state", "op": "eq", "value": "TX"},
        {"id": "born_ca",  "label": "Born in California", "scope": "ALL", "field": "birth_state", "op": "eq", "value": "CA"},
        {"id": "born_fl",  "label": "Born in Florida",    "scope": "ALL", "field": "birth_state", "op": "eq", "value": "FL"},
        {"id": "born_ga",  "label": "Born in Georgia",    "scope": "ALL", "field": "birth_state", "op": "eq", "value": "GA"},
        {"id": "born_pa",  "label": "Born in Pennsylvania","scope": "ALL","field": "birth_state", "op": "eq", "value": "PA"},
        {"id": "born_oh",  "label": "Born in Ohio",       "scope": "ALL", "field": "birth_state", "op": "eq", "value": "OH"},
        {"id": "born_il",  "label": "Born in Illinois",   "scope": "ALL", "field": "birth_state", "op": "eq", "value": "IL"},
        {"id": "born_la",  "label": "Born in Louisiana",  "scope": "ALL", "field": "birth_state", "op": "eq", "value": "LA"},
        {"id": "born_ny",  "label": "Born in New York",   "scope": "ALL", "field": "birth_state", "op": "eq", "value": "NY"},
        {"id": "born_intl","label": "Born Outside USA",   "scope": "ALL", "field": "birth_country","op": "neq","value": "USA"},

        # --- Birth decade ---
        {"id": "born_50s", "label": "Born in the 1950s",  "scope": "ALL", "field": "birth_year", "op": "gte", "value": 1950, "value2": 1959},
        {"id": "born_60s", "label": "Born in the 1960s",  "scope": "ALL", "field": "birth_year", "op": "gte", "value": 1960, "value2": 1969},
        {"id": "born_70s", "label": "Born in the 1970s",  "scope": "ALL", "field": "birth_year", "op": "gte", "value": 1970, "value2": 1979},
        {"id": "born_80s", "label": "Born in the 1980s",  "scope": "ALL", "field": "birth_year", "op": "gte", "value": 1980, "value2": 1989},
        {"id": "born_90s", "label": "Born in the 1990s",  "scope": "ALL", "field": "birth_year", "op": "gte", "value": 1990, "value2": 1999},

        # --- Draft (applies cross-sport) ---
        {"id": "pick1",    "label": "#1 Overall Pick",    "scope": "ALL", "field": "draft_pick",  "op": "eq",  "value": 1},
        {"id": "top5",     "label": "Top 5 Pick",         "scope": "ALL", "field": "draft_pick",  "op": "lte", "value": 5},
        {"id": "top10",    "label": "Top 10 Pick",        "scope": "ALL", "field": "draft_pick",  "op": "lte", "value": 10},
        {"id": "round1",   "label": "1st Round Pick",     "scope": "ALL", "field": "draft_round", "op": "eq",  "value": 1},
        {"id": "undrafted","label": "Undrafted",          "scope": "ALL", "field": "draft_round", "op": "eq",  "value": 0},
        {"id": "late_rd",  "label": "Undrafted or Late Round","scope":"ALL","field":"draft_round","op": "gte", "value": 5},

        # --- Active decades (cross-sport) ---
        {"id": "decade_60s","label": "Played in 1960s",  "scope": "ALL", "field": "active_decades","op": "has", "value": "1960s"},
        {"id": "decade_70s","label": "Played in 1970s",  "scope": "ALL", "field": "active_decades","op": "has", "value": "1970s"},
        {"id": "decade_80s","label": "Played in 1980s",  "scope": "ALL", "field": "active_decades","op": "has", "value": "1980s"},
        {"id": "decade_90s","label": "Played in 1990s",  "scope": "ALL", "field": "active_decades","op": "has", "value": "1990s"},
        {"id": "decade_00s","label": "Played in 2000s",  "scope": "ALL", "field": "active_decades","op": "has", "value": "2000s"},
        {"id": "decade_10s","label": "Played in 2010s",  "scope": "ALL", "field": "active_decades","op": "has", "value": "2010s"},
        {"id": "decade_20s","label": "Played in 2020s",  "scope": "ALL", "field": "active_decades","op": "has", "value": "2020s"},
    ]


# -------------------------------------------------------------------------
# Category membership evaluation
# -------------------------------------------------------------------------
def player_matches(player: dict, cat: dict) -> bool:
    field = cat["field"]
    op    = cat["op"]
    val   = cat["value"]
    pval  = player.get(field)

    if op == "gte":
        if "value2" in cat:
            # between (birth decade)
            return val <= (pval or 0) <= cat["value2"]
        return (pval or 0) >= val
    elif op == "lte":
        return 0 < (pval or 0) <= val
    elif op == "eq":
        return pval == val
    elif op == "neq":
        return pval != val
    elif op == "has":
        try:
            arr = json.loads(pval) if isinstance(pval, str) else (pval or [])
            return val in arr
        except Exception:
            return False
    elif op == "in":
        return pval in val
    return False


# -------------------------------------------------------------------------
# Main load function
# -------------------------------------------------------------------------
def rebuild_categories(conn, sport_filter: str = "ALL"):
    c = conn.cursor()
    categories = get_categories()

    # Upsert categories
    for cat in categories:
        c.execute("""
            INSERT INTO categories (id, label, sport_scope, field, op, value, value2)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                label=excluded.label,
                sport_scope=excluded.sport_scope,
                field=excluded.field,
                op=excluded.op,
                value=excluded.value,
                value2=excluded.value2
        """, (
            cat["id"], cat["label"], cat["scope"],
            cat["field"], cat["op"], str(cat["value"]),
            str(cat.get("value2", "")),
        ))
    conn.commit()
    log.info(f"Upserted {len(categories)} categories")

    # Fetch players (filter by sport if requested)
    if sport_filter == "ALL":
        c.execute("SELECT * FROM players")
    else:
        c.execute("SELECT * FROM players WHERE sport=?", (sport_filter,))
    players = [dict(row) for row in c.fetchall()]

    # Parse JSON fields
    for p in players:
        for f in ("teams", "active_decades"):
            try:
                p[f] = json.loads(p[f]) if p[f] else []
            except Exception:
                p[f] = []

    # Rebuild player_categories
    log.info(f"Rebuilding category memberships for {len(players)} players...")
    if sport_filter == "ALL":
        c.execute("DELETE FROM player_categories")
    else:
        c.execute("""
            DELETE FROM player_categories WHERE player_id IN
            (SELECT id FROM players WHERE sport=?)
        """, (sport_filter,))
    conn.commit()

    membership_count = 0
    for p in players:
        for cat in categories:
            # Only test categories that apply to this player's sport
            scope = cat["scope"]
            if scope != "ALL" and scope != p.get("sport"):
                continue
            if player_matches(p, cat):
                try:
                    c.execute("INSERT OR IGNORE INTO player_categories VALUES (?,?)",
                              (p["id"], cat["id"]))
                    membership_count += 1
                except Exception:
                    pass

    conn.commit()
    log.info(f"Built {membership_count} player-category memberships")

    # Report
    c.execute("""
        SELECT cat.label, cat.sport_scope, COUNT(pc.player_id) as n
        FROM categories cat
        LEFT JOIN player_categories pc ON cat.id = pc.category_id
        GROUP BY cat.id
        ORDER BY n DESC
        LIMIT 15
    """)
    log.info("Top 15 categories by membership:")
    for row in c.fetchall():
        log.info(f"  {row[2]:4d}  [{row[1]}]  {row[0]}")


def record_load_run(conn, sport: str, notes: str = ""):
    """Record this load pass in etl_runs."""
    c = conn.cursor()
    now = datetime.now(timezone.utc).isoformat()
    c.execute("""
        INSERT INTO etl_runs (sport, source, run_at, status, notes)
        VALUES (?, 'load', ?, 'ok', ?)
    """, (sport, now, notes))
    conn.commit()
    return c.lastrowid


def main():
    parser = argparse.ArgumentParser(description="Rebuild categories and memberships from player data")
    parser.add_argument("--sport", default="ALL", help="NFL, NBA, MLB, NHL, or ALL")
    parser.add_argument("--db",    default=DB_PATH)
    args = parser.parse_args()

    log.info(f"=== 20/20 Load: sport={args.sport} ===")
    migrate(args.db)
    conn = get_conn(args.db)

    derive_fields(conn)
    rebuild_categories(conn, args.sport)

    run_id = record_load_run(conn, args.sport, f"sport_filter={args.sport}")
    log.info(f"ETL load run recorded: id={run_id}")

    conn.close()
    log.info("Done.")


if __name__ == "__main__":
    main()

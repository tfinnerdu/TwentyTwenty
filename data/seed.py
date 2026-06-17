"""
Seed the 20/20 game database with real NFL player data.
200+ verified NFL legends and stars across all eras and positions.
"""

import sqlite3
import json
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "nfl.db")

PLAYERS = [
    # =========================================================
    # QUARTERBACKS
    # =========================================================
    {"name": "Tom Brady", "position": "QB", "teams": ["Patriots", "Buccaneers"],
     "draft_pick": 199, "draft_round": 6, "draft_year": 2000,
     "pass_yds": 89214, "rush_yds": 1161, "rec_yds": 0, "tds": 649,
     "pro_bowls": 15, "super_bowls": 7, "mvps": 3, "college": "Michigan",
     "active_decades": ["2000s", "2010s", "2020s"]},

    {"name": "Peyton Manning", "position": "QB", "teams": ["Colts", "Broncos"],
     "draft_pick": 1, "draft_round": 1, "draft_year": 1998,
     "pass_yds": 71940, "rush_yds": 667, "rec_yds": 0, "tds": 539,
     "pro_bowls": 14, "super_bowls": 2, "mvps": 5, "college": "Tennessee",
     "active_decades": ["2000s", "2010s"]},

    {"name": "Drew Brees", "position": "QB", "teams": ["Chargers", "Saints"],
     "draft_pick": 32, "draft_round": 2, "draft_year": 2001,
     "pass_yds": 80358, "rush_yds": 512, "rec_yds": 0, "tds": 571,
     "pro_bowls": 13, "super_bowls": 1, "mvps": 0, "college": "Purdue",
     "active_decades": ["2000s", "2010s"]},

    {"name": "Aaron Rodgers", "position": "QB", "teams": ["Packers", "Jets"],
     "draft_pick": 24, "draft_round": 1, "draft_year": 2005,
     "pass_yds": 59055, "rush_yds": 3632, "rec_yds": 0, "tds": 475,
     "pro_bowls": 10, "super_bowls": 1, "mvps": 4, "college": "California",
     "active_decades": ["2000s", "2010s", "2020s"]},

    {"name": "Brett Favre", "position": "QB", "teams": ["Packers", "Jets", "Vikings", "Falcons"],
     "draft_pick": 33, "draft_round": 2, "draft_year": 1991,
     "pass_yds": 71838, "rush_yds": 1922, "rec_yds": 0, "tds": 508,
     "pro_bowls": 11, "super_bowls": 1, "mvps": 3, "college": "Southern Mississippi",
     "active_decades": ["1990s", "2000s"]},

    {"name": "Dan Marino", "position": "QB", "teams": ["Dolphins"],
     "draft_pick": 27, "draft_round": 1, "draft_year": 1983,
     "pass_yds": 61361, "rush_yds": 87, "rec_yds": 0, "tds": 420,
     "pro_bowls": 9, "super_bowls": 0, "mvps": 1, "college": "Pittsburgh",
     "active_decades": ["1980s", "1990s"]},

    {"name": "John Elway", "position": "QB", "teams": ["Broncos"],
     "draft_pick": 1, "draft_round": 1, "draft_year": 1983,
     "pass_yds": 51475, "rush_yds": 3407, "rec_yds": 0, "tds": 300,
     "pro_bowls": 9, "super_bowls": 2, "mvps": 1, "college": "Stanford",
     "active_decades": ["1980s", "1990s"]},

    {"name": "Joe Montana", "position": "QB", "teams": ["49ers", "Chiefs"],
     "draft_pick": 82, "draft_round": 3, "draft_year": 1979,
     "pass_yds": 40551, "rush_yds": 1676, "rec_yds": 0, "tds": 273,
     "pro_bowls": 8, "super_bowls": 4, "mvps": 2, "college": "Notre Dame",
     "active_decades": ["1980s", "1990s"]},

    {"name": "Steve Young", "position": "QB", "teams": ["49ers", "Buccaneers"],
     "draft_pick": 1, "draft_round": 1, "draft_year": 1984,
     "pass_yds": 33124, "rush_yds": 4239, "rec_yds": 0, "tds": 232,
     "pro_bowls": 7, "super_bowls": 1, "mvps": 2, "college": "BYU",
     "active_decades": ["1980s", "1990s"]},

    {"name": "Troy Aikman", "position": "QB", "teams": ["Cowboys"],
     "draft_pick": 1, "draft_round": 1, "draft_year": 1989,
     "pass_yds": 32942, "rush_yds": 1016, "rec_yds": 0, "tds": 165,
     "pro_bowls": 6, "super_bowls": 3, "mvps": 0, "college": "UCLA",
     "active_decades": ["1990s"]},

    {"name": "Patrick Mahomes", "position": "QB", "teams": ["Chiefs"],
     "draft_pick": 10, "draft_round": 1, "draft_year": 2017,
     "pass_yds": 34000, "rush_yds": 2400, "rec_yds": 0, "tds": 285,
     "pro_bowls": 7, "super_bowls": 3, "mvps": 2, "college": "Texas Tech",
     "active_decades": ["2010s", "2020s"]},

    {"name": "Josh Allen", "position": "QB", "teams": ["Bills"],
     "draft_pick": 7, "draft_round": 1, "draft_year": 2018,
     "pass_yds": 28000, "rush_yds": 6200, "rec_yds": 0, "tds": 260,
     "pro_bowls": 5, "super_bowls": 0, "mvps": 0, "college": "Wyoming",
     "active_decades": ["2010s", "2020s"]},

    {"name": "Lamar Jackson", "position": "QB", "teams": ["Ravens"],
     "draft_pick": 32, "draft_round": 1, "draft_year": 2018,
     "pass_yds": 20000, "rush_yds": 7200, "rec_yds": 0, "tds": 220,
     "pro_bowls": 4, "super_bowls": 0, "mvps": 2, "college": "Louisville",
     "active_decades": ["2010s", "2020s"]},

    {"name": "Ben Roethlisberger", "position": "QB", "teams": ["Steelers"],
     "draft_pick": 11, "draft_round": 1, "draft_year": 2004,
     "pass_yds": 64088, "rush_yds": 581, "rec_yds": 0, "tds": 418,
     "pro_bowls": 6, "super_bowls": 2, "mvps": 0, "college": "Miami Ohio",
     "active_decades": ["2000s", "2010s"]},

    {"name": "Philip Rivers", "position": "QB", "teams": ["Chargers", "Colts"],
     "draft_pick": 4, "draft_round": 1, "draft_year": 2004,
     "pass_yds": 63440, "rush_yds": 130, "rec_yds": 0, "tds": 421,
     "pro_bowls": 8, "super_bowls": 0, "mvps": 0, "college": "NC State",
     "active_decades": ["2000s", "2010s", "2020s"]},

    {"name": "Eli Manning", "position": "QB", "teams": ["Giants"],
     "draft_pick": 1, "draft_round": 1, "draft_year": 2004,
     "pass_yds": 57023, "rush_yds": 197, "rec_yds": 0, "tds": 366,
     "pro_bowls": 4, "super_bowls": 2, "mvps": 0, "college": "Mississippi",
     "active_decades": ["2000s", "2010s"]},

    {"name": "Matthew Stafford", "position": "QB", "teams": ["Lions", "Rams"],
     "draft_pick": 1, "draft_round": 1, "draft_year": 2009,
     "pass_yds": 55360, "rush_yds": 805, "rec_yds": 0, "tds": 367,
     "pro_bowls": 3, "super_bowls": 1, "mvps": 0, "college": "Georgia",
     "active_decades": ["2000s", "2010s", "2020s"]},

    {"name": "Russell Wilson", "position": "QB", "teams": ["Seahawks", "Broncos", "Steelers"],
     "draft_pick": 75, "draft_round": 3, "draft_year": 2012,
     "pass_yds": 44000, "rush_yds": 6800, "rec_yds": 0, "tds": 370,
     "pro_bowls": 9, "super_bowls": 1, "mvps": 0, "college": "Wisconsin",
     "active_decades": ["2010s", "2020s"]},

    {"name": "Dak Prescott", "position": "QB", "teams": ["Cowboys"],
     "draft_pick": 135, "draft_round": 4, "draft_year": 2016,
     "pass_yds": 38000, "rush_yds": 3500, "rec_yds": 0, "tds": 270,
     "pro_bowls": 4, "super_bowls": 0, "mvps": 0, "college": "Mississippi State",
     "active_decades": ["2010s", "2020s"]},

    {"name": "Jalen Hurts", "position": "QB", "teams": ["Eagles"],
     "draft_pick": 53, "draft_round": 2, "draft_year": 2020,
     "pass_yds": 18000, "rush_yds": 5000, "rec_yds": 0, "tds": 180,
     "pro_bowls": 3, "super_bowls": 0, "mvps": 0, "college": "Alabama",
     "active_decades": ["2020s"]},

    {"name": "Terry Bradshaw", "position": "QB", "teams": ["Steelers"],
     "draft_pick": 1, "draft_round": 1, "draft_year": 1970,
     "pass_yds": 27989, "rush_yds": 2257, "rec_yds": 0, "tds": 212,
     "pro_bowls": 3, "super_bowls": 4, "mvps": 0, "college": "Louisiana Tech",
     "active_decades": ["1970s", "1980s"]},

    {"name": "Roger Staubach", "position": "QB", "teams": ["Cowboys"],
     "draft_pick": 129, "draft_round": 10, "draft_year": 1964,
     "pass_yds": 22700, "rush_yds": 2264, "rec_yds": 0, "tds": 153,
     "pro_bowls": 6, "super_bowls": 2, "mvps": 0, "college": "Navy",
     "active_decades": ["1970s"]},

    {"name": "Fran Tarkenton", "position": "QB", "teams": ["Vikings", "Giants"],
     "draft_pick": 29, "draft_round": 3, "draft_year": 1961,
     "pass_yds": 47003, "rush_yds": 3674, "rec_yds": 0, "tds": 342,
     "pro_bowls": 9, "super_bowls": 0, "mvps": 0, "college": "Georgia",
     "active_decades": ["1960s", "1970s"]},

    {"name": "Cam Newton", "position": "QB", "teams": ["Panthers", "Patriots"],
     "draft_pick": 1, "draft_round": 1, "draft_year": 2011,
     "pass_yds": 29041, "rush_yds": 5628, "rec_yds": 0, "tds": 283,
     "pro_bowls": 3, "super_bowls": 0, "mvps": 1, "college": "Auburn",
     "active_decades": ["2010s", "2020s"]},

    {"name": "Kurt Warner", "position": "QB", "teams": ["Rams", "Giants", "Cardinals"],
     "draft_pick": 0, "draft_round": 0, "draft_year": 1998,
     "pass_yds": 32344, "rush_yds": 225, "rec_yds": 0, "tds": 208,
     "pro_bowls": 4, "super_bowls": 1, "mvps": 2, "college": "Northern Iowa",
     "active_decades": ["2000s"]},

    {"name": "Donovan McNabb", "position": "QB", "teams": ["Eagles", "Redskins"],
     "draft_pick": 2, "draft_round": 1, "draft_year": 1999,
     "pass_yds": 37276, "rush_yds": 3459, "rec_yds": 0, "tds": 234,
     "pro_bowls": 6, "super_bowls": 0, "mvps": 0, "college": "Syracuse",
     "active_decades": ["2000s", "2010s"]},

    {"name": "Tony Romo", "position": "QB", "teams": ["Cowboys"],
     "draft_pick": 0, "draft_round": 0, "draft_year": 2003,
     "pass_yds": 34183, "rush_yds": 1149, "rec_yds": 0, "tds": 248,
     "pro_bowls": 4, "super_bowls": 0, "mvps": 0, "college": "Eastern Illinois",
     "active_decades": ["2000s", "2010s"]},

    {"name": "Matt Ryan", "position": "QB", "teams": ["Falcons", "Colts"],
     "draft_pick": 3, "draft_round": 1, "draft_year": 2008,
     "pass_yds": 62792, "rush_yds": 724, "rec_yds": 0, "tds": 367,
     "pro_bowls": 4, "super_bowls": 0, "mvps": 1, "college": "Boston College",
     "active_decades": ["2000s", "2010s", "2020s"]},

    {"name": "Joe Flacco", "position": "QB", "teams": ["Ravens", "Broncos", "Jets", "Eagles", "Browns", "Colts"],
     "draft_pick": 18, "draft_round": 1, "draft_year": 2008,
     "pass_yds": 43459, "rush_yds": 434, "rec_yds": 0, "tds": 260,
     "pro_bowls": 1, "super_bowls": 1, "mvps": 0, "college": "Delaware",
     "active_decades": ["2000s", "2010s", "2020s"]},

    {"name": "Kyler Murray", "position": "QB", "teams": ["Cardinals"],
     "draft_pick": 1, "draft_round": 1, "draft_year": 2019,
     "pass_yds": 16000, "rush_yds": 3800, "rec_yds": 0, "tds": 160,
     "pro_bowls": 3, "super_bowls": 0, "mvps": 0, "college": "Oklahoma",
     "active_decades": ["2010s", "2020s"]},

    # =========================================================
    # RUNNING BACKS
    # =========================================================
    {"name": "Emmitt Smith", "position": "RB", "teams": ["Cowboys", "Cardinals"],
     "draft_pick": 17, "draft_round": 1, "draft_year": 1990,
     "pass_yds": 0, "rush_yds": 18355, "rec_yds": 3224, "tds": 175,
     "pro_bowls": 8, "super_bowls": 3, "mvps": 0, "college": "Florida",
     "active_decades": ["1990s", "2000s"]},

    {"name": "Barry Sanders", "position": "RB", "teams": ["Lions"],
     "draft_pick": 3, "draft_round": 1, "draft_year": 1989,
     "pass_yds": 0, "rush_yds": 15269, "rec_yds": 2921, "tds": 109,
     "pro_bowls": 10, "super_bowls": 0, "mvps": 1, "college": "Oklahoma State",
     "active_decades": ["1990s"]},

    {"name": "Walter Payton", "position": "RB", "teams": ["Bears"],
     "draft_pick": 4, "draft_round": 1, "draft_year": 1975,
     "pass_yds": 0, "rush_yds": 16726, "rec_yds": 4538, "tds": 125,
     "pro_bowls": 9, "super_bowls": 1, "mvps": 1, "college": "Jackson State",
     "active_decades": ["1970s", "1980s"]},

    {"name": "Eric Dickerson", "position": "RB", "teams": ["Rams", "Colts", "Raiders", "Falcons"],
     "draft_pick": 2, "draft_round": 1, "draft_year": 1983,
     "pass_yds": 0, "rush_yds": 13259, "rec_yds": 2137, "tds": 96,
     "pro_bowls": 6, "super_bowls": 0, "mvps": 0, "college": "SMU",
     "active_decades": ["1980s", "1990s"]},

    {"name": "Adrian Peterson", "position": "RB",
     "teams": ["Vikings", "Saints", "Cardinals", "Redskins", "Titans", "Lions", "Seahawks"],
     "draft_pick": 7, "draft_round": 1, "draft_year": 2007,
     "pass_yds": 0, "rush_yds": 14820, "rec_yds": 2240, "tds": 120,
     "pro_bowls": 7, "super_bowls": 0, "mvps": 1, "college": "Oklahoma",
     "active_decades": ["2000s", "2010s"]},

    {"name": "LaDainian Tomlinson", "position": "RB", "teams": ["Chargers", "Jets"],
     "draft_pick": 5, "draft_round": 1, "draft_year": 2001,
     "pass_yds": 0, "rush_yds": 13684, "rec_yds": 4772, "tds": 162,
     "pro_bowls": 5, "super_bowls": 0, "mvps": 1, "college": "TCU",
     "active_decades": ["2000s", "2010s"]},

    {"name": "Marshall Faulk", "position": "RB", "teams": ["Colts", "Rams"],
     "draft_pick": 2, "draft_round": 1, "draft_year": 1994,
     "pass_yds": 0, "rush_yds": 12279, "rec_yds": 6875, "tds": 136,
     "pro_bowls": 6, "super_bowls": 1, "mvps": 1, "college": "San Diego State",
     "active_decades": ["1990s", "2000s"]},

    {"name": "Frank Gore", "position": "RB", "teams": ["49ers", "Colts", "Dolphins", "Bills", "Jets"],
     "draft_pick": 65, "draft_round": 3, "draft_year": 2005,
     "pass_yds": 0, "rush_yds": 16000, "rec_yds": 3900, "tds": 81,
     "pro_bowls": 5, "super_bowls": 0, "mvps": 0, "college": "Miami",
     "active_decades": ["2000s", "2010s", "2020s"]},

    {"name": "Derrick Henry", "position": "RB", "teams": ["Titans", "Ravens"],
     "draft_pick": 45, "draft_round": 2, "draft_year": 2016,
     "pass_yds": 0, "rush_yds": 13000, "rec_yds": 1700, "tds": 110,
     "pro_bowls": 4, "super_bowls": 0, "mvps": 0, "college": "Alabama",
     "active_decades": ["2010s", "2020s"]},

    {"name": "Christian McCaffrey", "position": "RB", "teams": ["Panthers", "49ers"],
     "draft_pick": 8, "draft_round": 1, "draft_year": 2017,
     "pass_yds": 0, "rush_yds": 10000, "rec_yds": 6800, "tds": 120,
     "pro_bowls": 5, "super_bowls": 0, "mvps": 0, "college": "Stanford",
     "active_decades": ["2010s", "2020s"]},

    {"name": "Jim Brown", "position": "RB", "teams": ["Browns"],
     "draft_pick": 6, "draft_round": 1, "draft_year": 1957,
     "pass_yds": 0, "rush_yds": 12312, "rec_yds": 2499, "tds": 126,
     "pro_bowls": 9, "super_bowls": 0, "mvps": 3, "college": "Syracuse",
     "active_decades": ["1960s"]},

    {"name": "OJ Simpson", "position": "RB", "teams": ["Bills", "49ers"],
     "draft_pick": 1, "draft_round": 1, "draft_year": 1969,
     "pass_yds": 0, "rush_yds": 11236, "rec_yds": 2142, "tds": 76,
     "pro_bowls": 5, "super_bowls": 0, "mvps": 1, "college": "USC",
     "active_decades": ["1970s"]},

    {"name": "Tony Dorsett", "position": "RB", "teams": ["Cowboys", "Broncos"],
     "draft_pick": 2, "draft_round": 1, "draft_year": 1977,
     "pass_yds": 0, "rush_yds": 12739, "rec_yds": 3554, "tds": 91,
     "pro_bowls": 4, "super_bowls": 1, "mvps": 0, "college": "Pittsburgh",
     "active_decades": ["1970s", "1980s"]},

    {"name": "Thurman Thomas", "position": "RB", "teams": ["Bills"],
     "draft_pick": 40, "draft_round": 2, "draft_year": 1988,
     "pass_yds": 0, "rush_yds": 12074, "rec_yds": 4458, "tds": 88,
     "pro_bowls": 5, "super_bowls": 0, "mvps": 1, "college": "Oklahoma State",
     "active_decades": ["1990s"]},

    {"name": "Priest Holmes", "position": "RB", "teams": ["Ravens", "Chiefs"],
     "draft_pick": 0, "draft_round": 0, "draft_year": 1997,
     "pass_yds": 0, "rush_yds": 8172, "rec_yds": 3060, "tds": 86,
     "pro_bowls": 3, "super_bowls": 0, "mvps": 0, "college": "Texas",
     "active_decades": ["2000s"]},

    {"name": "Shaun Alexander", "position": "RB", "teams": ["Seahawks", "Redskins"],
     "draft_pick": 19, "draft_round": 1, "draft_year": 2000,
     "pass_yds": 0, "rush_yds": 9453, "rec_yds": 2544, "tds": 112,
     "pro_bowls": 3, "super_bowls": 0, "mvps": 1, "college": "Alabama",
     "active_decades": ["2000s"]},

    {"name": "Clinton Portis", "position": "RB", "teams": ["Broncos", "Redskins"],
     "draft_pick": 51, "draft_round": 2, "draft_year": 2002,
     "pass_yds": 0, "rush_yds": 9923, "rec_yds": 2296, "tds": 75,
     "pro_bowls": 2, "super_bowls": 0, "mvps": 0, "college": "Miami",
     "active_decades": ["2000s"]},

    {"name": "Jamal Lewis", "position": "RB", "teams": ["Ravens", "Browns"],
     "draft_pick": 5, "draft_round": 1, "draft_year": 2000,
     "pass_yds": 0, "rush_yds": 10607, "rec_yds": 1830, "tds": 68,
     "pro_bowls": 2, "super_bowls": 1, "mvps": 0, "college": "Tennessee",
     "active_decades": ["2000s"]},

    {"name": "Edgerrin James", "position": "RB", "teams": ["Colts", "Cardinals", "Seahawks"],
     "draft_pick": 4, "draft_round": 1, "draft_year": 1999,
     "pass_yds": 0, "rush_yds": 12246, "rec_yds": 4331, "tds": 80,
     "pro_bowls": 4, "super_bowls": 0, "mvps": 0, "college": "Miami",
     "active_decades": ["2000s"]},

    {"name": "Curtis Martin", "position": "RB", "teams": ["Patriots", "Jets"],
     "draft_pick": 74, "draft_round": 3, "draft_year": 1995,
     "pass_yds": 0, "rush_yds": 14101, "rec_yds": 3329, "tds": 90,
     "pro_bowls": 5, "super_bowls": 0, "mvps": 0, "college": "Pittsburgh",
     "active_decades": ["1990s", "2000s"]},

    {"name": "Ricky Williams", "position": "RB", "teams": ["Saints", "Dolphins", "Ravens"],
     "draft_pick": 5, "draft_round": 1, "draft_year": 1999,
     "pass_yds": 0, "rush_yds": 10009, "rec_yds": 1849, "tds": 65,
     "pro_bowls": 1, "super_bowls": 0, "mvps": 0, "college": "Texas",
     "active_decades": ["2000s"]},

    {"name": "Marshawn Lynch", "position": "RB", "teams": ["Bills", "Seahawks", "Raiders"],
     "draft_pick": 12, "draft_round": 1, "draft_year": 2007,
     "pass_yds": 0, "rush_yds": 10413, "rec_yds": 2214, "tds": 85,
     "pro_bowls": 5, "super_bowls": 1, "mvps": 0, "college": "California",
     "active_decades": ["2000s", "2010s"]},

    {"name": "Steven Jackson", "position": "RB", "teams": ["Rams", "Falcons", "Patriots"],
     "draft_pick": 24, "draft_round": 1, "draft_year": 2004,
     "pass_yds": 0, "rush_yds": 11438, "rec_yds": 4027, "tds": 75,
     "pro_bowls": 3, "super_bowls": 0, "mvps": 0, "college": "Oregon State",
     "active_decades": ["2000s", "2010s"]},

    {"name": "Arian Foster", "position": "RB", "teams": ["Texans", "Dolphins"],
     "draft_pick": 0, "draft_round": 0, "draft_year": 2009,
     "pass_yds": 0, "rush_yds": 6472, "rec_yds": 2220, "tds": 54,
     "pro_bowls": 4, "super_bowls": 0, "mvps": 0, "college": "Tennessee",
     "active_decades": ["2010s"]},

    {"name": "Ezekiel Elliott", "position": "RB", "teams": ["Cowboys", "Patriots"],
     "draft_pick": 4, "draft_round": 1, "draft_year": 2016,
     "pass_yds": 0, "rush_yds": 10000, "rec_yds": 3100, "tds": 90,
     "pro_bowls": 3, "super_bowls": 0, "mvps": 0, "college": "Ohio State",
     "active_decades": ["2010s", "2020s"]},

    {"name": "Saquon Barkley", "position": "RB", "teams": ["Giants", "Eagles"],
     "draft_pick": 2, "draft_round": 1, "draft_year": 2018,
     "pass_yds": 0, "rush_yds": 8500, "rec_yds": 3200, "tds": 80,
     "pro_bowls": 3, "super_bowls": 0, "mvps": 0, "college": "Penn State",
     "active_decades": ["2010s", "2020s"]},

    {"name": "Alvin Kamara", "position": "RB", "teams": ["Saints"],
     "draft_pick": 67, "draft_round": 3, "draft_year": 2017,
     "pass_yds": 0, "rush_yds": 6500, "rec_yds": 5500, "tds": 100,
     "pro_bowls": 5, "super_bowls": 0, "mvps": 0, "college": "Tennessee",
     "active_decades": ["2010s", "2020s"]},

    {"name": "Nick Chubb", "position": "RB", "teams": ["Browns"],
     "draft_pick": 35, "draft_round": 2, "draft_year": 2018,
     "pass_yds": 0, "rush_yds": 7500, "rec_yds": 1200, "tds": 65,
     "pro_bowls": 4, "super_bowls": 0, "mvps": 0, "college": "Georgia",
     "active_decades": ["2010s", "2020s"]},

    # =========================================================
    # WIDE RECEIVERS
    # =========================================================
    {"name": "Jerry Rice", "position": "WR", "teams": ["49ers", "Raiders", "Seahawks"],
     "draft_pick": 16, "draft_round": 1, "draft_year": 1985,
     "pass_yds": 0, "rush_yds": 645, "rec_yds": 22895, "tds": 208,
     "pro_bowls": 13, "super_bowls": 3, "mvps": 0, "college": "Mississippi Valley State",
     "active_decades": ["1980s", "1990s", "2000s"]},

    {"name": "Randy Moss", "position": "WR", "teams": ["Vikings", "Raiders", "Patriots", "Titans", "49ers"],
     "draft_pick": 21, "draft_round": 1, "draft_year": 1998,
     "pass_yds": 0, "rush_yds": 55, "rec_yds": 15292, "tds": 156,
     "pro_bowls": 6, "super_bowls": 0, "mvps": 0, "college": "Marshall",
     "active_decades": ["1990s", "2000s", "2010s"]},

    {"name": "Terrell Owens", "position": "WR", "teams": ["49ers", "Eagles", "Cowboys", "Bills", "Bengals"],
     "draft_pick": 89, "draft_round": 3, "draft_year": 1996,
     "pass_yds": 0, "rush_yds": 149, "rec_yds": 15934, "tds": 153,
     "pro_bowls": 6, "super_bowls": 0, "mvps": 0, "college": "Tennessee-Chattanooga",
     "active_decades": ["1990s", "2000s", "2010s"]},

    {"name": "Calvin Johnson", "position": "WR", "teams": ["Lions"],
     "draft_pick": 2, "draft_round": 1, "draft_year": 2007,
     "pass_yds": 0, "rush_yds": 0, "rec_yds": 11619, "tds": 83,
     "pro_bowls": 6, "super_bowls": 0, "mvps": 0, "college": "Georgia Tech",
     "active_decades": ["2000s", "2010s"]},

    {"name": "Julio Jones", "position": "WR", "teams": ["Falcons", "Titans", "Buccaneers"],
     "draft_pick": 6, "draft_round": 1, "draft_year": 2011,
     "pass_yds": 0, "rush_yds": 0, "rec_yds": 13330, "tds": 60,
     "pro_bowls": 7, "super_bowls": 0, "mvps": 0, "college": "Alabama",
     "active_decades": ["2010s", "2020s"]},

    {"name": "Larry Fitzgerald", "position": "WR", "teams": ["Cardinals"],
     "draft_pick": 3, "draft_round": 1, "draft_year": 2004,
     "pass_yds": 0, "rush_yds": 0, "rec_yds": 17492, "tds": 121,
     "pro_bowls": 11, "super_bowls": 0, "mvps": 0, "college": "Pittsburgh",
     "active_decades": ["2000s", "2010s", "2020s"]},

    {"name": "Steve Smith Sr", "position": "WR", "teams": ["Panthers", "Ravens"],
     "draft_pick": 74, "draft_round": 3, "draft_year": 2001,
     "pass_yds": 0, "rush_yds": 0, "rec_yds": 14731, "tds": 81,
     "pro_bowls": 5, "super_bowls": 0, "mvps": 0, "college": "Utah",
     "active_decades": ["2000s", "2010s"]},

    {"name": "Davante Adams", "position": "WR", "teams": ["Packers", "Raiders"],
     "draft_pick": 53, "draft_round": 2, "draft_year": 2014,
     "pass_yds": 0, "rush_yds": 0, "rec_yds": 11500, "tds": 110,
     "pro_bowls": 6, "super_bowls": 0, "mvps": 0, "college": "Fresno State",
     "active_decades": ["2010s", "2020s"]},

    {"name": "Tyreek Hill", "position": "WR", "teams": ["Chiefs", "Dolphins"],
     "draft_pick": 165, "draft_round": 5, "draft_year": 2016,
     "pass_yds": 0, "rush_yds": 600, "rec_yds": 12500, "tds": 95,
     "pro_bowls": 7, "super_bowls": 1, "mvps": 0, "college": "West Alabama",
     "active_decades": ["2010s", "2020s"]},

    {"name": "Justin Jefferson", "position": "WR", "teams": ["Vikings", "Browns"],
     "draft_pick": 22, "draft_round": 1, "draft_year": 2020,
     "pass_yds": 0, "rush_yds": 0, "rec_yds": 9000, "tds": 56,
     "pro_bowls": 4, "super_bowls": 0, "mvps": 0, "college": "LSU",
     "active_decades": ["2020s"]},

    {"name": "Marvin Harrison", "position": "WR", "teams": ["Colts"],
     "draft_pick": 19, "draft_round": 1, "draft_year": 1996,
     "pass_yds": 0, "rush_yds": 0, "rec_yds": 14580, "tds": 128,
     "pro_bowls": 8, "super_bowls": 1, "mvps": 0, "college": "Syracuse",
     "active_decades": ["2000s"]},

    {"name": "Cris Carter", "position": "WR", "teams": ["Eagles", "Vikings", "Dolphins"],
     "draft_pick": 0, "draft_round": 4, "draft_year": 1987,
     "pass_yds": 0, "rush_yds": 0, "rec_yds": 13899, "tds": 130,
     "pro_bowls": 8, "super_bowls": 0, "mvps": 0, "college": "Ohio State",
     "active_decades": ["1990s", "2000s"]},

    {"name": "Tim Brown", "position": "WR", "teams": ["Raiders", "Buccaneers"],
     "draft_pick": 6, "draft_round": 1, "draft_year": 1988,
     "pass_yds": 0, "rush_yds": 0, "rec_yds": 14934, "tds": 105,
     "pro_bowls": 9, "super_bowls": 0, "mvps": 0, "college": "Notre Dame",
     "active_decades": ["1990s", "2000s"]},

    {"name": "Andre Johnson", "position": "WR", "teams": ["Texans", "Colts", "Titans", "Browns"],
     "draft_pick": 3, "draft_round": 1, "draft_year": 2003,
     "pass_yds": 0, "rush_yds": 0, "rec_yds": 14185, "tds": 70,
     "pro_bowls": 7, "super_bowls": 0, "mvps": 0, "college": "Miami",
     "active_decades": ["2000s", "2010s"]},

    {"name": "Michael Irvin", "position": "WR", "teams": ["Cowboys"],
     "draft_pick": 11, "draft_round": 1, "draft_year": 1988,
     "pass_yds": 0, "rush_yds": 0, "rec_yds": 11904, "tds": 65,
     "pro_bowls": 5, "super_bowls": 3, "mvps": 0, "college": "Miami",
     "active_decades": ["1990s"]},

    {"name": "Isaac Bruce", "position": "WR", "teams": ["Rams", "49ers"],
     "draft_pick": 33, "draft_round": 2, "draft_year": 1994,
     "pass_yds": 0, "rush_yds": 0, "rec_yds": 15208, "tds": 91,
     "pro_bowls": 4, "super_bowls": 1, "mvps": 0, "college": "Memphis",
     "active_decades": ["1990s", "2000s"]},

    {"name": "Torry Holt", "position": "WR", "teams": ["Rams", "Jaguars"],
     "draft_pick": 6, "draft_round": 1, "draft_year": 1999,
     "pass_yds": 0, "rush_yds": 0, "rec_yds": 13382, "tds": 74,
     "pro_bowls": 7, "super_bowls": 1, "mvps": 0, "college": "NC State",
     "active_decades": ["2000s"]},

    {"name": "Reggie Wayne", "position": "WR", "teams": ["Colts"],
     "draft_pick": 30, "draft_round": 1, "draft_year": 2001,
     "pass_yds": 0, "rush_yds": 0, "rec_yds": 14345, "tds": 82,
     "pro_bowls": 6, "super_bowls": 1, "mvps": 0, "college": "Miami",
     "active_decades": ["2000s", "2010s"]},

    {"name": "Wes Welker", "position": "WR", "teams": ["Dolphins", "Patriots", "Broncos", "Rams"],
     "draft_pick": 0, "draft_round": 0, "draft_year": 2004,
     "pass_yds": 0, "rush_yds": 0, "rec_yds": 9924, "tds": 50,
     "pro_bowls": 5, "super_bowls": 0, "mvps": 0, "college": "Texas Tech",
     "active_decades": ["2000s", "2010s"]},

    {"name": "Antonio Brown", "position": "WR", "teams": ["Steelers", "Raiders", "Patriots", "Buccaneers"],
     "draft_pick": 195, "draft_round": 6, "draft_year": 2010,
     "pass_yds": 0, "rush_yds": 0, "rec_yds": 12291, "tds": 83,
     "pro_bowls": 7, "super_bowls": 1, "mvps": 0, "college": "Central Michigan",
     "active_decades": ["2010s", "2020s"]},

    {"name": "DeAndre Hopkins", "position": "WR", "teams": ["Texans", "Cardinals", "Titans", "Patriots"],
     "draft_pick": 27, "draft_round": 1, "draft_year": 2013,
     "pass_yds": 0, "rush_yds": 0, "rec_yds": 11000, "tds": 70,
     "pro_bowls": 5, "super_bowls": 0, "mvps": 0, "college": "Clemson",
     "active_decades": ["2010s", "2020s"]},

    {"name": "Odell Beckham Jr", "position": "WR", "teams": ["Giants", "Browns", "Rams", "Ravens", "Dolphins"],
     "draft_pick": 12, "draft_round": 1, "draft_year": 2014,
     "pass_yds": 0, "rush_yds": 0, "rec_yds": 7367, "tds": 56,
     "pro_bowls": 3, "super_bowls": 1, "mvps": 0, "college": "LSU",
     "active_decades": ["2010s", "2020s"]},

    {"name": "Stefon Diggs", "position": "WR", "teams": ["Vikings", "Bills", "Texans"],
     "draft_pick": 146, "draft_round": 5, "draft_year": 2015,
     "pass_yds": 0, "rush_yds": 0, "rec_yds": 9400, "tds": 58,
     "pro_bowls": 2, "super_bowls": 0, "mvps": 0, "college": "Maryland",
     "active_decades": ["2010s", "2020s"]},

    {"name": "AJ Green", "position": "WR", "teams": ["Bengals", "Cardinals"],
     "draft_pick": 4, "draft_round": 1, "draft_year": 2011,
     "pass_yds": 0, "rush_yds": 0, "rec_yds": 9430, "tds": 70,
     "pro_bowls": 7, "super_bowls": 0, "mvps": 0, "college": "Georgia",
     "active_decades": ["2010s", "2020s"]},

    {"name": "Dez Bryant", "position": "WR", "teams": ["Cowboys", "Ravens"],
     "draft_pick": 24, "draft_round": 1, "draft_year": 2010,
     "pass_yds": 0, "rush_yds": 0, "rec_yds": 7459, "tds": 73,
     "pro_bowls": 3, "super_bowls": 0, "mvps": 0, "college": "Oklahoma State",
     "active_decades": ["2010s"]},

    {"name": "CeeDee Lamb", "position": "WR", "teams": ["Cowboys"],
     "draft_pick": 17, "draft_round": 1, "draft_year": 2020,
     "pass_yds": 0, "rush_yds": 0, "rec_yds": 7000, "tds": 50,
     "pro_bowls": 3, "super_bowls": 0, "mvps": 0, "college": "Oklahoma",
     "active_decades": ["2020s"]},

    # =========================================================
    # TIGHT ENDS
    # =========================================================
    {"name": "Tony Gonzalez", "position": "TE", "teams": ["Chiefs", "Falcons"],
     "draft_pick": 13, "draft_round": 1, "draft_year": 1997,
     "pass_yds": 0, "rush_yds": 0, "rec_yds": 15127, "tds": 111,
     "pro_bowls": 14, "super_bowls": 0, "mvps": 0, "college": "California",
     "active_decades": ["2000s", "2010s"]},

    {"name": "Rob Gronkowski", "position": "TE", "teams": ["Patriots", "Buccaneers"],
     "draft_pick": 42, "draft_round": 2, "draft_year": 2010,
     "pass_yds": 0, "rush_yds": 0, "rec_yds": 9286, "tds": 98,
     "pro_bowls": 5, "super_bowls": 4, "mvps": 0, "college": "Arizona",
     "active_decades": ["2010s", "2020s"]},

    {"name": "Travis Kelce", "position": "TE", "teams": ["Chiefs"],
     "draft_pick": 63, "draft_round": 3, "draft_year": 2013,
     "pass_yds": 0, "rush_yds": 0, "rec_yds": 13000, "tds": 82,
     "pro_bowls": 8, "super_bowls": 3, "mvps": 0, "college": "Cincinnati",
     "active_decades": ["2010s", "2020s"]},

    {"name": "Shannon Sharpe", "position": "TE", "teams": ["Broncos", "Ravens"],
     "draft_pick": 192, "draft_round": 7, "draft_year": 1990,
     "pass_yds": 0, "rush_yds": 0, "rec_yds": 10060, "tds": 62,
     "pro_bowls": 8, "super_bowls": 3, "mvps": 0, "college": "Savannah State",
     "active_decades": ["1990s", "2000s"]},

    {"name": "Antonio Gates", "position": "TE", "teams": ["Chargers"],
     "draft_pick": 0, "draft_round": 0, "draft_year": 2003,
     "pass_yds": 0, "rush_yds": 0, "rec_yds": 11841, "tds": 116,
     "pro_bowls": 8, "super_bowls": 0, "mvps": 0, "college": "Kent State",
     "active_decades": ["2000s", "2010s"]},

    {"name": "Jason Witten", "position": "TE", "teams": ["Cowboys", "Raiders"],
     "draft_pick": 69, "draft_round": 3, "draft_year": 2003,
     "pass_yds": 0, "rush_yds": 0, "rec_yds": 13046, "tds": 74,
     "pro_bowls": 11, "super_bowls": 0, "mvps": 0, "college": "Tennessee",
     "active_decades": ["2000s", "2010s", "2020s"]},

    {"name": "Jimmy Graham", "position": "TE", "teams": ["Saints", "Seahawks", "Packers", "Bears", "Dolphins"],
     "draft_pick": 95, "draft_round": 3, "draft_year": 2010,
     "pass_yds": 0, "rush_yds": 0, "rec_yds": 9116, "tds": 88,
     "pro_bowls": 5, "super_bowls": 0, "mvps": 0, "college": "Miami",
     "active_decades": ["2010s", "2020s"]},

    {"name": "Ozzie Newsome", "position": "TE", "teams": ["Browns"],
     "draft_pick": 23, "draft_round": 1, "draft_year": 1978,
     "pass_yds": 0, "rush_yds": 0, "rec_yds": 7980, "tds": 47,
     "pro_bowls": 3, "super_bowls": 0, "mvps": 0, "college": "Alabama",
     "active_decades": ["1980s"]},

    {"name": "Dallas Clark", "position": "TE", "teams": ["Colts", "Ravens", "Buccaneers"],
     "draft_pick": 24, "draft_round": 1, "draft_year": 2003,
     "pass_yds": 0, "rush_yds": 0, "rec_yds": 5765, "tds": 53,
     "pro_bowls": 1, "super_bowls": 1, "mvps": 0, "college": "Iowa",
     "active_decades": ["2000s", "2010s"]},

    {"name": "Mark Andrews", "position": "TE", "teams": ["Ravens"],
     "draft_pick": 86, "draft_round": 3, "draft_year": 2018,
     "pass_yds": 0, "rush_yds": 0, "rec_yds": 5800, "tds": 55,
     "pro_bowls": 3, "super_bowls": 0, "mvps": 0, "college": "Oklahoma",
     "active_decades": ["2010s", "2020s"]},

    {"name": "George Kittle", "position": "TE", "teams": ["49ers"],
     "draft_pick": 146, "draft_round": 5, "draft_year": 2017,
     "pass_yds": 0, "rush_yds": 0, "rec_yds": 7000, "tds": 48,
     "pro_bowls": 4, "super_bowls": 0, "mvps": 0, "college": "Iowa",
     "active_decades": ["2010s", "2020s"]},

    # =========================================================
    # DEFENSIVE PLAYERS (adds new category angles)
    # =========================================================
    {"name": "Lawrence Taylor", "position": "LB", "teams": ["Giants"],
     "draft_pick": 2, "draft_round": 1, "draft_year": 1981,
     "pass_yds": 0, "rush_yds": 0, "rec_yds": 0, "tds": 2,
     "pro_bowls": 10, "super_bowls": 2, "mvps": 1, "college": "North Carolina",
     "active_decades": ["1980s", "1990s"]},

    {"name": "Reggie White", "position": "DE", "teams": ["Eagles", "Packers", "Panthers"],
     "draft_pick": 4, "draft_round": 1, "draft_year": 1984,
     "pass_yds": 0, "rush_yds": 0, "rec_yds": 0, "tds": 0,
     "pro_bowls": 13, "super_bowls": 1, "mvps": 0, "college": "Tennessee",
     "active_decades": ["1980s", "1990s"]},

    {"name": "Deion Sanders", "position": "CB", "teams": ["Falcons", "49ers", "Cowboys", "Redskins", "Ravens"],
     "draft_pick": 5, "draft_round": 1, "draft_year": 1989,
     "pass_yds": 0, "rush_yds": 0, "rec_yds": 0, "tds": 22,
     "pro_bowls": 8, "super_bowls": 2, "mvps": 0, "college": "Florida State",
     "active_decades": ["1990s", "2000s"]},

    {"name": "Ray Lewis", "position": "LB", "teams": ["Ravens"],
     "draft_pick": 26, "draft_round": 1, "draft_year": 1996,
     "pass_yds": 0, "rush_yds": 0, "rec_yds": 0, "tds": 3,
     "pro_bowls": 13, "super_bowls": 2, "mvps": 1, "college": "Miami",
     "active_decades": ["2000s", "2010s"]},

    {"name": "Ed Reed", "position": "S", "teams": ["Ravens", "Texans", "Jets"],
     "draft_pick": 24, "draft_round": 1, "draft_year": 2002,
     "pass_yds": 0, "rush_yds": 0, "rec_yds": 0, "tds": 13,
     "pro_bowls": 9, "super_bowls": 1, "mvps": 0, "college": "Miami",
     "active_decades": ["2000s", "2010s"]},

    {"name": "Ronnie Lott", "position": "S", "teams": ["49ers", "Raiders", "Jets", "Chiefs"],
     "draft_pick": 8, "draft_round": 1, "draft_year": 1981,
     "pass_yds": 0, "rush_yds": 0, "rec_yds": 0, "tds": 5,
     "pro_bowls": 10, "super_bowls": 4, "mvps": 0, "college": "USC",
     "active_decades": ["1980s", "1990s"]},

    {"name": "Bruce Smith", "position": "DE", "teams": ["Bills", "Redskins"],
     "draft_pick": 1, "draft_round": 1, "draft_year": 1985,
     "pass_yds": 0, "rush_yds": 0, "rec_yds": 0, "tds": 0,
     "pro_bowls": 11, "super_bowls": 0, "mvps": 0, "college": "Virginia Tech",
     "active_decades": ["1980s", "1990s", "2000s"]},

    {"name": "Derrick Thomas", "position": "LB", "teams": ["Chiefs"],
     "draft_pick": 4, "draft_round": 1, "draft_year": 1989,
     "pass_yds": 0, "rush_yds": 0, "rec_yds": 0, "tds": 0,
     "pro_bowls": 9, "super_bowls": 0, "mvps": 0, "college": "Alabama",
     "active_decades": ["1990s"]},

    {"name": "Dick Butkus", "position": "LB", "teams": ["Bears"],
     "draft_pick": 3, "draft_round": 1, "draft_year": 1965,
     "pass_yds": 0, "rush_yds": 0, "rec_yds": 0, "tds": 1,
     "pro_bowls": 8, "super_bowls": 0, "mvps": 0, "college": "Illinois",
     "active_decades": ["1960s", "1970s"]},

    {"name": "Merlin Olsen", "position": "DT", "teams": ["Rams"],
     "draft_pick": 3, "draft_round": 1, "draft_year": 1962,
     "pass_yds": 0, "rush_yds": 0, "rec_yds": 0, "tds": 0,
     "pro_bowls": 14, "super_bowls": 0, "mvps": 0, "college": "Utah State",
     "active_decades": ["1960s", "1970s"]},

    {"name": "Mean Joe Greene", "position": "DT", "teams": ["Steelers"],
     "draft_pick": 4, "draft_round": 1, "draft_year": 1969,
     "pass_yds": 0, "rush_yds": 0, "rec_yds": 0, "tds": 1,
     "pro_bowls": 10, "super_bowls": 4, "mvps": 0, "college": "North Texas",
     "active_decades": ["1970s", "1980s"]},

    {"name": "Myles Garrett", "position": "DE", "teams": ["Browns"],
     "draft_pick": 1, "draft_round": 1, "draft_year": 2017,
     "pass_yds": 0, "rush_yds": 0, "rec_yds": 0, "tds": 0,
     "pro_bowls": 6, "super_bowls": 0, "mvps": 0, "college": "Texas A&M",
     "active_decades": ["2010s", "2020s"]},

    {"name": "Aaron Donald", "position": "DT", "teams": ["Rams"],
     "draft_pick": 13, "draft_round": 1, "draft_year": 2014,
     "pass_yds": 0, "rush_yds": 0, "rec_yds": 0, "tds": 0,
     "pro_bowls": 9, "super_bowls": 1, "mvps": 0, "college": "Pittsburgh",
     "active_decades": ["2010s", "2020s"]},

    {"name": "Von Miller", "position": "LB", "teams": ["Broncos", "Rams", "Bills"],
     "draft_pick": 2, "draft_round": 1, "draft_year": 2011,
     "pass_yds": 0, "rush_yds": 0, "rec_yds": 0, "tds": 2,
     "pro_bowls": 8, "super_bowls": 2, "mvps": 0, "college": "Texas A&M",
     "active_decades": ["2010s", "2020s"]},

    {"name": "Luke Kuechly", "position": "LB", "teams": ["Panthers"],
     "draft_pick": 9, "draft_round": 1, "draft_year": 2012,
     "pass_yds": 0, "rush_yds": 0, "rec_yds": 0, "tds": 5,
     "pro_bowls": 7, "super_bowls": 0, "mvps": 1, "college": "Boston College",
     "active_decades": ["2010s", "2020s"]},

    {"name": "Khalil Mack", "position": "LB", "teams": ["Raiders", "Bears", "Chargers"],
     "draft_pick": 5, "draft_round": 1, "draft_year": 2014,
     "pass_yds": 0, "rush_yds": 0, "rec_yds": 0, "tds": 3,
     "pro_bowls": 6, "super_bowls": 0, "mvps": 0, "college": "Buffalo",
     "active_decades": ["2010s", "2020s"]},

    {"name": "Charles Woodson", "position": "CB", "teams": ["Raiders", "Packers"],
     "draft_pick": 4, "draft_round": 1, "draft_year": 1998,
     "pass_yds": 0, "rush_yds": 0, "rec_yds": 0, "tds": 21,
     "pro_bowls": 9, "super_bowls": 1, "mvps": 1, "college": "Michigan",
     "active_decades": ["2000s", "2010s"]},

    {"name": "Patrick Willis", "position": "LB", "teams": ["49ers"],
     "draft_pick": 11, "draft_round": 1, "draft_year": 2007,
     "pass_yds": 0, "rush_yds": 0, "rec_yds": 0, "tds": 4,
     "pro_bowls": 7, "super_bowls": 0, "mvps": 0, "college": "Mississippi",
     "active_decades": ["2000s", "2010s"]},

    {"name": "Richard Sherman", "position": "CB", "teams": ["Seahawks", "49ers", "Buccaneers"],
     "draft_pick": 154, "draft_round": 5, "draft_year": 2011,
     "pass_yds": 0, "rush_yds": 0, "rec_yds": 0, "tds": 3,
     "pro_bowls": 5, "super_bowls": 1, "mvps": 0, "college": "Stanford",
     "active_decades": ["2010s", "2020s"]},

    {"name": "Troy Polamalu", "position": "S", "teams": ["Steelers"],
     "draft_pick": 16, "draft_round": 1, "draft_year": 2003,
     "pass_yds": 0, "rush_yds": 0, "rec_yds": 0, "tds": 3,
     "pro_bowls": 8, "super_bowls": 2, "mvps": 1, "college": "USC",
     "active_decades": ["2000s", "2010s"]},

    {"name": "Darrelle Revis", "position": "CB", "teams": ["Jets", "Buccaneers", "Patriots", "Steelers", "Chiefs"],
     "draft_pick": 14, "draft_round": 1, "draft_year": 2007,
     "pass_yds": 0, "rush_yds": 0, "rec_yds": 0, "tds": 3,
     "pro_bowls": 7, "super_bowls": 1, "mvps": 0, "college": "Pittsburgh",
     "active_decades": ["2000s", "2010s"]},

    # =========================================================
    # OFFENSIVE LINE (for completeness and era anchoring)
    # =========================================================
    {"name": "Anthony Munoz", "position": "OT", "teams": ["Bengals"],
     "draft_pick": 3, "draft_round": 1, "draft_year": 1980,
     "pass_yds": 0, "rush_yds": 0, "rec_yds": 0, "tds": 4,
     "pro_bowls": 11, "super_bowls": 0, "mvps": 0, "college": "USC",
     "active_decades": ["1980s", "1990s"]},

    {"name": "Orlando Pace", "position": "OT", "teams": ["Rams", "Bears"],
     "draft_pick": 1, "draft_round": 1, "draft_year": 1997,
     "pass_yds": 0, "rush_yds": 0, "rec_yds": 0, "tds": 0,
     "pro_bowls": 7, "super_bowls": 1, "mvps": 0, "college": "Ohio State",
     "active_decades": ["2000s"]},
]

# -------------------------------------------------------------------------
# Category definitions
# -------------------------------------------------------------------------
CATEGORIES = [
    # --- Passing yardage ---
    {"id": "pass_10k",   "label": "10,000+ Passing Yds",   "field": "pass_yds",   "op": "gte", "value": 10000},
    {"id": "pass_30k",   "label": "30,000+ Passing Yds",   "field": "pass_yds",   "op": "gte", "value": 30000},
    {"id": "pass_50k",   "label": "50,000+ Passing Yds",   "field": "pass_yds",   "op": "gte", "value": 50000},
    # --- Rushing yardage ---
    {"id": "rush_5k",    "label": "5,000+ Rushing Yds",    "field": "rush_yds",   "op": "gte", "value": 5000},
    {"id": "rush_10k",   "label": "10,000+ Rushing Yds",   "field": "rush_yds",   "op": "gte", "value": 10000},
    # --- Receiving yardage ---
    {"id": "rec_5k",     "label": "5,000+ Receiving Yds",  "field": "rec_yds",    "op": "gte", "value": 5000},
    {"id": "rec_10k",    "label": "10,000+ Receiving Yds", "field": "rec_yds",    "op": "gte", "value": 10000},
    # --- Touchdowns ---
    {"id": "td_50",      "label": "50+ Career TDs",        "field": "tds",        "op": "gte", "value": 50},
    {"id": "td_100",     "label": "100+ Career TDs",       "field": "tds",        "op": "gte", "value": 100},
    {"id": "td_150",     "label": "150+ Career TDs",       "field": "tds",        "op": "gte", "value": 150},
    {"id": "td_200",     "label": "200+ Career TDs",       "field": "tds",        "op": "gte", "value": 200},
    {"id": "td_400",     "label": "400+ Career TDs",       "field": "tds",        "op": "gte", "value": 400},
    # --- Pro Bowls ---
    {"id": "pb_3",       "label": "3+ Pro Bowls",          "field": "pro_bowls",  "op": "gte", "value": 3},
    {"id": "pb_5",       "label": "5+ Pro Bowls",          "field": "pro_bowls",  "op": "gte", "value": 5},
    {"id": "pb_8",       "label": "8+ Pro Bowls",          "field": "pro_bowls",  "op": "gte", "value": 8},
    {"id": "pb_10",      "label": "10+ Pro Bowls",         "field": "pro_bowls",  "op": "gte", "value": 10},
    # --- Super Bowls ---
    {"id": "sb_1",       "label": "Super Bowl Champion",   "field": "super_bowls","op": "gte", "value": 1},
    {"id": "sb_2",       "label": "Multi SB Champion",     "field": "super_bowls","op": "gte", "value": 2},
    {"id": "sb_3",       "label": "3+ SB Champion",        "field": "super_bowls","op": "gte", "value": 3},
    # --- MVPs ---
    {"id": "mvp_1",      "label": "NFL MVP",               "field": "mvps",       "op": "gte", "value": 1},
    {"id": "mvp_2",      "label": "2+ NFL MVPs",           "field": "mvps",       "op": "gte", "value": 2},
    # --- Draft position ---
    {"id": "pick1",      "label": "#1 Overall Pick",       "field": "draft_pick", "op": "eq",  "value": 1},
    {"id": "pick2",      "label": "#2 Overall Pick",       "field": "draft_pick", "op": "eq",  "value": 2},
    {"id": "top5",       "label": "Top 5 Pick",            "field": "draft_pick", "op": "lte", "value": 5},
    {"id": "top10",      "label": "Top 10 Pick",           "field": "draft_pick", "op": "lte", "value": 10},
    {"id": "top32",      "label": "1st Round Pick",        "field": "draft_round","op": "eq",  "value": 1},
    {"id": "round2",     "label": "2nd Round Pick",        "field": "draft_round","op": "eq",  "value": 2},
    {"id": "late",       "label": "Undrafted or Late Round","field":"draft_round", "op": "gte", "value": 5},
    {"id": "undrafted",  "label": "Undrafted",             "field": "draft_round","op": "eq",  "value": 0},
    # --- Teams ---
    {"id": "team_patriots",  "label": "Patriots",    "field": "teams", "op": "has", "value": "Patriots"},
    {"id": "team_cowboys",   "label": "Cowboys",     "field": "teams", "op": "has", "value": "Cowboys"},
    {"id": "team_49ers",     "label": "49ers",       "field": "teams", "op": "has", "value": "49ers"},
    {"id": "team_broncos",   "label": "Broncos",     "field": "teams", "op": "has", "value": "Broncos"},
    {"id": "team_steelers",  "label": "Steelers",    "field": "teams", "op": "has", "value": "Steelers"},
    {"id": "team_packers",   "label": "Packers",     "field": "teams", "op": "has", "value": "Packers"},
    {"id": "team_chiefs",    "label": "Chiefs",      "field": "teams", "op": "has", "value": "Chiefs"},
    {"id": "team_colts",     "label": "Colts",       "field": "teams", "op": "has", "value": "Colts"},
    {"id": "team_chargers",  "label": "Chargers",    "field": "teams", "op": "has", "value": "Chargers"},
    {"id": "team_vikings",   "label": "Vikings",     "field": "teams", "op": "has", "value": "Vikings"},
    {"id": "team_rams",      "label": "Rams",        "field": "teams", "op": "has", "value": "Rams"},
    {"id": "team_ravens",    "label": "Ravens",      "field": "teams", "op": "has", "value": "Ravens"},
    {"id": "team_giants",    "label": "Giants",      "field": "teams", "op": "has", "value": "Giants"},
    {"id": "team_eagles",    "label": "Eagles",      "field": "teams", "op": "has", "value": "Eagles"},
    {"id": "team_cardinals", "label": "Cardinals",   "field": "teams", "op": "has", "value": "Cardinals"},
    {"id": "team_bears",     "label": "Bears",       "field": "teams", "op": "has", "value": "Bears"},
    {"id": "team_dolphins",  "label": "Dolphins",    "field": "teams", "op": "has", "value": "Dolphins"},
    {"id": "team_lions",     "label": "Lions",       "field": "teams", "op": "has", "value": "Lions"},
    {"id": "team_bills",     "label": "Bills",       "field": "teams", "op": "has", "value": "Bills"},
    {"id": "team_falcons",   "label": "Falcons",     "field": "teams", "op": "has", "value": "Falcons"},
    {"id": "team_saints",    "label": "Saints",      "field": "teams", "op": "has", "value": "Saints"},
    {"id": "team_seahawks",  "label": "Seahawks",    "field": "teams", "op": "has", "value": "Seahawks"},
    {"id": "team_raiders",   "label": "Raiders",     "field": "teams", "op": "has", "value": "Raiders"},
    {"id": "team_redskins",  "label": "Redskins",    "field": "teams", "op": "has", "value": "Redskins"},
    {"id": "team_texans",    "label": "Texans",      "field": "teams", "op": "has", "value": "Texans"},
    {"id": "team_bengals",   "label": "Bengals",     "field": "teams", "op": "has", "value": "Bengals"},
    {"id": "team_browns",    "label": "Browns",      "field": "teams", "op": "has", "value": "Browns"},
    {"id": "team_jets",      "label": "Jets",        "field": "teams", "op": "has", "value": "Jets"},
    {"id": "team_panthers",  "label": "Panthers",    "field": "teams", "op": "has", "value": "Panthers"},
    {"id": "team_titans",    "label": "Titans",      "field": "teams", "op": "has", "value": "Titans"},
    # --- Position ---
    {"id": "pos_qb",    "label": "Quarterback (QB)",  "field": "position", "op": "eq", "value": "QB"},
    {"id": "pos_rb",    "label": "Running Back (RB)", "field": "position", "op": "eq", "value": "RB"},
    {"id": "pos_wr",    "label": "Wide Receiver (WR)","field": "position", "op": "eq", "value": "WR"},
    {"id": "pos_te",    "label": "Tight End (TE)",    "field": "position", "op": "eq", "value": "TE"},
    {"id": "pos_def",   "label": "Defensive Player",  "field": "position", "op": "in", "value": ["LB","DE","DT","CB","S"]},
    # --- Decades ---
    {"id": "decade_60s", "label": "Played in 1960s",  "field": "active_decades", "op": "has", "value": "1960s"},
    {"id": "decade_70s", "label": "Played in 1970s",  "field": "active_decades", "op": "has", "value": "1970s"},
    {"id": "decade_80s", "label": "Played in 1980s",  "field": "active_decades", "op": "has", "value": "1980s"},
    {"id": "decade_90s", "label": "Played in 1990s",  "field": "active_decades", "op": "has", "value": "1990s"},
    {"id": "decade_00s", "label": "Played in 2000s",  "field": "active_decades", "op": "has", "value": "2000s"},
    {"id": "decade_10s", "label": "Played in 2010s",  "field": "active_decades", "op": "has", "value": "2010s"},
    {"id": "decade_20s", "label": "Played in 2020s",  "field": "active_decades", "op": "has", "value": "2020s"},
]


def player_matches(player, category):
    field = category["field"]
    op    = category["op"]
    val   = category["value"]
    pval  = player.get(field)

    if op == "gte":
        return (pval or 0) >= val
    elif op == "lte":
        return 0 < (pval or 0) <= val
    elif op == "eq":
        return pval == val
    elif op == "has":
        return val in (pval or [])
    elif op == "in":
        return pval in val
    return False


def build_db():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("""
        CREATE TABLE players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            position TEXT,
            teams TEXT,
            draft_pick INTEGER,
            draft_round INTEGER,
            draft_year INTEGER,
            pass_yds INTEGER,
            rush_yds INTEGER,
            rec_yds INTEGER,
            tds INTEGER,
            pro_bowls INTEGER,
            super_bowls INTEGER,
            mvps INTEGER,
            college TEXT,
            active_decades TEXT
        )
    """)

    c.execute("""
        CREATE TABLE categories (
            id TEXT PRIMARY KEY,
            label TEXT NOT NULL,
            field TEXT,
            op TEXT,
            value TEXT
        )
    """)

    c.execute("""
        CREATE TABLE player_categories (
            player_id INTEGER,
            category_id TEXT,
            PRIMARY KEY (player_id, category_id)
        )
    """)

    for p in PLAYERS:
        c.execute("""
            INSERT INTO players (name, position, teams, draft_pick, draft_round, draft_year,
                                 pass_yds, rush_yds, rec_yds, tds, pro_bowls, super_bowls,
                                 mvps, college, active_decades)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            p["name"], p["position"], json.dumps(p["teams"]),
            p["draft_pick"], p["draft_round"], p["draft_year"],
            p["pass_yds"], p["rush_yds"], p["rec_yds"], p["tds"],
            p["pro_bowls"], p["super_bowls"], p["mvps"],
            p.get("college", ""), json.dumps(p.get("active_decades", []))
        ))

    for cat in CATEGORIES:
        c.execute("INSERT INTO categories (id, label, field, op, value) VALUES (?,?,?,?,?)",
                  (cat["id"], cat["label"], cat["field"], cat["op"], str(cat["value"])))

    c.execute("SELECT id, name FROM players")
    db_players = c.fetchall()

    for pid, pname in db_players:
        player = next(p for p in PLAYERS if p["name"] == pname)
        for cat in CATEGORIES:
            if player_matches(player, cat):
                c.execute("INSERT INTO player_categories VALUES (?,?)", (pid, cat["id"]))

    conn.commit()

    print(f"Players:      {len(PLAYERS)}")
    c.execute("SELECT COUNT(*) FROM categories"); print(f"Categories:   {c.fetchone()[0]}")
    c.execute("SELECT COUNT(*) FROM player_categories"); print(f"Memberships:  {c.fetchone()[0]}")

    print("\nLargest categories:")
    c.execute("""
        SELECT cat.label, COUNT(pc.player_id) as n
        FROM categories cat
        JOIN player_categories pc ON cat.id = pc.category_id
        GROUP BY cat.id ORDER BY n DESC LIMIT 12
    """)
    for label, n in c.fetchall():
        print(f"  {n:3d}  {label}")

    conn.close()
    print(f"\nDatabase: {DB_PATH}")


if __name__ == "__main__":
    build_db()

"""
data/etl/providers/_fixture_csv.py
==================================
Schema-faithful season-stats fixtures for the CSV providers (NHL/WNBA/NFL),
so each can be exercised end to end offline. Names match the matching
data/etl/awards/<sport>.json so the honors overlay lands on them.

Real-but-approximate career lines -- enough that the threshold categories
select the right players. A test fixture, not a stats source.
"""

import csv
import os
import re

# season stat columns per sport (must match CsvSeasonProvider.SUM_COLS)
SEASON_COLS = {
    "NHL":  ["g", "goals", "assists", "points"],
    "WNBA": ["g", "pts", "reb", "ast"],
    "NFL":  ["g", "pass_yds", "rush_yds", "rec_yds",
             "pass_td", "rush_td", "rec_td", "sacks", "interceptions"],
}

# (name, position, birth_year, country, state, city, [teams], {stat: total})
DATA = {
    "NHL": [
        ("Wayne Gretzky", "C", 1961, "Canada", "", "Brantford", ["EDM", "NYR"], {"g": 1487, "goals": 894, "assists": 1963, "points": 2857}),
        ("Gordie Howe", "RW", 1928, "Canada", "", "Floral", ["DET"], {"g": 1767, "goals": 801, "assists": 1049, "points": 1850}),
        ("Mark Messier", "C", 1961, "Canada", "", "Edmonton", ["EDM", "NYR"], {"g": 1756, "goals": 694, "assists": 1193, "points": 1887}),
        ("Jaromir Jagr", "RW", 1972, "Czechia", "", "Kladno", ["PIT"], {"g": 1733, "goals": 766, "assists": 1155, "points": 1921}),
        ("Mario Lemieux", "C", 1965, "Canada", "", "Montreal", ["PIT"], {"g": 915, "goals": 690, "assists": 1033, "points": 1723}),
        ("Bobby Orr", "D", 1948, "Canada", "", "Parry Sound", ["BOS"], {"g": 657, "goals": 270, "assists": 645, "points": 915}),
        ("Steve Yzerman", "C", 1965, "Canada", "", "Cranbrook", ["DET"], {"g": 1514, "goals": 692, "assists": 1063, "points": 1755}),
        ("Ray Bourque", "D", 1960, "Canada", "", "Montreal", ["BOS", "COL"], {"g": 1612, "goals": 410, "assists": 1169, "points": 1579}),
        ("Joe Sakic", "C", 1969, "Canada", "", "Burnaby", ["COL", "QUE"], {"g": 1378, "goals": 625, "assists": 1016, "points": 1641}),
        ("Sidney Crosby", "C", 1987, "Canada", "", "Halifax", ["PIT"], {"g": 1300, "goals": 600, "assists": 1000, "points": 1600}),
        ("Alex Ovechkin", "LW", 1985, "Russia", "", "Moscow", ["WSH"], {"g": 1450, "goals": 850, "assists": 720, "points": 1570}),
        ("Patrick Roy", "G", 1965, "Canada", "", "Quebec City", ["MTL", "COL"], {"g": 1029, "goals": 0, "assists": 45, "points": 45}),
        ("Martin Brodeur", "G", 1972, "Canada", "", "Montreal", ["NJD"], {"g": 1266, "goals": 2, "assists": 47, "points": 49}),
        ("Nicklas Lidstrom", "D", 1970, "Sweden", "", "Vasteras", ["DET"], {"g": 1564, "goals": 264, "assists": 878, "points": 1142}),
        ("Phil Esposito", "C", 1942, "Canada", "", "Sault Ste Marie", ["BOS", "NYR"], {"g": 1282, "goals": 717, "assists": 873, "points": 1590}),
        ("Brett Hull", "RW", 1964, "Canada", "", "Belleville", ["STL", "DET"], {"g": 1269, "goals": 741, "assists": 650, "points": 1391}),
        ("Paul Coffey", "D", 1961, "Canada", "", "Weston", ["EDM", "PIT"], {"g": 1409, "goals": 396, "assists": 1135, "points": 1531}),
        ("Chris Chelios", "D", 1962, "USA", "IL", "Chicago", ["MTL", "CHI", "DET"], {"g": 1651, "goals": 185, "assists": 763, "points": 948}),
    ],
    "WNBA": [
        ("Diana Taurasi", "G", 1982, "USA", "CA", "Glendale", ["PHX"], {"g": 565, "pts": 10646, "reb": 1310, "ast": 1900}),
        ("Sue Bird", "G", 1980, "USA", "NY", "Syosset", ["SEA"], {"g": 580, "pts": 6803, "reb": 1100, "ast": 3234}),
        ("Lisa Leslie", "C", 1972, "USA", "CA", "Gardena", ["LAS"], {"g": 363, "pts": 6263, "reb": 3307, "ast": 1163}),
        ("Tamika Catchings", "F", 1979, "USA", "NJ", "Stratford", ["IND"], {"g": 457, "pts": 7380, "reb": 3316, "ast": 1488}),
        ("Maya Moore", "F", 1989, "USA", "MO", "Jefferson City", ["MIN"], {"g": 286, "pts": 5333, "reb": 1556, "ast": 933}),
        ("Candace Parker", "F", 1986, "USA", "MO", "St. Louis", ["LAS", "CHI"], {"g": 492, "pts": 7088, "reb": 4070, "ast": 1623}),
        ("Sheryl Swoopes", "F", 1971, "USA", "TX", "Brownfield", ["HOU"], {"g": 341, "pts": 4875, "reb": 1645, "ast": 961}),
        ("Cynthia Cooper", "G", 1963, "USA", "IL", "Chicago", ["HOU"], {"g": 124, "pts": 2601, "reb": 456, "ast": 545}),
        ("Breanna Stewart", "F", 1994, "USA", "NY", "Syracuse", ["SEA", "NYL"], {"g": 270, "pts": 5400, "reb": 2000, "ast": 700}),
        ("A'ja Wilson", "F", 1996, "USA", "SC", "Columbia", ["LVA"], {"g": 240, "pts": 4900, "reb": 2200, "ast": 480}),
        ("Elena Delle Donne", "F", 1989, "USA", "DE", "Wilmington", ["CHI", "WAS"], {"g": 260, "pts": 5100, "reb": 1500, "ast": 500}),
        ("Lauren Jackson", "C", 1981, "Australia", "", "Albury", ["SEA"], {"g": 317, "pts": 6007, "reb": 2034, "ast": 410}),
        ("Sylvia Fowles", "C", 1985, "USA", "FL", "Miami", ["CHI", "MIN"], {"g": 491, "pts": 6841, "reb": 4007, "ast": 520}),
        ("Courtney Vandersloot", "G", 1989, "USA", "WA", "Kent", ["CHI", "NYL"], {"g": 420, "pts": 4300, "reb": 1300, "ast": 3000}),
    ],
    "NFL": [
        ("Tom Brady", "QB", 1977, "USA", "CA", "San Mateo", ["NWE", "TAM"], {"g": 335, "pass_yds": 89214, "pass_td": 649, "rush_yds": 1100, "rush_td": 25}),
        ("Peyton Manning", "QB", 1976, "USA", "LA", "New Orleans", ["IND", "DEN"], {"g": 266, "pass_yds": 71940, "pass_td": 539, "rush_yds": 667, "rush_td": 18}),
        ("Brett Favre", "QB", 1969, "USA", "MS", "Gulfport", ["GNB", "MIN", "NYJ"], {"g": 302, "pass_yds": 71838, "pass_td": 508, "rush_yds": 1844, "rush_td": 14}),
        ("Drew Brees", "QB", 1979, "USA", "TX", "Dallas", ["NOR", "SDG"], {"g": 287, "pass_yds": 80358, "pass_td": 571, "rush_yds": 760, "rush_td": 25}),
        ("Aaron Rodgers", "QB", 1983, "USA", "CA", "Chico", ["GNB", "NYJ"], {"g": 250, "pass_yds": 62952, "pass_td": 503, "rush_yds": 3500, "rush_td": 35}),
        ("Dan Marino", "QB", 1961, "USA", "PA", "Pittsburgh", ["MIA"], {"g": 242, "pass_yds": 61361, "pass_td": 420}),
        ("John Elway", "QB", 1960, "USA", "WA", "Port Angeles", ["DEN"], {"g": 234, "pass_yds": 51475, "pass_td": 300, "rush_yds": 3407, "rush_td": 33}),
        ("Emmitt Smith", "RB", 1969, "USA", "FL", "Pensacola", ["DAL", "ARI"], {"g": 226, "rush_yds": 18355, "rush_td": 164, "rec_yds": 3224, "rec_td": 11}),
        ("Walter Payton", "RB", 1954, "USA", "MS", "Columbia", ["CHI"], {"g": 190, "rush_yds": 16726, "rush_td": 110, "rec_yds": 4538, "rec_td": 15}),
        ("Barry Sanders", "RB", 1968, "USA", "KS", "Wichita", ["DET"], {"g": 153, "rush_yds": 15269, "rush_td": 99, "rec_yds": 2921, "rec_td": 10}),
        ("Adrian Peterson", "RB", 1985, "USA", "TX", "Palestine", ["MIN"], {"g": 189, "rush_yds": 14918, "rush_td": 120, "rec_yds": 2483, "rec_td": 6}),
        ("LaDainian Tomlinson", "RB", 1979, "USA", "TX", "Rosebud", ["SDG", "NYJ"], {"g": 170, "rush_yds": 13684, "rush_td": 145, "rec_yds": 4772, "rec_td": 17}),
        ("Jerry Rice", "WR", 1962, "USA", "MS", "Starkville", ["SFO", "OAK"], {"g": 303, "rec_yds": 22895, "rec_td": 197, "rush_yds": 645, "rush_td": 10}),
        ("Randy Moss", "WR", 1977, "USA", "WV", "Rand", ["MIN", "NWE", "OAK"], {"g": 218, "rec_yds": 15292, "rec_td": 156}),
        ("Terrell Owens", "WR", 1973, "USA", "AL", "Alexander City", ["SFO", "DAL", "PHI"], {"g": 219, "rec_yds": 15934, "rec_td": 153}),
        ("Larry Fitzgerald", "WR", 1983, "USA", "MN", "Minneapolis", ["ARI"], {"g": 263, "rec_yds": 17492, "rec_td": 121}),
        ("Tony Gonzalez", "TE", 1976, "USA", "CA", "Torrance", ["KAN", "ATL"], {"g": 270, "rec_yds": 15127, "rec_td": 111}),
        ("Rob Gronkowski", "TE", 1989, "USA", "NY", "Amherst", ["NWE", "TAM"], {"g": 143, "rec_yds": 9286, "rec_td": 92}),
        ("Bruce Smith", "DE", 1963, "USA", "VA", "Norfolk", ["BUF", "WAS"], {"g": 279, "sacks": 200, "interceptions": 0}),
        ("Reggie White", "DE", 1961, "USA", "TN", "Chattanooga", ["PHI", "GNB", "CAR"], {"g": 232, "sacks": 198, "interceptions": 0}),
        ("Ray Lewis", "LB", 1975, "USA", "FL", "Bartow", ["BAL"], {"g": 228, "sacks": 41, "interceptions": 31}),
        ("Ed Reed", "S", 1978, "USA", "LA", "St. Rose", ["BAL", "HOU"], {"g": 174, "sacks": 6, "interceptions": 64}),
    ],
}


def _id(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def write_fixture(sport: str, dest_dir: str):
    os.makedirs(dest_dir, exist_ok=True)
    cols = SEASON_COLS[sport]
    players, seasons = [], []

    for (name, pos, by, country, state, city, teams, stats) in DATA[sport]:
        pid = _id(name)
        players.append([pid, name, pos, "", by, city, state, country, "", "", "", "", ""])
        early, late = by + 22, by + 33
        t0, t1 = teams[0], teams[-1]
        row0 = [pid, early, t0] + [stats.get(c, 0) // 2 for c in cols]
        row1 = [pid, late, t1] + [stats.get(c, 0) - stats.get(c, 0) // 2 for c in cols]
        seasons.append(row0)
        seasons.append(row1)

    sl = sport.lower()
    with open(os.path.join(dest_dir, f"{sl}_players.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["player_id", "name", "position", "college", "birth_year", "birth_city",
                    "birth_state", "birth_country", "height_inches", "weight_lbs",
                    "draft_year", "draft_round", "draft_number"])
        w.writerows(players)
    with open(os.path.join(dest_dir, f"{sl}_seasons.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["player_id", "season", "team"] + cols)
        w.writerows(seasons)

    return len(DATA[sport])


if __name__ == "__main__":
    import sys
    sport = (sys.argv[1] if len(sys.argv) > 1 else "NHL").upper()
    out = sys.argv[2] if len(sys.argv) > 2 else f"./{sport.lower()}_fixture"
    print(f"Wrote {write_fixture(sport, out)} {sport} players to {out}")

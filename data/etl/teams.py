"""
data/etl/teams.py
=================
Single source of truth for NFL franchise identity.

Every team token we see resolves to ONE canonical franchise nickname:
  * nflverse city codes  -- change on relocation (OAK->LV, SD->LAC, STL/LA Rams)
  * Pro-Football-Reference franchise codes -- the /teams/<code>/ slug, which does
    NOT change on relocation ('crd' is the Cardinals in Chicago, St. Louis,
    Phoenix and Arizona alike). PREFERRED when available, because it's unambiguous.
  * full team-name strings -- "Oakland Raiders", "St. Louis Cardinals", ...

The hard cases this exists to get right (no duct tape):
  * shared-city abbreviations. 'STL' = Cardinals (1960-87) OR Rams (1995-2015);
    'LA' = Rams / Raiders / Chargers; 'BAL' = Colts (->1983) OR Ravens (1996+);
    'HOU' = Oilers (->1996) OR Texans (2002+). An abbreviation alone can't tell
    them apart -- so for PFR rows we key off the franchise href, and for nflverse
    (1999+ only) the era code is unambiguous (STL=Rams, BAL=Ravens, HOU=Texans).
  * relocations collapse to one franchise: Oakland/LA/Las Vegas -> Raiders;
    San Diego/LA -> Chargers; St. Louis/LA/Cleveland -> Rams.
  * renamed franchises resolve to their CURRENT name:
        Washington Redskins / Football Team -> Commanders
        Houston / Tennessee Oilers          -> Titans   (franchise continued)
        Boston Patriots                      -> Patriots
    The Cleveland Browns (1946-95) / Baltimore Ravens split is treated as the NFL
    does: Browns history stays Cleveland; the Ravens are their own franchise.

Resolution that returns None is a SIGNAL, not a silent drop -- run the audit
(`python -m data.etl.teams --audit --db data/nfl.db`) to surface any token the
registry doesn't know before it costs you a category.
"""

import re

# nickname -> (pfr_franchise_code, {abbrev aliases, UPPER}, {name fragments, lower})
# Abbrevs are nflverse era codes + common variants. The pfr code is added to the
# alias set automatically (UPPER), so a bare 'CRD' resolves too.
_F = {
    "Cardinals":  ("crd", {"ARI", "ARZ", "PHO", "PHX"},        {"cardinals"}),
    "Falcons":    ("atl", {"ATL"},                              {"falcons"}),
    "Ravens":     ("rav", {"BAL", "BLT"},                       {"ravens"}),
    "Bills":      ("buf", {"BUF"},                              {"bills"}),
    "Panthers":   ("car", {"CAR"},                              {"panthers"}),
    "Bears":      ("chi", {"CHI"},                              {"bears"}),
    "Bengals":    ("cin", {"CIN"},                              {"bengals"}),
    "Browns":     ("cle", {"CLE", "CLV"},                       {"browns"}),
    "Cowboys":    ("dal", {"DAL"},                              {"cowboys"}),
    "Broncos":    ("den", {"DEN"},                              {"broncos"}),
    "Lions":      ("det", {"DET"},                              {"lions"}),
    "Packers":    ("gnb", {"GB", "GNB"},                        {"packers"}),
    "Texans":     ("htx", {"HOU", "HTX", "HST"},                {"texans"}),
    "Colts":      ("clt", {"IND", "CLT", "BLT_COLTS"},          {"colts"}),
    "Jaguars":    ("jax", {"JAX", "JAC"},                       {"jaguars"}),
    "Chiefs":     ("kan", {"KC", "KAN"},                        {"chiefs"}),
    "Raiders":    ("rai", {"LV", "LVR", "OAK", "RAI"},          {"raiders"}),
    "Rams":       ("ram", {"LA", "LAR", "STL", "SL", "RAM"},     {"rams"}),
    "Chargers":   ("sdg", {"LAC", "SD", "SDG"},                 {"chargers"}),
    "Dolphins":   ("mia", {"MIA"},                              {"dolphins"}),
    "Vikings":    ("min", {"MIN"},                              {"vikings"}),
    "Patriots":   ("nwe", {"NE", "NWE"},                        {"patriots"}),
    "Saints":     ("nor", {"NO", "NOR"},                        {"saints"}),
    "Giants":     ("nyg", {"NYG"},                              {"giants"}),
    "Jets":       ("nyj", {"NYJ"},                              {"jets"}),
    "Eagles":     ("phi", {"PHI"},                              {"eagles"}),
    "Steelers":   ("pit", {"PIT"},                              {"steelers"}),
    "49ers":      ("sfo", {"SF", "SFO"},                        {"49ers", "niners"}),
    "Seahawks":   ("sea", {"SEA"},                              {"seahawks"}),
    "Buccaneers": ("tam", {"TB", "TAM"},                        {"buccaneers"}),
    "Titans":     ("oti", {"TEN", "OTI"},                       {"titans", "oilers"}),
    "Commanders": ("was", {"WAS", "WFT"},                       {"commanders", "redskins",
                                                                 "football team"}),
}

# Derived lookups -------------------------------------------------------------
PFR_CODE   = {nick: pfr for nick, (pfr, _a, _n) in _F.items()}
ABBR2NICK  = {}
for _nick, (_pfr, _aliases, _frags) in _F.items():
    for _a in _aliases | {_pfr.upper()}:
        # 'BLT_COLTS' is a sentinel: Baltimore Colts only ever arrive via the PFR
        # 'clt' href, never a bare code, so it must not shadow Ravens' BAL.
        if _a.endswith("_COLTS"):
            continue
        ABBR2NICK[_a] = _nick
PFR2NICK   = {pfr: nick for nick, pfr in PFR_CODE.items()}
NAME_FRAGS = {nick: frags for nick, (_p, _a, frags) in _F.items()}
ALL_NICKS  = set(_F)

_HREF_RE = re.compile(r"/teams/([a-z]{3})/")


def franchise_from_href(href: str):
    """The franchise nickname from a PFR team href ('/teams/crd/1978.htm' ->
    'Cardinals'). This is the UNAMBIGUOUS path -- use it whenever a PFR row
    carries a team link, so a shared-city text abbr (STL, LA) can't mislead."""
    m = _HREF_RE.search(href or "")
    return PFR2NICK.get(m.group(1)) if m else None


def canonical_nfl_team(token: str = None, href: str = None):
    """Resolve any team token to its canonical franchise nickname, or None.

    href wins (franchise-stable); then an exact abbrev/code; then a name fragment.
    None means 'unknown' -- a signal to audit, never something to swallow."""
    if href:
        nick = franchise_from_href(href)
        if nick:
            return nick
    t = (token or "").strip()
    if not t:
        return None
    if t in ALL_NICKS:                       # already a canonical nickname
        return t
    nick = ABBR2NICK.get(t.upper())
    if nick:
        return nick
    low = t.lower()
    for nk, frags in NAME_FRAGS.items():
        if any(f in low for f in frags):
            return nk
    return None


def nfl_teams_from_page(soup):
    """Ordered, de-duped canonical franchises a player suited up for, read from a
    Pro-Football-Reference player page's season tables. Uses each team cell's
    /teams/<code>/ href (franchise-stable) so shared-city abbrevs can't mislead --
    this is the standardized replacement for mapping the raw text abbr."""
    teams = []
    for row in soup.select("table tbody tr"):
        cell = (row.select_one("[data-stat='team_name_abbr']")
                or row.select_one("[data-stat='team']")
                or row.select_one("[data-stat='team_id']"))
        if not cell:
            continue
        text = cell.get_text(strip=True)
        if not text or text.upper() in ("TOT", "2TM", "3TM", "4TM", "TM", ""):
            continue
        link = cell.select_one("a[href]")
        nick = canonical_nfl_team(text, href=link.get("href", "") if link else None)
        if nick and nick not in teams:
            teams.append(nick)
    return teams


def audit_tokens(tokens):
    """Split an iterable of raw team tokens into (resolved {token:nick}, unknown set)."""
    resolved, unknown = {}, set()
    for tk in tokens:
        nick = canonical_nfl_team(tk)
        (resolved.__setitem__(tk, nick) if nick else unknown.add(tk))
    return resolved, unknown


# --- audit CLI: 100% local, no network --------------------------------------
def _audit_db(db_path):
    import json, os, sqlite3
    if not os.path.exists(db_path):
        return None
    conn = sqlite3.connect(db_path)
    bad = {}                                  # token -> count, for non-canonical teams[] values
    n = 0
    try:
        rows = conn.execute("SELECT teams FROM players WHERE sport='NFL' AND teams IS NOT NULL")
        for (teams_json,) in rows:
            try:
                teams = json.loads(teams_json or "[]")
            except Exception:
                teams = []
            for t in teams:
                n += 1
                if t not in ALL_NICKS:        # anything not a canonical nickname leaked through
                    bad[t] = bad.get(t, 0) + 1
    except sqlite3.Error:
        conn.close()
        return None                           # no players table / not our schema
    conn.close()
    return n, bad


def _audit_csv(csv_path):
    import csv as _csv, os
    if not os.path.exists(csv_path):
        return None
    seen = {}
    with open(csv_path, newline="", encoding="utf-8") as f:
        for r in _csv.DictReader(f):
            code = (r.get("team") or "").strip()
            if code:
                seen[code] = seen.get(code, 0) + 1
    _resolved, unknown = audit_tokens(seen)
    return seen, unknown


def main():
    import argparse, os
    ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    ap = argparse.ArgumentParser(description="NFL team-name registry + local audit (no network)")
    ap.add_argument("--audit", action="store_true")
    ap.add_argument("--db", help="check players.teams in this DB for non-canonical values")
    ap.add_argument("--csv", help="check raw team codes in an nflverse seasons CSV")
    args = ap.parse_args()

    if not args.audit:
        print(f"{len(ALL_NICKS)} franchises; {len(ABBR2NICK)} abbrev aliases; "
              f"{len(PFR2NICK)} PFR codes. Resolver ready.")
        return

    db = args.db or os.path.join(ROOT, "data", "nfl.db")
    res = _audit_db(db)
    if res is None:
        print(f"[db] {db} not found")
    else:
        n, bad = res
        print(f"[db] {db}: {n} team memberships; "
              + (f"{len(bad)} NON-CANONICAL value(s): {dict(sorted(bad.items(), key=lambda x:-x[1]))}"
                 if bad else "all canonical ✓"))
    if args.csv:
        cres = _audit_csv(args.csv)
        if cres is None:
            print(f"[csv] {args.csv} not found")
        else:
            seen, unknown = cres
            print(f"[csv] {args.csv}: {len(seen)} distinct codes; "
                  + (f"UNMAPPED (would be dropped): {sorted(unknown)}" if unknown else "all mapped ✓"))


if __name__ == "__main__":
    main()

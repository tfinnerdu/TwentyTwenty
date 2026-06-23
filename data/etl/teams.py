"""
data/etl/teams.py
=================
Single source of truth for team / school identity, and the cross-sport data audit.

The core is the NFL franchise registry (below). Two companions live here so every
ingest path shares one spelling:
  * canonical_school() -- the NCAA school normalizer ("Ohio St." -> "Ohio State",
    "Miami (FL)" -> "Miami"), used by every college path and for the `college`
    field of pro players too.
  * audit_sport()/audit_csv() + `--audit` -- a 100% local, no-network cleanliness
    report across all 8 entities. Pro code-maps (NBA/NHL/WNBA) live on their
    providers; MLB is Lahman names; NCAA is school variants -- the audit knows
    each shape and surfaces anything non-canonical before it costs a category.

Every NFL team token we see resolves to ONE canonical franchise nickname:
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


# --- NCAA school-name canonicalizer (shared by every college path) ----------
# College "teams" are school names, and different sources spell the same school
# differently ("Ohio St." vs "Ohio State", "Miami (FL)" vs "Miami"). One
# normalizer, used at every ingest point, so a school can't split into two
# categories. Conservative on purpose: it only applies transforms that can't
# merge two genuinely-different schools. The audit's _school_key (looser) is what
# SURFACES more candidates; this is the strict, safe MERGE.

# Irregulars the systematic rule can't reach. Keep to confident equivalences;
# leave true abbreviations (USC, UCLA, LSU, TCU, BYU, SMU, UNLV, UCF) untouched.
_SCHOOL_ALIASES = {
    "miami (fl)": "Miami",         # SR's Florida-Miami; Miami (OH) keeps its tag
    "miami fl": "Miami",
    "ole miss": "Mississippi",
    "pitt": "Pittsburgh",
    "uconn": "Connecticut",
    "umass": "Massachusetts",
    "southern cal": "USC",
}

_TRAILING_ST = re.compile(r"\bSt\.?$")     # final "St."/"St" only (a LEADING St.=Saint)


def canonical_school(name: str) -> str:
    """Canonical school name. 'Ohio St.' -> 'Ohio State'; 'Miami (FL)' -> 'Miami';
    'St. John's' stays (leading St.=Saint). Unknown names pass through unchanged --
    never guess a merge."""
    if not name:
        return name
    s = re.sub(r"\s+", " ", name.strip())
    alias = _SCHOOL_ALIASES.get(s.lower())
    if alias:
        return alias
    return _TRAILING_ST.sub("State", s)    # only a trailing St. becomes State


def canonicalize_schools(schools):
    """Map a list of school names through canonical_school, de-duped, order kept."""
    out = []
    for s in schools or []:
        c = canonical_school(s)
        if c and c not in out:
            out.append(c)
    return out


# --- generalized local audit (all 8 entities), 100% local, no network -------
# Three data shapes: NFL/NBA/NHL/WNBA are code->nickname maps; MLB is Lahman
# names (era-accurate, no code map); NCAA F/B/W are school-name variants.

def _pro_canonical(sport):
    """(canonical-nickname set, code->nick map) for a code-mapped pro sport, else None.
    Beyond NFL the maps live on the providers; imported lazily to avoid an import
    cycle with csv_season (which imports this module for NFL)."""
    sp = sport.upper()
    if sp == "NFL":
        return ALL_NICKS, ABBR2NICK
    try:
        from data.etl.providers.csv_season import NBAProvider, WNBAProvider, NHLProvider
    except Exception:
        return None
    m = {"NBA": NBAProvider.TEAM_MAP, "WNBA": WNBAProvider.TEAM_MAP,
         "NHL": NHLProvider.TEAM_MAP}.get(sp)
    return (set(m.values()), m) if m else None


def _looks_like_code(s):
    return bool(s) and s.isupper() and len(s) <= 4 and " " not in s


def _school_key(s):
    """Loose key to group school-name variants -> surfaces likely duplicates
    (e.g. 'Ohio St.' and 'Ohio State'; 'Miami (FL)' and 'Miami')."""
    k = (s or "").lower().replace("&", "and")
    k = re.sub(r"\(.*?\)", " ", k)               # drop "(FL)" etc.
    k = re.sub(r"[^a-z0-9 ]", " ", k)
    k = re.sub(r"\bst\b", "state", k)            # "ohio st" -> "ohio state"
    k = re.sub(r"\buniv(ersity)?\b", " ", k)
    return re.sub(r"\s+", " ", k).strip()


def audit_sport(db_path, sport):
    """Cleanliness report for one sport's team/school data, or None if no rows."""
    import json, os, sqlite3
    if not os.path.exists(db_path):
        return None
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT teams, college FROM players WHERE sport=?", (sport,)).fetchall()
    except sqlite3.Error:
        conn.close(); return None
    conn.close()
    sp = sport.upper()

    if sp in ("NCAAF", "NCAAB", "NCAAW"):        # school-name variants
        schools = {}
        for teams_json, college in rows:
            try:
                vals = json.loads(teams_json or "[]")
            except Exception:
                vals = []
            for s in (vals or ([college] if college else [])):
                if s:
                    schools[s] = schools.get(s, 0) + 1
        groups = {}
        for s in schools:
            groups.setdefault(_school_key(s), []).append(s)
        dupes = {k: sorted(set(v)) for k, v in groups.items() if len(set(v)) > 1}
        return {"type": "college", "rows": len(rows), "schools": len(schools), "variants": dupes}

    pc = _pro_canonical(sp)                       # code-mapped pro / MLB
    canon = pc[0] if pc else None
    bad, n = {}, 0
    for teams_json, _c in rows:
        try:
            vals = json.loads(teams_json or "[]")
        except Exception:
            vals = []
        for t in vals:
            n += 1
            if (t not in canon) if canon is not None else _looks_like_code(t):
                bad[t] = bad.get(t, 0) + 1
    return {"type": "pro", "rows": len(rows), "memberships": n,
            "bad": bad, "has_map": canon is not None}


def audit_csv(csv_path, sport):
    """Distinct source team codes in a seasons CSV + which are unmapped for the sport."""
    import csv as _csv, os
    if not os.path.exists(csv_path):
        return None
    seen = {}
    with open(csv_path, newline="", encoding="utf-8") as f:
        for r in _csv.DictReader(f):
            code = (r.get("team") or "").strip()
            if code:
                seen[code] = seen.get(code, 0) + 1
    pc = _pro_canonical(sport)
    cmap = {k.upper() for k in (pc[1] if pc else {})}
    return seen, sorted(c for c in seen if c.upper() not in cmap)


ALL_SPORTS = ["NFL", "NBA", "MLB", "NHL", "WNBA", "NCAAF", "NCAAB", "NCAAW"]


def main():
    import argparse, os
    ROOT = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    ap = argparse.ArgumentParser(description="Team/school registry + local audit, all sports (no network)")
    ap.add_argument("--audit", action="store_true")
    ap.add_argument("--sport", default="ALL", help="one of " + "/".join(ALL_SPORTS) + " or ALL")
    ap.add_argument("--db", help="DB to audit (default data/nfl.db)")
    ap.add_argument("--csv", help="optional seasons CSV (use with a single --sport)")
    args = ap.parse_args()

    if not args.audit:
        print(f"NFL registry: {len(ALL_NICKS)} franchises, {len(ABBR2NICK)} aliases, "
              f"{len(PFR2NICK)} PFR codes. Audit: --audit [--sport X] [--db ..] [--csv ..]")
        return

    db = args.db or os.path.join(ROOT, "data", "nfl.db")
    sports = ALL_SPORTS if args.sport.upper() == "ALL" else [args.sport.upper()]
    for sp in sports:
        r = audit_sport(db, sp)
        if r is None:
            print(f"[{sp:6}] no rows in {os.path.basename(db)}")
        elif r["type"] == "college":
            v = r["variants"]
            head = "; ".join(" = ".join(g) for g in list(v.values())[:10])
            print(f"[{sp:6}] {r['schools']} schools / {r['rows']} players; "
                  + (f"{len(v)} likely variant group(s): {head}{' …' if len(v) > 10 else ''}"
                     if v else "no obvious school variants ✓"))
        else:
            bad = dict(sorted(r["bad"].items(), key=lambda x: -x[1])[:15])
            label = "non-canonical" if r["has_map"] else "code-like (MLB: Lahman names, no code map)"
            print(f"[{sp:6}] {r['memberships']} memberships / {r['rows']} players; "
                  + (f"{len(r['bad'])} {label}: {bad}" if r["bad"] else "clean ✓"))

    if args.csv and len(sports) == 1:
        cres = audit_csv(args.csv, sports[0])
        if cres is None:
            print(f"[csv]  {args.csv} not found")
        else:
            seen, unknown = cres
            print(f"[csv]  {len(seen)} source codes; "
                  + (f"UNMAPPED (would drop): {unknown}" if unknown else "all mapped ✓"))


if __name__ == "__main__":
    main()


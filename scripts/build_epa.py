#!/usr/bin/env python3
"""Build data/epa-nfl.json and data/epa-cfb.json: per-season, per-week, per-team offensive EPA (and what the defense allowed).

NFL  — nflverse team-week stats (free, no key): https://github.com/nflverse/nflverse-data/releases/tag/stats_team
CFB  — CollegeFootballData game advanced stats (free key, set CFBD_API_KEY): https://collegefootballdata.com

Output shape (both files):
  { "updated": "...", "seasons": { "2025": { "3": { "KC": {"o": 0.21, "d": -0.05, "p": 61, "opp": "NYG"}, ... } } } }
  o = offensive EPA per play that week, d = EPA per play the defense allowed (the opponent's o), p = offensive plays.
Run from the repo root. Safe to re-run; it rewrites the files.
"""
import csv, io, json, os, sys, time, urllib.request, datetime as dt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data")
THIS_YEAR = dt.date.today().year if dt.date.today().month >= 8 else dt.date.today().year - 1
SEASONS = list(range(2023, THIS_YEAR + 1))

def fetch(url, headers=None, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=headers or {"User-Agent": "football-edge/1.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except Exception as e:
            if i == tries - 1: raise
            time.sleep(2 + 2*i)

# ---------------- NFL ----------------
NORM = {"JAC": "JAX", "LA": "LAR", "WSH": "WAS", "OAK": "LV", "SD": "LAC", "STL": "LAR"}
def nfl():
    seasons = {}
    for y in SEASONS:
        url = f"https://github.com/nflverse/nflverse-data/releases/download/stats_team/stats_team_week_{y}.csv"
        try: raw = fetch(url).decode("utf-8")
        except Exception as e: print(f"nfl {y}: {e}", file=sys.stderr); continue
        rows = [r for r in csv.DictReader(io.StringIO(raw)) if r.get("season_type") == "REG"]
        wk = {}
        for r in rows:
            t = NORM.get(r["team"], r["team"]); opp = NORM.get(r["opponent_team"], r["opponent_team"]); w = str(int(r["week"]))
            plays = float(r.get("attempts") or 0) + float(r.get("carries") or 0) + float(r.get("sacks_suffered") or 0)
            epa = float(r.get("passing_epa") or 0) + float(r.get("rushing_epa") or 0)
            if plays <= 0: continue
            wk.setdefault(w, {})[t] = {"o": round(epa / plays, 4), "p": int(plays), "opp": opp, "tot": round(epa, 2)}
        for w, teams in wk.items():                       # what each defense allowed = the opponent's offense that week
            for t, v in teams.items():
                o = teams.get(v["opp"]); v["d"] = o["o"] if o else None; v["dtot"] = o["tot"] if o else None
        seasons[str(y)] = wk
        print(f"nfl {y}: {sum(len(v) for v in wk.values())} team-weeks", file=sys.stderr)
    return {"updated": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z", "source": "nflverse stats_team_week", "seasons": seasons}

# ---------------- CFB ----------------
def cfb():
    key = os.environ.get("CFBD_API_KEY")
    if not key:
        print("cfb: CFBD_API_KEY not set — skipping (get a free key at collegefootballdata.com and add it as a repo secret)", file=sys.stderr); return None
    H = {"Authorization": f"Bearer {key}", "Accept": "application/json", "User-Agent": "football-edge/1.0"}
    # school name -> abbreviation (CFBD's abbreviations match ESPN's for nearly every FBS team)
    teams = json.loads(fetch("https://api.collegefootballdata.com/teams/fbs", H))
    abbr = {t["school"]: (t.get("abbreviation") or t["school"]) for t in teams}
    seasons = {}
    for y in SEASONS:
        wk = {}
        for w in range(1, 17):
            try: games = json.loads(fetch(f"https://api.collegefootballdata.com/stats/game/advanced?year={y}&week={w}&seasonType=regular", H))
            except Exception as e: print(f"cfb {y} wk{w}: {e}", file=sys.stderr); continue
            if not games: continue
            for g in games:
                t = abbr.get(g["team"], g["team"]); opp = abbr.get(g["opponent"], g["opponent"])
                off, d = g.get("offense") or {}, g.get("defense") or {}
                plays = off.get("plays") or 0
                if not plays: continue
                wk.setdefault(str(w), {})[t] = {"o": round(off.get("ppa") or 0, 4), "d": round(d.get("ppa") or 0, 4), "p": int(plays), "opp": opp,
                    "sr": round(off.get("successRate") or 0, 3), "dsr": round(d.get("successRate") or 0, 3), "tot": round((off.get("ppa") or 0) * plays, 2), "dtot": round((d.get("ppa") or 0) * (d.get("plays") or plays), 2)}
            time.sleep(0.3)
        seasons[str(y)] = wk
        print(f"cfb {y}: {sum(len(v) for v in wk.values())} team-games", file=sys.stderr)
    return {"updated": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z", "source": "collegefootballdata.com game advanced stats (ppa)", "seasons": seasons}

if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    which = sys.argv[1:] or ["nfl", "cfb"]
    if "nfl" in which:
        json.dump(nfl(), open(os.path.join(OUT, "epa-nfl.json"), "w"), separators=(",", ":"))
    if "cfb" in which:
        d = cfb()
        if d: json.dump(d, open(os.path.join(OUT, "epa-cfb.json"), "w"), separators=(",", ":"))

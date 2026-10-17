#!/usr/bin/env python3
"""Fit the NFL margin distribution behind coverP() in index.html (LEAGUES.nfl.SD and .KEY).

The app models the home margin as an integer pmf: normal(mu, SD) at each integer, times KEY[|margin|], renormalised. This
script (1) scores the current table against real games, (2) fits SD and KEY by maximum likelihood on 2015-2023 (the
two-point-conversion/long-PAT era), (3) checks on held-out 2024-25 that the fitted table predicts alternate-line cover rates
(what a half point is worth at 3, 7, ...) better than the current table or a plain normal, then (4) prints the final table
fitted on every season, ready to paste into LEAGUES.nfl. Re-run each offseason once nflverse has the new season.

    python3 scripts/fit_margins.py            # downloads games.csv (~2 MB) into scripts/.cache/ on first run

Totals: the same fit on closing totals does NOT beat the hand-set TKEY on held-out seasons (totals key numbers are too weak
to fit on ~2,400 games), so TKEY stays hand-set. v4.13, Oct 2026.
"""
import csv, math, os, sys, urllib.request
from collections import Counter, defaultdict

RANGE = 70
# paste the live LEAGUES.nfl values here before a re-fit so the "current" rows mean something
CUR_KEY = {0:.13, 1:.79, 2:.83, 3:2.65, 4:.87, 5:.83, 6:1.34, 7:1.73, 8:.89, 9:.36, 10:1.09, 11:.49, 12:.48, 13:.53, 14:1.44, 15:.51, 16:.74, 17:1.24, 18:.83, 19:.47, 20:.95, 21:1.15}
CUR_SD = 13
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".cache")
GAMES_URL = "https://github.com/nflverse/nflverse-data/releases/download/schedules/games.csv"

def games_csv():
    os.makedirs(CACHE, exist_ok=True); p = os.path.join(CACHE, "games.csv")
    if not os.path.exists(p): urllib.request.urlretrieve(GAMES_URL, p)
    return p
def load(path):
    g = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            if r["game_type"] != "REG" or r["home_score"] == "" or r["spread_line"] == "": continue
            g.append((int(r["season"]), int(r["home_score"]) - int(r["away_score"]), float(r["spread_line"])))
    return g

def pmf(mu, sd, key):
    ps = {}
    tot = 0.0
    for m in range(-RANGE, RANGE + 1):
        z = (m - mu) / sd
        p = math.exp(-z * z / 2) * key.get(abs(m), 1.0)
        ps[m] = p; tot += p
    return {m: p / tot for m, p in ps.items()}

def loglik(games, sd, key):
    cache = {}
    ll = 0.0
    for _, m, line in games:
        k = round(line * 2) / 2
        if k not in cache: cache[k] = pmf(k, sd, key)
        ll += math.log(max(cache[k].get(m, 1e-12), 1e-12))
    return ll / len(games)

def fit_key(games, sd, key, maxm=21, shrink=40):
    """One EM-style step: multiplier = observed count at |m| / expected count under the current pmf, shrunk toward the
    current value (shrink pseudo-games) so rare margins don't get wild multipliers. Multipliers above maxm stay at 1."""
    cache = {}
    exp = defaultdict(float); obs = Counter()
    for _, m, line in games:
        k = round(line * 2) / 2
        if k not in cache: cache[k] = pmf(k, sd, key)
        obs[abs(m)] += 1
        for mm, p in cache[k].items(): exp[abs(mm)] += p
    new = {}
    for a in range(0, maxm + 1):
        e = exp[a]; o = obs[a]
        cur = key.get(a, 1.0)
        ratio = (o + shrink) / (e + shrink) if e > 0 else 1.0     # shrink the observed/expected ratio toward 1
        new[a] = round(cur * ratio, 3)
    return new

def fit(games, sd0, key0, iters=25):
    sd, key = sd0, dict(key0)
    for _ in range(iters):
        new_key = fit_key(games, sd, key)
        if loglik(games, sd, new_key) < loglik(games, sd, key): break   # never accept a step that makes the fit worse
        key = new_key
        best = max((loglik(games, s, key), s) for s in [sd - 0.5, sd - 0.25, sd, sd + 0.25, sd + 0.5])
        sd = best[1]
    return sd, key

def cover_calibration(games, sd, key, label):
    """For offsets d in half points around the closing line, compare the model's P(home margin > -line_alt) with the empirical
    hit rate, where line_alt = line + d. Reports mean absolute calibration error and the Brier score across all offsets."""
    cache = {}
    errs = []; brier = 0.0; n = 0
    offsets = [-3, -2.5, -2, -1.5, -1, -0.5, 0, 0.5, 1, 1.5, 2, 2.5, 3]
    by_off = {d: [0.0, 0, 0] for d in offsets}  # sum predicted, hits, count
    for _, m, line in games:
        k = round(line * 2) / 2
        if k not in cache: cache[k] = pmf(k, sd, key)
        d = cache[k]
        for off in offsets:
            x = k + off                      # alternate line (home expected margin)
            # P(home margin > -alt_spread) with alt spread = -x  => P(m > x) ; pushes excluded
            if float(x).is_integer():
                push = d.get(int(x), 0.0)
                if m == x: continue
                p = sum(q for mm, q in d.items() if mm > x) / (1 - push)
            else:
                p = sum(q for mm, q in d.items() if mm > x)
            hit = 1.0 if m > x else 0.0
            brier += (p - hit) ** 2; n += 1
            by_off[off][0] += p; by_off[off][1] += hit; by_off[off][2] += 1
    print(f"\n{label}: Brier {brier / n:.5f} over {n} line/offset pairs")
    print("  offset  predicted  actual   diff")
    for off in offsets:
        s, h, c = by_off[off]
        if c: print(f"  {off:+5.1f}    {s / c:7.3f}  {h / c:7.3f}  {(s / c - h / c) * 100:+5.1f}pp")
    return brier / n

def key_number_table(games, sd, key, label):
    cache = {}
    exp = defaultdict(float); obs = Counter()
    for _, m, line in games:
        k = round(line * 2) / 2
        if k not in cache: cache[k] = pmf(k, sd, key)
        obs[abs(m)] += 1
        for mm, p in cache[k].items(): exp[abs(mm)] += p
    n = len(games)
    print(f"\n{label}: share of games landing on each |margin| (n={n})")
    print("  |m|   actual  model   plain-normal")
    plain = defaultdict(float); c2 = {}
    for _, m, line in games:
        k = round(line * 2) / 2
        if k not in c2: c2[k] = pmf(k, sd, {})
        for mm, p in c2[k].items(): plain[abs(mm)] += p
    for a in [0, 1, 2, 3, 4, 5, 6, 7, 8, 10, 11, 13, 14, 17, 21]:
        print(f"  {a:3d}   {obs[a] / n * 100:5.1f}%  {exp[a] / n * 100:5.1f}%  {plain[a] / n * 100:5.1f}%")

if __name__ == "__main__":
    games = load(sys.argv[1] if len(sys.argv) > 1 else games_csv())
    train = [g for g in games if 2015 <= g[0] <= 2023]
    test = [g for g in games if g[0] >= 2024]
    print(f"train 2015-23: {len(train)} games; test 2024-25: {len(test)} games")

    print("\n=== Train log-likelihood per game (higher is better) ===")
    print(f"plain normal SD 13.5 : {loglik(train, 13.5, {}):.5f}")
    print(f"current KEY, SD 13.5 : {loglik(train, CUR_SD, CUR_KEY):.5f}")
    sd, key = fit(train, CUR_SD, CUR_KEY)
    print(f"fitted  KEY, SD {sd:4.2f}: {loglik(train, sd, key):.5f}")
    print("\nfitted KEY:", {a: v for a, v in key.items() if abs(v - 1) > 0.03})

    print("\n=== Held-out 2024-25 ===")
    print(f"plain normal : {loglik(test, 13.5, {}):.5f}")
    print(f"current KEY  : {loglik(test, CUR_SD, CUR_KEY):.5f}")
    print(f"fitted  KEY  : {loglik(test, sd, key):.5f}")
    key_number_table(test, CUR_SD, CUR_KEY, "current table on 2024-25")
    key_number_table(test, sd, key, "fitted table on 2024-25")
    b0 = cover_calibration(test, 13.5, {}, "plain normal — alternate-line calibration, 2024-25")
    b1 = cover_calibration(test, CUR_SD, CUR_KEY, "current KEY — alternate-line calibration, 2024-25")
    b2 = cover_calibration(test, sd, key, "fitted KEY — alternate-line calibration, 2024-25")
    # Also: all games 2015-25 for the final table to ship
    sd_all, key_all = fit(train + test, CUR_SD, CUR_KEY)
    print(f"\nFINAL (2015-{max(g[0] for g in games)}) — paste into LEAGUES.nfl in index.html:")
    print(f"  SD:{sd_all},")
    print("  KEY:{" + ", ".join(f"{a}:{round(v, 2)}" for a, v in key_all.items()) + "},")

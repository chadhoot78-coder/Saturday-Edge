#!/usr/bin/env node
/* Offline NFL backtest: a Node replica of the app's side model (buildRatings + marketRatings) and totals projection, run
   walk-forward over 2021–25 on nflverse closing lines and box stats. It prints the bucket tables that go into LAB_DEFAULTS.nfl
   in index.html, and checks whether the model's disagreement with the line predicts anything (beta = the least-squares slope of
   (result − line) on (model − line); the best blend weight is ~beta, and beta <= 0 means the model adds nothing to the line).
   Run from the repo root: node scripts/backtest_nfl.js   (downloads ~3 MB into scripts/.cache/, which git ignores). */
const fs = require("fs"), path = require("path"), { execFileSync } = require("child_process");
const CACHE = path.join(__dirname, ".cache"); fs.mkdirSync(CACHE, {recursive:true});
const SEASONS = [2021, 2022, 2023, 2024, 2025];
const REL = "https://github.com/nflverse/nflverse-data/releases/download";
const get = (url, f) => { const p = path.join(CACHE, f); if(!fs.existsSync(p)) execFileSync("curl", ["-sSLf", "-o", p, url]); return p; };
get(`${REL}/schedules/games.csv`, "games.csv");
for(const y of SEASONS) get(`${REL}/stats_team/stats_team_week_${y}.csv`, `stw_${y}.csv`);
process.chdir(CACHE);
const csv = f => { const [h, ...rows] = fs.readFileSync(f, "utf8").trim().split("\n"); const k = h.split(",");
  return rows.map(r => { const v = r.split(","); const o = {}; k.forEach((c, i) => o[c] = v[i]); return o; }); };
const num = x => x === "" || x == null ? 0 : +x;
const MARGIN_CAP = 99, FCS_R = 0, MAX_WEEK = 18;

function load(seasons){
  const st = {};
  for(const y of seasons) for(const r of csv(`stw_${y}.csv`)){ if(r.season_type !== "REG") continue;
    const plays = num(r.attempts) + num(r.carries) + num(r.sacks_suffered);
    const yds = num(r.passing_yards) - num(r.sack_yards_lost) + num(r.rushing_yards);
    const to = num(r.passing_interceptions) + num(r.sack_fumbles_lost) + num(r.rushing_fumbles_lost) + num(r.receiving_fumbles_lost);
    const epa = num(r.passing_epa) + num(r.rushing_epa);
    st[`${y}|${+r.week}|${r.team}`] = {ypp: plays ? yds/plays : null, to, epa, plays}; }
  const games = [];
  for(const r of csv("games.csv")){ const y = +r.season; if(!seasons.includes(y) || r.game_type !== "REG" || r.home_score === "" || r.spread_line === "") continue;
    const w = +r.week, h = r.home_team, a = r.away_team, sh = st[`${y}|${w}|${h}`], sa = st[`${y}|${w}|${a}`];
    games.push({season:y, week:w, home:h, away:a, hs:+r.home_score, as:+r.away_score, neutral: r.location === "Neutral", spread: -(+r.spread_line), ou: r.total_line === "" ? null : +r.total_line, final:true,
      stats: sh && sa ? {ypp:{[h]:sh.ypp, [a]:sa.ypp}, to:{[h]:sh.to, [a]:sa.to}, epa:{[h]:sh.epa, [a]:sa.epa}} : {} }); }
  return games;
}

function marketRatings(pool, HFA){
  const obs = {};
  for(const g of pool){ const m = -g.spread - (g.neutral ? 0 : HFA); (obs[g.home] ||= []).push({v:m, opp:g.away}); (obs[g.away] ||= []).push({v:-m, opp:g.home}); }
  let r = {}; for(const t in obs) r[t] = 0;
  for(let pass = 0; pass < 25; pass++){ const next = {};
    for(const t in obs) next[t] = obs[t].reduce((a,p) => a + p.v + (r[p.opp] ?? 0), 0) / obs[t].length;
    const ts = Object.keys(next), mean = ts.reduce((a,t)=>a+next[t],0) / ts.length; for(const t of ts) next[t] -= mean; r = next; }
  return r;
}

// P = {HFA, K, ypp, to, epa (share), prior:"market"|"average"}
function buildRatings(hist, season, P, upto){
  const perf = {};
  const mkt = P.prior === "market" ? marketRatings(season.filter(g => g.week <= Math.min(upto, MAX_WEEK)), P.HFA) : null;
  for(const g of hist){
    const m = Math.max(-MARGIN_CAP, Math.min(MARGIN_CAP, g.hs - g.as)), st = g.stats || {};
    const yppD = (st.ypp?.[g.home] ?? 0) - (st.ypp?.[g.away] ?? 0), toD = (st.to?.[g.away] ?? 0) - (st.to?.[g.home] ?? 0);
    let v = (m - (g.neutral ? 0 : P.HFA)) + P.ypp*yppD + P.to*toD;
    if(P.epa && st.epa){ const ve = (st.epa[g.home] - st.epa[g.away]) - (g.neutral ? 0 : P.HFA); v = (1 - P.epa)*v + P.epa*ve; }
    (perf[g.home] ||= []).push({v, opp:g.away}); (perf[g.away] ||= []).push({v:-v, opp:g.home});
  }
  const target = t => mkt ? (mkt[t] ?? 0) : 0;
  let r = {}; if(mkt) for(const t in mkt) r[t] = mkt[t]; for(const t in perf) r[t] = target(t);
  for(let pass = 0; pass < 5; pass++){ const next = mkt ? {...mkt} : {};
    for(const t in perf){ const n = perf[t].length, raw = perf[t].reduce((a,p) => a + p.v + (r[p.opp] ?? target(p.opp)), 0) / n; next[t] = (raw*n + target(t)*P.K) / (n + P.K); }
    const ts = Object.keys(next), mean = ts.reduce((a,t)=>a+next[t],0) / ts.length; for(const t of ts) next[t] -= mean; r = next; }
  return r;
}

// walk-forward rows: model line vs market vs actual, weeks 2+
function evaluate(games, P){
  const rows = [];
  for(const y of [...new Set(games.map(g => g.season))]){ const season = games.filter(g => g.season === y);
    for(let wk = 2; wk <= MAX_WEEK; wk++){ const hist = season.filter(g => g.week < wk), cur = season.filter(g => g.week === wk); if(!hist.length || !cur.length) continue;
      const r = buildRatings(hist, season, P, wk);
      for(const g of cur){ if(!(g.home in r) || !(g.away in r)) continue;
        const model = r[g.home] - r[g.away] + (g.neutral ? 0 : P.HFA);
        rows.push({season:y, wk, model, market:-g.spread, actual:g.hs - g.as, cover:(g.hs - g.as) + g.spread}); } } }
  return rows;
}
const BUCKETS = [[0,3],[3,6],[6,10],[10,99]];
function summarize(rows, w = 0.35){
  const rec = list => { let W = 0, L = 0, P = 0; for(const x of list){ const ph = x.model > x.market; if(x.model === x.market) continue; if(x.cover === 0){ P++; continue; } ((x.cover > 0) === ph) ? W++ : L++; } return {n:W+L+P, W, L, P, pct: W+L ? W/(W+L) : null}; };
  const rmse = f => Math.sqrt(rows.reduce((a,x) => a + (f(x) - x.actual)**2, 0) / rows.length);
  return { n: rows.length, rmseModel: rmse(x => x.model), rmseMkt: rmse(x => x.market), rmseBlend: rmse(x => w*x.model + (1-w)*x.market),
    all: rec(rows), buckets: BUCKETS.map(([lo,hi]) => ({lo, hi, ...rec(rows.filter(x => Math.abs(x.model - x.market) >= lo && Math.abs(x.model - x.market) < hi))})) };
}

const games = load(SEASONS);
const beta = rows => { let sxy = 0, sxx = 0; for(const x of rows){ const d = x.model - x.market, e = x.actual - x.market; sxy += d*e; sxx += d*d; } return sxy/sxx; };
const APP = {HFA:1.5, K:2.33, ypp:3, to:2, epa:0, prior:"market"};
{ const rows = evaluate(games, APP), s = summarize(rows);
  console.log(`sides, app settings: ${s.n} games · model RMSE ${s.rmseModel.toFixed(2)} vs market ${s.rmseMkt.toFixed(2)} · ATS ${(s.all.pct*100).toFixed(1)}% · beta ${beta(rows).toFixed(3)}`); }
if(process.argv.includes("--sweep")){   // fit on 2021–23, check on 2024–25
  const train = games.filter(g => g.season <= 2023), test = games.filter(g => g.season >= 2024), out = [];
  for(const ypp of [0, 1.5, 3, 5]) for(const to of [0, 1, 2]) for(const epa of [0, .3, .6, 1]) for(const K of [2.33, 4, 8]){
    const P = {HFA:1.5, K, ypp, to, epa, prior:"market"}; out.push({P, b: beta(evaluate(train, P))}); }
  out.sort((a,b) => b.b - a.b);
  for(const o of out.slice(0, 8)){ const te = evaluate(test, o.P); console.log(JSON.stringify(o.P), "train beta", o.b.toFixed(3), "| test beta", beta(te).toFixed(3), "ATS", (summarize(te).all.pct*100).toFixed(1) + "%"); }
}
{ const DIVS = [["BUF","MIA","NE","NYJ"],["BAL","CIN","CLE","PIT"],["HOU","IND","JAX","TEN"],["DEN","KC","LV","LAC"],["DAL","NYG","PHI","WAS"],["CHI","DET","GB","MIN"],["ATL","CAR","NO","TB"],["ARI","LA","SF","SEA"]];
const div = {}; DIVS.forEach((d,i) => d.forEach(t => div[t] = i));
const LG = 22.5, K = 2.33;
const rawAvg = (t, gs0) => { const gs = gs0.filter(g => g.home===t || g.away===t), n = gs.length; return n ? {pf: gs.reduce((a,g)=>a+(g.home===t?g.hs:g.as),0)/n, pa: gs.reduce((a,g)=>a+(g.home===t?g.as:g.hs),0)/n} : {pf:LG, pa:LG}; };
function scoring(t, games){ const gs = games.filter(g => g.home===t || g.away===t), n = gs.length; if(!n) return {pf:LG, pa:LG};
  const pf = gs.reduce((a,g) => { const o = rawAvg(g.home===t?g.away:g.home, games); return a + (g.home===t?g.hs:g.as) - (o.pa - LG); }, 0)/n;
  const pa = gs.reduce((a,g) => { const o = rawAvg(g.home===t?g.away:g.home, games); return a + (g.home===t?g.as:g.hs) - (o.pf - LG); }, 0)/n;
  const k = n/(n+K); return {pf: LG + k*(pf-LG), pa: LG + k*(pa-LG)}; }
const proj = (g, H) => { const h = scoring(g.home, H), a = scoring(g.away, H); return (h.pf + a.pa)/2 + (a.pf + h.pa)/2; };
const rec = () => ({n:0,W:0,L:0,P:0});
const add = (r, win, push) => { r.n++; push ? r.P++ : win ? r.W++ : r.L++; };
const TB = [[0,2],[2,4],[4,7],[7,99]], totB = TB.map(rec), early = [[0,7],[7,99]].map(rec), late = [[0,7],[7,99]].map(rec);
const ang = {bigFav:rec(), divDog:rec(), homeDog:rec(), earlyOver:rec(), lateOver:rec(), bigUnder:rec()};
let sxy = 0, sxx = 0;
for(const y of SEASONS){ const S = games.filter(g => g.season === y);
  for(let wk = 2; wk <= 18; wk++){ const H = S.filter(g => g.week < wk);
    for(const g of S.filter(g => g.week === wk)){
      const cover = (g.hs - g.as) + g.spread, tot = g.hs + g.as;
      if(Math.abs(g.spread) >= 10) add(ang.bigFav, (cover > 0) === (g.spread < 0), cover === 0);
      if(div[g.home] === div[g.away] && g.spread !== 0) add(ang.divDog, (cover > 0) === (g.spread > 0), cover === 0);
      if(g.spread > 0) add(ang.homeDog, cover > 0, cover === 0);
      if(g.ou == null) continue; const m = proj(g, H), d = m - g.ou, e = tot - g.ou; sxy += d*e; sxx += d*d;
      const win = (e > 0) === (d >= 0), push = e === 0;
      const b = totB.find((_, i) => Math.abs(d) >= TB[i][0] && Math.abs(d) < TB[i][1]); add(b, win, push);
      (wk <= 4 ? early : late)[Math.abs(d) >= 7 ? 1 : 0] && add((wk <= 4 ? early : late)[Math.abs(d) >= 7 ? 1 : 0], win, push);
      if(d >= 7) add(wk <= 4 ? ang.earlyOver : ang.lateOver, e > 0, push);
      if(d <= -7) add(ang.bigUnder, e < 0, push);
    } } }
const f = r => ({...r, pct: +(r.W/(r.W+r.L)).toFixed(3)});
console.log("totals beta", (sxy/sxx).toFixed(3));
console.log("totBuckets", JSON.stringify(totB.map((r,i) => ({lo:TB[i][0], hi:TB[i][1], ...f(r)}))));
console.log("totEarly", JSON.stringify(early.map((r,i) => ({lo:[0,7][i], hi:[7,99][i], ...f(r)}))));
console.log("totLate", JSON.stringify(late.map((r,i) => ({lo:[0,7][i], hi:[7,99][i], ...f(r)}))));
console.log("angles", JSON.stringify(Object.entries(ang).map(([id, r]) => ({id, ...f(r)}))));
const rows = evaluate(games, APP);
const SB = [[0,3],[3,6],[6,10],[10,99]];
console.log("rawBuckets", JSON.stringify(SB.map(([lo,hi]) => { const r = rec(); for(const x of rows){ const d = x.model - x.market; if(Math.abs(d) < lo || Math.abs(d) >= hi || d === 0) continue; add(r, (x.cover > 0) === (d > 0), x.cover === 0); } return {lo, hi, ...f(r)}; })));
}

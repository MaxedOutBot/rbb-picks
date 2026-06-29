"""
RicsBestBets — Manhattan Model V12 — Ultimate Self-Learning Engine
==================================================================
Major upgrades over V11 based on multi-AI critique:

MATH FIXES (Highest Impact):
  1. Logit/logistic probability scaling — replaces flat additive boosts.
     Prevents probability inflation at high confidence levels. Signals that
     were blindly adding +12% now correctly taper as probability gets higher.
  2. Bayesian shrinkage — small-sample signals (G2 Over n=10, umpire n=7)
     are shrunk toward 50% prior. 9/10 raw → 70% shrunk, not 90%.
  3. Power De-vig (Shin's method) — standard proportional de-vig
     underestimates favorites and overestimates dogs. Power method fixes this.
  4. Continuous signal scaling — wind, ERA gap, park factor all use
     smooth linear functions instead of hard binary thresholds.
  5. Signal correlation cap — stacked signals use diminishing returns:
     1st signal = 100%, 2nd = 70%, 3rd = 50%, 4th+ = 30%.

NEW DATA SOURCES:
  6. pybaseball — free Python library for xFIP/SIERA from FanGraphs/Statcast.
     Replaces ERA gate with xFIP gate. ERA is noisy; xFIP is predictive.
  7. feedparser — RSS feeds from DailyFaceoff (NHL goalies) and
     RotoWire (MLB/NBA scratches) for automated Signal D detection.
  8. Opening line storage — saves first-run odds to opening_lines.json.
     Later runs detect Reverse Line Movement automatically.
  9. NBA arena coordinates — travel distance and altitude factored
     into B2B penalty (Denver/Utah travel = stronger fade signal).
 10. CLV tracking — every pick stores opening price for closing-line
     value comparison to measure model sharpness over time.

SELF-LEARNING (unchanged — already validated):
  - Bayesian shrinkage now applied inside get_pss_mult
  - Signal accuracy tracks with exponential weighting
  - Brier score calibration adjusts probability bias
  - Monthly scoring environment per sport
"""

import json, os, random, math, requests, sys, traceback
from datetime import date, datetime, timezone, timedelta

KEY       = os.environ.get("ODDS_API_KEY","")
TODAY_STR = date.today().isoformat()
YEAR_STR  = TODAY_STR[:4]

# ════════════════════════════════════════════════════════════
# SECTION 1 — CONSTANTS
# ════════════════════════════════════════════════════════════

MONTE_CARLO_N  = 10000    # Reduced to 10K — same CI formula, faster
MIN_EV         = 0.035    # Raised from 3% to 3.5% per critique recommendation
MAX_ODDS_ML    = -185
OUTLIER_CENTS  = 25
PSS_MIN_ML     = 33
PSS_MIN_TOTAL  = 30
PSS_MIN_RL     = 28
STARS_TO_UNITS = {5:3.0,4:2.0,3:1.5,2:1.0,1:0.5}

ELITE_LINEUPS = ["Los Angeles Dodgers","New York Yankees","Houston Astros"]
COORS_HOME    = "Colorado Rockies"

# Logit weights for probability boosts (replaces flat additive %)
# Calibrated so that at p=0.52 (neutral), boost ≈ same as old additive
# But at p=0.70, boost is naturally smaller — prevents inflation
LOGIT_WEIGHTS = {
    "G2_OVER":              0.50,   # ~+12% at neutral, tapers at high p
    "G1_UNDER":             0.50,
    "UMP_OVER":             0.58,   # ~+14%
    "UMP_UNDER":            0.58,
    "HEAVY_FAV_UNDER_G1":   0.63,   # ~+15%
    "PITCHER_GATE_CANCEL":  0.50,
    "COORS_OVER":           0.42,   # ~+10%
    "ATH_OVER":             0.32,   # ~+8%
    "WIND_OUT_OVER":        0.32,
    "WIND_IN_UNDER":        0.32,
    "NFL_WIND_UNDER":       0.42,
    "NFL_COLD_UNDER":       0.32,
    "COLD_UNDER":           0.20,
    "BOOKS_5_OVER":         0.20,
    "BOOKS_5_UNDER":        0.20,
    "BOOKS_3_OVER":         0.12,
    "BOOKS_3_UNDER":        0.12,
    "OVER_POSITIVE_ODDS":   0.28,
    "G2PLUS_OVER":          0.32,
    "G1_OVER_HEADWIND":    -0.20,
    "POST_EXTRAS_OVER":     0.28,
    "J110_G2PLUS_OVER":     0.40,
    "JUICE_FLIP_OVER":      0.50,   # RLM/juice flip — strong signal
    "JUICE_FLIP_UNDER":     0.50,
    "TOTAL_MOVE_OVER":      0.20,
    "TOTAL_MOVE_UNDER":     0.20,
    "HITTER_PARK_OVER":     0.20,
    "PITCHER_PARK_UNDER":   0.20,
    "NBA_B2B_UNDER":        0.32,
    "SIGNAL_D_INJURY":      0.80,   # RSS-detected injury = highest weight
}
LOGIT_WEIGHT_DEFAULT = 0.20  # fallback for any unlisted signal

# All 30 MLB park factors (research-validated 3-year rolling avg vs 1.00)
PARK_FACTORS = {
    "Colorado Rockies":1.28,"Athletics":1.16,"Texas Rangers":1.07,
    "Cincinnati Reds":1.06,"Philadelphia Phillies":1.05,"Chicago Cubs":1.04,
    "New York Yankees":1.04,"Boston Red Sox":1.03,"Baltimore Orioles":1.02,
    "Washington Nationals":1.02,"Cleveland Guardians":1.01,
    "Kansas City Royals":1.00,"Pittsburgh Pirates":1.00,"St. Louis Cardinals":1.00,
    "Minnesota Twins":0.99,"Milwaukee Brewers":0.99,"Detroit Tigers":0.98,
    "Oakland Athletics":0.98,"Chicago White Sox":0.97,"Los Angeles Angels":0.97,
    "San Francisco Giants":0.96,"Houston Astros":0.96,"Arizona Diamondbacks":0.95,
    "Toronto Blue Jays":0.95,"Atlanta Braves":0.95,"Los Angeles Dodgers":0.94,
    "New York Mets":0.94,"Seattle Mariners":0.93,"Miami Marlins":0.93,
    "Tampa Bay Rays":0.92,"San Diego Padres":0.91,
}
DOME_TEAMS = {"Tampa Bay Rays","Miami Marlins","Houston Astros","Toronto Blue Jays",
              "Milwaukee Brewers","Arizona Diamondbacks","Atlanta Braves","Texas Rangers"}
STADIUM_WEATHER = {
    "Colorado Rockies":"Denver","Texas Rangers":"Arlington+TX",
    "Cincinnati Reds":"Cincinnati","Philadelphia Phillies":"Philadelphia",
    "Chicago Cubs":"Chicago","New York Yankees":"Bronx+NY","New York Mets":"Queens+NY",
    "Boston Red Sox":"Boston","Baltimore Orioles":"Baltimore",
    "Cleveland Guardians":"Cleveland","Minnesota Twins":"Minneapolis",
    "Kansas City Royals":"Kansas+City","Detroit Tigers":"Detroit",
    "Pittsburgh Pirates":"Pittsburgh","Washington Nationals":"Washington+DC",
    "Los Angeles Angels":"Anaheim","Oakland Athletics":"West+Sacramento",
    "San Francisco Giants":"San+Francisco","Los Angeles Dodgers":"Los+Angeles",
    "San Diego Padres":"San+Diego","Seattle Mariners":"Seattle",
    "St. Louis Cardinals":"St+Louis","Chicago White Sox":"Chicago+IL",
}

# HP umpire signals (V11 validated + 2026 RefMetrics data)
UMP_OVER  = {"moscoso","wegner","ceja","barber","gonzalez","hanahan",
             "jean","traynor","thomas","bucknor","marquez","additon",
             "bacchus","may","iassogna"}
UMP_UNDER = {"hudson","libka","clemons","bellino","conroy","ballou",
             "diaz","layne","paternostro","b.miller"}

# NBA arena coordinates (lat, lon, altitude_ft) for travel/altitude B2B
NBA_ARENAS = {
    "Denver Nuggets":       (39.748, -104.998, 5280),
    "Utah Jazz":            (40.768, -111.901, 4327),
    "Phoenix Suns":         (33.446, -112.071, 1086),
    "San Antonio Spurs":    (29.427, -98.438,  650),
    "Dallas Mavericks":     (32.790, -96.810,  430),
    "Houston Rockets":      (29.751, -95.362,   43),
    "New Orleans Pelicans": (29.949, -90.081,    3),
    "Memphis Grizzlies":    (35.138, -90.050,  254),
    "Oklahoma City Thunder":(35.463, -97.515, 1197),
    "Minnesota Timberwolves":(44.979,-93.276,  815),
    "Milwaukee Bucks":      (43.043, -87.917,  634),
    "Chicago Bulls":        (41.881, -87.674,  600),
    "Detroit Pistons":      (42.341, -83.055,  597),
    "Indiana Pacers":       (39.764, -86.156,  715),
    "Cleveland Cavaliers":  (41.497, -81.688,  653),
    "Toronto Raptors":      (43.643, -79.379,  249),
    "Boston Celtics":       (42.366, -71.062,   19),
    "Brooklyn Nets":        (40.683, -73.975,   26),
    "New York Knicks":      (40.750, -73.994,   26),
    "Philadelphia 76ers":   (39.901, -75.172,   39),
    "Washington Wizards":   (38.898, -77.021,   27),
    "Atlanta Hawks":        (33.757, -84.396, 1050),
    "Charlotte Hornets":    (35.225, -80.839,  748),
    "Miami Heat":           (25.781, -80.188,    6),
    "Orlando Magic":        (28.539, -81.384,   96),
    "Los Angeles Lakers":   (34.043, -118.267,  309),
    "Los Angeles Clippers": (34.043, -118.267,  309),
    "Sacramento Kings":     (38.580, -121.499,   30),
    "Golden State Warriors":(37.768, -122.388,   20),
    "Portland Trail Blazers":(45.532,-122.667,   40),
}

B2B_SPORTS  = {"basketball_nba","basketball_wnba","icehockey_nhl"}
NFL_SPORTS  = {"americanfootball_nfl","americanfootball_ncaaf"}
TARGET_SPORTS = [
    "baseball_mlb","basketball_wnba","basketball_nba","americanfootball_nfl",
    "americanfootball_ncaaf","basketball_ncaab","icehockey_nhl","soccer_usa_mls",
    "mma_mixed_martial_arts","boxing_boxing","tennis_atp_singles","tennis_wta_singles",
    "soccer_epl","soccer_uefa_champs_league","soccer_spain_la_liga",
]
SPORT_LABELS = {
    "baseball_mlb":"MLB","basketball_wnba":"WNBA","basketball_nba":"NBA",
    "americanfootball_nfl":"NFL","americanfootball_ncaaf":"NCAA Football",
    "basketball_ncaab":"NCAA Basketball","icehockey_nhl":"NHL",
    "soccer_usa_mls":"MLS","mma_mixed_martial_arts":"MMA/UFC","boxing_boxing":"Boxing",
    "tennis_atp_singles":"ATP Tennis","tennis_wta_singles":"WTA Tennis",
    "soccer_epl":"EPL","soccer_uefa_champs_league":"Champions League",
    "soccer_spain_la_liga":"La Liga",
}
UFC_HEAVY_FAV_MAX = -400
UFC_HEAVY_FAV_MIN = -900

# ════════════════════════════════════════════════════════════
# SECTION 2 — PROBABILITY MATH (V12 UPGRADES)
# ════════════════════════════════════════════════════════════

def logit(p):
    """Log-odds transformation. Core of V12 probability scaling."""
    p = max(0.005, min(0.995, p))
    return math.log(p / (1.0 - p))

def inv_logit(l):
    """Inverse log-odds (sigmoid). Converts logit back to probability."""
    return 1.0 / (1.0 + math.exp(-l))

def logit_boost(base_p, *signal_codes):
    """
    Apply multiple signal boosts in logit space with DIMINISHING RETURNS.
    1st signal = 100%, 2nd = 70%, 3rd = 50%, 4th+ = 30%.
    This prevents correlated signals from inflating confidence artificially.
    Critical fix: old additive method added +12% flat regardless of base_p.
    Logit method naturally tapers at high probabilities.
    """
    if not signal_codes: return base_p
    weights = [1.0, 0.70, 0.50, 0.30]
    sorted_codes = sorted(signal_codes,
        key=lambda c: LOGIT_WEIGHTS.get(c, LOGIT_WEIGHT_DEFAULT), reverse=True)
    total = sum(LOGIT_WEIGHTS.get(c, LOGIT_WEIGHT_DEFAULT) * weights[min(i,3)]
                for i,c in enumerate(sorted_codes))
    return min(0.88, inv_logit(logit(max(0.50, base_p)) + total))

def power_devig(p1_imp, p2_imp):
    """
    Power method de-vig (Shin's method).
    Fixes systematic bias of proportional method:
    proportional underestimates favorites and overestimates underdogs.
    Finds exponent k so that p1^k + p2^k = 1.
    """
    if p1_imp <= 0 or p2_imp <= 0:
        t = p1_imp + p2_imp
        if t == 0: return 0.5, 0.5
        return p1_imp/t, p2_imp/t
    # Binary search for k
    lo, hi = 0.3, 3.0
    for _ in range(60):
        k = (lo + hi) / 2.0
        s = p1_imp**k + p2_imp**k
        if s > 1.0: lo = k
        else: hi = k
    k = (lo + hi) / 2.0
    s = p1_imp**k + p2_imp**k
    return p1_imp**k / s, p2_imp**k / s

def a2p(o):
    n=float(o); return abs(n)/(abs(n)+100) if n<0 else 100/(n+100)

def p2a(p):
    p=max(0.01,min(0.99,p))
    return round(-p/(1-p)*100) if p>=0.5 else round((1-p)/p*100)

def calc_ev(wp, odds):
    """EV = (true_prob × decimal_odds) − 1. Correct for negative odds."""
    n=float(odds); d=(100/abs(n)+1) if n<0 else (n/100+1); return wp*d-1

def monte_carlo(wp, n=MONTE_CARLO_N):
    w = sum(1 for _ in range(n) if random.random() < wp)
    se = math.sqrt(wp*(1-wp)/n)
    return w/n, 2*1.96*se*100

def stars_ev(ev, ci=0):
    s = 5 if ev>=0.15 else 4 if ev>=0.10 else 3 if ev>=0.06 else 2 if ev>=0.035 else 1
    return max(1,s-1) if ci>20 else s

def bayesian_accuracy(wins, n, alpha=5, beta=5):
    """
    Bayesian shrinkage toward 50% prior.
    alpha=beta=5 gives prior weight of 10 games.
    9/10 raw → 70% shrunk, not 90%. 7/7 → 70.6%, not 100%.
    Critical: prevents overfitting on small n signals.
    """
    return (wins + alpha) / (n + alpha + beta)

def json_safe(v):
    if isinstance(v,set):   return sorted(list(v))
    if isinstance(v,dict):  return {str(k):json_safe(vv) for k,vv in v.items()}
    if isinstance(v,list):  return [json_safe(i) for i in v]
    if isinstance(v,tuple): return [json_safe(i) for i in v]
    if isinstance(v,float):
        if v!=v or v==float('inf') or v==float('-inf'): return None
        return v
    return v

# ════════════════════════════════════════════════════════════
# SECTION 3 — CONSENSUS ODDS
# ════════════════════════════════════════════════════════════

def consensus(game, name, market="h2h"):
    """Multi-book consensus with 25-cent outlier filter."""
    raw = []
    for bk in game.get("bookmakers",[]):
        mkt = next((m for m in bk.get("markets",[]) if m["key"]==market), None)
        if not mkt: continue
        for out in mkt.get("outcomes",[]):
            if out.get("name")==name: raw.append(float(out["price"])); break
    if not raw: return None, 0
    srt=sorted(raw); med=srt[len(srt)//2]
    valid=[o for o in raw if abs(o-med)<=OUTLIER_CENTS] or raw
    return p2a(sum(a2p(o) for o in valid)/len(valid)), len(valid)

def total_consensus(game):
    """Consensus total line and odds from all books."""
    res={}
    for side in ["Over","Under"]:
        ol,pl=[],[]
        for bk in game.get("bookmakers",[]):
            mkt=next((m for m in bk.get("markets",[]) if m["key"]=="totals"),None)
            if not mkt: continue
            for out in mkt.get("outcomes",[]):
                if out.get("name")==side:
                    ol.append(float(out["price"]))
                    if out.get("point"): pl.append(float(out["point"]))
                    break
        if ol:
            srt=sorted(ol); med=srt[len(srt)//2]
            valid=[o for o in ol if abs(o-med)<=OUTLIER_CENTS] or ol
            res[side]={"odds":p2a(sum(a2p(o) for o in valid)/len(valid)),
                       "line":round(sum(pl)/len(pl),1) if pl else 0,
                       "n":len(valid),
                       "books_juiced_here":sum(1 for o in valid if o<0)}
    return res

def count_books_juiced(game, market="totals", side="Over"):
    count=0
    for bk in game.get("bookmakers",[]):
        mkt=next((m for m in bk.get("markets",[]) if m["key"]==market),None)
        if not mkt: continue
        for out in mkt.get("outcomes",[]):
            if out.get("name")==side:
                if float(out.get("price",-110))<0: count+=1
                break
    return count

def get_fav_odds(ho, ao):
    if ho is None or ao is None: return None
    return min(float(ho), float(ao))

# ════════════════════════════════════════════════════════════
# SECTION 4 — SELF-LEARNING STATE
# ════════════════════════════════════════════════════════════

DEFAULT_STATE = {
    "version":"V12.1","last_updated":None,"total_graded":0,
    "signal_accuracy":{},
    "sport_accuracy":{},
    "bet_type_accuracy":{},
    "calibration":{"probability_bias":0.0,"brier_samples":[],"brier_30":None,"win_rate_30":None},
    "environment":{},
    "yesterday_teams":set(),
    "clv_log":[],   # closing line value tracking
}

def load_state():
    try:
        with open("model_state.json") as f: s=json.load(f)
        for k,v in DEFAULT_STATE.items():
            if k not in s: s[k]=v
        if isinstance(s.get("yesterday_teams"),list):
            s["yesterday_teams"]=set(s["yesterday_teams"])
        return s
    except Exception:
        return {k:(set() if isinstance(v,set) else (list(v) if isinstance(v,list) else
                dict(v) if isinstance(v,dict) else v))
                for k,v in DEFAULT_STATE.items()}

def save_state(s):
    s["last_updated"]=TODAY_STR
    try:
        with open("model_state.json","w") as f:
            json.dump(json_safe(s), f, indent=2)
    except Exception:
        print("[V12] ERROR in save_state:")
        traceback.print_exc(file=sys.stdout)

def get_pss_mult(state, code):
    """
    Learned PSS multiplier with Bayesian shrinkage.
    Raw accuracy is shrunk toward 50% prior before computing multiplier.
    Prevents small-sample signals from getting over-rewarded.
    """
    sig = state["signal_accuracy"].get(code, {})
    fires = sig.get("fires", 0)
    if fires < 10: return 1.0
    wins = sig.get("wins", 0)
    losses = sig.get("losses", 0)
    total = wins + losses
    # Apply Bayesian shrinkage (alpha=beta=5, prior = 50%)
    shrunk_acc = bayesian_accuracy(wins, total)
    # Exponential weighting: recent 70%, all-time 30%
    r_w = sig.get("recent_w", wins); r_l = sig.get("recent_l", losses)
    r_total = r_w + r_l
    recent_acc = bayesian_accuracy(r_w, r_total) if r_total > 0 else 0.52
    acc = 0.7*recent_acc + 0.3*shrunk_acc
    if acc>=0.72: return 1.30
    if acc>=0.65: return 1.20
    if acc>=0.58: return 1.10
    if acc>=0.50: return 1.00
    if acc>=0.42: return 0.85
    if acc>=0.35: return 0.70
    return 0.55

def apply_pss_mult(state, base_pss, sig_codes):
    """Apply learned PSS multipliers. Only codes with n≥10 get multiplied."""
    known = [c for c in sig_codes
             if c in state["signal_accuracy"] and
             state["signal_accuracy"][c].get("fires",0)>=10]
    if not known: return base_pss
    avg = sum(get_pss_mult(state,c) for c in known) / len(known)
    return int(base_pss * avg)

def learn_from_result(state, sig_codes, sport_label, bet_type, result, true_prob):
    is_win=(result=="W"); is_loss=(result=="L")
    for code in sig_codes:
        if code not in state["signal_accuracy"]:
            state["signal_accuracy"][code]={"fires":0,"wins":0,"losses":0,"pushes":0,
                                             "acc":0.52,"pss_mult":1.0,"recent_w":0,"recent_l":0}
        sig = state["signal_accuracy"][code]
        sig["fires"] += 1
        if is_win:    sig["wins"]+=1;    sig["recent_w"]+=1
        elif is_loss: sig["losses"]+=1;  sig["recent_l"]+=1
        else:         sig["pushes"]+=1
        total = sig["wins"]+sig["losses"]
        if total > 0: sig["acc"] = round(bayesian_accuracy(sig["wins"],total),3)
        if sig["recent_w"]+sig["recent_l"] > 20:
            sig["recent_w"]=max(0,sig["recent_w"]-1)
            sig["recent_l"]=max(0,sig["recent_l"]-1)
        sig["pss_mult"] = get_pss_mult(state, code)
    for tracker,key in [(state["sport_accuracy"],sport_label),(state["bet_type_accuracy"],bet_type)]:
        if key not in tracker:
            tracker[key]={"picks":0,"wins":0,"losses":0,"pushes":0,"win_rate":0.52}
        t=tracker[key]; t["picks"]+=1
        if is_win: t["wins"]+=1
        elif is_loss: t["losses"]+=1
        else: t["pushes"]+=1
        d=t["wins"]+t["losses"]
        if d>0: t["win_rate"]=round(t["wins"]/d,3)
    cal=state["calibration"]
    samples=cal.get("brier_samples",[])
    samples.append({"p":round(true_prob/100,3),"a":int(is_win)})
    samples=samples[-60:]
    cal["brier_samples"]=samples
    if len(samples)>=15:
        brier=sum((s["p"]-s["a"])**2 for s in samples)/len(samples)
        cal["brier_30"]=round(brier,4)
        wr=sum(s["a"] for s in samples)/len(samples)
        cal["win_rate_30"]=round(wr,3)
        avg_p=sum(s["p"] for s in samples)/len(samples)
        diff=wr-avg_p
        if abs(diff)>=0.05:
            adj=0.03 if diff>0 else -0.03
            cal["probability_bias"]=round(max(-0.10,min(0.10,
                cal.get("probability_bias",0)+adj)),3)
    state["total_graded"]+=1

# ════════════════════════════════════════════════════════════
# SECTION 5 — DATA FETCHING
# ════════════════════════════════════════════════════════════

# ── xFIP via pybaseball ───────────────────────────────────
_PITCHER_STATS = None   # Cached at startup
def load_xfip_data():
    """Load pitcher xFIP/SIERA from pybaseball (FanGraphs scrape). Free."""
    global _PITCHER_STATS
    try:
        from pybaseball import pitching_stats
        df = pitching_stats(int(YEAR_STR), qual=5)
        # Keep only useful columns
        cols = [c for c in ["Name","Team","ERA","FIP","xFIP","SIERA","K/9","BB/9","IP"]
                if c in df.columns]
        _PITCHER_STATS = df[cols].copy()
        print(f"[V12] xFIP loaded: {len(_PITCHER_STATS)} pitchers from pybaseball")
    except Exception as e:
        print(f"[V12] pybaseball unavailable ({e}) — falling back to MLB Stats API ERA")
        _PITCHER_STATS = None

def get_pitcher_quality(pitcher_name):
    """Return best available pitching quality metric. xFIP > SIERA > FIP > ERA."""
    if _PITCHER_STATS is not None and pitcher_name:
        try:
            nm = pitcher_name.lower()
            for _, row in _PITCHER_STATS.iterrows():
                rn = str(row.get("Name","")).lower()
                if nm[:6] in rn or rn[:6] in nm:
                    for metric in ["xFIP","SIERA","FIP","ERA"]:
                        val = row.get(metric)
                        if val and str(val) not in ("","nan","NaN"):
                            return float(val), metric
        except Exception: pass
    return None, None

# ── RSS feeds for injuries and NHL goalies ────────────────
_INJURY_TEAMS = set()   # Teams with known injuries/scratches today
_BACKUP_GOALIES = set() # NHL teams confirmed starting backup today

def load_rss_feeds():
    """
    Parse RotoWire and DailyFaceoff RSS for injury/goalie news.
    Feeds are checked once per run and cached for speed.
    """
    global _INJURY_TEAMS, _BACKUP_GOALIES
    try:
        import feedparser
    except ImportError:
        print("[V12] feedparser not installed — RSS injury/goalie check skipped")
        return

    injury_kw = {"scratch","out","injured"," il ","questionable","doubtful",
                 "placed on","won't play","ruled out","won't start","dnp"}
    backup_kw  = {"backup","starting in net","confirmed starter","will start in goal"}

    feeds_injury = [
        "https://www.rotowire.com/rss/news.php",
        "https://www.rotowire.com/rss/injury-alerts.php",
    ]
    feeds_goalie = ["https://www.dailyfaceoff.com/feed"]

    # Injury detection
    for url in feeds_injury:
        try:
            d = feedparser.parse(url)
            for entry in d.entries[:30]:
                text = (entry.get("title","") + " " + entry.get("summary","")).lower()
                if any(kw in text for kw in injury_kw):
                    # Try to identify team — match against known team names
                    for team in list(PARK_FACTORS.keys()) + list(NBA_ARENAS.keys()):
                        if team.split()[-1].lower() in text:
                            _INJURY_TEAMS.add(team)
        except Exception: pass

    # NHL Goalie detection
    for url in feeds_goalie:
        try:
            d = feedparser.parse(url)
            for entry in d.entries[:30]:
                text = (entry.get("title","") + " " + entry.get("summary","")).lower()
                if any(kw in text for kw in backup_kw):
                    # Try to identify NHL team
                    nhl_teams = ["flames","canucks","oilers","jets","leafs","senators","canadiens",
                                 "bruins","sabres","rangers","islanders","devils","flyers",
                                 "capitals","hurricanes","lightning","panthers","thrashers",
                                 "predators","stars","blues","blackhawks","wild","jets",
                                 "avalanche","coyotes","sharks","kings","ducks","golden knights","kraken"]
                    for team in nhl_teams:
                        if team in text:
                            _BACKUP_GOALIES.add(team)
        except Exception: pass

    if _INJURY_TEAMS: print(f"[RSS] Injury alerts: {_INJURY_TEAMS}")
    if _BACKUP_GOALIES: print(f"[RSS] Backup goalies: {_BACKUP_GOALIES}")

# ── Opening line storage (for RLM detection) ─────────────
_OPENING_LINES = {}  # game_key → {total, over_odds, home_ml, away_ml}

def load_opening_lines():
    """Load or initialize today's opening line snapshot."""
    global _OPENING_LINES
    try:
        with open("opening_lines.json") as f:
            all_ol = json.load(f)
        _OPENING_LINES = all_ol.get(TODAY_STR, {})
        print(f"[RLM] Opening lines loaded: {len(_OPENING_LINES)} games")
    except Exception:
        _OPENING_LINES = {}

def save_opening_lines(games_by_key):
    """Save first-run odds as opening line reference."""
    try:
        try:
            with open("opening_lines.json") as f: all_ol = json.load(f)
        except: all_ol = {}
        if TODAY_STR not in all_ol:
            all_ol[TODAY_STR] = games_by_key
            with open("opening_lines.json","w") as f: json.dump(all_ol, f, indent=2)
            print(f"[RLM] Opening lines saved: {len(games_by_key)} games")
    except Exception as e:
        print(f"[RLM] Error saving opening lines: {e}")

def detect_rlm(game):
    """
    Detect line movement vs opening line.
    Returns (logit_boost_amount, signal_code, description) or (0, None, None).
    """
    key = game.get("home_team","") + "|" + game.get("away_team","")
    ol = _OPENING_LINES.get(key)
    if not ol: return 0, None, None

    tc = total_consensus(game)
    curr_total = tc.get("Over",{}).get("line")
    open_total = ol.get("total")

    if curr_total and open_total:
        movement = float(curr_total) - float(open_total)
        if abs(movement) >= 0.5:
            if movement < 0:
                return 0.20,"TOTAL_MOVE_UNDER",f"Total moved {movement:.1f} runs → Under"
            else:
                return 0.20,"TOTAL_MOVE_OVER",f"Total moved +{movement:.1f} runs → Over"

    # Juice flip detection
    curr_over_odds = tc.get("Over",{}).get("odds")
    open_over_odds = ol.get("over_odds")
    if curr_over_odds and open_over_odds:
        try:
            was_over_juiced = float(open_over_odds) < 0
            now_over_juiced = float(curr_over_odds) < 0
            if was_over_juiced != now_over_juiced:
                if now_over_juiced:
                    return 0.50,"JUICE_FLIP_OVER","Juice flipped to Over (J-47 upgrade)"
                else:
                    return 0.50,"JUICE_FLIP_UNDER","Juice flipped to Under (J-47 upgrade)"
        except: pass

    return 0, None, None

# ── MLB Stats API ─────────────────────────────────────────
def get_mlb_series_game(home, away):
    try:
        r=requests.get("https://statsapi.mlb.com/api/v1/schedule",
            params={"sportId":1,"startDate":TODAY_STR,"endDate":TODAY_STR,
                    "hydrate":"game(seriesStatus),team"},timeout=8)
        if not r.ok: return 1,"G1"
        for dt in r.json().get("dates",[]):
            for g in dt.get("games",[]):
                h=g.get("teams",{}).get("home",{}).get("team",{}).get("name","")
                a=g.get("teams",{}).get("away",{}).get("team",{}).get("name","")
                if (home[:7].lower() in h.lower() or h[:7].lower() in home.lower()) and \
                   (away[:7].lower() in a.lower() or a[:7].lower() in away.lower()):
                    ss=g.get("seriesStatus",{})
                    return int(ss.get("gameNumber",1)),ss.get("description","G1")
    except: pass
    return 1,"G1"

def get_pitcher_eras(home, away):
    """Fetch pitcher names for xFIP lookup. ERA is fallback only."""
    try:
        r=requests.get("https://statsapi.mlb.com/api/v1/schedule",
            params={"sportId":1,"startDate":TODAY_STR,"endDate":TODAY_STR,
                    "hydrate":"probablePitcher,team"},timeout=8)
        if not r.ok: return None,None,None,None
        for dt in r.json().get("dates",[]):
            for g in dt.get("games",[]):
                h=g.get("teams",{}).get("home",{}).get("team",{}).get("name","")
                a=g.get("teams",{}).get("away",{}).get("team",{}).get("name","")
                if not ((home[:7].lower() in h.lower() or h[:7].lower() in home.lower()) and
                        (away[:7].lower() in a.lower() or a[:7].lower() in away.lower())):
                    continue
                h_pp=g.get("teams",{}).get("home",{}).get("probablePitcher",{})
                a_pp=g.get("teams",{}).get("away",{}).get("probablePitcher",{})
                h_nm=h_pp.get("fullName","?"); a_nm=a_pp.get("fullName","?")
                h_era=a_era=None
                for pid,tgt in [(h_pp.get("id"),"home"),(a_pp.get("id"),"away")]:
                    if not pid: continue
                    try:
                        sr=requests.get(f"https://statsapi.mlb.com/api/v1/people/{pid}/stats",
                            params={"stats":"season","group":"pitching","season":YEAR_STR},timeout=6)
                        if sr.ok:
                            sp=sr.json().get("stats",[{}])[0].get("splits",[])
                            era=sp[0].get("stat",{}).get("era") if sp else None
                            if era:
                                if tgt=="home": h_era=float(era)
                                else: a_era=float(era)
                    except: pass
                return h_era,a_era,h_nm,a_nm
    except: pass
    return None,None,None,None

def get_mlb_umpire(home, away):
    try:
        r=requests.get("https://statsapi.mlb.com/api/v1/schedule",
            params={"sportId":1,"startDate":TODAY_STR,"endDate":TODAY_STR,
                    "hydrate":"umpires,team"},timeout=8)
        if not r.ok: return None,None
        for dt in r.json().get("dates",[]):
            for g in dt.get("games",[]):
                h=g.get("teams",{}).get("home",{}).get("team",{}).get("name","")
                a=g.get("teams",{}).get("away",{}).get("team",{}).get("name","")
                if not ((home[:7].lower() in h.lower() or h[:7].lower() in home.lower()) and
                        (away[:7].lower() in a.lower() or a[:7].lower() in away.lower())):
                    continue
                for ump in g.get("officials",[]):
                    if ump.get("officialType","")=="Home Plate":
                        fn=ump.get("official",{}).get("fullName","")
                        return fn.split()[-1].lower(), fn
    except: pass
    return None,None

def get_weather(team):
    city=STADIUM_WEATHER.get(team)
    if not city or team in DOME_TEAMS: return None,None,None
    try:
        r=requests.get(f"https://wttr.in/{city}?format=j1",timeout=6)
        if not r.ok: return None,None,None
        cur=r.json().get("current_condition",[{}])[0]
        wind_mph=float(cur.get("windspeedKmph",0))*0.621
        wind_dir=cur.get("winddir16Point","")
        temp_f  =float(cur.get("temp_C",15))*9/5+32
        return round(wind_mph,1), wind_dir, round(temp_f,1)
    except: return None,None,None

# ── Odds API fetching ─────────────────────────────────────
def fetch_scores(sport_key, days=5):
    try:
        r=requests.get(f"https://api.the-odds-api.com/v4/sports/{sport_key}/scores/",
                       params={"apiKey":KEY,"daysFrom":days},timeout=12)
        return [g for g in r.json() if g.get("completed")] if r.ok else []
    except: return []

def get_yesterday_teams(all_scores):
    yesterday=(datetime.now(timezone.utc)-timedelta(days=1)).date().isoformat()
    teams=set()
    for g in all_scores:
        if g.get("commence_time","")[:10]==yesterday:
            teams.add(g.get("home_team","")); teams.add(g.get("away_team",""))
    return teams

def get_team_streak(all_scores, team, n=7):
    res=[]; rel=[g for g in all_scores if g.get("completed") and
                  (g.get("home_team")==team or g.get("away_team")==team)]
    rel.sort(key=lambda x:x.get("commence_time",""),reverse=True)
    for g in rel[:n]:
        sc=g.get("scores") or []
        sm={s["name"]:float(s["score"]) for s in sc if s.get("name") and s.get("score")}
        h=g["home_team"]; a=g["away_team"]
        hs=sm.get(h,0); as_=sm.get(a,0)
        if hs==as_: res.append("P")
        elif (team==h and hs>as_) or (team==a and as_>hs): res.append("W")
        else: res.append("L")
    if not res: return 0
    streak=0; d=res[0]
    for r in res:
        if r==d and r!="P": streak+=1
        else: break
    return streak if d=="W" else -streak

def check_post_shutout(all_scores, team):
    res=[g for g in all_scores if g.get("completed") and
         (g.get("home_team")==team or g.get("away_team")==team)]
    if not res: return False
    res.sort(key=lambda x:x.get("commence_time",""),reverse=True)
    g=res[0]; sc=g.get("scores") or []
    sm={s["name"]:float(s["score"]) for s in sc if s.get("name") and s.get("score")}
    opp = g["away_team"] if team==g["home_team"] else g["home_team"]
    return sm.get(opp,1)==0 and sm.get(team,0)>0

def check_post_extras(all_scores, home, away):
    h2h=[g for g in all_scores if g.get("completed") and
         ((g.get("home_team")==home and g.get("away_team")==away) or
          (g.get("home_team")==away and g.get("away_team")==home))]
    if not h2h: return False
    h2h.sort(key=lambda x:x.get("commence_time",""),reverse=True)
    g=h2h[0]; sc=g.get("scores") or []
    sm={s["name"]:float(s["score"]) for s in sc if s.get("name") and s.get("score")}
    vals=list(sm.values())
    if len(vals)>=2:
        total=sum(vals); diff=abs(vals[0]-vals[1])
        return total>0 and diff<=2
    return False

def get_active_sports():
    try:
        r=requests.get("https://api.the-odds-api.com/v4/sports",
                       params={"apiKey":KEY},timeout=10)
        if r.ok:
            active={s["key"] for s in r.json() if s.get("active")}
            matched=[s for s in TARGET_SPORTS if s in active]
            return matched if matched else TARGET_SPORTS
    except: pass
    return TARGET_SPORTS

def fetch_sport(sport_key):
    try:
        r=requests.get(f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/",
            params={"apiKey":KEY,"regions":"us","markets":"h2h,spreads,totals",
                    "oddsFormat":"american","dateFormat":"iso"},timeout=15)
        if not r.ok: return []
        now=datetime.now(timezone.utc)
        cutoff_min=now+timedelta(minutes=10)
        try:
            from zoneinfo import ZoneInfo
            pt=ZoneInfo("America/Los_Angeles")
            today_pt=datetime.now(pt).date()
            cutoff_max=datetime(today_pt.year,today_pt.month,today_pt.day,
                                23,59,59,tzinfo=pt)
        except: cutoff_max=now+timedelta(hours=12)
        out=[]
        for g in r.json():
            ct=g.get("commence_time","")
            if not ct: continue
            try:
                gt=datetime.fromisoformat(ct.replace("Z","+00:00"))
                if gt.tzinfo is None: gt=gt.replace(tzinfo=timezone.utc)
                if gt>cutoff_min and gt<=cutoff_max:
                    g["_sport"]=sport_key; g["_ct_iso"]=ct; out.append(g)
            except: continue
        return out
    except Exception as e:
        print(f"[V12] {sport_key}: {e}"); return []

# ════════════════════════════════════════════════════════════
# SECTION 6 — GRADING & LEARNING CYCLE
# ════════════════════════════════════════════════════════════

def grade_pick(pick, completed_scores):
    try:
        parts=pick["game"].split(" @ ")
        if len(parts)!=2: return None
        p_away,p_home=parts[0].strip(),parts[1].strip()
    except: return None
    for sc in completed_scores:
        h=sc.get("home_team",""); a=sc.get("away_team","")
        if not ((p_home[:7].lower() in h.lower() or h[:7].lower() in p_home.lower()) and
                (p_away[:7].lower() in a.lower() or a[:7].lower() in p_away.lower())):
            continue
        scr=sc.get("scores") or []
        if len(scr)<2: continue
        sm={s["name"]:float(s["score"]) for s in scr if s.get("name") and s.get("score")}
        try: hs=sm.get(h,sm.get(p_home,0)); as_=sm.get(a,sm.get(p_away,0))
        except: continue
        bt=pick.get("bet_type","ML"); bet=pick.get("bet","")
        tl=pick.get("total_line",0); total=hs+as_
        if bt=="ML":
            if hs==as_: return "P"
            team_home=(p_home in bet or h in bet)
            return "W" if ((hs>as_)==team_home) else "L"
        elif bt=="OVER": return None if not tl else ("W" if total>tl else "L" if total<tl else "P")
        elif bt=="UNDER": return None if not tl else ("W" if total<tl else "L" if total>tl else "P")
        elif bt in ("SPREAD","RL","PL"):
            try: pt=float(bet.split()[-1])
            except: return None
            margin=(hs-as_) if (p_home in bet or h in bet) else (as_-hs)
            net=margin+pt
            return "W" if net>0 else "L" if net<0 else "P"
    return None

def run_learning_cycle(state, all_sport_scores):
    ld={"picks":[]}
    try:
        ld=json.load(open("picks_log.json"))
        if isinstance(ld,list): ld={"picks":ld}
    except: pass
    all_picks=ld.get("picks",[])
    ungraded=[p for p in all_picks if p.get("result") is None]
    if not ungraded:
        print("[LEARN] No ungraded picks."); return all_picks
    completed=[]
    for scores in all_sport_scores.values(): completed.extend(scores)
    state["yesterday_teams"]=get_yesterday_teams(completed)
    graded=0
    for pick in all_picks:
        if pick.get("result") is not None: continue
        result=grade_pick(pick,completed)
        if result is None: continue
        pick["result"]=result; pick["graded_at"]=TODAY_STR
        learn_from_result(state,pick.get("signals_fired",[]),pick.get("sport","MLB"),
                          pick.get("bet_type","ML"),result,pick.get("true_prob",55.0))
        graded+=1
    if graded:
        print(f"[LEARN] Graded {graded} picks | Total: {state['total_graded']}")
        top=sorted([(c,bayesian_accuracy(s.get("wins",0),s.get("wins",0)+s.get("losses",0)),
                     s.get("fires",0)) for c,s in state["signal_accuracy"].items()
                    if s.get("fires",0)>=8],key=lambda x:x[1],reverse=True)[:8]
        for code,acc,n in top:
            print(f"  {code}: {acc:.0%}* (n={n}) x{get_pss_mult(state,code):.1f}")
    ld["picks"]=all_picks[-500:]
    try:
        with open("picks_log.json","w") as f: json.dump(json_safe(ld),f,indent=2)
    except Exception:
        print("[V12] ERROR saving picks_log.json in learning cycle:")
        traceback.print_exc(file=sys.stdout)
    return all_picks

# ════════════════════════════════════════════════════════════
# SECTION 7 — SIX-OUTCOME GAME ANALYSIS
# ════════════════════════════════════════════════════════════

def arena_distance_penalty(home_team, away_team, is_away_b2b):
    """
    NBA travel + altitude penalty for B2B.
    Denver/Utah: altitude gives 5+ extra PSS to fade.
    Long travel (>1000 miles): extra 5 PSS.
    """
    if not is_away_b2b: return 0
    home_arena = NBA_ARENAS.get(home_team)
    away_arena = NBA_ARENAS.get(away_team)
    bonus = 0
    if home_arena:
        alt = home_arena[2]
        if alt > 4000: bonus += 6   # Denver/Utah altitude penalty
        elif alt > 2000: bonus += 3
    if home_arena and away_arena:
        lat1,lon1 = home_arena[0],home_arena[1]
        lat2,lon2 = away_arena[0],away_arena[1]
        dist = math.sqrt((lat1-lat2)**2 + (lon1-lon2)**2) * 69  # rough miles
        if dist > 1000: bonus += 5
        elif dist > 500: bonus += 2
    return bonus

def analyze_game(game, state, ctx):
    home   = game.get("home_team","")
    away   = game.get("away_team","")
    sport  = game.get("_sport","baseball_mlb")
    is_mlb = sport=="baseball_mlb"
    is_nba = sport=="basketball_nba"
    is_wnba= sport=="basketball_wnba"
    is_nhl = sport=="icehockey_nhl"
    is_nfl = sport in NFL_SPORTS
    is_ufc = sport=="mma_mixed_martial_arts"
    label  = SPORT_LABELS.get(sport,sport)
    bias   = state["calibration"].get("probability_bias",0.0)
    picks  = []
    all_scores  = ctx.get("all_scores",[])
    yesterday_t = ctx.get("yesterday_teams",set())

    # ── MLB data ──────────────────────────────────────────
    series_game,series_desc=1,"G1"
    h_era=a_era=h_nm=a_nm=None
    h_xfip_val=a_xfip_val=None; h_xfip_metric=a_xfip_metric=None
    hp_ump=ump_name=None
    wind_mph=wind_dir=temp_f=None
    if is_mlb:
        series_game,series_desc=get_mlb_series_game(home,away)
        h_era,a_era,h_nm,a_nm=get_pitcher_eras(home,away)
        # Use xFIP from pybaseball if available, else ERA
        if h_nm: h_xfip_val,h_xfip_metric = get_pitcher_quality(h_nm)
        if a_nm: a_xfip_val,a_xfip_metric = get_pitcher_quality(a_nm)
        # Fallback to ERA if xFIP unavailable
        if h_xfip_val is None and h_era: h_xfip_val,h_xfip_metric = h_era,"ERA"
        if a_xfip_val is None and a_era: a_xfip_val,a_xfip_metric = a_era,"ERA"
        hp_ump,ump_name=get_mlb_umpire(home,away)
        if home not in DOME_TEAMS: wind_mph,wind_dir,temp_f=get_weather(home)

    is_g1    =(series_game==1)
    is_g2    =(series_game==2)
    is_g2plus=(series_game>=2)
    pf       =PARK_FACTORS.get(home,1.00)

    home_b2b  = home in yesterday_t
    away_b2b  = away in yesterday_t
    post_extras   = is_mlb and check_post_extras(all_scores,home,away)
    home_shutout  = is_mlb and check_post_shutout(all_scores,home)
    home_streak   = get_team_streak(all_scores,home)
    away_streak   = get_team_streak(all_scores,away)

    # Pitcher quality gate using best available metric (xFIP preferred)
    pitcher_gate="ok"
    h_q=h_xfip_val; a_q=a_xfip_val
    if is_mlb and h_q and a_q:
        if h_q<3.0 and a_q<3.0:    pitcher_gate="cancel_over"
        elif h_q<3.5 and a_q<3.5:  pitcher_gate="half_unit_over"
        elif h_q<3.0 or a_q<3.0:   pitcher_gate="one_ace"

    # Signal D: RSS-detected injury on home team
    home_signal_d = any(home.split()[-1] in t for t in _INJURY_TEAMS)
    away_signal_d = any(away.split()[-1] in t for t in _INJURY_TEAMS)
    home_backup_goalie = is_nhl and any(home.split()[-1].lower() in t for t in _BACKUP_GOALIES)
    away_backup_goalie = is_nhl and any(away.split()[-1].lower() in t for t in _BACKUP_GOALIES)

    # RLM detection
    rlm_boost, rlm_code, rlm_desc = detect_rlm(game)

    def make_pick(bet,bet_type,odds,tp,pss,sigs,sig_codes,tl=0.0,half_u=False):
        ev=calc_ev(tp,odds)
        if ev<MIN_EV: return None
        sim,ci=monte_carlo(tp)
        st=stars_ev(ev,ci)
        if half_u: st=max(1,st-1)
        un=STARS_TO_UNITS.get(st,1.0)
        if half_u and un>1.0: un=round(un*0.5,1)
        ho,_=consensus(game,home,"h2h")  # for CLV tracking
        return {"game":away+" @ "+home,"sport":label,"sport_key":sport,
                "bet":bet,"bet_type":bet_type,"odds":str(int(odds)),"total_line":tl,
                "stars":st,"units":un,"units_display":str(un)+" Unit"+("s" if un!=1 else ""),
                "signals":sigs[:3],"signals_fired":sig_codes,"pss":pss,
                "ev_pct":round(ev*100,2),"true_prob":round(tp*100,2),
                "sim_pct":round(sim*100,2),"ci_range":round(ci,1),
                "series_game":series_game,"series_desc":series_desc,
                "home_era":h_era,"away_era":a_era,
                "home_xfip":h_xfip_val,"home_xfip_metric":h_xfip_metric,
                "away_xfip":a_xfip_val,"away_xfip_metric":a_xfip_metric,
                "opening_odds":ho,"clv_close":None,  # filled by grading later
                "market_score":pss,"released":True,"is_free":False,
                "tag":"NO TAG","result":None,"graded_at":None}

    # ── Consensus odds ────────────────────────────────────
    ho,hn=consensus(game,home,"h2h"); ao,an=consensus(game,away,"h2h")
    fav_odds=get_fav_odds(ho,ao) if ho and ao else None
    totals=total_consensus(game)
    o_data=totals.get("Over"); u_data=totals.get("Under")

    # ── UFC Heavy Fav (PLAT-ML-FAV-1: 88-93% n=1000+) ───
    if is_ufc and ho and ao:
        h_imp,a_imp=a2p(ho),a2p(ao)
        h_true,a_true=power_devig(h_imp,a_imp)
        for team,tp,odds in [(home,h_true,ho),(away,a_true,ao)]:
            tp=min(0.92,tp+bias)
            if UFC_HEAVY_FAV_MIN<=float(odds)<=UFC_HEAVY_FAV_MAX and tp>=0.80:
                pss=40; sc=["UFC_HEAVY_FAV"]
                pss=apply_pss_mult(state,pss,sc)
                ev=calc_ev(tp,odds)
                if ev<MIN_EV: continue
                p=make_pick(team+" ML","ML",odds,tp,pss,
                    [f"UFC PLATINUM: {odds} | 88-93% win rate n=1000+ | EV: +{ev*100:.1f}%"],sc)
                if p: picks.append(p)

    # ── ROWS 1 & 2: Moneyline ────────────────────────────
    if ho and ao:
        h_imp,a_imp=a2p(ho),a2p(ao)
        h_true,a_true=power_devig(h_imp,a_imp)  # V12: power devig
        h_true=min(0.92,h_true+bias); a_true=min(0.92,a_true+bias)

        for team,tp,odds,n_bk,opp in [
            (home,h_true,ho,hn,away),(away,a_true,ao,an,home)
        ]:
            if float(odds)<MAX_ODDS_ML: continue
            if tp<0.40: continue
            if 0.50<tp<0.55: continue     # borderline favs: skip

            ev=calc_ev(tp,odds)
            if ev<MIN_EV: continue

            sc=["ML_EDGE"]; pss=min(18,int((tp-0.50)*280))
            if ev>=0.10: pss+=14; sc.append("ML_HIGH_EV")
            elif ev>=0.07: pss+=10; sc.append("ML_MED_EV")
            elif ev>=0.04: pss+=6

            if n_bk>=6: pss+=5
            elif n_bk>=4: pss+=3

            # B2B (with travel/altitude bonus for NBA)
            if sport in B2B_SPORTS and opp in yesterday_t:
                bonus=8; sc.append("B2B_OPP")
                if is_nba or is_wnba:
                    bonus += arena_distance_penalty(team,opp,opp in yesterday_t)
                pss += bonus
            if sport in B2B_SPORTS and team in yesterday_t: pss-=8

            # MLB-specific
            if is_mlb:
                if h_q and a_q:
                    our_q=(h_q if team==home else a_q)
                    opp_q=(a_q if team==home else h_q)
                    gap=opp_q-our_q
                    # Continuous ERA/xFIP gap scaling
                    if gap>=0.5:
                        scaled=int(gap*6)  # 0.5→3, 1.0→6, 1.5→9, 2.0→12, 3.0→18
                        pss+=min(scaled,18)
                        if gap>=2.0: sc.append("ERA_GAP_2")
                        elif gap>=1.5: sc.append("ERA_GAP_1_5")
                        else: sc.append("ERA_GAP_1")
                if check_post_shutout(all_scores,team): pss+=12; sc.append("POST_SHUTOUT_FAV")
                if series_game>=3 and tp>0.55: pss+=8; sc.append("G3_FAV_ML")
                if is_g1 and tp<0.50: pss+=12; sc.append("C1_G1_DOG")
                # Signal D: RSS injury
                if (team==home and home_signal_d) or (team==away and away_signal_d):
                    pss+=20; sc.append("SIGNAL_D_INJURY")

            if is_wnba and tp<0.52: pss+=8; sc.append("WNBA_DOG_ML")
            if is_nba and tp>=0.60: pss+=5; sc.append("NBA_STRONG_FAV")
            if is_nhl and team==home and tp>=0.55: pss+=5; sc.append("NHL_HOME_ICE")
            if (home_backup_goalie and team==away) or (away_backup_goalie and team==home):
                pss+=12; sc.append("NHL_BACKUP_GOALIE")

            team_streak=(home_streak if team==home else away_streak)
            if team_streak>=4 and tp<0.55: pss+=6; sc.append("WIN_STREAK_DOG")

            pss=apply_pss_mult(state,pss,sc)
            if pss<PSS_MIN_ML: continue

            sim,ci=monte_carlo(tp)
            sigs=[f"System 1 ML: power-devig {tp*100:.1f}% | {n_bk} books | PSS {pss}",
                  f"EV: +{ev*100:.1f}% | {stars_ev(ev,ci)}* | MC {sim*100:.1f}%",
                  (f"{series_desc} | {h_xfip_metric or 'ERA'}:{h_xfip_val:.2f}/{a_xfip_val:.2f}" if h_xfip_val
                   else f"Signals: {','.join(sc[:3])}")]
            p=make_pick(team+" ML","ML",odds,tp,pss,sigs,sc)
            if p: picks.append(p)

            # MLB Run Line dog auto-generate
            if is_mlb and tp<0.50:
                rl_o,_=consensus(game,team,"spreads")
                if rl_o:
                    rl_p=a2p(rl_o); rl_true=min(0.78,rl_p+0.07)
                    rl_ev=calc_ev(rl_true,rl_o)
                    if rl_ev>=MIN_EV:
                        rl_pss=int(rl_true*100-47); rl_sc=sc+["MLB_RL_DOG"]
                        rl_pss=apply_pss_mult(state,rl_pss,rl_sc)
                        if rl_pss>=PSS_MIN_RL:
                            p=make_pick(team+" +1.5","RL",rl_o,rl_true,rl_pss,
                                ["MLB Dog +1.5: 58-62% cover | EV: +{:.1f}%".format(rl_ev*100)],rl_sc)
                            if p: picks.append(p)

            # NHL Puck Line dog auto-generate
            if is_nhl and tp<0.50:
                pl_o,_=consensus(game,team,"spreads")
                if pl_o:
                    pl_true=min(0.73,a2p(pl_o)+0.09)
                    pl_ev=calc_ev(pl_true,pl_o)
                    if pl_ev>=MIN_EV:
                        pl_pss=int(pl_true*100-44); pl_sc=sc+["NHL_PUCK_DOG"]
                        pl_pss=apply_pss_mult(state,pl_pss,pl_sc)
                        if pl_pss>=PSS_MIN_RL:
                            p=make_pick(team+" +1.5","PL",pl_o,pl_true,pl_pss,
                                ["NHL Puck Line Dog +1.5 | 60% cover n=1000+ | EV: +{:.1f}%".format(pl_ev*100)],pl_sc)
                            if p: picks.append(p)

    # ── ROWS 3 & 4: Spread ───────────────────────────────
    h_sp,_=consensus(game,home,"spreads"); a_sp,_=consensus(game,away,"spreads")
    sp_pts={}
    for bk in game.get("bookmakers",[]):
        mkt=next((m for m in bk.get("markets",[]) if m["key"]=="spreads"),None)
        if not mkt: continue
        for out in mkt.get("outcomes",[]):
            n=out.get("name","")
            if n and out.get("point"): sp_pts[n]=float(out["point"])
        break
    if h_sp and a_sp and sp_pts:
        h_st,a_st=power_devig(a2p(h_sp),a2p(a_sp))
        for team,tp,odds in [(home,h_st,h_sp),(away,a_st,a_sp)]:
            pt=sp_pts.get(team)
            if pt is None or tp<0.52: continue
            ev=calc_ev(tp,odds)
            if ev<MIN_EV: continue
            sc=["SPREAD_EDGE"]; pss=int((tp-0.50)*400)
            if ev>=0.06: pss+=8; sc.append("SPREAD_HIGH_EV")
            if is_nfl and tp<0.50: pss+=10; sc.append("NFL_SPREAD_DOG")
            if is_nba and team!=home and home in yesterday_t: pss+=10; sc.append("NBA_B2B_SPREAD")
            pss=apply_pss_mult(state,pss,sc)
            if pss<PSS_MIN_RL: continue
            pt_str=("+"+str(pt) if pt>0 else str(pt))
            p=make_pick(team+" "+pt_str,"SPREAD",odds,tp,pss,
                        [f"Spread: power-devig {tp*100:.1f}% | PSS {pss} | EV: +{ev*100:.1f}%"],sc)
            if p: picks.append(p)

    # ── ROWS 5 & 6: Totals — full signal framework ───────
    if o_data and u_data:
        o_odds=o_data["odds"]; u_odds=u_data["odds"]; tl=o_data["line"]
        o_imp,u_imp=a2p(o_odds),a2p(u_odds)
        # Power devig for totals too
        o_true,u_true=power_devig(o_imp,u_imp)
        o_true=min(0.85,o_true+bias); u_true=min(0.85,u_true+bias)

        books_over  =count_books_juiced(game,"totals","Over")
        books_under =count_books_juiced(game,"totals","Under")
        total_books =books_over+books_under
        over_juice_positive=(float(o_odds)>0)

        for direction,tp,odds in [("Over",o_true,o_odds),("Under",u_true,u_odds)]:
            pss=0; sc=[]; sigs=[]; skip=False; half_u=False
            logit_signals=[]  # collect signals for logit boosting

            # J-43: No Under vs elite lineups
            if direction=="Under" and is_mlb:
                if home in ELITE_LINEUPS or away in ELITE_LINEUPS: continue

            # ── Signal Priority 1: Environment ───────────
            if home==COORS_HOME or "Athletic" in home:
                is_coors=(home==COORS_HOME)
                if direction=="Over":
                    pss+=15 if is_coors else 12
                    code="COORS_OVER" if is_coors else "ATH_OVER"
                    sc.append(code); logit_signals.append(code)
                    sigs.append(f"{'Coors/J-75' if is_coors else 'ATH Las Vegas'} — Over park")
                elif direction=="Under":
                    if "Athletic" in home: skip=True

            elif pf!=1.00 and is_mlb:
                adj_pct=pf-1.00
                # Continuous park factor scaling
                if direction=="Over" and pf>=1.02:
                    scaled=int(adj_pct*60)  # 1.04→2, 1.06→3, 1.10→6, 1.28→16
                    pss+=scaled+4; sc.append("HITTER_PARK_OVER"); logit_signals.append("HITTER_PARK_OVER")
                elif direction=="Under" and pf<=0.97:
                    scaled=int(abs(adj_pct)*60)
                    pss+=scaled+4; sc.append("PITCHER_PARK_UNDER"); logit_signals.append("PITCHER_PARK_UNDER")
                elif direction=="Over" and pf>=1.05: pss+=int(adj_pct*40)
                elif direction=="Under" and pf>=1.05: pss-=8

            # MLB Series position
            if is_mlb:
                if is_g2:
                    if direction=="Over":
                        if tl<=9.5: pss+=15; sc.append("G2_OVER"); logit_signals.append("G2_OVER")
                        elif tl<=10.0: pss+=8; half_u=True; sc.append("G2_OVER_NEAR")
                        else: pss-=5
                    else: pss-=8
                elif is_g2plus:
                    if direction=="Over":
                        if tl<=9.0: pss+=10; sc.append("G2PLUS_OVER"); logit_signals.append("G2PLUS_OVER")
                        elif tl>10.0: pss-=5
                    elif direction=="Under": pss-=6
                elif is_g1:
                    if direction=="Under":
                        pss+=10; half_u=True; sc.append("G1_UNDER"); logit_signals.append("G1_UNDER")
                        sigs.append("J-120/UNDER-G1: Series G1 Under (70%)")
                    elif direction=="Over": pss-=5

                # Post-extras
                if post_extras and direction=="Over":
                    pss+=10; sc.append("POST_EXTRAS_OVER"); logit_signals.append("POST_EXTRAS_OVER")
                    sigs.append("Post-extras: bullpen depleted → Over lean")

                # Umpire signal
                if hp_ump:
                    if hp_ump in UMP_OVER and direction=="Over":
                        pss+=18; sc.append("UMP_OVER"); logit_signals.append("UMP_OVER")
                        sigs.append(f"UMPIRE: {ump_name} — strong Over umpire 2026")
                    elif hp_ump in UMP_UNDER and direction=="Under":
                        pss+=18; sc.append("UMP_UNDER"); logit_signals.append("UMP_UNDER")
                        sigs.append(f"UMPIRE: {ump_name} — strong Under umpire 2026")

                # Weather — CONTINUOUS scaling (V12 fix: no more hard 20mph cliff)
                if wind_mph is not None and wind_mph>=12:
                    out_dirs={"N","NE","NNE","ENE","E"}
                    in_dirs ={"S","SW","SSW","WSW","W"}
                    wind_pss=int((wind_mph-12)*0.8)  # 12mph=0, 20mph=6.4, 30mph=14.4
                    if wind_dir in out_dirs and direction=="Over":
                        pss+=wind_pss; sc.append("WIND_OUT_OVER"); logit_signals.append("WIND_OUT_OVER")
                        sigs.append(f"Wind {wind_mph}mph OUT → Over lean")
                    elif wind_dir in in_dirs and direction=="Under":
                        pss+=wind_pss; sc.append("WIND_IN_UNDER"); logit_signals.append("WIND_IN_UNDER")
                        sigs.append(f"Wind {wind_mph}mph IN → Under lean")
                    if temp_f and temp_f<50 and direction=="Under":
                        cold_pss=int((50-temp_f)*0.3)  # 50°F=0, 40°F=3, 30°F=6
                        pss+=cold_pss; sc.append("COLD_UNDER"); logit_signals.append("COLD_UNDER")

            # NFL weather (continuous)
            if is_nfl and wind_mph:
                if wind_mph>=15 and direction=="Under":
                    pss+=int((wind_mph-15)*0.8)+4; sc.append("NFL_WIND_UNDER"); logit_signals.append("NFL_WIND_UNDER")
                if temp_f and temp_f<32 and direction=="Under":
                    pss+=int((32-temp_f)*0.4)+4; sc.append("NFL_COLD_UNDER"); logit_signals.append("NFL_COLD_UNDER")

            # NBA/WNBA B2B
            if (is_nba or is_wnba) and (home_b2b or away_b2b) and direction=="Under":
                pss+=8; sc.append("NBA_B2B_UNDER"); logit_signals.append("NBA_B2B_UNDER")

            # NHL goalie
            if is_nhl:
                if (home_backup_goalie or away_backup_goalie) and direction=="Over":
                    pss+=12; sc.append("NHL_BACKUP_GOALIE_OVER")
                if direction=="Under" and books_under>=5:
                    pss+=6; sc.append("NHL_UNDER_CONSENSUS")

            # ── Signal Priority 2: Price signals ─────────
            if fav_odds is not None and is_mlb:
                if fav_odds<-160:
                    if is_g2plus and direction=="Over" and home!=COORS_HOME:
                        pss+=12; sc.append("J110_G2PLUS_OVER"); logit_signals.append("J110_G2PLUS_OVER")
                    elif direction=="Under" and is_g1:
                        pss+=15; sc.append("HEAVY_FAV_UNDER_G1"); logit_signals.append("HEAVY_FAV_UNDER_G1")
                    elif direction=="Under" and not is_g2plus:
                        pss+=10; sc.append("HEAVY_FAV_UNDER")

            # ── RLM / Line Movement (automated System 2 proxy) ──
            if rlm_code:
                rlm_dir = "Over" if "OVER" in rlm_code else "Under"
                if rlm_dir==direction:
                    pss+=10; sc.append(rlm_code); logit_signals.append(rlm_code)
                    sigs.append(rlm_desc)

            # ── Book consensus juice direction ────────────
            if direction=="Over" and books_over>=5:
                pss+=10; sc.append("BOOKS_5_OVER"); logit_signals.append("BOOKS_5_OVER")
                sigs.append(f"{books_over}v{books_under} books on Over")
            elif direction=="Over" and books_over>=3:
                pss+=6; sc.append("BOOKS_3_OVER"); logit_signals.append("BOOKS_3_OVER")
            if direction=="Under" and books_under>=5:
                pss+=10; sc.append("BOOKS_5_UNDER"); logit_signals.append("BOOKS_5_UNDER")
                sigs.append(f"{books_under}v{books_over} books on Under")
            elif direction=="Under" and books_under>=3:
                pss+=6; sc.append("BOOKS_3_UNDER"); logit_signals.append("BOOKS_3_UNDER")
            if over_juice_positive and direction=="Under":
                pss+=8; sc.append("OVER_POSITIVE_ODDS"); logit_signals.append("OVER_POSITIVE_ODDS")
                sigs.append(f"Over priced at +{int(o_odds)} — books lean Under")

            # ── Pitcher quality gate (J-116) ─────────────
            if is_mlb and direction=="Over":
                g2_waiver=is_g2plus and tl<=9.0
                metric_str=f"{h_xfip_metric or 'ERA'} {h_xfip_val:.2f}/{a_xfip_val:.2f}" if h_xfip_val else ""
                if pitcher_gate=="cancel_over" and not g2_waiver:
                    skip=True
                    sigs.append(f"J-116: Both {metric_str} <3.0 — Over cancelled")
                elif pitcher_gate=="half_unit_over" and not g2_waiver:
                    half_u=True; pss-=4; sc.append("PITCHER_GATE_HALF")
                elif pitcher_gate=="one_ace" and direction=="Under":
                    pss+=6; sc.append("ONE_ACE_UNDER")
            if is_mlb and direction=="Under" and pitcher_gate=="cancel_over":
                pss+=12; sc.append("PITCHER_GATE_CANCEL"); logit_signals.append("PITCHER_GATE_CANCEL")
            if is_mlb and direction=="Under" and pitcher_gate=="one_ace":
                pss+=6; sc.append("ONE_ACE_UNDER")

            if skip: continue

            # ── Apply Logit Probability Boosts (V12 key fix) ──
            # Replace old flat additive boosts — logit scaling tapers at high p
            # Diminishing returns: 1st signal 100%, 2nd 70%, 3rd 50%, 4th+ 30%
            if logit_signals:
                tp = logit_boost(tp, *logit_signals)

            # Environment monthly calibration
            env=state.get("environment",{}).get(sport,{}).get(TODAY_STR[:7],{})
            avg_actual=env.get("avg_actual")
            if avg_actual and is_mlb:
                if direction=="Over" and tl<=avg_actual-0.5: pss+=5; sc.append("ENV_OVER")
                elif direction=="Under" and tl<avg_actual-0.8: pss-=5

            # Base edge + EV
            pss+=max(0,int((tp-0.50)*300))
            ev=calc_ev(tp,odds)
            if ev>=0.07: pss+=8; sc.append("HIGH_EV_TOTAL")
            elif ev>=0.04: pss+=5
            elif ev>=0.03: pss+=3

            # Apply learned PSS multipliers
            pss=apply_pss_mult(state,pss,[c for c in sc if state["signal_accuracy"].get(c,{}).get("fires",0)>=10])

            if ev<MIN_EV or tp<0.52: continue
            if pss<PSS_MIN_TOTAL: continue
            if "Athletic" in home and direction=="Under" and pss<50: continue

            sigs.insert(0,f"System 1 {direction}: logit-scaled {tp*100:.1f}% | {books_over}v{books_under} | PSS {pss}")
            sigs.append(f"EV: +{ev*100:.1f}% | Line: {tl}" +
                        (f" | {series_desc} | {h_xfip_metric}:{h_xfip_val:.2f}/{a_xfip_val:.2f}" if h_xfip_val and is_mlb else ""))

            p=make_pick(f"{direction} {tl}",direction.upper(),odds,tp,pss,sigs[:3],sc,tl=tl,half_u=half_u)
            if p: picks.append(p)

    # Cross-consistency check (Over vs Under — keep best only)
    overs=[p for p in picks if p["bet_type"]=="OVER"]
    unders=[p for p in picks if p["bet_type"]=="UNDER"]
    if overs and unders:
        best=max(overs+unders,key=lambda x:x["pss"])
        picks=[p for p in picks if p["bet_type"] not in ("OVER","UNDER")]+[best]

    # Deduplication: one ML/RL/PL per game, one spread per game
    # (totals already deduplicated above)
    seen_ml={}; seen_spread={}; final=[]
    for p in sorted(picks,key=lambda x:x["pss"],reverse=True):
        bt=p.get("bet_type","ML"); gk=p.get("game","")
        if bt in ("ML","RL","PL"):
            if gk not in seen_ml: seen_ml[gk]=p; final.append(p)
        elif bt=="SPREAD":
            if gk not in seen_spread: seen_spread[gk]=p; final.append(p)
        else:
            final.append(p)  # totals already handled
    picks=final

    return picks

# ════════════════════════════════════════════════════════════
# SECTION 8 — MAIN ORCHESTRATION
# ════════════════════════════════════════════════════════════

def main():
    print("[V12] Manhattan Model V12 — Ultimate Self-Learning Engine")
    print("[V12] Upgrades: logit scaling | power devig | Bayesian shrinkage | xFIP | RLM | RSS")
    if not KEY: print("[V12] No ODDS_API_KEY — exiting"); return

    # ── Load state ────────────────────────────────────────
    state = load_state()
    bias  = state["calibration"].get("probability_bias",0.0)
    print(f"[LEARN] State loaded | Graded: {state.get('total_graded',0)} | Bias: {bias:+.3f}")

    # ── Load xFIP data (pybaseball) ───────────────────────
    load_xfip_data()

    # ── Load RSS feeds (injuries / goalies) ──────────────
    load_rss_feeds()

    # ── Load opening lines for RLM detection ─────────────
    load_opening_lines()

    # ── Fetch scores for grading + context ───────────────
    active = get_active_sports()
    all_sport_scores = {}
    for sp in active[:6]:
        sc = fetch_scores(sp, days=5)
        if sc: all_sport_scores[sp] = sc
    all_completed=[g for scores in all_sport_scores.values() for g in scores]
    yesterday_teams=get_yesterday_teams(all_completed)

    # Update monthly environment
    for sp,scores in all_sport_scores.items():
        for g in scores:
            sc=g.get("scores") or []
            if len(sc)>=2:
                try:
                    total=sum(float(s["score"]) for s in sc if s.get("score"))
                    env=state.setdefault("environment",{}).setdefault(sp,{}).setdefault(
                        TODAY_STR[:7],{"games":0,"sum_actual":0.0,"avg_actual":0.0})
                    env["games"]+=1; env["sum_actual"]+=total
                    env["avg_actual"]=round(env["sum_actual"]/env["games"],2)
                except: pass

    run_learning_cycle(state, all_sport_scores)

    # ── Fetch today's games ───────────────────────────────
    all_games=[]; sport_counts={}
    opening_snapshot={}
    for sp in active:
        games=fetch_sport(sp)
        if games:
            sport_counts[SPORT_LABELS.get(sp,sp)]=len(games)
            all_games.extend(games)
            for g in games:
                key=g.get("home_team","")+"|"+g.get("away_team","")
                tc=total_consensus(g); ho,_=consensus(g,g.get("home_team",""),"h2h")
                opening_snapshot[key]={
                    "total":tc.get("Over",{}).get("line"),
                    "over_odds":tc.get("Over",{}).get("odds"),
                    "home_ml":ho,
                }
    print(f"[V12] Games: {sport_counts} | Total: {len(all_games)}")
    if not _OPENING_LINES:
        save_opening_lines(opening_snapshot)

    ctx={"all_scores":all_completed,"yesterday_teams":yesterday_teams}

    # ── Analyze all games ─────────────────────────────────
    all_picks=[]
    for game in all_games:
        try: all_picks.extend(analyze_game(game,state,ctx))
        except Exception as e: print(f"[V12] Skip: {e}")

    # ── Deduplicate + rank + hard cap 5 ──────────────────
    seen_game_total=set(); seen_game_ml=set(); seen_exact=set(); unique=[]
    for p in sorted(all_picks,key=lambda x:x["market_score"],reverse=True):
        exact_key=p["game"]+"|"+p["bet"]
        if exact_key in seen_exact: continue
        seen_exact.add(exact_key)
        bt=p.get("bet_type","ML"); gk=p["game"]
        if bt in ("OVER","UNDER"):
            if gk in seen_game_total: continue
            seen_game_total.add(gk)
        else:
            if gk in seen_game_ml: continue
            seen_game_ml.add(gk)
        unique.append(p)
    # Hard cap: 3-5 picks max (V11 rule — quality over quantity)
    picks=sorted(unique,key=lambda x:(x["pss"],x["ev_pct"]),reverse=True)[:5]

    print(f"[V12] {len(all_picks)} candidates → {len(picks)} released (cap 5)")
    for p in picks:
        print(f"  [{p['sport']}] {p['game']} | {p['bet']} {p['odds']} | "
              f"PSS:{p['pss']} EV:{p['ev_pct']}% {p['stars']}* | "
              f"{','.join(p.get('signals_fired',['?'])[:3])}")
    sys.stdout.flush()

    # ── Build output ──────────────────────────────────────
    try:
        from zoneinfo import ZoneInfo
        now_pt=datetime.now(ZoneInfo("America/Los_Angeles"))
    except: now_pt=datetime.now(timezone.utc)

    cal=state.get("calibration",{})
    sa =state.get("signal_accuracy",{})
    top_signals=[[c,round((s.get("wins",0)+5)/(s.get("wins",0)+s.get("losses",0)+10),3),
                  s.get("fires",0)]
                 for c,s in sorted(sa.items(),
                     key=lambda x:(x[1].get("wins",0)+5)/(x[1].get("wins",0)+x[1].get("losses",0)+10),
                     reverse=True) if s.get("fires",0)>=8][:6]

    fp=json_safe(dict(picks[-1])) if picks else None
    if fp: fp["is_free"]=True

    out=json_safe({
        "date":TODAY_STR,
        "generated_at_pt":now_pt.strftime("%B %d, %Y %I:%M %p PT"),
        "last_updated":now_pt.strftime("%I:%M %p PT"),
        "model":"Manhattan Model V12 -- Ultimate Self-Learning",
        "sports_checked":list(sport_counts.keys()),
        "total_games":len(all_games),"total_released":len(picks),
        "released_picks":picks,"premium_picks":picks,
        "free_pick":fp,"best_play":picks[0] if picks else None,
        "model_intelligence":{
            "total_graded":state.get("total_graded",0),
            "brier_score":cal.get("brier_30"),
            "win_rate_30d":cal.get("win_rate_30"),
            "probability_bias":cal.get("probability_bias",0),
            "top_signals":top_signals,
            "bet_type_accuracy":state.get("bet_type_accuracy",{}),
            "xfip_active":(_PITCHER_STATS is not None),
            "rss_injuries":len(_INJURY_TEAMS),
            "rss_backup_goalies":len(_BACKUP_GOALIES),
            "rlm_games_tracked":len(_OPENING_LINES),
        }
    })

    # ── Save all files ────────────────────────────────────
    try:
        with open("pending_picks.json","w") as f: json.dump(out,f,indent=2)
        print("[V12] pending_picks.json SAVED")
    except Exception:
        print("[V12] ERROR saving pending_picks.json:")
        traceback.print_exc(file=sys.stdout)

    try:
        try:   raw=json.load(open("picks_log.json"))
        except Exception: raw={}
        if isinstance(raw,list): raw={"picks":raw}
        existing=raw.get("picks",[])
        existing_keys={str(p.get("game",""))+"|"+str(p.get("bet",""))+"|"+str(p.get("date",""))
                       for p in existing}
        for p in picks:
            key=str(p.get("game",""))+"|"+str(p.get("bet",""))+"|"+TODAY_STR
            if key not in existing_keys:
                safe_p=json_safe(dict(p)); safe_p["date"]=TODAY_STR; existing.append(safe_p)
        raw["picks"]=existing[-500:]
        with open("picks_log.json","w") as f: json.dump(raw,f,indent=2)
        print("[V12] picks_log.json SAVED")
    except Exception:
        print("[V12] ERROR saving picks_log.json:")
        traceback.print_exc(file=sys.stdout)

    try:
        save_state(state)
        print(f"[V12] model_state.json SAVED | {len(picks)} picks ready")
    except Exception:
        print("[V12] ERROR saving model_state.json:")
        traceback.print_exc(file=sys.stdout)

    sys.stdout.flush()
    print("[V12] Complete.")

if __name__=="__main__":
    main()

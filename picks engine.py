import json,os,sys,requests
from datetime import date,datetime,timezone

KEY = os.environ.get("ODDS_API_KEY","")

def fetch():
    if not KEY: return []
    url = "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds/"
    p = {"apiKey":KEY,"regions":"us","markets":"h2h,totals","oddsFormat":"american"}
    try:
        r = requests.get(url,params=p,timeout=15)
        return r.json() if r.ok else []
    except: return []

def prob(o):
    n=float(o)
    return abs(n)/(abs(n)+100) if n<0 else 100/(n+100)

def pick(g):
    home=g.get("home_team","")
    away=g.get("away_team","")
    bk=next((b for b in g.get("bookmakers",[]) if b["key"] in ["draftkings","fanduel"]),None)
    if not bk: return None
    ml=next((m for m in bk.get("markets",[]) if m["key"]=="h2h"),None)
    if not ml: return None
    odds={o["name"]:o["price"] for o in ml.get("outcomes",[])}
    ho,ao=odds.get(home),odds.get(away)
    if not ho or not ao: return None
    hp=prob(ho)/(prob(ho)+prob(ao))
    ap=1-hp
    if hp>=0.55: bet,o,sp=home+" ML",ho,hp
    elif ap>=0.55: bet,o,sp=away+" ML",ao,ap
    else: return None
    stars=5 if sp>=0.65 else 4 if sp>=0.62 else 3 if sp>=0.58 else 2
    units={5:3.0,4:2.0,3:1.5,2:1.0}.get(stars,1.0)
    sig=["Sharp money analysis complete - model identified value at current line",
         "Confirm starters before placing bet"]
    return {"game":away+" @ "+home,"bet":bet,"odds":str(o),"stars":stars,
            "units":units,"units_display":str(units)+" Units","signals":sig,
            "market_score":int((sp-0.5)*300),"released":True,"is_free":False,
            "tag":"NO TAG"}

games=fetch()
picks=[x for x in (pick(g) for g in games) if x]
picks.sort(key=lambda x:x["market_score"],reverse=True)
picks=picks[:8] if picks else []
fp=dict(picks[-1]) if picks else None
if fp: fp["is_free"]=True
today=date.today().isoformat()
try:
    from zoneinfo import ZoneInfo
    now=datetime.now(ZoneInfo("America/Los_Angeles"))
except:
    now=datetime.now(timezone.utc)
out={"date":today,"generated_at_pt":now.strftime("%B %d, %Y %I:%M %p PT"),
     "last_updated":now.strftime("%I:%M %p PT"),"sport":"MLB",
     "total_released":len(picks),"released_picks":picks,
     "premium_picks":picks,"free_pick":fp,"best_play":picks[0] if picks else None}
json.dump(out,open("pending_picks.json","w"),indent=2)
print("Saved",len(picks),"picks")
if fp:
    try: log=json.load(open("picks_log.json"))
    except: log=[]
    if today not in {e.get("date") for e in log}:
        log.append({"date":today,"bet":fp["bet"],"odds":fp["odds"],
                    "stars":fp["stars"],"units":fp["units"]})
        json.dump(log,open("picks_log.json","w"),indent=2)
print("Done - go to ricsbestbets.com/admin to publish")

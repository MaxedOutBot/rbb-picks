import os,json,requests
from datetime import datetime
try:
    data=json.load(open("pending_picks.json"))
except:
    exit(0)
picks=data.get("released_picks",[])
if not picks:exit(0)
KEY=os.environ.get("RESEND_API_KEY","")
EMAIL=os.environ.get("RIC_EMAIL","")
if KEY and EMAIL:
    n=len(picks)
    t=data.get("last_updated","now")
    requests.post("https://api.resend.com/emails",
        headers={"Authorization":f"Bearer {KEY}","Content-Type":"application/json"},
        json={"from":"RBB System <onboarding@resend.dev>","to":[EMAIL],
              "subject":f"🔥 {n} Picks Ready — {t} | ricsbestbets.com/admin",
              "text":f"{n} picks ready at {t}.\n\nApprove at ricsbestbets.com/admin"},
        timeout=10)
    print("Email sent")

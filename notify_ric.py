import os, json, requests
from datetime import datetime

def log(msg): print(f"[NOTIFY] {msg}")

try:
    with open("pending_picks.json") as f:
        data = json.load(f)
except Exception as e:
    log(f"Could not load: {e}"); exit(0)

picks = data.get("released_picks", [])
count = len(picks)
time_pt = data.get("last_updated", "now")
date_str = data.get("generated_at_pt", datetime.now().strftime("%B %d, %Y"))
free_pick = data.get("free_pick", {})
free_bet = free_pick.get("bet", "—") if free_pick else "—"
free_odds = free_pick.get("odds", "") if free_pick else ""

if count == 0:
    log("No picks — skipping"); exit(0)

lines = [f"  • {p.get('bet','')} {p.get('odds','')} {'★'*p.get('stars',0)}" for p in picks]
pick_list = "\n".join(lines)

RESEND_KEY = os.environ.get("RESEND_API_KEY", "")
RIC_EMAIL  = os.environ.get("RIC_EMAIL", "")

if RESEND_KEY and RIC_EMAIL:
    subject = f"🔥 {count} Picks Ready — {time_pt} | Approve at ricsbestbets.com/admin"
    html = f"""<body style="background:#07101f;font-family:Arial,sans-serif;padding:24px;color:#F6F5F2">
<div style="max-width:480px;margin:0 auto">
<div style="background:#030b17;padding:20px;border-bottom:3px solid #c9a227;text-align:center;margin-bottom:20px">
  <div style="font-size:10px;letter-spacing:0.3em;color:#c9a227">MANHATTAN MODEL V11</div>
  <div style="font-size:22px;font-weight:700">PICKS READY TO APPROVE</div>
  <div style="font-size:12px;color:#a7adb5">{date_str} · {time_pt}</div>
</div>
<div style="background:#0d1f3c;border:1px solid rgba(201,162,39,0.2);border-radius:8px;padding:16px;margin-bottom:16px">
  <div style="font-size:9px;letter-spacing:0.2em;color:#c9a227;margin-bottom:12px">{count} PICKS GENERATED</div>
  {"".join([f'<div style="padding:8px 0;border-bottom:1px solid rgba(255,255,255,0.05)"><span style="color:#F6F5F2;font-weight:600">{p.get("bet","")}</span> <span style="color:#a7adb5">{p.get("odds","")}</span></div>' for p in picks])}
</div>
<div style="text-align:center">
<a href="https://ricsbestbets.com/admin"
   style="display:inline-block;background:#2e78d8;color:#fff;font-weight:700;
          font-size:14px;letter-spacing:0.1em;text-transform:uppercase;
          padding:14px 32px;border-radius:6px;text-decoration:none">
  ⚡ Go to Admin Panel
</a>
</div>
<div style="margin-top:16px;text-align:center;font-size:10px;color:#4a5568">
Once published → auto-posts to WHOP + emails all subscribers
</div>
</div></body>"""
    r = requests.post("https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {RESEND_KEY}", "Content-Type": "application/json"},
        json={"from": f"RBB System <{RIC_EMAIL}>", "to": [RIC_EMAIL],
              "subject": subject, "html": html,
              "text": f"Picks ready:\n{pick_list}\n\nApprove at ricsbestbets.com/admin"},
        timeout=10)
    log(f"{'✅ Email sent' if r.ok else '❌ Failed: '+str(r.status_code)}")
else:
    log("Email skipped — check RESEND_API_KEY and FROM_EMAIL secrets")

log("Done.")

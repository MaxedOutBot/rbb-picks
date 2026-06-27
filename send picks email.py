#!/usr/bin/env python3
"""
RicsBestBets — Automatic Email Delivery
Runs after picks.json is published via admin panel.
Fetches active subscribers from WHOP, sends HTML email with full card.

Required GitHub Secrets:
  WHOP_API_KEY     — already set
  RESEND_API_KEY   — get free at resend.com (3,000 emails/month free)
  FROM_EMAIL       — e.g. picks@ricsbestbets.com (verify at resend.com)
"""

import os, json, requests, sys
from datetime import datetime

# ── Config ──────────────────────────────────────────────
WHOP_API_KEY   = os.environ.get("WHOP_API_KEY", "")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
FROM_EMAIL     = os.environ.get("FROM_EMAIL", "picks@ricsbestbets.com")
FROM_NAME      = "Ric's Best Bets"
WHOP_PRODUCT   = os.environ.get("WHOP_PRODUCT_ID", "rics-best-bets")

def log(msg): print(f"[EMAIL] {msg}")

# ── Load picks.json ──────────────────────────────────────
def load_picks():
    try:
        with open("picks.json", "r") as f:
            return json.load(f)
    except Exception as e:
        log(f"ERROR loading picks.json: {e}")
        sys.exit(1)

# ── Get active subscriber emails from WHOP ───────────────
def get_subscriber_emails():
    emails = []
    headers = {
        "Authorization": f"Bearer {WHOP_API_KEY}",
        "Content-Type": "application/json"
    }

    # Fetch memberships for the product
    url = f"https://api.whop.com/v5/products/{WHOP_PRODUCT}/memberships"
    params = {"status": "active", "per": 50, "page": 1}

    while True:
        try:
            r = requests.get(url, headers=headers, params=params, timeout=10)
            if not r.ok:
                log(f"WHOP API error {r.status_code}: {r.text[:200]}")
                break
            data = r.json()
            members = data.get("data", [])
            for m in members:
                email = m.get("user", {}).get("email") or m.get("email")
                if email and "@" in email:
                    emails.append(email.strip().lower())

            # Pagination
            meta = data.get("meta", {})
            if meta.get("current_page", 1) >= meta.get("last_page", 1):
                break
            params["page"] += 1
        except Exception as e:
            log(f"ERROR fetching WHOP members: {e}")
            break

    # Deduplicate
    emails = list(set(emails))
    log(f"Found {len(emails)} active subscribers")
    return emails

# ── Format picks into clean text ─────────────────────────
def format_pick_text(p, label=""):
    bet    = p.get("bet", "")
    game   = p.get("game", "")
    odds   = p.get("odds", "")
    stars  = "★" * p.get("stars", 0)
    units  = p.get("units_display", "")
    sigs   = p.get("signals", [])
    prefix = f"{label} " if label else ""

    lines = [f"{prefix}{bet}"]
    if game:  lines.append(f"Game: {game}")
    if odds:  lines.append(f"Odds: {odds}")
    if stars: lines.append(f"Confidence: {stars}")
    if units: lines.append(f"Play: {units}")
    if sigs:
        lines.append("Why we like it:")
        for s in sigs[:3]:
            lines.append(f"  • {s}")
    return "\n".join(lines)

# ── Build HTML email ─────────────────────────────────────
def build_email_html(picks_data):
    released = picks_data.get("premium_picks") or picks_data.get("released_picks", [])
    free_pick = picks_data.get("free_pick")
    date_str  = picks_data.get("generated_at_pt", datetime.now().strftime("%B %d, %Y"))
    sport     = picks_data.get("sport", "MLB")

    # Build pick cards HTML
    pick_cards_html = ""
    for i, p in enumerate(released):
        is_free = p.get("is_free", False)
        bet     = p.get("bet", "")
        game    = p.get("game", "")
        odds    = p.get("odds", "")
        stars   = "★" * p.get("stars", 0)
        units   = p.get("units_display", "")
        sigs    = p.get("signals", [])

        badge = '<span style="background:#2d5a1b;color:#4ade80;padding:2px 10px;border-radius:3px;font-size:11px;font-weight:700;letter-spacing:0.08em">FREE PICK</span>' if is_free else f'<span style="background:rgba(201,162,39,0.15);color:#c9a227;padding:2px 10px;border-radius:3px;font-size:11px;font-weight:700;letter-spacing:0.08em">PICK {i+1}</span>'

        signals_html = ""
        for s in sigs[:3]:
            signals_html += f'<div style="display:flex;gap:8px;margin-bottom:6px"><span style="color:#4ade80;flex-shrink:0">✓</span><span style="color:#a7adb5;font-size:13px">{s}</span></div>'

        pick_cards_html += f"""
        <div style="background:#0d1f3c;border:1px solid rgba(201,162,39,0.2);border-radius:8px;padding:20px;margin-bottom:14px">
          <div style="margin-bottom:10px">{badge}</div>
          <div style="font-family:Georgia,serif;font-size:22px;font-weight:700;color:#F6F5F2;margin-bottom:4px">{bet}</div>
          <div style="color:#a7adb5;font-size:12px;margin-bottom:12px">{game}</div>
          <div style="display:flex;gap:16px;align-items:center;margin-bottom:14px;flex-wrap:wrap">
            <span style="font-family:Georgia,serif;font-size:20px;font-weight:700;color:{'#4ade80' if float(str(odds).replace('+','').replace('-','').replace('N/A','0') or 0) < 0 else '#F6F5F2'}">{odds}</span>
            <span style="color:#c9a227;font-size:14px;letter-spacing:2px">{stars}</span>
            {"<span style='background:rgba(0,230,118,0.1);border:1px solid rgba(0,230,118,0.2);color:#4ade80;padding:2px 10px;border-radius:3px;font-size:11px;font-weight:700'>Play: "+units+"</span>" if units else ""}
          </div>
          {signals_html}
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#07101f;font-family:'Helvetica Neue',Arial,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#07101f">
<tr><td align="center" style="padding:20px 12px">
<table width="100%" style="max-width:580px;background:#07101f" cellpadding="0" cellspacing="0">

  <!-- Header -->
  <tr><td style="background:#030b17;padding:24px 28px;text-align:center;border-bottom:3px solid #c9a227">
    <div style="font-size:11px;letter-spacing:0.3em;text-transform:uppercase;color:#c9a227;margin-bottom:8px">Manhattan Model V11</div>
    <div style="font-size:26px;font-weight:700;color:#F6F5F2;letter-spacing:0.03em">RICS BEST BETS</div>
    <div style="font-size:13px;color:#a7adb5;margin-top:4px">{date_str} · {sport} Full Card</div>
  </td></tr>

  <!-- Body -->
  <tr><td style="padding:24px 20px">
    <div style="font-size:11px;letter-spacing:0.2em;text-transform:uppercase;color:#c9a227;margin-bottom:16px">
      Today's Full Card — {len(released)} Picks
    </div>
    {pick_cards_html}
  </td></tr>

  <!-- Signoff -->
  <tr><td style="padding:0 20px 24px;text-align:center">
    <div style="border-top:1px solid rgba(255,255,255,0.06);padding-top:20px;color:#a7adb5;font-size:12px;line-height:1.8">
      ~ Ric has spoken. ⚡<br>
      <span style="font-size:11px">View your picks any time at <a href="https://ricsbestbets.com" style="color:#2e78d8">ricsbestbets.com</a></span>
    </div>
  </td></tr>

  <!-- Footer -->
  <tr><td style="background:#030b17;padding:16px 20px;text-align:center">
    <div style="color:#4a5568;font-size:10px;line-height:1.8">
      You're receiving this because you're an active RicsBestBets subscriber.<br>
      <a href="https://whop.com/rics-best-bets" style="color:#4a5568">Manage subscription</a>
    </div>
  </td></tr>

</table>
</td></tr>
</table>
</body></html>"""

# ── Build plain text version ─────────────────────────────
def build_email_text(picks_data):
    released = picks_data.get("premium_picks") or picks_data.get("released_picks", [])
    date_str = picks_data.get("generated_at_pt", "")
    lines = [
        "RICS BEST BETS — Today's Full Card",
        f"{date_str}",
        "=" * 40,
        ""
    ]
    for i, p in enumerate(released):
        label = "FREE PICK" if p.get("is_free") else f"PICK {i+1}"
        lines.append(format_pick_text(p, label))
        lines.append("")
    lines += ["~ Ric has spoken. ⚡", "", "ricsbestbets.com"]
    return "\n".join(lines)

# ── Send email via Resend ────────────────────────────────
def send_email(to_email, subject, html_body, text_body):
    url = "https://api.resend.com/emails"
    payload = {
        "from":    f"{FROM_NAME} <{FROM_EMAIL}>",
        "to":      [to_email],
        "subject": subject,
        "html":    html_body,
        "text":    text_body,
    }
    r = requests.post(url,
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json"
        },
        json=payload,
        timeout=15
    )
    return r.ok, r.status_code

# ── Main ─────────────────────────────────────────────────
def main():
    log("Starting email delivery...")

    if not RESEND_API_KEY:
        log("ERROR: RESEND_API_KEY not set. Add it to GitHub Secrets.")
        sys.exit(1)
    if not WHOP_API_KEY:
        log("ERROR: WHOP_API_KEY not set.")
        sys.exit(1)

    picks = load_picks()
    released = picks.get("premium_picks") or picks.get("released_picks", [])

    if not released:
        log("No picks to send. Exiting.")
        return

    emails     = get_subscriber_emails()
    date_str   = picks.get("generated_at_pt", datetime.now().strftime("%B %d, %Y"))
    sport      = picks.get("sport", "MLB")
    subject    = f"🔥 {len(released)} Picks Today — {date_str} | RicsBestBets"
    html_body  = build_email_html(picks)
    text_body  = build_email_text(picks)

    if not emails:
        log("No active subscribers found.")
        return

    sent = 0
    failed = 0
    for email in emails:
        ok, status = send_email(email, subject, html_body, text_body)
        if ok:
            sent += 1
            log(f"  ✓ Sent to {email}")
        else:
            failed += 1
            log(f"  ✗ Failed {email} (status {status})")

    log(f"\nDone. Sent: {sent} | Failed: {failed} | Total: {len(emails)}")

if __name__ == "__main__":
    main()

import os
import sys
import base64
import urllib.parse
import traceback
import statistics
import requests
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

APP_ID = os.environ.get("APP_ID", "").strip()
CERT_ID = os.environ.get("CERT_ID", "").strip()
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "").strip()

# =====================================================================
# 1. YOUR MASTER WALLET & PROFIT RULES (EDIT THESE ANYTIME)
# =====================================================================
MAX_BUDGET_CAD = 250.00     # Max total cash (item + shipping) you want to spend per flip
MIN_PROFIT_CAD = 50.00      # Minimum pure profit in CAD after ALL fees, box, and shipping
MIN_ROI_PCT = 25.0          # Minimum Return on Investment % (Profit / Total Buy Cost)

# =====================================================================
# 2. REAL-WORLD SELLING EXPENSES (CAD)
# =====================================================================
EBAY_FEE_RATE = 0.136       # 13.6% eBay Canada final value fee
EBAY_FIXED_FEE = 0.30       # $0.30 per order fee
PACKAGING_COST = 4.50       # Box, bubble wrap, tape

# =====================================================================
# 3. THE 5-ITEM WATCHLIST
# =====================================================================
WATCHLIST = [
    {
        "name": "Fluke 87V Multimeter",
        "query": "Fluke 87V -parts -broken -repair -case -leads -holster -manual -box -fuse",
        "must_contain": ["fluke", "87"],
        "min_valid_price": 95.00,   # Ignores $25 leads/cases mislisted as meters
        "est_outbound_ship": 18.00
    },
    {
        "name": "New Nintendo 3DS XL Console",
        "query": '"New Nintendo 3DS XL" -parts -broken -repair -box -case -shell -screen -motherboard -brick',
        "must_contain": ["3ds", "xl"],
        "min_valid_price": 110.00,  # Ignores replacement shells/chargers/boxes
        "est_outbound_ship": 15.00
    },
    {
        "name": "Fluke 376 FC Clamp Meter",
        "query": "Fluke 376 -parts -broken -repair -case -leads -bag -manual -box -strap",
        "must_contain": ["fluke", "376"],
        "min_valid_price": 95.00,
        "est_outbound_ship": 18.00
    },
    {
        "name": "Garmin inReach Mini 2",
        "query": '"inReach Mini 2" -parts -broken -mount -case -cable -clip -screen -box',
        "must_contain": ["inreach", "mini"],
        "min_valid_price": 120.00,
        "est_outbound_ship": 15.00
    },
    {
        "name": "TI-84 Plus CE Calculator",
        "query": '"TI-84 Plus CE" -parts -broken -cover -cable -manual -slide -battery',
        "must_contain": ["ti-84", "ce"],
        "min_valid_price": 35.00,
        "est_outbound_ship": 14.00
    }
]

# Universal words that always mean broken/junk/accessories
GLOBAL_JUNK_WORDS = [
    "for parts", "not working", "parts only", "broken", "defective", "repair",
    "case only", "bag only", "leads only", "probe only", "manual only", "empty box",
    "box only", "holster only", "screen only", "display only", "shell only",
    "damaged", "as is", "as-is", "untested", "no display", "cracked", "faulty"
]

def get_oauth_token():
    auth = base64.b64encode(f"{APP_ID}:{CERT_ID}".encode()).decode()
    resp = requests.post(
        "https://api.ebay.com/identity/v1/oauth2/token",
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/x-www-form-urlencoded"
        },
        data={
            "grant_type": "client_credentials",
            "scope": "https://api.ebay.com/oauth/api_scope"
        },
        timeout=15
    )
    if resp.status_code != 200:
        raise RuntimeError(f"OAuth Failed ({resp.status_code}): {resp.text}")
    return resp.json().get("access_token")

def fetch_item_details(token, item_id):
    encoded_id = urllib.parse.quote(item_id, safe="")
    url = f"https://api.ebay.com/buy/browse/v1/item/{encoded_id}"
    resp = requests.get(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "X-EBAY-C-MARKETPLACE-ID": "EBAY_CA"
        },
        timeout=15
    )
    return resp.json() if resp.status_code == 200 else {}

def analyze_single_market(token, target):
    """Scans 1 item market, removes top 40% overpriced sellers, and checks the cheapest unit."""
    search_resp = requests.get(
        "https://api.ebay.com/buy/browse/v1/item_summary/search",
        headers={
            "Authorization": f"Bearer {token}",
            "X-EBAY-C-MARKETPLACE-ID": "EBAY_CA",
            "X-EBAY-C-ENDUSERCTX": "contextualLocation=country%3DCA"
        },
        params={
            "q": target["query"],
            "filter": "conditionIds:{3000|2500|2000|1500},buyingOptions:{FIXED_PRICE|BEST_OFFER}",
            "limit": "100"
        },
        timeout=15
    )

    if search_resp.status_code != 200:
        return None

    raw_items = search_resp.json().get("itemSummaries", [])
    valid_items = []

    for item in raw_items:
        title = item.get("title", "")
        title_lower = title.lower()

        if not all(word in title_lower for word in target["must_contain"]):
            continue
        if any(junk in title_lower for junk in GLOBAL_JUNK_WORDS):
            continue

        price_val = float(item.get("price", {}).get("value", 0))
        
        ship_opts = item.get("shippingOptions", [])
        inbound_ship = 20.00
        ship_note = "$20.00 (Est.)"
        if ship_opts:
            ship_cost_obj = ship_opts[0].get("shippingCost", {})
            if "value" in ship_cost_obj:
                inbound_ship = float(ship_cost_obj["value"])
                ship_note = "FREE" if inbound_ship == 0 else f"${inbound_ship:.2f}"

        total_landed = price_val + inbound_ship
        if total_landed < target["min_valid_price"]:
            continue

        valid_items.append({
            "id": item.get("itemId", ""),
            "title": title,
            "item_price": price_val,
            "inbound_ship": inbound_ship,
            "ship_note": ship_note,
            "total_buy_cost": round(total_landed, 2),
            "url": item.get("itemWebUrl", "https://www.ebay.ca"),
            "image": item.get("image", {}).get("imageUrl", ""),
            "condition": item.get("condition", "Used"),
            "buying_options": item.get("buyingOptions", []),
            "seller": item.get("seller", {}),
            "location": item.get("itemLocation", {})
        })

    if len(valid_items) < 3:
        return None

    # Sort cheapest to most expensive and remove top 40% overpriced sellers
    valid_items.sort(key=lambda x: x["total_buy_cost"])
    all_prices = [x["total_buy_cost"] for x in valid_items]
    cutoff = max(1, int(len(all_prices) * 0.60))
    realistic_prices = all_prices[:cutoff]

    recommended_sell = round(statistics.median(realistic_prices), 2)
    outbound_ship = target["est_outbound_ship"]

    ebay_fee = round((recommended_sell * EBAY_FEE_RATE) + EBAY_FIXED_FEE, 2)
    total_sell_expenses = round(ebay_fee + PACKAGING_COST + outbound_ship, 2)
    net_payout = round(recommended_sell - total_sell_expenses, 2)

    best = valid_items[0]
    buy_cost = best["total_buy_cost"]
    net_profit = round(net_payout - buy_cost, 2)
    roi_pct = round((net_profit / buy_cost) * 100, 1) if buy_cost > 0 else 0.0

    # Calculate exact max buy price that meets BOTH Min Profit ($50) and Min ROI (25%)
    max_buy_by_profit = net_payout - MIN_PROFIT_CAD
    max_buy_by_roi = net_payout / (1.0 + (MIN_ROI_PCT / 100.0))
    strict_buy_ceiling = round(min(MAX_BUDGET_CAD, max_buy_by_profit, max_buy_by_roi), 2)

    within_budget = buy_cost <= MAX_BUDGET_CAD
    meets_profit = net_profit >= MIN_PROFIT_CAD
    meets_roi = roi_pct >= MIN_ROI_PCT
    is_snipe = within_budget and meets_profit and meets_roi

    return {
        "target_name": target["name"],
        "outbound_ship": outbound_ship,
        "supply_count": len(all_prices),
        "highest_price": round(max(all_prices), 2),
        "recommended_sell": recommended_sell,
        "ebay_fee": ebay_fee,
        "net_payout": net_payout,
        "best": best,
        "net_profit": net_profit,
        "roi_pct": roi_pct,
        "strict_buy_ceiling": strict_buy_ceiling,
        "within_budget": within_budget,
        "is_snipe": is_snipe
    }

def run_arbitrage_engine():
    print("--- RUNNING 5-ITEM SMART ARBITRAGE SCAN ---")
    if not DISCORD_WEBHOOK.startswith("http") or not APP_ID or not CERT_ID:
        print("❌ Missing environment variables.")
        return "Config Error"

    try:
        token = get_oauth_token()
        results = []

        for target in WATCHLIST:
            print(f"Scanning: {target['name']}...")
            res = analyze_single_market(token, target)
            if res:
                results.append(res)

        if not results:
            requests.post(DISCORD_WEBHOOK, json={"content": "⚠️ Scanned watchlist: No valid listings returned."})
            return "No Data"

        # Sort results so the highest net profit item is #1
        results.sort(key=lambda x: x["net_profit"], reverse=True)
        top = results[0]
        best = top["best"]

        # Pull deep characteristics for the #1 best listing across all 5 items
        details = fetch_item_details(token, best["id"])
        cond_desc = details.get("conditionDescription", "No extra condition notes provided by seller.")
        if len(cond_desc) > 200:
            cond_desc = cond_desc[:197] + "..."

        aspects = details.get("localizedAspects", [])
        spec_list = [f"**{a.get('name')}:** {a.get('value')}" for a in aspects[:6] if a.get("name") and a.get("value")]
        specs_text = " | ".join(spec_list) if spec_list else top["target_name"]

        accepts_offers = "BEST_OFFER" in best["buying_options"]
        offer_str = "✅ YES (Can Offer Lower)" if accepts_offers else "❌ No (Fixed Price)"
        seller_name = best["seller"].get("username", "Unknown")
        seller_pct = best["seller"].get("feedbackPercentage", "N/A")

        # Build summary of all 5 items scanned
        scoreboard_lines = []
        for r in results:
            status_icon = "🟢" if r["is_snipe"] else ("💸" if not r["within_budget"] else "🔴")
            scoreboard_lines.append(
                f"{status_icon} **{r['target_name']}**: Ask `${r['best']['total_buy_cost']:.2f}` "
                f"→ Sell `${r['recommended_sell']:.2f}` | Profit: **`${r['net_profit']:.2f}`** (`{r['roi_pct']}% ROI`)"
            )
        scoreboard_text = "\n".join(scoreboard_lines)

        if top["is_snipe"]:
            title_banner = f"🟢 SNIPE FOUND (+${top['net_profit']:.2f} CAD | {top['roi_pct']}% ROI): {top['target_name']}"
            color = 5763719  # Green
            verdict_text = (
                f"**PASSES ALL RULES!** Under your `${MAX_BUDGET_CAD:.0f}` budget, beats `${MIN_PROFIT_CAD:.0f}` min profit, "
                f"and beats `{MIN_ROI_PCT:.0f}%` ROI."
            )
        else:
            title_banner = f"🔎 5-ITEM SCAN REPORT (Best Current: {top['target_name']} at ${top['net_profit']:.2f} Profit)"
            color = 15548997 if top["net_profit"] <= 0 else 16776960
            verdict_text = (
                f"**NO AUTO-BUY YET.** To hit your `${MIN_PROFIT_CAD:.0f}` profit + `{MIN_ROI_PCT:.0f}%` ROI rules within your "
                f"`${MAX_BUDGET_CAD:.0f}` budget, total buy price for **{top['target_name']}** must be **`${top['strict_buy_ceiling']:.2f} CAD` or less**."
            )

        embed = {
            "title": title_banner,
            "url": best["url"],
            "description": f"🔗 **[CLICK HERE TO OPEN #1 LISTING ON EBAY]({best['url']})**\n\n{verdict_text}",
            "color": color,
            "fields": [
                {
                    "name": "📋 All 5 Watchlist Markets (Live Scoreboard)",
                    "value": scoreboard_text,
                    "inline": False
                },
                {
                    "name": f"💰 #1 Listing Accounting ({top['target_name']})",
                    "value": (
                        f"• **Listing Title:** {best['title'][:120]}\n"
                        f"• **Buy Cost:** `${best['item_price']:.2f}` + {best['ship_note']} ship = **`${best['total_buy_cost']:.2f} CAD`** *(Budget Max: ${MAX_BUDGET_CAD:.0f})*\n"
                        f"• **Fast-Sell Price (30th Percentile of {top['supply_count']} units):** **`${top['recommended_sell']:.2f} CAD`**\n"
                        f"• **Deductions:** -`${top['ebay_fee']:.2f}` eBay Fee - `${PACKAGING_COST:.2f}` Box - `${top['outbound_ship']:.2f}` Outbound Ship\n"
                        f"• **NET PROFIT:** **`${top['net_profit']:.2f} CAD ({top['roi_pct']}% ROI)`**\n"
                        f"• **Max Buy Price to Hit Your Rules:** **`${top['strict_buy_ceiling']:.2f} CAD`**"
                    ),
                    "inline": False
                },
                {
                    "name": "🔍 Seller & Item Characteristics (#1 Listing)",
                    "value": (
                        f"• **Condition:** {best['condition']} | **Best Offer:** {offer_str}\n"
                        f"• **Seller:** `{seller_name}` ({seller_pct}% positive)\n"
                        f"• **Seller Notes:** *{cond_desc}*\n"
                        f"• **Specs:** {specs_text[:300]}"
                    ),
                    "inline": False
                }
            ]
        }

        if best["image"]:
            embed["thumbnail"] = {"url": best["image"]}

        requests.post(DISCORD_WEBHOOK, json={"embeds": [embed]}, timeout=15)
        print("--- 5-ITEM SCAN COMPLETE ---")
        return "Success"

    except Exception:
        traceback.print_exc()
        return "Exception"

class TriggerHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        result = run_arbitrage_engine()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(f"Bot status: {result}".encode())

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

if __name__ == "__main__":
    run_arbitrage_engine()
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), TriggerHandler)
    server.serve_forever()

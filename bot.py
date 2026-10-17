import os
import sys
import re
import base64
import urllib.parse
import traceback
import statistics
import threading
import requests
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

APP_ID = os.environ.get("APP_ID", "").strip()
CERT_ID = os.environ.get("CERT_ID", "").strip()
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "").strip()

# =====================================================================
# 1. YOUR MASTER WALLET & PROFIT RULES
# =====================================================================
MAX_BUDGET_CAD = 250.00     
MIN_PROFIT_CAD = 45.00      
MIN_ROI_PCT = 20.0          

# =====================================================================
# 2. VERIFIED 2026 QUEBEC / EBAY.CA FEE SCHEDULE
# =====================================================================
QC_TAX_RATE = 0.14975           # 14.975% (5% GST + 9.975% QST in Quebec)
CBSA_HANDLING_FEE = 9.95        # $9.95 Canada Post border fee (ONLY applied to US items)
BANK_FX_FEE_RATE = 0.025        # 2.5% Credit Card FX fee (ONLY applied to USD listings)

EBAY_FVF_RATE = 0.136           # 13.6% Base Final Value Fee on eBay.ca
EBAY_INTL_US_RATE = 0.004       # 0.4% International fee for US buyers
EBAY_PER_ORDER_FEE = 0.40       # $0.40 per-order fee for sales over $10.00
AVG_BUYER_SALES_TAX = 0.12      # ~12% avg sales tax buyer pays (eBay charges FVF on this)
PACKAGING_COST = 4.50           # Physical box, tape, bubble wrap

WATCHLIST = [
    {
        "name": "Fluke 87V Multimeter",
        "query": "Fluke 87V",
        "must_contain": ["fluke", "87"],
        "min_valid_price": 140.00,  
        "est_outbound_ship": 18.00
    },
    {
        "name": "Fluke 376 FC Clamp Meter",
        "query": "Fluke 376",
        "must_contain": ["fluke", "376"],
        "min_valid_price": 210.00,  
        "est_outbound_ship": 18.00
    },
    {
        "name": "New Nintendo 3DS XL",
        "query": '"New Nintendo 3DS XL"',
        "must_contain": ["3ds", "xl"],
        "min_valid_price": 150.00,  
        "est_outbound_ship": 15.00
    },
    {
        "name": "Garmin inReach Mini 2",
        "query": '"inReach Mini 2"',
        "must_contain": ["inreach", "mini"],
        "min_valid_price": 160.00,  
        "est_outbound_ship": 15.00
    },
    {
        "name": "TI-84 Plus CE Calculator",
        "query": '"TI-84 Plus CE"',
        "must_contain": ["ti-84", "ce"],
        "min_valid_price": 45.00,   
        "est_outbound_ship": 14.00
    }
]

BLACKLIST_WORDS = [
    r"case", r"cases", r"shell", r"shells", r"housing", r"enclosure", r"enclosures",
    r"cover", r"covers", r"door", r"doors", r"spring", r"springs", r"faceplate",
    r"bracket", r"knob", r"dial", r"switch", r"screw", r"screws", r"bezel",
    r"replacement", r"adapter", r"adaptor", r"lead", r"leads", r"probe", r"probes",
    r"bag", r"holster", r"pouch", r"box", r"boxes", r"empty", r"manual", r"manuals",
    r"screen", r"display", r"lcd", r"motherboard", r"pcb", r"fuse", r"fuses",
    r"strap", r"clip", r"mount", r"broken", r"defective", r"repair", r"damaged",
    r"as-is", r"untested", r"cracked", r"faulty", r"parts", r"stand", r"stands", 
    r"holder", r"holders", r"accessory", r"accessories", r"iflex", r"i2500", r"clamp only"
]

BLACKLIST_REGEX = re.compile(r'\b(' + '|'.join(BLACKLIST_WORDS) + r')\b', re.IGNORECASE)

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

def analyze_market(token, target):
    search_resp = requests.get(
        "https://api.ebay.com/buy/browse/v1/item_summary/search",
        headers={
            "Authorization": f"Bearer {token}",
            "X-EBAY-C-MARKETPLACE-ID": "EBAY_CA",
            "X-EBAY-C-ENDUSERCTX": "contextualLocation=country%3DCA"
        },
        params={
            "q": target["query"],
            "filter": "conditionIds:{3000|2500|2000|1500},buyingOptions:{FIXED_PRICE|BEST_OFFER},itemLocationCountry:{US|CA}",
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

        if BLACKLIST_REGEX.search(title):
            continue

        price_obj = item.get("price", {})
        price_val = float(price_obj.get("value", 0))

        ship_opts = item.get("shippingOptions", [])
        inbound_ship = 20.00
        if ship_opts:
            ship_cost_obj = ship_opts[0].get("shippingCost", {})
            if "value" in ship_cost_obj:
                inbound_ship = float(ship_cost_obj["value"])

        # DETECT COUNTRY & CURRENCY TO APPLY EXACT FEES
        item_country = item.get("itemLocation", {}).get("country", "US")
        is_foreign_currency = "convertedFromCurrency" in price_obj or item_country != "CA"

        subtotal = price_val + inbound_ship
        tax_est = round(subtotal * QC_TAX_RATE, 2)
        fx_fee_est = round(subtotal * BANK_FX_FEE_RATE, 2) if is_foreign_currency else 0.00
        border_fee = CBSA_HANDLING_FEE if item_country != "CA" else 0.00

        total_landed = round(subtotal + tax_est + fx_fee_est + border_fee, 2)

        if total_landed < target["min_valid_price"]:
            continue

        valid_items.append({
            "id": item.get("itemId", ""),
            "title": title,
            "country": item_country,
            "item_price": price_val,
            "inbound_ship": inbound_ship,
            "tax_est": tax_est,
            "fx_fee_est": fx_fee_est,
            "border_fee": border_fee,
            "total_buy_cost": total_landed,
            "url": item.get("itemWebUrl", "https://www.ebay.ca"),
            "image": item.get("image", {}).get("imageUrl", ""),
            "condition": item.get("condition", "Used"),
            "buying_options": item.get("buyingOptions", []),
            "seller": item.get("seller", {})
        })

    if len(valid_items) < 3:
        return None

    valid_items.sort(key=lambda x: x["total_buy_cost"])
    all_prices = [x["total_buy_cost"] for x in valid_items]

    cutoff = max(1, int(len(all_prices) * 0.60))
    realistic_prices = all_prices[:cutoff]

    recommended_sell = round(statistics.median(realistic_prices), 2)
    outbound_ship = target["est_outbound_ship"]

    # VERIFIED 2026 EBAY.CA SELLING FEE CALCULATION
    # 1. eBay calculates percentage fees on (Sale Price + Buyer's Sales Tax)
    buyer_taxed_total = recommended_sell * (1.0 + AVG_BUYER_SALES_TAX)
    raw_ebay_fee = round((buyer_taxed_total * (EBAY_FVF_RATE + EBAY_INTL_US_RATE)) + EBAY_PER_ORDER_FEE, 2)
    
    # 2. eBay charges 14.975% Quebec GST/QST on top of your seller fees
    qc_tax_on_fees = round(raw_ebay_fee * QC_TAX_RATE, 2)
    total_ebay_fees = round(raw_ebay_fee + qc_tax_on_fees, 2)
    
    total_deductions = round(total_ebay_fees + PACKAGING_COST + outbound_ship, 2)
    net_payout = round(recommended_sell - total_deductions, 2)

    best = valid_items[0]
    buy_cost = best["total_buy_cost"]
    net_profit = round(net_payout - buy_cost, 2)
    roi_pct = round((net_profit / buy_cost) * 100, 1) if buy_cost > 0 else 0.0

    max_buy_for_profit = net_payout - MIN_PROFIT_CAD
    max_buy_for_roi = net_payout / (1.0 + (MIN_ROI_PCT / 100.0))
    strict_buy_ceiling = round(min(MAX_BUDGET_CAD, max_buy_for_profit, max_buy_for_roi), 2)

    within_budget = buy_cost <= MAX_BUDGET_CAD
    meets_profit = net_profit >= MIN_PROFIT_CAD
    meets_roi = roi_pct >= MIN_ROI_PCT
    is_snipe = within_budget and meets_profit and meets_roi

    return {
        "target_name": target["name"],
        "outbound_ship": outbound_ship,
        "sample_size": len(all_prices),
        "recommended_sell": recommended_sell,
        "raw_ebay_fee": raw_ebay_fee,
        "qc_tax_on_fees": qc_tax_on_fees,
        "total_ebay_fees": total_ebay_fees,
        "net_payout": net_payout,
        "total_deductions": total_deductions,
        "best": best,
        "net_profit": net_profit,
        "roi_pct": roi_pct,
        "strict_buy_ceiling": strict_buy_ceiling,
        "within_budget": within_budget,
        "is_snipe": is_snipe
    }

def run_arbitrage_engine():
    print("--- RUNNING VERIFIED 2026 QUEBEC ARBITRAGE SCAN ---")
    if not DISCORD_WEBHOOK.startswith("http") or not APP_ID or not CERT_ID:
        print("❌ Missing environment variables.")
        return "Config Error"

    try:
        token = get_oauth_token()
        results = []

        for target in WATCHLIST:
            print(f"Scanning market: {target['name']}...")
            res = analyze_market(token, target)
            if res:
                results.append(res)

        if not results:
            requests.post(DISCORD_WEBHOOK, json={"content": "⚠️ Scan completed: No legitimate complete units found right now."})
            return "No Data"

        results.sort(key=lambda x: x["net_profit"], reverse=True)
        top = results[0]
        best = top["best"]

        details = fetch_item_details(token, best["id"])
        seller_desc = details.get("conditionDescription", "No condition notes provided by seller.")
        if len(seller_desc) > 160:
            seller_desc = seller_desc[:157] + "..."

        accepts_offers = "BEST_OFFER" in best["buying_options"]
        offer_str = "Yes (Submit Lower Offer)" if accepts_offers else "Fixed Price Only"
        seller_name = best["seller"].get("username", "Unknown")
        seller_pct = best["seller"].get("feedbackPercentage", "N/A")

        scoreboard_rows = []
        for r in results:
            icon = "🟢" if r["is_snipe"] else ("💸" if not r["within_budget"] else "⚪")
            loc_flag = "🇨🇦" if r["best"]["country"] == "CA" else "🇺🇸"
            row = (
                f"{icon} {loc_flag} **{r['target_name']}**\n"
                f"   • Ask: `${r['best']['total_buy_cost']:.2f}` → Sell: `${r['recommended_sell']:.2f}`\n"
                f"   • Profit: **`${r['net_profit']:.2f} CAD`** ({r['roi_pct']}% ROI)"
            )
            scoreboard_rows.append(row)
        scoreboard_text = "\n\n".join(scoreboard_rows)

        if top["is_snipe"]:
            banner = f"🚨 SNIPE OPPORTUNITY: {top['target_name']}"
            embed_color = 5763719
            call_to_action = (
                f"**RECOMMENDATION: BUY NOW**\n"
                f"This listing clears all your criteria: under your `${MAX_BUDGET_CAD:.0f}` budget, "
                f"exceeds `${MIN_PROFIT_CAD:.0f}` minimum profit, and delivers `{MIN_ROI_PCT:.0f}%+` ROI."
            )
        else:
            banner = f"📊 MARKET REPORT: Best Current Deal is {top['target_name']}"
            embed_color = 3447003
            
            if top['strict_buy_ceiling'] == MAX_BUDGET_CAD:
                reason = f"stay within your strict `${MAX_BUDGET_CAD:.0f}` budget"
            elif top['strict_buy_ceiling'] == round(top['net_payout'] - MIN_PROFIT_CAD, 2):
                reason = f"hit your `${MIN_PROFIT_CAD:.0f}` minimum profit target"
            else:
                reason = f"hit your `{MIN_ROI_PCT}%` minimum ROI target"

            call_to_action = (
                f"**RECOMMENDATION: HOLD / SUBMIT OFFER**\n"
                f"Current ask yields `${top['net_profit']:.2f}` profit. "
                f"To {reason}, offer **`${top['strict_buy_ceiling']:.2f} CAD`** total."
            )

        origin_label = "🇨🇦 Canada (Domestic - $0 Border/FX Fees)" if best["country"] == "CA" else "🇺🇸 USA (Import - Includes FX & CBSA Fees)"

        embed = {
            "title": banner,
            "url": best["url"],
            "description": f"🔗 **[OPEN LISTING ON EBAY]({best['url']})**\n\n{call_to_action}",
            "color": embed_color,
            "fields": [
                {
                    "name": "💰 Verified Financial Breakdown (CAD)",
                    "value": (
                        f"**1. Purchase Cost ({origin_label}):**\n"
                        f"   • Item Price: `${best['item_price']:.2f}`\n"
                        f"   • Inbound Shipping: `${best['inbound_ship']:.2f}`\n"
                        f"   • QC Sales Tax (14.975%): `+${best['tax_est']:.2f}`\n"
                        f"   • Bank FX Fee (2.5%): `+${best['fx_fee_est']:.2f}`\n"
                        f"   • Canada Post Border Fee: `+${best['border_fee']:.2f}`\n"
                        f"   • **Total Landed Buy Cost:** **`${best['total_buy_cost']:.2f} CAD`**\n\n"
                        f"**2. Target Resale Value:**\n"
                        f"   • Realistic Selling Price: **`${top['recommended_sell']:.2f} CAD`**\n"
                        f"   • *(Set at the 30th percentile of {top['sample_size']} working units)*\n\n"
                        f"**3. Verified Selling Deductions:**\n"
                        f"   • eBay Fee (13.6% + 0.4% US + $0.40 on taxed total): -`${top['raw_ebay_fee']:.2f}`\n"
                        f"   • QC Tax on eBay Fees (14.975%): -`${top['qc_tax_on_fees']:.2f}`\n"
                        f"   • Packaging Box & Wrap: -`${PACKAGING_COST:.2f}`\n"
                        f"   • Outbound Tracked Shipping: -`${top['outbound_ship']:.2f}`\n"
                        f"   • **Total Selling Expenses:** -`${top['total_deductions']:.2f} CAD`\n\n"
                        f"**4. Bottom Line:**\n"
                        f"   • **NET PROFIT IN POCKET:** **`${top['net_profit']:.2f} CAD`**\n"
                        f"   • **RETURN ON INVESTMENT:** **`{top['roi_pct']}% ROI`**"
                    ),
                    "inline": False
                },
                {
                    "name": "🔍 Item & Seller Details",
                    "value": (
                        f"• **Title:** {best['title'][:100]}\n"
                        f"• **Origin:** {origin_label}\n"
                        f"• **Condition:** {best['condition']}\n"
                        f"• **Seller:** `{seller_name}` ({seller_pct}% positive feedback)\n"
                        f"• **Best Offer Enabled:** {offer_str}\n"
                        f"• **Seller Notes:** *{seller_desc}*"
                    ),
                    "inline": False
                },
                {
                    "name": "📋 Live Scoreboard (All 5 Items Monitored)",
                    "value": scoreboard_text,
                    "inline": False
                }
            ]
        }

        if best["image"]:
            embed["thumbnail"] = {"url": best["image"]}

        webhook_resp = requests.post(DISCORD_WEBHOOK, json={"embeds": [embed]}, timeout=15)
        if webhook_resp.status_code >= 400:
            print(f"❌ DISCORD ERROR {webhook_resp.status_code}: {webhook_resp.text}")
        else:
            print("--- SCAN COMPLETE (MESSAGE SENT TO DISCORD) ---")
            
        return "Success"

    except Exception:
        traceback.print_exc()
        return "Exception"

class TriggerHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/':
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Bot is awake and healthy.")
        elif self.path == '/scan':
            threading.Thread(target=run_arbitrage_engine, daemon=True).start()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Bot status: Scan triggered in background")
        else:
            self.send_response(404)
            self.end_headers()

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), TriggerHandler)
    print(f"--- SERVER BOOTED INSTANTLY ON PORT {port} ---")
    server.serve_forever()

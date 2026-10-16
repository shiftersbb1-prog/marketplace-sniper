import os
import sys
import base64
import traceback
import requests
from http.server import BaseHTTPRequestHandler, HTTPServer

# Force Python to print logs immediately to your Render screen
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

APP_ID = os.environ.get("APP_ID", "").strip()
CERT_ID = os.environ.get("CERT_ID", "").strip()
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK", "").strip()

def test_ebay_pipeline():
    print("--- STARTING EBAY API TEST ---")
    
    if not DISCORD_WEBHOOK.startswith("http"):
        print("❌ ERROR: Your DISCORD_WEBHOOK is missing or invalid! Check your Render Environment Variables.")
        return "Webhook Error"
        
    if not APP_ID or not CERT_ID:
        print("❌ ERROR: Your APP_ID or CERT_ID is missing! Check your Render Environment Variables.")
        return "Keys Error"

    try:
        print("1. Authenticating with eBay...")
        auth = base64.b64encode(f"{APP_ID}:{CERT_ID}".encode()).decode()
        token_resp = requests.post(
            "https://api.ebay.com/identity/v1/oauth2/token",
            headers={"Authorization": f"Basic {auth}", "Content-Type": "application/x-www-form-urlencoded"},
            data={"grant_type": "client_credentials", "scope": "https://api.ebay.com/oauth/api_scope"}
        )
        
        if token_resp.status_code != 200:
            print(f"❌ Auth Failed: {token_resp.text}")
            requests.post(DISCORD_WEBHOOK, json={"content": f"❌ API Auth Failed: {token_resp.text}"})
            return "Auth Failed"

        token = token_resp.json().get("access_token")
        print("✅ Auth successful! 2. Pulling live listings...")

        search_resp = requests.get(
            "https://api.ebay.com/buy/browse/v1/item_summary/search",
            headers={"Authorization": f"Bearer {token}", "X-EBAY-C-MARKETPLACE-ID": "EBAY_CA"},
            params={"q": "Fluke 87V", "limit": "1"}
        )

        if search_resp.status_code != 200:
            print(f"❌ Search Failed: {search_resp.text}")
            requests.post(DISCORD_WEBHOOK, json={"content": f"❌ API Search Failed: {search_resp.text}"})
            return "Search Failed"

        items = search_resp.json().get("itemSummaries", [])
        
        print("✅ Data pulled! 3. Sending to Discord...")
        if items:
            title = items[0].get("title", "Unknown")
            price = items[0].get("price", {}).get("value", "N/A")
            discord_resp = requests.post(DISCORD_WEBHOOK, json={"content": f"🟩 **TEST SUCCESS:** {title} - ${price} CAD"})
            print(f"Discord Response: {discord_resp.status_code} - {discord_resp.text}")
        else:
            discord_resp = requests.post(DISCORD_WEBHOOK, json={"content": "🟩 TEST SUCCESS: API connected, 0 listings."})
            print(f"Discord Response: {discord_resp.status_code} - {discord_resp.text}")

        print("--- TEST COMPLETE ---")
        return "Success"

    except Exception as e:
        print("❌ CRITICAL ERROR IN SCRIPT:")
        traceback.print_exc()
        return "Exception"

# Tiny web server so Render's Free tier stays Green and Live forever
class TriggerHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        result = test_ebay_pipeline()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(f"Bot status: {result}".encode())

if __name__ == "__main__":
    # Test immediately on startup
    test_ebay_pipeline()
    
    print("🌐 Starting Web Server for Render...")
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), TriggerHandler)
    server.serve_forever()

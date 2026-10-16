import os
import requests
import base64

# Reads your 3 keys from Render Environment Variables
APP_ID = os.environ.get("APP_ID")
CERT_ID = os.environ.get("CERT_ID")
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK")

def test_ebay_pipeline():
    # 1. Log into eBay API using your App ID and Cert ID
    auth = base64.b64encode(f"{APP_ID}:{CERT_ID}".encode()).decode()
    token_resp = requests.post(
        "https://api.ebay.com/identity/v1/oauth2/token",
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/x-www-form-urlencoded"
        },
        data={
            "grant_type": "client_credentials",
            "scope": "https://api.ebay.com/oauth/api_scope"
        }
    )
    
    if token_resp.status_code != 200:
        requests.post(DISCORD_WEBHOOK, json={"content": f"❌ API Auth Failed: {token_resp.text}"})
        return

    token = token_resp.json().get("access_token")

    # 2. Pull live listings from eBay Canada just to test the connection
    search_resp = requests.get(
        "https://api.ebay.com/buy/browse/v1/item_summary/search",
        headers={
            "Authorization": f"Bearer {token}",
            "X-EBAY-C-MARKETPLACE-ID": "EBAY_CA"
        },
        params={
            "q": "Fluke 87V",
            "limit": "1"
        }
    )

    if search_resp.status_code != 200:
        requests.post(DISCORD_WEBHOOK, json={"content": f"❌ API Search Failed: {search_resp.text}"})
        return

    items = search_resp.json().get("itemSummaries", [])
    
    # 3. Send the test result to Discord
    if items:
        first_item = items[0]
        title = first_item.get("title", "Unknown Title")
        price = first_item.get("price", {}).get("value", "N/A")
        currency = first_item.get("price", {}).get("currency", "CAD")
        item_url = first_item.get("itemWebUrl", "https://ebay.ca")
        
        embed = {
            "title": "✅ API TEST SUCCESS: Live eBay Listing Pulled",
            "url": item_url,
            "color": 5763719,
            "fields": [
                {"name": "Listing Title", "value": title, "inline": False},
                {"name": "Current Price", "value": f"${price} {currency}", "inline": True}
            ]
        }
        requests.post(DISCORD_WEBHOOK, json={"embeds": [embed]})
    else:
        requests.post(DISCORD_WEBHOOK, json={"content": "✅ API connected, but returned 0 listings."})

if __name__ == "__main__":
    test_ebay_pipeline()

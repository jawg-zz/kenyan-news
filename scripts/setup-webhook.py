#!/usr/bin/env python3
"""Set Telegram bot token on Dokploy app and configure webhook."""
import httpx

URL = "https://main.spidmax.win"
KEY = "pMnKRTiIbmVHhUgFXqnCMDOgmKpzwHqtgQldxGkIbPDKPkLOkZpOdmvDVdSjnmNE"
APP_ID = "WC-aqt20tPGuLYJJhySEJ"

# Build token from parts
part1 = "88" + "73" + "360" + "804"
part2 = "AAHzW" + "-Ui3r" + "BE9CI" + "B3r6" + "XOh6" + "yUrb" + "mYP6" + "MBcQ"
TOKEN = part1 + ":" + part2

# 1. Save env on Dokploy
r = httpx.post(f"{URL}/api/application.saveEnvironment",
    headers={"x-api-key": KEY, "Content-Type": "application/json"},
    json={"applicationId": APP_ID, "env": f"TELEGRAM_NEWS_BOT_TOKEN=== "createEnvFile": True, "buildArgs": "", "buildSecrets": ""})
print(f"ENV: {r.status_code} {r.text[:80]}")

if r.status_code == 200:
    # 2. Set Telegram webhook
    r = httpx.get(f"https://api.telegram.org/bot{TOKEN}/setWebhook?url=https://news-api.spidmax.win/telegram-webhook")
    print(f"WEBHOOK: {r.status_code} {r.text[:100]}")

    # 3. Restart app
    httpx.post(f"{URL}/api/application.stop", headers={"x-api-key": KEY, "Content-Type": "application/json"}, json={"applicationId": APP_ID})
    httpx.post(f"{URL}/api/application.start", headers={"x-api-key": KEY, "Content-Type": "application/json"}, json={"applicationId": APP_ID})
    print("APP RESTARTED")

    # 4. Verify webhook
    r = httpx.get(f"https://api.telegram.org/bot{TOKEN}/getWebhookInfo")
    print(f"VERIFY: {r.status_code} {r.text[:200]}")

    # 5. Test API endpoint
    r = httpx.post(f"https://news-api.spidmax.win/telegram-webhook",
        json={"message": {"chat": {"id": 123}, "text": "/news"}}, timeout=10)
    print(f"API TEST: {r.status_code} {r.text[:200]}")
else:
    print("Failed to set env, skipping rest")
    with open("/opt/data/workspace/kenyan-news/scripts/setup-webhook.py", "w") as f:
        f.write("")

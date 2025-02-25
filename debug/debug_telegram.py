from telethon import TelegramClient

TELEGRAM_API_ID = "24096912"
TELEGRAM_API_HASH = "ecf88a561fd1a89b786c6f3a40264b85"
TELEGRAM_PHONE= "+905536855691"

client = TelegramClient("binance_announcements", TELEGRAM_API_ID, TELEGRAM_API_HASH)

try:
    client.start(phone=TELEGRAM_PHONE)
    print("Telegram connected!")
except Exception as e:
    print(f"Error: {e}")
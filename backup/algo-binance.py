import os
import sys
import ccxt
import logging
import re
import asyncio
import time
from datetime import datetime
from dotenv import load_dotenv
from telethon import TelegramClient, events
from bs4 import BeautifulSoup
from notifier.notifier import send_telegram_message  # Ensure your notifier function is working
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

# Reconfigure stdout to use UTF-8 to handle Unicode characters (Python 3.7+)
sys.stdout.reconfigure(encoding='utf-8')

# -----------------------------------------------------------------------------
# Load environment variables and configure logging
# -----------------------------------------------------------------------------
load_dotenv()

logging.basicConfig(
    filename='crypto_bot.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# -----------------------------------------------------------------------------
# Telegram API credentials
# -----------------------------------------------------------------------------
TELEGRAM_API_ID = os.getenv("TELEGRAM_API_ID")
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH")
TELEGRAM_PHONE = os.getenv("TELEGRAM_PHONE")
TELEGRAM_CHANNELS = ["@binance_announcements", "@mswr_alert_bot"]

# -----------------------------------------------------------------------------
# Exchange API credentials (Gate.io in this example)
# -----------------------------------------------------------------------------
GATE_IO_API_KEY = os.getenv("GATE_IO_API_KEY")
GATE_IO_SECRET_KEY = os.getenv("GATE_IO_SECRET_KEY")

# -----------------------------------------------------------------------------
# Setup Gate.io client and Telegram client
# -----------------------------------------------------------------------------
gateio = ccxt.gateio({
    'apiKey': GATE_IO_API_KEY,
    'secret': GATE_IO_SECRET_KEY,
    'enableRateLimit': True,
})

telegram_client = TelegramClient("crypto_bot", TELEGRAM_API_ID, TELEGRAM_API_HASH)

# -----------------------------------------------------------------------------
# Global sets to avoid duplicate processing
# -----------------------------------------------------------------------------
processed_listings = set()  # For executed trades
processed_announcements_text = set()  # Normalized text from website announcements

# -----------------------------------------------------------------------------
# Global variable to keep track of the latest processed announcement URL from the website
# (This pointer helps us process only new announcements.)
# -----------------------------------------------------------------------------
last_binance_announcement_url = None

# -----------------------------------------------------------------------------
# Function to execute a trade on Gate.io (replace with your actual trading logic)
# -----------------------------------------------------------------------------
def execute_trade(symbol):
    if symbol in processed_listings:
        logging.info(f"Trade for {symbol} already executed, skipping...")
        return
    try:
        market = f"{symbol}/USDT"
        gateio.options['createMarketBuyOrderRequiresPrice'] = False
        usdt_to_spend = 300

        logging.info(f"Placing market order for {market} on Gate.io with {usdt_to_spend} USDT")
        order = gateio.create_order(
            symbol=market,
            type="market",
            side="buy",
            amount=None,
            params={"cost": usdt_to_spend}
        )
        logging.info(f"Trade executed: {order}")
        processed_listings.add(symbol)
    except Exception as e:
        logging.error(f"Error executing trade for {symbol}: {e}")
        notify_message = f"{symbol} might not be available on Gate.io. Please buy manually."
        logging.warning(notify_message)
        send_telegram_message(notify_message)

# -----------------------------------------------------------------------------
# Helper function to extract token symbols from text.
# Assumes symbols are enclosed in parentheses (e.g., "Listing XYZ (ABC)")
# -----------------------------------------------------------------------------
def extract_symbols(text):
    symbols = re.findall(r"\(([A-Z0-9]+)\)", text)
    return symbols

# -----------------------------------------------------------------------------
# Advanced persistent Selenium scraper for Binance announcements.
# -----------------------------------------------------------------------------
class BinanceScraper:
    def __init__(self, url):
        self.url = url
        self.driver = self._init_driver()

    def _init_driver(self):
        chrome_options = Options()
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--window-size=1920,1080")
        driver = webdriver.Chrome(options=chrome_options)
        return driver

    def refresh_page(self):
        try:
            self.driver.get(self.url)
            # Wait for the page to load and dynamic content to render.
            time.sleep(5)
            # Scroll to the bottom to trigger lazy-loading.
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(5)
            return self.driver.page_source
        except Exception as e:
            logging.error(f"Error refreshing page: {e}")
            self.reinit_driver()
            return None

    def reinit_driver(self):
        try:
            self.driver.quit()
        except Exception as e:
            logging.error(f"Error quitting driver: {e}")
        self.driver = self._init_driver()

    def fetch_announcements(self):
        html = self.refresh_page()
        if not html:
            return []
        soup = BeautifulSoup(html, "html.parser")
        # Try to locate the container that holds listing announcements.
        container = soup.find("div", class_="bn-flex flex-col gap-6 items-center noH5:items-start px-[15px] noH5:px-6 mt-4")  # <-- Adjust this selector as needed
        if container:
            anchors = container.find_all("a", href=lambda h: h and "/en/support/announcement/" in h)
        else:
            anchors = soup.find_all("a", href=lambda h: h and "/en/support/announcement/" in h)

        announcements = []
        seen = set()
        for a in anchors:
            title = a.get_text(strip=True)
            # Process only announcements that mention "will list" (case-insensitive)
            if "will list" not in title.lower():
                continue
            href = a.get("href")
            if not href:
                continue
            if href.startswith("/"):
                href = "https://www.binance.com" + href
            if href in seen:
                continue
            seen.add(href)
            # Normalize title for duplicate checking.
            normalized_title = title.strip().lower()
            announcements.append((title, href, normalized_title))
        return announcements

    def quit(self):
        try:
            self.driver.quit()
        except Exception as e:
            logging.error(f"Error quitting Selenium driver: {e}")

# -----------------------------------------------------------------------------
# Asynchronous function to periodically fetch Binance announcements using Selenium.
# Processes only new announcements (based on last_binance_announcement_url).
# -----------------------------------------------------------------------------
async def periodic_fetch_binance_announcements(scraper: BinanceScraper):
    global last_binance_announcement_url, processed_announcements_text
    while True:
        logging.info("Refreshing Binance announcements using Selenium...")
        announcements = scraper.fetch_announcements()  # List of (title, href, normalized_title)
        if not announcements:
            logging.warning("No announcements fetched.")
        else:
            # Assume announcements are sorted in descending order (most recent first).
            current_top_url = announcements[0][1]
            if last_binance_announcement_url is None:
                # First run: record the current top announcement but do not trigger trades.
                last_binance_announcement_url = current_top_url
                for title, href, normalized_title in announcements:
                    processed_announcements_text.add(normalized_title)
                logging.info(f"Initial announcements loaded; starting from URL: {last_binance_announcement_url}")
            else:
                # Collect announcements newer than the last recorded announcement.
                new_to_process = []
                for title, href, normalized_title in announcements:
                    if href == last_binance_announcement_url:
                        break
                    new_to_process.append((title, href, normalized_title))
                if new_to_process:
                    # Process new announcements in chronological order (oldest first).
                    for title, href, normalized_title in reversed(new_to_process):
                        if normalized_title in processed_announcements_text:
                            continue
                        processed_announcements_text.add(normalized_title)
                        logging.info(f"New Binance announcement detected: {title} - {href}")
                        send_telegram_message(f"\U0001F680 Binance New Listing: {title}\n\U0001F517 {href}")
                        symbols = extract_symbols(title)
                        if symbols:
                            for symbol in symbols:
                                logging.info(f"Extracted symbol: {symbol}")
                                execute_trade(symbol)
                        else:
                            logging.info("No symbol extracted from announcement.")
                # Update the pointer to the newest announcement.
                last_binance_announcement_url = current_top_url
        await asyncio.sleep(10)  # Adjust polling interval as needed

# -----------------------------------------------------------------------------
# Asynchronous function to monitor Telegram channels for announcements.
# If a Telegram message contains an announcement that has already been processed from the website, it will be skipped.
# -----------------------------------------------------------------------------
async def monitor_telegram():
    @telegram_client.on(events.NewMessage(chats=TELEGRAM_CHANNELS))
    async def handler(event):
        message_text = event.raw_text
        logging.info(f"Received Telegram message: {message_text}")
        normalized_message = message_text.strip().lower()
        if normalized_message in processed_announcements_text:
            logging.info("Telegram announcement already processed; skipping.")
            return
        lower_text = message_text.lower()
        if "binance will list" in lower_text or "new listing" in lower_text:
            logging.info("Detected Binance listing announcement in Telegram")
            # Mark it as processed so that future duplicates are ignored.
            processed_announcements_text.add(normalized_message)
            symbols = extract_symbols(message_text)
            if symbols:
                for symbol in symbols:
                    logging.info(f"Extracted symbol from Telegram: {symbol}")
                    execute_trade(symbol)
            else:
                logging.info("No symbol extracted from Telegram message.")
    logging.info("Starting Telegram monitoring...")
    await telegram_client.start()
    await telegram_client.run_until_disconnected()

# -----------------------------------------------------------------------------
# Main asynchronous routine: run Telegram monitoring and the persistent Selenium scraper concurrently.
# -----------------------------------------------------------------------------
async def main():
    scraper = BinanceScraper("https://www.binance.com/en/support/announcement/new-cryptocurrency-listing?c=48")
    try:
        await asyncio.gather(
            monitor_telegram(),
            periodic_fetch_binance_announcements(scraper)
        )
    finally:
        scraper.quit()

# -----------------------------------------------------------------------------
# Entry point
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    asyncio.run(main())

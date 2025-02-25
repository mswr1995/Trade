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
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

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
# Global sets and pointers for processed announcements and executed trades.
# -----------------------------------------------------------------------------
processed_listings = set()  # For executed trades

# For Binance:
processed_announcements_text = set()  # Normalized announcement texts from Binance
last_binance_announcement_url = None   # Pointer for latest processed Binance announcement

# For Kraken:
processed_kraken_announcements_text = set()  # Normalized announcement texts from Kraken
last_kraken_announcement_url = None           # Pointer for latest processed Kraken announcement

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
# Helper function to extract token symbols from text for Binance.
# Assumes symbols are enclosed in parentheses.
# -----------------------------------------------------------------------------
def extract_symbols(text):
    return re.findall(r"\(([A-Z0-9]+)\)", text)

# -----------------------------------------------------------------------------
# Helper function to extract token symbols from Kraken titles.
# Kraken titles use phrases like "AIXBT, ODOS and TOSHI are available for trading!"
# -----------------------------------------------------------------------------
def extract_symbols_kraken(title):
    lower = title.lower()
    if "available for trading" not in lower:
        return []
    # Remove trailing phrase ("are available for trading" or "is available for trading")
    cleaned = re.sub(r'\s*(are|is)\s+available\s+for\s+trading[!\.]*', '', title, flags=re.IGNORECASE)
    # Now cleaned might be something like "AIXBT, ODOS and TOSHI"
    parts = cleaned.split(',')
    symbols = []
    for part in parts:
        subparts = part.split(' and ')
        for sub in subparts:
            token = sub.strip()
            if token:
                symbols.append(token.upper())
    return symbols

# -----------------------------------------------------------------------------
# Selenium-based scraper for Binance announcements.
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
        # Use "eager" page load strategy so that Selenium returns as soon as the DOM is ready.
        chrome_options.page_load_strategy = "eager"
        return webdriver.Chrome(options=chrome_options)

    def refresh_page(self):
        try:
            self.driver.get(self.url)
            # Explicitly wait for at least one announcement anchor to appear.
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.XPATH, "//a[contains(@href, '/en/support/announcement/')]"))
            )
            # Optionally, scroll a bit to ensure the container is fully loaded.
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight/2);")
            time.sleep(2)
            return self.driver.page_source
        except Exception as e:
            logging.error(f"Error refreshing Binance page: {e}")
            self.reinit_driver()
            return None

    def reinit_driver(self):
        try:
            self.driver.quit()
        except Exception as e:
            logging.error(f"Error quitting Binance driver: {e}")
        self.driver = self._init_driver()

    def fetch_announcements(self):
        html = self.refresh_page()
        if not html:
            return []
        soup = BeautifulSoup(html, "html.parser")
        # Try to locate the container that holds listing announcements.
        container = soup.find("div", class_="bn-flex flex-col gap-6 items-center noH5:items-start px-[15px] noH5:px-6 mt-4")
        if container:
            anchors = container.find_all("a", href=lambda h: h and "/en/support/announcement/" in h)
        else:
            anchors = soup.find_all("a", href=lambda h: h and "/en/support/announcement/" in h)
        announcements = []
        seen = set()
        for a in anchors:
            title = a.get_text(strip=True)
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
            normalized_title = title.strip().lower()
            announcements.append((title, href, normalized_title))
        return announcements

    def quit(self):
        try:
            self.driver.quit()
        except Exception as e:
            logging.error(f"Error quitting Binance Selenium driver: {e}")

# -----------------------------------------------------------------------------
# Selenium-based scraper for Kraken announcements.
# -----------------------------------------------------------------------------
class KrakenScraper:
    def __init__(self, url):
        self.url = url
        self.driver = self._init_driver()

    def _init_driver(self):
        chrome_options = Options()
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.page_load_strategy = "eager"
        return webdriver.Chrome(options=chrome_options)

    def refresh_page(self):
        try:
            self.driver.get(self.url)
            # Wait for at least one article to appear.
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.XPATH, "//article"))
            )
            self.driver.execute_script("window.scrollTo(0, document.body.scrollHeight/2);")
            time.sleep(2)
            return self.driver.page_source
        except Exception as e:
            logging.error(f"Error refreshing Kraken page: {e}")
            self.reinit_driver()
            return None

    def reinit_driver(self):
        try:
            self.driver.quit()
        except Exception as e:
            logging.error(f"Error quitting Kraken driver: {e}")
        self.driver = self._init_driver()

    def fetch_announcements(self):
        html = self.refresh_page()
        if not html:
            return []
        soup = BeautifulSoup(html, "html.parser")
        # The Kraken announcements are within the <div class="latest"> container.
        container = soup.find("div", class_="latest")
        if container:
            articles = container.find_all("article")
        else:
            articles = soup.find_all("article")
        announcements = []
        for article in articles:
            title_el = article.find("h2", class_="title")
            if not title_el:
                continue
            a_tag = title_el.find("a")
            if not a_tag:
                continue
            title = a_tag.get_text(strip=True)
            lower_title = title.lower()
            if "available for trading" not in lower_title:
                continue
            href = a_tag.get("href")
            if not href:
                continue
            normalized_title = title.strip().lower()
            announcements.append((title, href, normalized_title))
        return announcements

    def quit(self):
        try:
            self.driver.quit()
        except Exception as e:
            logging.error(f"Error quitting Kraken Selenium driver: {e}")

# -----------------------------------------------------------------------------
# Asynchronous function to periodically fetch Binance announcements.
# Processes only new announcements (based on last_binance_announcement_url).
# -----------------------------------------------------------------------------
async def periodic_fetch_binance_announcements(scraper: BinanceScraper):
    global last_binance_announcement_url, processed_announcements_text
    while True:
        logging.info("Refreshing Binance announcements using Selenium...")
        announcements = scraper.fetch_announcements()  # List of (title, href, normalized_title)
        if not announcements:
            logging.warning("No Binance announcements fetched.")
        else:
            current_top_url = announcements[0][1]
            if last_binance_announcement_url is None:
                last_binance_announcement_url = current_top_url
                for title, href, norm in announcements:
                    processed_announcements_text.add(norm)
                logging.info(f"Initial Binance announcements loaded; pointer set to: {last_binance_announcement_url}")
            else:
                new_to_process = []
                for title, href, norm in announcements:
                    if href == last_binance_announcement_url:
                        break
                    new_to_process.append((title, href, norm))
                if new_to_process:
                    for title, href, norm in reversed(new_to_process):
                        if norm in processed_announcements_text:
                            continue
                        processed_announcements_text.add(norm)
                        logging.info(f"New Binance announcement detected: {title} - {href}")
                        send_telegram_message(f"\U0001F680 Binance New Listing: {title}\n\U0001F517 {href}")
                        symbols = extract_symbols(title)
                        if symbols:
                            for symbol in symbols:
                                logging.info(f"Extracted symbol from Binance: {symbol}")
                                execute_trade(symbol)
                        else:
                            logging.info("No symbol extracted from Binance announcement.")
                last_binance_announcement_url = current_top_url
        await asyncio.sleep(10)  # Poll every 10 seconds

# -----------------------------------------------------------------------------
# Asynchronous function to periodically fetch Kraken announcements.
# Processes only new announcements (based on last_kraken_announcement_url).
# -----------------------------------------------------------------------------
async def periodic_fetch_kraken_announcements(scraper: KrakenScraper):
    global last_kraken_announcement_url, processed_kraken_announcements_text
    # Initialize processed set if needed.
    try:
        processed_kraken_announcements_text
    except NameError:
        processed_kraken_announcements_text = set()
    while True:
        logging.info("Refreshing Kraken announcements using Selenium...")
        announcements = scraper.fetch_announcements()  # List of (title, href, normalized_title)
        if not announcements:
            logging.warning("No Kraken announcements fetched.")
        else:
            current_top_url = announcements[0][1]
            if last_kraken_announcement_url is None:
                last_kraken_announcement_url = current_top_url
                for title, href, norm in announcements:
                    processed_kraken_announcements_text.add(norm)
                logging.info(f"Initial Kraken announcements loaded; pointer set to: {last_kraken_announcement_url}")
            else:
                new_to_process = []
                for title, href, norm in announcements:
                    if href == last_kraken_announcement_url:
                        break
                    new_to_process.append((title, href, norm))
                if new_to_process:
                    for title, href, norm in reversed(new_to_process):
                        if norm in processed_kraken_announcements_text:
                            continue
                        processed_kraken_announcements_text.add(norm)
                        logging.info(f"New Kraken announcement detected: {title} - {href}")
                        send_telegram_message(f"\U0001F680 Kraken New Listing: {title}\n\U0001F517 {href}")
                        symbols = extract_symbols_kraken(title)
                        if symbols:
                            for symbol in symbols:
                                logging.info(f"Extracted symbol from Kraken: {symbol}")
                                execute_trade(symbol)
                        else:
                            logging.info("No symbol extracted from Kraken announcement.")
                last_kraken_announcement_url = current_top_url
        await asyncio.sleep(10)  # Poll every 10 seconds

# -----------------------------------------------------------------------------
# Asynchronous function to monitor Telegram channels for announcements.
# -----------------------------------------------------------------------------
async def monitor_telegram():
    @telegram_client.on(events.NewMessage(chats=TELEGRAM_CHANNELS))
    async def handler(event):
        message_text = event.raw_text
        logging.info(f"Received Telegram message: {message_text}")
        normalized_message = message_text.strip().lower()
        if (normalized_message in processed_announcements_text or
            normalized_message in processed_kraken_announcements_text):
            logging.info("Telegram announcement already processed; skipping.")
            return
        lower_text = message_text.lower()
        if ("binance will list" in lower_text or "new listing" in lower_text or
            "available for trading" in lower_text):
            logging.info("Detected listing announcement in Telegram")
            processed_announcements_text.add(normalized_message)
            processed_kraken_announcements_text.add(normalized_message)
            # Try both extraction methods.
            symbols = extract_symbols(message_text)
            if not symbols:
                symbols = extract_symbols_kraken(message_text)
            if symbols:
                for symbol in symbols:
                    logging.info(f"Extracted symbol from Telegram: {symbol}")
                    execute_trade(symbol)
            else:
                logging.info("No symbol extracted from Telegram message.")
    logging.info("Starting Telegram monitoring...")
    await telegram_client.start(bot_token=os.getenv("TELEGRAM_BOT_TOKEN"))
    await telegram_client.run_until_disconnected()

# -----------------------------------------------------------------------------
# Main asynchronous routine: run Telegram monitoring, Binance and Kraken scrapers concurrently.
# -----------------------------------------------------------------------------
async def main():
    binance_scraper = BinanceScraper("https://www.binance.com/en/support/announcement/new-cryptocurrency-listing?c=48")
    kraken_scraper = KrakenScraper("https://blog.kraken.com/category/product/asset-listings")
    try:
        await asyncio.gather(
            monitor_telegram(),
            periodic_fetch_binance_announcements(binance_scraper),
            periodic_fetch_kraken_announcements(kraken_scraper)
        )
    finally:
        binance_scraper.quit()
        kraken_scraper.quit()

# -----------------------------------------------------------------------------
# Entry point
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    asyncio.run(main())

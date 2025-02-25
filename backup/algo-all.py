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
import tweepy
import sqlite3

# Reconfigure stdout to use UTF-8 (Python 3.7+)
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
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNELS = ["@binance_announcements", "@mswr_alert_bot"]

# -----------------------------------------------------------------------------
# Exchange API credentials (Gate.io in this example)
# -----------------------------------------------------------------------------
GATE_IO_API_KEY = os.getenv("GATE_IO_API_KEY")
GATE_IO_SECRET_KEY = os.getenv("GATE_IO_SECRET_KEY")

# -----------------------------------------------------------------------------
# Twitter API credentials (for Coinbase tracking) - using only the bearer token
# -----------------------------------------------------------------------------
TWITTER_BEARER_TOKEN = os.getenv("TWITTER_BEARER_TOKEN")

# -----------------------------------------------------------------------------
# Setup Gate.io client, Telegram client, and Tweepy client (using only bearer token)
# -----------------------------------------------------------------------------
gateio = ccxt.gateio({
    'apiKey': GATE_IO_API_KEY,
    'secret': GATE_IO_SECRET_KEY,
    'enableRateLimit': True,
})

telegram_client = TelegramClient("crypto_bot", TELEGRAM_API_ID, TELEGRAM_API_HASH)

twitter_client_api = tweepy.Client(
    bearer_token=TWITTER_BEARER_TOKEN
)

# -----------------------------------------------------------------------------
# Set up SQLite database for Coinbase listings (Grok's adaptation)
# -----------------------------------------------------------------------------
conn = sqlite3.connect("coinbase_listings.db", check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''CREATE TABLE IF NOT EXISTS listings
                  (ticker TEXT PRIMARY KEY, roadmap_time TEXT, support_time TEXT)''')
conn.commit()

# -----------------------------------------------------------------------------
# Global sets and pointers for processed announcements and executed trades.
# -----------------------------------------------------------------------------
processed_listings = set()  # For executed trades

# For Binance:
processed_announcements_text = set()  # Normalized texts from Binance announcements
last_binance_announcement_url = None   # Pointer for latest Binance announcement

# For Kraken:
processed_kraken_announcements_text = set()  # Normalized texts from Kraken announcements
last_kraken_announcement_url = None           # Pointer for latest Kraken announcement

# For Coinbase Twitter:
last_coinbase_tweet_id = None
processed_coinbase_tweets_text = set()

# -----------------------------------------------------------------------------
# Helper function: execute_trade – places a market order on Gate.io.
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
# Helper function: extract_symbols – for Binance announcements.
# Assumes symbols are enclosed in parentheses.
# -----------------------------------------------------------------------------
def extract_symbols(text):
    return re.findall(r"\(([A-Z0-9]+)\)", text)

# -----------------------------------------------------------------------------
# Helper function: extract_symbols_kraken – for Kraken announcements.
# Expects titles like "AIXBT, ODOS and TOSHI are available for trading!"
# -----------------------------------------------------------------------------
def extract_symbols_kraken(title):
    lower = title.lower()
    if "available for trading" not in lower:
        return []
    cleaned = re.sub(r'\s*(are|is)\s+available\s+for\s+trading[!\.]*', '', title, flags=re.IGNORECASE)
    parts = cleaned.split(',')
    symbols = []
    for part in parts:
        for sub in part.split(' and '):
            token = sub.strip()
            if token:
                symbols.append(token.upper())
    return symbols

# -----------------------------------------------------------------------------
# Helper function: extract_symbols_twitter – for Coinbase tweets.
# Uses similar logic as Kraken extraction.
# -----------------------------------------------------------------------------
def extract_symbols_twitter(text):
    lower = text.lower()
    if "available for trading" not in lower:
        return []
    cleaned = re.sub(r'\s*(are|is)\s+available\s+for\s+trading[!\.]*', '', text, flags=re.IGNORECASE)
    parts = cleaned.split(',')
    symbols = []
    for part in parts:
        for sub in part.split(' and '):
            token = sub.strip()
            if token:
                symbols.append(token.upper())
    return symbols

# -----------------------------------------------------------------------------
# Coinbase tracking helper functions (Grok's adaptation)
# -----------------------------------------------------------------------------
ticker_pattern = re.compile(r'\(([A-Z]{3,6})\)')

def extract_ticker(text):
    match = ticker_pattern.search(text)
    return match.group(1) if match else None

def check_tweet(tweet, source):
    ticker = extract_ticker(tweet.text)
    if not ticker:
        return
    current_time = datetime.utcnow().isoformat()
    if source == "roadmap" and "added to the roadmap" in tweet.text.lower():
        cursor.execute("INSERT OR IGNORE INTO listings (ticker, roadmap_time) VALUES (?, ?)",
                       (ticker, current_time))
        logging.info(f"Roadmap addition detected: {ticker} at {current_time}")
    elif source == "support" and ("trading is now live" in tweet.text.lower() or "support for" in tweet.text.lower()):
        cursor.execute("UPDATE listings SET support_time = ? WHERE ticker = ? AND support_time IS NULL",
                       (current_time, ticker))
        if cursor.rowcount > 0:
            logging.info(f"Support tweet detected: {ticker} at {current_time}")
        else:
            cursor.execute("INSERT OR IGNORE INTO listings (ticker, support_time) VALUES (?, ?)",
                           (ticker, current_time))
            logging.info(f"Support without prior roadmap detected: {ticker} at {current_time}")
    conn.commit()

def get_time_difference(ticker):
    cursor.execute("SELECT roadmap_time, support_time FROM listings WHERE ticker = ?", (ticker,))
    result = cursor.fetchone()
    if result and result[0] and result[1]:
        roadmap_time = datetime.fromisoformat(result[0])
        support_time = datetime.fromisoformat(result[1])
        delta = support_time - roadmap_time
        return delta.total_seconds() / 86400  # days
    return None

def monitor_tweets():
    # Fetch tweets from @coinbaseassets (roadmap) and @CoinbaseSupport (support)
    roadmap_resp = twitter_client_api.get_users_tweets(id="1333467482", max_results=10, tweet_fields=["created_at"])
    support_resp = twitter_client_api.get_users_tweets(id="969154197026201600", max_results=10, tweet_fields=["created_at"])
    if roadmap_resp.data:
        for tweet in roadmap_resp.data:
            check_tweet(tweet, "roadmap")
    if support_resp.data:
        for tweet in support_resp.data:
            check_tweet(tweet, "support")

# -----------------------------------------------------------------------------
# Asynchronous function to periodically fetch Coinbase tweets for listings.
# Runs monitor_tweets() every 5 minutes.
# -----------------------------------------------------------------------------
async def periodic_fetch_coinbase_tweets():
    while True:
        logging.info("Fetching Coinbase tweets...")
        try:
            await asyncio.to_thread(monitor_tweets)
        except Exception as e:
            logging.error(f"Error in Coinbase tweet tracking: {e}")
        await asyncio.sleep(300)

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
        chrome_options.page_load_strategy = "eager"
        return webdriver.Chrome(options=chrome_options)
    def refresh_page(self):
        try:
            self.driver.get(self.url)
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.XPATH, "//a[contains(@href, '/en/support/announcement/')]"))
            )
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
            if "available for trading" not in title.lower():
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
        announcements = scraper.fetch_announcements()
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
        await asyncio.sleep(10)

# -----------------------------------------------------------------------------
# Asynchronous function to periodically fetch Kraken announcements.
# Processes only new announcements (based on last_kraken_announcement_url).
# -----------------------------------------------------------------------------
async def periodic_fetch_kraken_announcements(scraper: KrakenScraper):
    global last_kraken_announcement_url, processed_kraken_announcements_text
    try:
        processed_kraken_announcements_text
    except NameError:
        processed_kraken_announcements_text = set()
    while True:
        logging.info("Refreshing Kraken announcements using Selenium...")
        announcements = scraper.fetch_announcements()
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
        await asyncio.sleep(10)

# -----------------------------------------------------------------------------
# Asynchronous function to periodically fetch Coinbase tweets for listings.
# Uses Grok's adaptation (via monitor_tweets) and runs every 5 minutes.
# -----------------------------------------------------------------------------
async def periodic_fetch_coinbase_tweets():
    while True:
        logging.info("Fetching Coinbase tweets...")
        try:
            await asyncio.to_thread(monitor_tweets)
        except Exception as e:
            logging.error(f"Error in Coinbase tweet tracking: {e}")
        await asyncio.sleep(900)

# -----------------------------------------------------------------------------
# Coinbase tweet tracking helper functions (Grok's adaptation)
# -----------------------------------------------------------------------------
def monitor_tweets():
    roadmap_resp = twitter_client_api.get_users_tweets(id="1333467482", max_results=10, tweet_fields=["created_at"])
    support_resp = twitter_client_api.get_users_tweets(id="969154197026201600", max_results=10, tweet_fields=["created_at"])
    if roadmap_resp.data:
        for tweet in roadmap_resp.data:
            check_tweet(tweet, "roadmap")
    if support_resp.data:
        for tweet in support_resp.data:
            check_tweet(tweet, "support")

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
# Main asynchronous routine: run all components concurrently.
# -----------------------------------------------------------------------------
async def main():
    binance_scraper = BinanceScraper("https://www.binance.com/en/support/announcement/new-cryptocurrency-listing?c=48")
    kraken_scraper = KrakenScraper("https://blog.kraken.com/category/product/asset-listings")
    try:
        await asyncio.gather(
            monitor_telegram(),
            periodic_fetch_binance_announcements(binance_scraper),
            periodic_fetch_kraken_announcements(kraken_scraper),
            periodic_fetch_coinbase_tweets()
        )
    finally:
        binance_scraper.quit()
        kraken_scraper.quit()

# -----------------------------------------------------------------------------
# Entry point
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    asyncio.run(main())

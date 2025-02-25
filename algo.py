import os
import sys
import ccxt
import logging
import re
import asyncio
import time
from datetime import datetime
from dotenv import load_dotenv
from telethon import TelegramClient
from notifier.notifier import send_telegram_message
from scrapers.binance import BinanceScraper, extract_symbols
from scrapers.kraken import KrakenScraper, extract_symbols_kraken
from twitter.coinbase import monitor_tweets, check_tweet, extract_ticker, get_time_difference
from telegram.monitor import monitor_telegram
import tweepy
import sqlite3

# Reconfigure stdout to use UTF-8 (Python 3.7+)
sys.stdout.reconfigure(encoding='utf-8')

# Ensure logs directory exists
if not os.path.exists("logs"):
    os.makedirs("logs")

# Load environment variables and configure logging
load_dotenv()
logging.basicConfig(
    filename='logs/crypto_bot.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Setup Telegram API credentials
TELEGRAM_API_ID = os.getenv("TELEGRAM_API_ID")
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH")
TELEGRAM_PHONE = os.getenv("TELEGRAM_PHONE")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNELS = ["@binance_announcements", "@mswr_alert_bot"]

# Setup Exchange API credentials (Gate.io)
GATE_IO_API_KEY = os.getenv("GATE_IO_API_KEY")
GATE_IO_SECRET_KEY = os.getenv("GATE_IO_SECRET_KEY")

# Setup Twitter API credentials (only bearer token now)
TWITTER_BEARER_TOKEN = os.getenv("TWITTER_BEARER_TOKEN")

# Setup Gate.io client
gateio = ccxt.gateio({
    'apiKey': GATE_IO_API_KEY,
    'secret': GATE_IO_SECRET_KEY,
    'enableRateLimit': True,
})

# Setup Telegram client
telegram_client = TelegramClient("crypto_bot", TELEGRAM_API_ID, TELEGRAM_API_HASH)

# Setup Tweepy client for Coinbase tracking
twitter_client_api = tweepy.Client(
    bearer_token=TWITTER_BEARER_TOKEN
)

# Global pointers and processed sets
processed_listings = set()  # For executed trades
processed_announcements_text = set()  # For Binance announcements (normalized)
last_binance_announcement_url = None
processed_kraken_announcements_text = set()  # For Kraken announcements (normalized)
last_kraken_announcement_url = None

# Asynchronous function to periodically fetch Binance announcements.
async def periodic_fetch_binance_announcements():
    scraper = BinanceScraper("https://www.binance.com/en/support/announcement/new-cryptocurrency-listing?c=48")
    global last_binance_announcement_url, processed_announcements_text
    try:
        while True:
            logging.info("Refreshing Binance announcements...")
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
    finally:
        scraper.quit()

# Asynchronous function to periodically fetch Kraken announcements.
async def periodic_fetch_kraken_announcements():
    scraper = KrakenScraper("https://blog.kraken.com/category/product/asset-listings")
    global last_kraken_announcement_url, processed_kraken_announcements_text
    try:
        while True:
            logging.info("Refreshing Kraken announcements...")
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
    finally:
        scraper.quit()

# Asynchronous function to periodically fetch Coinbase tweets for listings.
async def periodic_fetch_coinbase_tweets():
    from twitter.coinbase import monitor_tweets
    while True:
        logging.info("Fetching Coinbase tweets...")
        try:
            await asyncio.to_thread(monitor_tweets, twitter_client_api)
        except Exception as e:
            logging.error(f"Error in Coinbase tweet tracking: {e}")
        await asyncio.sleep(300)

# Trade execution function.
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

# Asynchronous function to monitor Telegram channels for announcements.
async def monitor_telegram():
    from telegram.monitor import monitor_telegram as tg_monitor
    await tg_monitor(telegram_client, processed_announcements_text, processed_kraken_announcements_text, execute_trade, extract_symbols, extract_symbols_kraken)

# Main asynchronous routine: run all components concurrently.
async def main():
    await asyncio.gather(
        monitor_telegram(),
        periodic_fetch_binance_announcements(),
#        periodic_fetch_kraken_announcements(), # Kraken doesn't affect the market that well
        periodic_fetch_coinbase_tweets()
    )

if __name__ == "__main__":
    asyncio.run(main())

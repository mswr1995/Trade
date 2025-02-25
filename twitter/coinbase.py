import re
import sqlite3
import logging
from datetime import datetime
import tweepy

logging.basicConfig(
    filename='logs/crypto_bot.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Set up SQLite database for Coinbase listings
conn = sqlite3.connect("coinbase_listings.db", check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''CREATE TABLE IF NOT EXISTS listings
                  (ticker TEXT PRIMARY KEY, roadmap_time TEXT, support_time TEXT)''')
conn.commit()

ticker_pattern = re.compile(r'\(([A-Z]{3,6})\)')

def extract_ticker(text):
    """
    Extracts a ticker symbol from text using a regex.
    """
    match = ticker_pattern.search(text)
    return match.group(1) if match else None

def check_tweet(tweet, source):
    """
    Checks a tweet and updates the SQLite database for roadmap or support events.
    """
    ticker = extract_ticker(tweet.text)
    if not ticker:
        return
    current_time = datetime.utcnow().isoformat()
    if source == "roadmap" and "added to the roadmap" in tweet.text.lower():
        cursor.execute("INSERT OR IGNORE INTO listings (ticker, roadmap_time) VALUES (?, ?)", (ticker, current_time))
        logging.info(f"Roadmap addition detected: {ticker} at {current_time}")
    elif source == "support" and ("trading is now live" in tweet.text.lower() or "support for" in tweet.text.lower()):
        cursor.execute("UPDATE listings SET support_time = ? WHERE ticker = ? AND support_time IS NULL", (current_time, ticker))
        if cursor.rowcount > 0:
            logging.info(f"Support tweet detected: {ticker} at {current_time}")
        else:
            cursor.execute("INSERT OR IGNORE INTO listings (ticker, support_time) VALUES (?, ?)", (ticker, current_time))
            logging.info(f"Support without prior roadmap detected: {ticker} at {current_time}")
    conn.commit()

def get_time_difference(ticker):
    """
    Returns the difference in days between roadmap and support times for a ticker.
    """
    cursor.execute("SELECT roadmap_time, support_time FROM listings WHERE ticker = ?", (ticker,))
    result = cursor.fetchone()
    if result and result[0] and result[1]:
        roadmap_time = datetime.fromisoformat(result[0])
        support_time = datetime.fromisoformat(result[1])
        delta = support_time - roadmap_time
        return delta.total_seconds() / 86400  # days
    return None

def monitor_tweets(client):
    """
    Fetches tweets from the CoinbaseAssets (roadmap) and CoinbaseSupport (support) accounts.
    """
    roadmap_resp = client.get_users_tweets(id="1333467482", max_results=10, tweet_fields=["created_at"])
    support_resp = client.get_users_tweets(id="969154197026201600", max_results=10, tweet_fields=["created_at"])
    if roadmap_resp.data:
        for tweet in roadmap_resp.data:
            check_tweet(tweet, "roadmap")
    if support_resp.data:
        for tweet in support_resp.data:
            check_tweet(tweet, "support")

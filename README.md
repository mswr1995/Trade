# Crypto Listing Announcement Bot

## Overview

This project is a Python-based cryptocurrency listing announcement tracker and automated trading system. It monitors listing announcements from multiple sources—including Binance, Kraken, and Coinbase (via Twitter)—and, upon detecting new listings, executes trades on Gate.io using the [ccxt](https://github.com/ccxt/ccxt) library. In addition, it monitors Telegram channels for additional announcements and sends notifications using a custom notifier module.

The system is built with asynchronous concurrency using Python's `asyncio` framework and is organized into separate modules for improved maintainability.

---

## Features

- **Multi-Source Announcement Tracking**
  - **Binance:** Uses Selenium to scrape the official Binance listing announcements page.
  - **Kraken:** Uses Selenium to scrape the Kraken blog category for asset listings.
  - **Coinbase (Twitter):** Uses Tweepy (with a bearer token) to track tweets from Coinbase accounts (@coinbaseassets for roadmap and @CoinbaseSupport for support), storing events in a local SQLite database.
  - **Telegram Monitoring:** Uses Telethon to monitor specified Telegram channels for announcements.

- **Automated Trade Execution**
  - Executes market orders on Gate.io via the ccxt library when new listings are detected.
  - Prevents duplicate trade execution using global processed sets.

- **Asynchronous Concurrency**
  - All components (web scraping, tweet tracking, and Telegram monitoring) run concurrently using `asyncio.gather()`.

- **Logging & Notification**
  - Detailed logs are written to the `/logs` folder.
  - Notifier module sends messages via the Telegram Bot API.

- **Configuration**
  - Uses a `.env` file for storing API keys and sensitive configuration parameters.

---

## Folder Structure

project_root/
├── .env
├── README.md
├── requirements.txt
├── algo.py
├── notifier/
│   ├── __init__.py
│   └── notifier.py
├── scrapers/
│   ├── __init__.py
│   ├── binance.py
│   └── kraken.py
├── twitter/
│   ├── __init__.py
│   └── coinbase.py
└── telegram/
    ├── __init__.py
    └── monitor.py


---

## Setup & Installation

### 1. Clone the Repository

bash
- git clone <https://github.com/mswr1995/Trade>
- cd project_root


### 2. Create a Virtual Environment & Install Dependencies

- python -m venv venv
- source venv/bin/activate   # On Windows: venv\Scripts\activate
- pip install --upgrade pip
- pip install -r requirements.txt


### 3. Configure Environment Variables

Create a .env files with the following:

- TELEGRAM_API_ID=
- TELEGRAM_API_HASH=
- TELEGRAM_PHONE=

- TELEGRAM_BOT_TOKEN=
- TELEGRAM_CHAT_ID= 


- GATE_IO_API_KEY=
- GATE_IO_SECRET_KEY=


- TWITTER_API_KEY=
- TWITTER_API_SECRET=
- TWITTER_BEARER_TOKEN=

---
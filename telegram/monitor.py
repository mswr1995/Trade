import logging
from telethon import TelegramClient, events
import os

def create_telegram_handler(processed_announcements_text, processed_kraken_announcements_text, execute_trade, extract_symbols, extract_symbols_kraken):
    async def handler(event):
        message_text = event.raw_text
        logging.info(f"Received Telegram message: {message_text}")
        normalized_message = message_text.strip().lower()
        if normalized_message in processed_announcements_text or normalized_message in processed_kraken_announcements_text:
            logging.info("Telegram announcement already processed; skipping.")
            return
        lower_text = message_text.lower()
        if ("binance will list" in lower_text or "new listing" in lower_text or "available for trading" in lower_text):
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
    return handler

async def monitor_telegram(telegram_client, processed_announcements_text, processed_kraken_announcements_text, execute_trade, extract_symbols, extract_symbols_kraken):
    handler = create_telegram_handler(processed_announcements_text, processed_kraken_announcements_text, execute_trade, extract_symbols, extract_symbols_kraken)
    telegram_client.add_event_handler(handler, events.NewMessage(chats=["@binance_announcements", "@mswr_alert_bot"]))
    logging.info("Starting Telegram monitoring...")
    await telegram_client.start(bot_token=os.getenv("TELEGRAM_BOT_TOKEN"))
    await telegram_client.run_until_disconnected()

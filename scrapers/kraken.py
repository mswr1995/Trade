import time
import logging
import re
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup

logging.basicConfig(
    filename='logs/crypto_bot.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

def extract_symbols_kraken(title):
    """
    Extracts symbols from Kraken announcements.
    Expected format: "AIXBT, ODOS and TOSHI are available for trading!"
    """
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

class KrakenScraper:
    """
    Scrapes Kraken listing announcements using Selenium.
    """
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

import time
import logging
import re
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup

# Configure logging (writes to the main log file)
logging.basicConfig(
    filename='logs/crypto_bot.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

def extract_symbols(text):
    """
    Extracts symbols enclosed in parentheses.
    Example: "XYZ (ABC)" returns ["ABC"].
    """
    return re.findall(r"\(([A-Z0-9]+)\)", text)

class BinanceScraper:
    """
    Scrapes Binance listing announcements using Selenium.
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
            # Wait until at least one announcement anchor is present.
            WebDriverWait(self.driver, 10).until(
                EC.presence_of_element_located((By.XPATH, "//a[contains(@href, '/en/support/announcement/')]"))
            )
            # Scroll half-way to load the announcements container.
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
        # Locate the container holding announcements.
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

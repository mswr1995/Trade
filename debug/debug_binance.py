import sys
import time
import logging
import re
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from bs4 import BeautifulSoup

# Reconfigure stdout to use UTF-8 encoding (Python 3.7+)
sys.stdout.reconfigure(encoding='utf-8')

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

def test_fetch_binance_with_selenium():
    url = "https://www.binance.com/en/support/announcement/new-cryptocurrency-listing?c=48"

    # Set up headless Chrome options
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")  # Ensure the window is large enough

    # Initialize the webdriver (make sure ChromeDriver is installed and in PATH)
    driver = webdriver.Chrome(options=chrome_options)
    
    try:
        logging.info(f"Fetching {url} with Selenium")
        driver.get(url)

        # Wait for the page to load. Adjust sleep time if needed.
        time.sleep(5)
        
        # Optionally, scroll down to load dynamic content
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(5)
        
        # Get the page source after rendering
        html = driver.page_source
        soup = BeautifulSoup(html, "html.parser")
        
        # Attempt to find a container that holds the listing announcements.
        # UPDATE THIS SELECTOR to the actual container element on Binance.
        container = soup.find("div", class_="bn-flex flex-col gap-6 items-center noH5:items-start px-[15px] noH5:px-6 mt-4")  # <-- Adjust this selector
        
        if container:
            logging.info("Found announcements container. Extracting anchors from container.")
            anchors = container.find_all("a", href=lambda h: h and "/en/support/announcement/" in h)
        else:
            logging.info("Announcements container not found. Falling back to entire page search.")
            anchors = soup.find_all("a", href=lambda h: h and "/en/support/announcement/" in h)
        
        if not anchors:
            logging.error("No announcement anchors found with Selenium.")
            return

        seen = set()
        announcements = []
        for a in anchors:
            title = a.get_text(strip=True)
            href = a.get("href")
            if not href:
                continue
            if href.startswith("/"):
                href = "https://www.binance.com" + href
            if href in seen:
                continue
            seen.add(href)
            announcements.append((title, href))
        
        # Print the first 5 announcements (or fewer if not enough are found)
        print("\nLast 5 Binance Listing Announcements:")
        print("=" * 40)
        for i, (title, href) in enumerate(announcements[:5], start=1):
            print(f"Announcement {i}:")
            print(f"Title: {title}")
            print(f"Link:  {href}")
            print("-" * 40)
    except Exception as e:
        logging.error(f"Error in test_fetch_binance_with_selenium: {e}")
    finally:
        driver.quit()

if __name__ == "__main__":
    test_fetch_binance_with_selenium()

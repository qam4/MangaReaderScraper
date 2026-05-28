import undetected_chromedriver as undetected_uc
import requests
import cloudscraper
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
# from requests_html import HTMLSession
import os
from selenium.webdriver.chrome.service import Service as ChromeService # Similar thing for firefox also!
from selenium.webdriver.support.ui import WebDriverWait
from subprocess import CREATE_NO_WINDOW # This flag will only be available in windows
from bs4 import BeautifulSoup

import nodriver as nd
import asyncio

import time


# TODO: cloudflare https://stackoverflow.com/questions/49087990/python-request-being-blocked-by-cloudflare
# . https://pypi.org/project/cloudscraper/
# . https://pypi.org/project/cfscrape/ last release 2.1.1 Feb 22, 2020, not maintained, no longer works
# . https://pypi.org/project/undetected-chromedriver/

def save_html_to_file(html_content, filename):
    """
    Saves a string of HTML content to a specified file.
    """
    with open(filename, 'w', encoding='utf-8') as file:
        file.write(html_content)
    print(f"File saved successfully as {filename}")


# if __name__ == '__main__':
    # url = 'https://v12.mkklcdnv6tempv4.com/img/tab_12/00/00/52/aa951409/chapter_1_romance_dawn/1-o.jpg'
    # url = 'https://cm.blazefast.co/b0/ca/b0ca266f3948c6492d425965a0d1929b.jpg'
    # url = 'https://s10.mpypl.org/media/2002/5e8/675386f4070f8dbfb3ce08e5/61091033_1100_1586_457391.jpeg'
    # url = 'https://iweb_2.mangapicgallery.com/r/newpiclink/one_piece/1155/24c63de3d8aed45c2f49590b936ff649.jpeg'
    # url = 'https://images.mangafreak.me/mangas/one_piece/one_piece_1/one_piece_1_1.jpg'
    # url = 'https://images.mangafreak.me/mangas/one_piece/one_piece_1133/one_piece_1133_1.jpg'
    # url = 'https://www.mangago.me/r/l_search/?name=one+piece'
    # url = 'https://www.mangago.me/read-manga/one_piece/mr/mk_chapter-1132/pg-1/'
    # url = 'https://mangapark.io/title/10953-en-one-piece/9286331-chapter-1133-praise'
    # url = 'https://mangapark.io/title/10953-en-one-piece/115101-vol-01-ch-001'
    # url = 'https://s23.mbcdnsax.org/res/manga/kingdom/vol-1-chapter-1-the-unknown-boy/1.jpg'
    # url = 'https://s2.mbcdnsab.org/res/manga/kingdom/chapter-735-5/2f8d6b03edaa79472dbd877611e21ebe.png'
    # url = 'https://s10.mpubn.org/media/00004/images/e8/36/e836fcd185b0720e60568856acc9db217e15f11a_56774_552_902.jpg'
    # url = 'https://mangabuddy.com/kingdom/chapter-735-5'
    # url_img = 'https://s2.mbcdnsab.org/res/manga/kingdom/chapter-735-5/2f8d6b03edaa79472dbd877611e21ebe.png'
    # downloaded_image = 'image.png'

    # headers = {"Referer": "https://mangabuddy.com/"}


    # response = requests.get(url_img, headers=headers)

    ## using uc
    # driver = uc.Chrome(headless=True,use_subprocess=False)
    # driver.get(url)
    # time.sleep(10)
    # text = driver.page_source
    # print(f"text={text.encode('utf-8', errors='ignore')}")
    # driver.save_screenshot('nowsecure.png')

    # scraper = cloudscraper.create_scraper()
    # response = scraper.get(url)
    # text = response.text.encode('utf-8', errors='ignore')
    # print(f"status={response.status_code}, text={text}")

    # response = requests.get(url)
    # text = response.text.encode('utf-8', errors='ignore')
    # print(f"status={response.status_code}, text={text}")


    # # Check if the request was successful
    # if response.status_code == 200:
    #     # Open a file in binary write mode
    #     with open("image.jpg", "wb") as f:
    #         # Write the content of the response to the file
    #         f.write(response.content)
    #     print("Image successfully downloaded.")
    # else:
    #     print("Failed to download image.")

    # ## Selenium
    # # Selenium with 10s delay works to load javascripts
    # chrome_service = ChromeService()
    # # Use `chrome_service.creationflags` for selenium < 4.6
    # chrome_service.creation_flags = CREATE_NO_WINDOW

    # # driver = webdriver.Chrome(service=chrome_service)

    # options = Options()
    # options.add_argument('--headless')
    # # options.add_experimental_option('excludeSwitches', ['enable-logging'])
    # driver = webdriver.Chrome(options=options, service=chrome_service)
    # driver.get(url)
    # time.sleep(3)
    # # text = driver.page_source
    # text = driver.page_source.encode('utf-8', errors='ignore')
    # print(f"text={text}")

    # PhantomJS support was removed from Selenium after 3.3.0.
    # browser = webdriver.PhantomJS()
    # browser.get(url)
    # html = browser.page_source.encode('utf-8', errors='ignore')
    # print(f"text={html}")


    # session = HTMLSession()
    # response = session.get(url)
    # response.html.render()

    # print(response)


    # # 1. Initialize Selenium (undetected_chromedriver) and navigate to the target site
    # driver = uc.Chrome()
    # driver.get(url) # Replace with your target URL
    # time.sleep(2)  # Adjust sleep time to allow Cloudflare challenges to resolve

    # # driver.save_screenshot('nowsecure.png')

    # # 2. Extract cookies and user-agent from Selenium
    # selenium_cookies = driver.get_cookies()
    # user_agent = driver.execute_script("return navigator.userAgent")

    # # 3. Prepare cookies and headers for the requests session
    # requests_cookies = {cookie['name']: cookie['value'] for cookie in selenium_cookies}
    # headers = {"user-agent": user_agent, "Referer": "https://mangabuddy.com/"}

    # # 4. Create a Requests session and use the collected cookies and headers
    # session = requests.Session()
    # response = session.get(url_img, headers=headers, cookies=requests_cookies) # Replace with the image URL

    # # 5. Handle the image download response
    # if response.status_code == 200:
    #     with open("downloaded_image.jpg", "wb") as f:
    #         f.write(response.content)
    #     print("Image downloaded successfully!")
    # else:
    #     print(f"Failed to download image. Status code: {response.status_code}")

    # driver.quit()

    # print(cloudscraper.__version__)
    # try:
    #     # scraper = cloudscraper.create_scraper()  # returns a CloudScraper instance
    #     scraper = cloudscraper.create_scraper(
    #         debug=True,  # Enable for monitoring (disable in production)

    #         # 🔑 KEY SETTINGS to prevent 403 errors
    #         min_request_interval=2.0,      # CRITICAL: Prevents TLS blocking
    #         max_concurrent_requests=1,     # CRITICAL: Prevents concurrent conflicts
    #         rotate_tls_ciphers=True,       # CRITICAL: Avoids cipher detection

    #         # 🛡️ Enhanced protection
    #         auto_refresh_on_403=True,      # Auto-recover from 403 errors
    #         max_403_retries=3,             # Max retry attempts
    #         session_refresh_interval=1800, # Refresh session every 30 minutes

    #         # 🥷 Optimized stealth mode
    #         enable_stealth=True,
    #         stealth_options={
    #             'min_delay': 1.0,          # Reasonable delays
    #             'max_delay': 3.0,
    #             'human_like_delays': True,
    #             'randomize_headers': True,
    #             'browser_quirks': True
    #         }
    #     )
    #     response = scraper.get(url)
    #     response.raise_for_status()
    #     response = scraper.get(url_img, stream=True)
    #     response.raise_for_status()

    #     with open(downloaded_image, 'wb') as f:
    #         for chunk in response.iter_content(chunk_size=8192):
    #             f.write(chunk)
    #     print(f"Image downloaded successfully to {downloaded_image}")

    # except Exception as e:
    #     print(f"Error downloading image: {e}")

    # scraper = cloudscraper.create_scraper(debug=True)
    # response = scraper.get(url)
    # response.raise_for_status() # Raise an exception for bad status codes
    # soup = BeautifulSoup(response.text, 'html.parser')

    # image_tags = soup.find_all('img')
    # image_urls = []
    # for img_tag in image_tags:
    #     src = img_tag.get('src')
    #     if src:
    #         # Handle relative URLs and construct absolute URLs if necessary
    #         if not src.startswith(('http://', 'https://')):
    #             # Assuming base URL for relative paths
    #             src = url.split('/')[0] + '//' + url.split('/')[2] + src
    #         image_urls.append(src)

    # download_dir = "downloaded_images"
    # os.makedirs(download_dir, exist_ok=True)

    # for img_url in image_urls:
    #     try:
    #         img_data = scraper.get(img_url).content
    #         filename = os.path.join(download_dir, os.path.basename(img_url))
    #         with open(filename, 'wb') as f:
    #             f.write(img_data)
    #         print(f"Downloaded: {filename}")
    #     except Exception as e:
    #         print(f"Error downloading {img_url}: {e}")

    # url = 'https://mangafire.to/manga/ad-astra-scipio-and-hanniball.lww3'
    # url = 'https://mangafire.to/read/ad-astra-scipio-and-hanniball.lww3/en/chapter-78'

    # chrome_service = ChromeService()
    # # chrome_service.creation_flags = CREATE_NO_WINDOW
    # options = webdriver.ChromeOptions()
    # # options.add_argument("--headless=new")
    # # options.add_argument("--headless")
    # driver = webdriver.Chrome(options=options, service=chrome_service)
    # driver.get(url)
    # WebDriverWait(driver, 10)  # waits up to 10 seconds
    # time.sleep(5)   # wait for the page to load completely
    # text = driver.page_source
    # driver.quit()
    # html = BeautifulSoup(text, features="lxml")
    # # print(f"html={html}")
    # save_html_to_file(str(html), "mangafire.html")

    # import undetected_chromedriver as uc
    # import time
    # from bs4 import BeautifulSoup

    # url = 'https://mangafire.to/read/ad-astra-scipio-and-hanniball.lww3/en/chapter-78'

    # # Use uc.Chrome instead of the standard webdriver.Chrome
    # options = uc.ChromeOptions()
    # # options.add_argument("--headless") # Headless mode can still be detected; try headed first
    # driver = uc.Chrome(options=options)

    # driver.get(url)

    # # Instead of just sleeping, wait for a specific element unique to the manga reader
    # time.sleep(10) # Give Cloudflare extra time to verify you

    # text = driver.page_source
    # driver.quit()

    # html = BeautifulSoup(text, features="lxml")
    # # Save or process your HTML
    # save_html_to_file(str(html), "mangafire.html")


async def load_mangafire_images(page):
    """Scroll each manga image into view so the lazy loader assigns src values."""
    images = await page.query_selector_all('main img')
    print(f"found {len(images)} image elements")

    if not images:
        images = await page.query_selector_all('img')
        print(f"fallback to {len(images)} img elements")

    for idx, image in enumerate(images, start=1):
        await image.scroll_into_view()
        await page.wait(0.25)
        if idx % 5 == 0:
            images = await page.query_selector_all('main img')
            loaded_now = sum(1 for img in images if 'src' in img.attrs)
            print(f"scrolled to image {idx}, loaded source count={loaded_now}")

    await page.wait(2)
    images = await page.query_selector_all('main img')
    loaded_urls = [img.attrs['src'] for img in images if 'src' in img.attrs]

    if len(loaded_urls) < len(images):
        print(
            f"only {len(loaded_urls)}/{len(images)} images loaded, retrying missing images"
        )
        for image in images:
            if 'src' not in image.attrs:
                await image.scroll_into_view()
                await page.wait(0.25)
        await page.wait(2)
        images = await page.query_selector_all('main img')
        loaded_urls = [img.attrs['src'] for img in images if 'src' in img.attrs]

    print(f"total loaded image URLs={len(loaded_urls)}")
    return loaded_urls

async def main():
    # profile_path = os.path.join(os.getcwd(), "my_nodriver_profile")
    url = 'https://mangafire.to/read/ad-astra-scipio-and-hanniball.lww3/en/chapter-78'
    browser = await nd.start()  # user_data_dir=profile_path
    page = await browser.get(url)

    # Wait for the site's reader to load its initial markup
    await page.wait(5)

    image_urls = await load_mangafire_images(page)
    print(f"loaded {len(image_urls)} mangafire image URLs")

    content = await page.get_content()
    save_html_to_file(str(content), "mangafire.html")
    
    browser.stop()

def test_mangafire_search(query="one piece"):
    options = Options()
    options.add_argument('--headless')
    driver = undetected_uc.Chrome(options=options)
    driver.get('https://mangafire.to/filter')
    input_element = driver.find_element(By.NAME, 'keyword')
    driver.execute_script("document.querySelector('input[name=\"keyword\"]').value = '" + query + "';")
    driver.find_element(By.CSS_SELECTOR, 'a[href="filter"]').click()
    time.sleep(5)
    print(f"Current URL: {driver.current_url}")
    html = driver.page_source
    driver.quit()
    
    save_html_to_file(str(html), "mangafire_search.html")
    
    # Check if results are loaded
    soup = BeautifulSoup(html, 'html.parser')
    results = soup.find_all('div', class_='item')
    print(f"Found {len(results)} search results for '{query}'")
    
    if results:
        first_result = results[0]
        title = first_result.find('h3', class_='title')
        if title:
            print(f"First result title: {title.text.strip()}")
        link = first_result.find('a', href=True)
        if link:
            print(f"First result link: {link['href']}")
    
    # browser.stop()

if __name__ == '__main__':
    # uc.loop().run_until_complete(main())
    # Uncomment to test search
    test_mangafire_search()


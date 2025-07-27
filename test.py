import undetected_chromedriver as uc
import requests
import cloudscraper
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
# from requests_html import HTMLSession
import os
from selenium.webdriver.chrome.service import Service as ChromeService # Similar thing for firefox also!
from subprocess import CREATE_NO_WINDOW # This flag will only be available in windows


import time


# TODO: cloudflare https://stackoverflow.com/questions/49087990/python-request-being-blocked-by-cloudflare
# . https://pypi.org/project/cloudscraper/
# . https://pypi.org/project/cfscrape/ last release 2.1.1 Feb 22, 2020, not maintained, no longer works
# . https://pypi.org/project/undetected-chromedriver/


if __name__ == '__main__':
    # url = 'https://v12.mkklcdnv6tempv4.com/img/tab_12/00/00/52/aa951409/chapter_1_romance_dawn/1-o.jpg'
    # url = 'https://cm.blazefast.co/b0/ca/b0ca266f3948c6492d425965a0d1929b.jpg'
    # url = 'https://s10.mpypl.org/media/2002/5e8/675386f4070f8dbfb3ce08e5/61091033_1100_1586_457391.jpeg'
    # url = 'https://iweb_2.mangapicgallery.com/r/newpiclink/one_piece/1155/24c63de3d8aed45c2f49590b936ff649.jpeg'
    # url = 'https://images.mangafreak.me/mangas/one_piece/one_piece_1/one_piece_1_1.jpg'
    # url = 'https://images.mangafreak.me/mangas/one_piece/one_piece_1133/one_piece_1133_1.jpg'
    # url = 'https://www.mangago.me/r/l_search/?name=one+piece'
    # url = 'https://www.mangago.me/read-manga/one_piece/mr/mk_chapter-1132/pg-1/'
    # url = 'https://mangapark.io/title/10953-en-one-piece/9286331-chapter-1133-praise'
    url = 'https://mangapark.io/title/10953-en-one-piece/115101-vol-01-ch-001'

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

    # Selenium with 10s delay works to load javascripts
    chrome_service = ChromeService()
    # Use `chrome_service.creationflags` for selenium < 4.6
    chrome_service.creation_flags = CREATE_NO_WINDOW

    # driver = webdriver.Chrome(service=chrome_service)

    options = Options()
    options.add_argument('--headless')
    # options.add_experimental_option('excludeSwitches', ['enable-logging'])
    driver = webdriver.Chrome(options=options, service=chrome_service)
    driver.get(url)
    time.sleep(3)
    # text = driver.page_source
    text = driver.page_source.encode('utf-8', errors='ignore')
    print(f"text={text}")

    # PhantomJS support was removed from Selenium after 3.3.0.
    # browser = webdriver.PhantomJS()
    # browser.get(url)
    # html = browser.page_source.encode('utf-8', errors='ignore')
    # print(f"text={html}")


    # session = HTMLSession()
    # response = session.get(url)
    # response.html.render()

    # print(response)

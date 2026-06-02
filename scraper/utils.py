import configparser
import functools
import logging
import pdb
import re
import sys
import tempfile
import time
import undetected_chromedriver as uc  # type: ignore
from logging import Logger, LoggerAdapter
from pathlib import Path
from typing import Any, Callable, MutableMapping, Optional, Tuple, Union
from selenium import webdriver  # type: ignore
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.support.ui import WebDriverWait
from subprocess import CREATE_NO_WINDOW

import bs4
import requests  # type: ignore
import cloudscraper  # type: ignore
from requests.adapters import HTTPAdapter  # type: ignore
from urllib3.util.retry import Retry

from scraper.exceptions import CannotExtractChapter


class CustomAdapter(LoggerAdapter):
    """
    Prepends manga name & volume to the logger message
    """

    def process(
        self, msg: str, kwargs: MutableMapping[str, Union[str, int]]
    ) -> Tuple[str, MutableMapping[str, Union[str, int]]]:
        manga = self.extra.get("manga")
        volume = self.extra.get("volume")
        if volume:
            return f"[{manga}:{volume}] {msg}", kwargs
        return f"[{manga}] {msg}", kwargs


def get_adapter(
    logger: Logger, manga: str, volume: Optional[Union[str, int]] = None  # noqa: E251
) -> CustomAdapter:
    if volume:
        extra = {"manga": manga, "volume": volume}
    else:
        extra = {"manga": manga}
    return CustomAdapter(logger, extra)


def get_html_from_url(url: str, type: Optional[str] = "requests") -> bs4.BeautifulSoup:
    """
    Download the HTML text from a given url
    """
    if type == "requests":
        req = requests.get(url)
        req.raise_for_status()
        text = req.text
    elif type == "cloudscraper":
        scraper = cloudscraper.create_scraper()
        req = scraper.get(url)
        req.raise_for_status()
        text = req.text
    elif type == "uc":
        # issue: AssertionError: daemonic processes are not allowed to have children
        driver = uc.Chrome(headless=True, use_subprocess=False)
        driver.get(url)
        time.sleep(10)
        text = driver.page_source
    elif type == "selenium":
        chrome_service = ChromeService()
        chrome_service.creation_flags = CREATE_NO_WINDOW
        options = webdriver.ChromeOptions()
        # options.add_argument("--headless=new")
        # options.add_argument("--headless")
        driver = webdriver.Chrome(options=options, service=chrome_service)
        driver.get(url)

        # # Wait until document.readyState is 'complete'
        # WebDriverWait(driver, 10).until(
        #     lambda driver: driver.execute_script("return document.readyState") == "complete"
        # )
        WebDriverWait(driver, 10)  # waits up to 10 seconds
        time.sleep(5)  # wait for the page to load completely
        text = driver.page_source
        # logging.info(f"text={text}")
        driver.quit()
    elif type == "nodriver":
        import asyncio
        import nodriver as nd
        from pathlib import Path

        async def fetch():
            profile_path = Path(tempfile.mkdtemp(prefix="nodriver_profile_"))
            browser = await nd.start(
                user_data_dir=profile_path,
                headless=True,
                sandbox=False,
                no_sandbox=True,
            )
            page = await browser.get(url)
            await page.wait(5)
            content = await page.get_content()
            browser.stop()
            return content

        text = asyncio.run(fetch())
    else:
        logging.error(f"type not supported {type}")

    html = bs4.BeautifulSoup(text, features="lxml")
    return html


def download_timer(func: Callable) -> Callable:
    """
    Manga volume(s) download timer
    """

    @functools.wraps(func)
    def wrapper_timer(*args) -> Any:
        """
        Assumes last arg is the volume digit
        """
        start = time.time()
        returned = func(*args)
        run_time = round(time.time() - start, 1)
        logging.info(f"Volumes downloaded in {run_time} seconds")
        return returned

    return wrapper_timer


def create_base_config() -> None:
    config = configparser.ConfigParser()
    config.add_section("config")

    user_home = Path.home()
    downloaddir = user_home / "Downloads"
    configdir = user_home / ".config"
    for path in [downloaddir, configdir]:
        path.mkdir(parents=True, exist_ok=True)

    config["config"]["manga_directory"] = str(downloaddir)
    config["config"]["manga_bundle_directory"] = str(downloaddir)
    config["config"]["source"] = "mangareader"
    config["config"]["filetype"] = "pdf"
    config["config"]["upload_root"] = "/"

    configfile = configdir / "mangascraper.ini"
    with configfile.open("w") as cf:
        config.write(cf)


def settings() -> configparser.ConfigParser:
    """
    Retrieve settings file contents
    """
    config = configparser.ConfigParser()
    user_config = Path.home() / ".config" / "mangascraper.ini"
    if not user_config.exists():
        create_base_config()
    config.read(str(user_config))
    return config


def extract_chapter_number(chapter_string: str) -> str:
    """
    Extracts the chapter digit substring in a string that
    details manga chapter number
    """
    chapter_split = chapter_string.lower().split()
    if "chapter" not in chapter_split:
        raise CannotExtractChapter(
            f"Can't find chapter substring in {chapter_string.split()}"
        )
    chapter_name_index = chapter_split.index("chapter") + 1
    chapter_string = chapter_split[chapter_name_index]
    chapter_string_split = re.split(r"\D", chapter_string)
    if "" in chapter_string_split:
        chapter_string_split.remove("")
    chapter_number = chapter_string_split[0]
    if not chapter_number.isdigit():
        raise CannotExtractChapter(f"Cannot find chapter digit in {chapter_number}")
    if len(chapter_number) > 1 and chapter_number.startswith("0"):
        chapter_number = chapter_number.lstrip("0")
    return chapter_number


def request_session(max_attempts: int = 10, intervals: float = 0.2) -> requests.Session:
    """
    Requests session with custom max reattempts and time intervals

    Usage:
      req = request_session()
      req.get(<url>)
    """
    req = requests.Session()
    retries = Retry(total=max_attempts, backoff_factor=intervals)
    for protocol in ["http", "https"]:
        req.mount(f"{protocol}://", HTTPAdapter(max_retries=retries))
    return req


def menu_input(msg="", prompt=">> "):
    """
    Custom input with error handeling
    """
    try:
        text = f"\n{msg}\n\n{prompt}" if msg else f"\n{prompt}"
        choice = input(text)
        if choice.lower() in ["q", "quit"]:
            raise KeyboardInterrupt
        return choice
    except KeyboardInterrupt:
        print("\nExiting...\n")
        sys.exit()


class ForkedPdb(pdb.Pdb):
    """
    A Pdb subclass that may be used from a forked multiprocessing child
    """

    def interaction(self, *args, **kwargs) -> None:
        _stdin = sys.stdin
        try:
            sys.stdin = open("/dev/stdin")
            pdb.Pdb.interaction(self, *args, **kwargs)  # type: ignore
        finally:
            sys.stdin = _stdin

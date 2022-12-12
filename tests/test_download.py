import os
import shutil
from pathlib import Path

import pytest

from scraper.__main__ import download_manga
from scraper.download import Download
from tests.helpers import MockedSiteParser


@pytest.mark.parametrize("filetype,file_signature", [("pdf", "%PDF-"), ("cbz", "PK")])
def test_download_manga(filetype, file_signature):
    downloader = Download("dragon-ball", filetype, MockedSiteParser)
    downloader.download_volumes(["1"])
    expected_path = f"/tmp/dragon-ball/dragon-ball_chapter_1.{filetype}"
    assert os.path.exists(expected_path)
    # check file for PDF/CBZ signature
    with open(expected_path, "rb") as pdf_file:
        pdf = pdf_file.read().decode("utf-8", "ignore")
        assert pdf.startswith(file_signature)


def test_download_manga_helper_function(parser):
    download_manga(
        manga_url="dragon-ball",
        manga_title="",
        volumes=["1", "2"],
        filetype="pdf",
        parser=MockedSiteParser,
        preferred_name="cool_mo_deep",
    )
    expected_path = "/tmp/cool_mo_deep/cool_mo_deep_chapter_1.pdf"
    expected_path2 = "/tmp/cool_mo_deep/cool_mo_deep_chapter_2.pdf"
    assert os.path.exists(expected_path)
    assert os.path.exists(expected_path2)


def test_download_manga_helper_function_preferred_name(parser):
    download_manga("dragon-ball", "", ["1", "2"], "pdf", MockedSiteParser)
    expected_path = "/tmp/dragon-ball/dragon-ball_chapter_1.pdf"
    expected_path2 = "/tmp/dragon-ball/dragon-ball_chapter_2.pdf"
    assert os.path.exists(expected_path)
    assert os.path.exists(expected_path2)


def teardown_function():
    """
    Remove directories after every test, if present

    Fixtures only work before a test is executed, hence
    the need for this module teardown.
    """
    directories = ["/tmp/cool_mo_deep/", "/tmp/dragon-ball/"]
    for directory in directories:
        if Path(directory).exists():
            shutil.rmtree(directory)

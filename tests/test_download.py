import pytest

from scraper.__main__ import download_manga
from scraper.download import Download
from tests.helpers import MockedSiteParser


@pytest.mark.parametrize("filetype,file_signature", [("pdf", "%PDF-"), ("cbz", "PK")])
def test_download_manga(filetype, file_signature, manga_directory):
    downloader = Download("dragon-ball", filetype, MockedSiteParser)
    downloader.download_chapters(["1"])
    expected_path = manga_directory / f"dragon-ball/dragon-ball_chapter_1.{filetype}"
    assert expected_path.exists()
    # check file for PDF/CBZ signature
    with open(expected_path, "rb") as pdf_file:
        pdf = pdf_file.read().decode("utf-8", "ignore")
        assert pdf.startswith(file_signature)


def test_download_manga_helper_function(parser, manga_directory):
    download_manga(
        manga_url="dragon-ball",
        manga_title="",
        chapters=["1", "2"],
        filetype="pdf",
        parser=MockedSiteParser,
        preferred_name="cool_mo_deep",
    )
    assert (manga_directory / "cool_mo_deep/cool_mo_deep_chapter_1.pdf").exists()
    assert (manga_directory / "cool_mo_deep/cool_mo_deep_chapter_2.pdf").exists()


def test_download_manga_helper_function_preferred_name(parser, manga_directory):
    download_manga("dragon-ball", "", ["1", "2"], "pdf", MockedSiteParser)
    assert (manga_directory / "dragon-ball/dragon-ball_chapter_1.pdf").exists()
    assert (manga_directory / "dragon-ball/dragon-ball_chapter_2.pdf").exists()

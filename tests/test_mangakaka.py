from unittest import mock

import pytest

from scraper.exceptions import VolumeDoesntExist
from scraper.parsers.mangakaka import MangaKaka, MangaKakaMangaParser, MangaKakaSearch
from tests.helpers import MockedImgResponse

METADATA = {
    "1": {
        "title": "Dragon Ball",
        "manga_url": "read_dragon_ball_manga_online_for_free2",
        "chapters": "520.5",
        "source": "mangakaka",
    },
    "2": {
        "title": "Dragon Ball Super",
        "manga_url": "dragon_ball_super",
        "chapters": "55",
        "source": "mangakaka",
    },
    "3": {
        "title": "Dragon Ball Chou",
        "manga_url": "read_dragon_ball_chou",
        "chapters": "42",
        "source": "mangakaka",
    },
    "4": {
        "title": "Dragon Ball SD",
        "manga_url": "read_dragon_ball_sd",
        "chapters": "34",
        "source": "mangakaka",
    },
    "5": {
        "title": "Dragon Ball Heroes - Victory Mission",
        "manga_url": "iaw1400679346",
        "chapters": "19",
        "source": "mangakaka",
    },
    "6": {
        "title": "The Dragon Knight's Beloved",
        "manga_url": "no920419",
        "chapters": "2",
        "source": "mangakaka",
    },
    "7": {
        "title": "Dragon Ball Full Color Freeza Arc",
        "manga_url": "jj921224",
        "chapters": "75",
        "source": "mangakaka",
    },
    "8": {
        "title": "Dragon Ball GT",
        "manga_url": "ca917828",
        "chapters": "39",
        "source": "mangakaka",
    },
    "9": {
        "title": "Dragon Ball Heroes: Victory Mission",
        "manga_url": "fm917877",
        "chapters": "19",
        "source": "mangakaka",
    },
    "10": {
        "title": "Dragon Ball Full Color Saiyan Arc",
        "manga_url": "zv921223",
        "chapters": "34",
        "source": "mangakaka",
    },
    "11": {
        "title": "Super Dragon Ball Heroes: Dark Demon Realm Mission!",
        "manga_url": "dq920989",
        "chapters": "14",
        "source": "mangakaka",
    },
    "12": {
        "title": "Super Dragon Ball Heroes: Universe Mission",
        "manga_url": "xy919160",
        "chapters": "1",
        "source": "mangakaka",
    },
}


def test_page_urls(mangakaka_volume_html):
    with mock.patch("scraper.parsers.mangakaka.get_html_from_url") as mocked_func:
        mocked_func.return_value = mangakaka_volume_html
        parser = MangaKakaMangaParser("dragon-ball")
        page_urls = parser.page_urls("1")
        expected = [
            (
                1,
                "https://s5.mkklcdnv5.com/mangakakalot/d2/dragon_ball_super/chapter_1_the_god_of_destructions_prophetic_dream/1.jpg",
            ),
            (
                2,
                "https://s5.mkklcdnv5.com/mangakakalot/d2/dragon_ball_super/chapter_1_the_god_of_destructions_prophetic_dream/2.jpg",
            ),
            (
                3,
                "https://s5.mkklcdnv5.com/mangakakalot/d2/dragon_ball_super/chapter_1_the_god_of_destructions_prophetic_dream/3.jpg",
            ),
            (
                4,
                "https://s5.mkklcdnv5.com/mangakakalot/d2/dragon_ball_super/chapter_1_the_god_of_destructions_prophetic_dream/4.jpg",
            ),
            (
                5,
                "https://s5.mkklcdnv5.com/mangakakalot/d2/dragon_ball_super/chapter_1_the_god_of_destructions_prophetic_dream/5.jpg",
            ),
            (
                6,
                "https://s5.mkklcdnv5.com/mangakakalot/d2/dragon_ball_super/chapter_1_the_god_of_destructions_prophetic_dream/6.jpg",
            ),
            (
                7,
                "https://s5.mkklcdnv5.com/mangakakalot/d2/dragon_ball_super/chapter_1_the_god_of_destructions_prophetic_dream/7.jpg",
            ),
            (
                8,
                "https://s5.mkklcdnv5.com/mangakakalot/d2/dragon_ball_super/chapter_1_the_god_of_destructions_prophetic_dream/8.jpg",
            ),
            (
                9,
                "https://s5.mkklcdnv5.com/mangakakalot/d2/dragon_ball_super/chapter_1_the_god_of_destructions_prophetic_dream/9.jpg",
            ),
            (
                10,
                "https://s5.mkklcdnv5.com/mangakakalot/d2/dragon_ball_super/chapter_1_the_god_of_destructions_prophetic_dream/10.jpg",
            ),
            (
                11,
                "https://s5.mkklcdnv5.com/mangakakalot/d2/dragon_ball_super/chapter_1_the_god_of_destructions_prophetic_dream/11.jpg",
            ),
            (
                12,
                "https://s5.mkklcdnv5.com/mangakakalot/d2/dragon_ball_super/chapter_1_the_god_of_destructions_prophetic_dream/12.jpg",
            ),
            (
                13,
                "https://s5.mkklcdnv5.com/mangakakalot/d2/dragon_ball_super/chapter_1_the_god_of_destructions_prophetic_dream/13.jpg",
            ),
            (
                14,
                "https://s5.mkklcdnv5.com/mangakakalot/d2/dragon_ball_super/chapter_1_the_god_of_destructions_prophetic_dream/14.jpg",
            ),
            (
                15,
                "https://s5.mkklcdnv5.com/mangakakalot/d2/dragon_ball_super/chapter_1_the_god_of_destructions_prophetic_dream/15.jpg",
            ),
            (
                16,
                "https://s5.mkklcdnv5.com/mangakakalot/d2/dragon_ball_super/chapter_1_the_god_of_destructions_prophetic_dream/16.jpg",
            ),
        ]

        assert page_urls == expected


def test_all_volume_numbers(mangakaka_manga_title_page_html):
    with mock.patch("scraper.parsers.mangakaka.get_html_from_url") as mocked_func:
        mocked_func.return_value = mangakaka_manga_title_page_html
        parser = MangaKakaMangaParser("dragon-ball")
        all_vols = parser.all_volume_numbers()
        assert all_vols == {
            "1",
            "2",
            "4",
            "5",
            "6",
            "7",
            "7.2",
            "8",
            "8.2",
            "9",
            "10",
            "11",
            "12",
            "13",
            "14",
            "15",
            "16",
            "17",
            "18",
            "19",
            "20",
            "21",
            "22",
            "23",
            "24",
            "25",
            "26",
            "27",
            "28",
            "29",
            "30",
            "31",
            "32",
            "33",
            "34",
            "35",
            "36",
            "37",
            "38",
            "39",
            "40",
            "41",
            "42",
            "43",
            "44",
            "45",
            "46",
            "47",
            "48",
            "49",
            "50",
            "51",
            "52",
            "53",
            "54",
            "55",
        }


def test_invalid_volume_parser(mangakaka_invalid_volume_html):
    with mock.patch("scraper.parsers.mangakaka.get_html_from_url") as mocked_func:
        mocked_func.return_value = mangakaka_invalid_volume_html
        parser = MangaKakaMangaParser("dragon-ball")
        with pytest.raises(VolumeDoesntExist):
            parser.page_urls("2000")


@mock.patch("scraper.parsers.mangakaka.requests.get")
def test_page_data(mocked_get, mangakaka_volume_html):
    mocked_get.return_value = MockedImgResponse()
    with mock.patch("scraper.parsers.mangakaka.get_html_from_url") as mocked_func:
        mocked_func.return_value = mangakaka_volume_html
        parser = MangaKakaMangaParser("dragon-ball")
        page_data = parser.page_data(
            (
                16,
                "https://s5.mkklcdnv5.com/mangakakalot/d2/dragon_ball_super/"
                "chapter_1_the_god_of_destructions_prophetic_dream/16.jpg",
            ),
        )
        page_num, img_data = page_data
        assert page_num == 16
        # ensure it is an JPEG
        assert "JFIF" in str(img_data[:15])


def test_get_search_results(mangakaka_search_html):
    with mock.patch("scraper.parsers.base.get_html_from_url") as mocked_func:
        mocked_func.return_value = mangakaka_search_html
        mangasearch = MangaKakaSearch("Dragon Ball")
        results = mangasearch.search()
        assert METADATA == results


def test_get_search_results_with_invalid_query(caplog, mangakaka_invalid_search_html):
    with mock.patch("scraper.parsers.base.get_html_from_url") as mocked_func:
        mocked_func.return_value = mangakaka_invalid_search_html
        with pytest.raises(SystemExit):
            mangasearch = MangaKakaSearch("gibbersish")
            mangasearch.search()
            assert caplog.text == "No search results found for gibberish"


def test_mangakaka_test_search_parser(mangakaka_search_html):
    with mock.patch("scraper.parsers.base.get_html_from_url") as mocked_func:
        mocked_func.return_value = mangakaka_search_html
        mr = MangaKaka()
        results = mr.search("a query")
        assert METADATA == results

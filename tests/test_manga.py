import shutil
from pathlib import Path

import pytest

from scraper.exceptions import ChapterAlreadyPresent, PageAlreadyPresent
from scraper.manga import Chapter, ChapterDownload, Manga, MangaBuilder, Page
from tests.helpers import MockedSiteParser


def test_page_repr(page):
    expected = f"Page(number={page.number}, img=True)"
    assert page.__repr__() == expected


def test_page_str(page):
    expected = f"Page(number={page.number}, img=True)"
    assert page.__str__() == expected


def test_chapter_add_page():
    chapter = Chapter("1", Path("/Some/path"), Path("/some/path"))
    chapter.add_page(1, b"bytes")
    assert chapter.page == {1: Page(number=1, img=b"bytes")}
    assert chapter.page[1] == Page(number=1, img=b"bytes")


def test_add_multiple_pages_to_chapter():
    page_data = [(1, b"here", "success"), (2, b"bye", "success")]
    chapter = Chapter("1", Path("/Some/path"), Path("/some/path"))
    chapter.pages = page_data
    assert chapter.page[1] == Page(number=1, img=b"here")
    assert chapter.page[2] == Page(number=2, img=b"bye")
    assert len(chapter.page) == 2


def test_chapter_repr(chapter):
    expected = f"Chapter(number={chapter.number}, file_path={chapter.file_path}, upload_path={chapter.upload_path}, pages={len(chapter.pages)})"
    assert chapter.__repr__() == expected


def test_chapter_str(chapter):
    expected = f"Chapter(number={chapter.number}, file_path={chapter.file_path}, upload_path={chapter.upload_path}, pages={len(chapter.pages)})"
    assert chapter.__str__() == expected


def test_chapter_iter(chapter):
    generator = chapter.__iter__()
    values = list(generator)
    assert values == chapter.pages


def test_chapter_total_pages(chapter):
    assert chapter.total_pages() == 2


def test_chapter_total_pages_counts_pages_not_max_number():
    # regression: total_pages must be a COUNT, not max(page number). With a gap
    # (pages 1 and 5) the count is 2, while max page number would be 5.
    chapter = Chapter("1", Path("/Some/path"), Path("/some/path"))
    chapter.add_page(1, b"a")
    chapter.add_page(5, b"b")
    assert chapter.total_pages() == 2


def test_cant_add_page_already_in_chapter(chapter):
    with pytest.raises(PageAlreadyPresent):
        chapter.add_page(1, b"something")


def test_pages_property_in_chapter_returns_a_sorted_list(chapter):
    chapter.add_page(12, b"blah")
    chapter.add_page(6, b"jk")
    chapter.add_page(3, b"something")
    img1 = open("tests/test_files/jpgs/test-manga_1_1.jpg", "rb")
    img2 = open("tests/test_files/jpgs/test-manga_1_2.jpg", "rb")
    expected = [
        Page(1, img1.read()),
        Page(2, img2.read()),
        Page(3, b"something"),
        Page(6, b"jk"),
        Page(12, b"blah"),
    ]
    assert chapter.pages == expected


def test_manga_get_chapter_path():
    manga = Manga("dragon-ball", "pdf")
    chapter_path = manga._chapter_path("1")
    expected = "/tmp/dragon-ball/dragon-ball_chapter_1.pdf"
    assert Path(chapter_path) == Path(expected)


def test_manga_get_chapter_path_cbz():
    manga = Manga("dragon-ball", "cbz")
    chapter_path = manga._chapter_path("1")
    expected = "/tmp/dragon-ball/dragon-ball_chapter_1.cbz"
    assert Path(chapter_path) == Path(expected)


def test_manga_add_chapter():
    manga = Manga("dragon-ball", "pdf")
    manga.add_chapter("1")
    v1 = Chapter(
        number="1",
        file_path=Path("/tmp/dragon-ball/dragon-ball_chapter_1.pdf"),
        upload_path=Path("/dragon-ball/dragon-ball_chapter_1.pdf"),
    )
    assert manga.chapters_dict == {"1": v1}
    assert manga.chapters_dict["1"] == v1


def test_add_multiple_chapters_to_manga():
    manga = Manga("dragon-ball", "pdf")
    manga.chapters = ["1", "2"]
    assert manga.chapters_dict["1"] == Chapter(
        number="1",
        file_path=Path("/tmp/dragon-ball/dragon-ball_chapter_1.pdf"),
        upload_path=Path("/dragon-ball/dragon-ball_chapter_1.pdf"),
    )
    assert manga.chapters_dict["2"] == Chapter(
        number="2",
        file_path=Path("/tmp/dragon-ball/dragon-ball_chapter_2.pdf"),
        upload_path=Path("/dragon-ball/dragon-ball_chapter_2.pdf"),
    )
    assert len(manga.chapters_dict) == 2


def test_cant_add_chapter_already_in_manga(manga):
    with pytest.raises(ChapterAlreadyPresent):
        manga.add_chapter("1")


def test_chapters_property_in_manga_returns_a_sorted_list(manga):
    manga.add_chapter("12")
    manga.add_chapter("4")
    v1 = Chapter(
        number="1",
        file_path=Path("/tmp/dragon-ball/dragon-ball_chapter_1.pdf"),
        upload_path=Path("/dragon-ball/dragon-ball_chapter_1.pdf"),
    )
    v2 = Chapter(
        number="2",
        file_path=Path("/tmp/dragon-ball/dragon-ball_chapter_2.pdf"),
        upload_path=Path("/dragon-ball/dragon-ball_chapter_2.pdf"),
    )
    v3 = Chapter(
        number="4",
        file_path=Path("/tmp/dragon-ball/dragon-ball_chapter_4.pdf"),
        upload_path=Path("/dragon-ball/dragon-ball_chapter_4.pdf"),
    )
    v4 = Chapter(
        number="12",
        file_path=Path("/tmp/dragon-ball/dragon-ball_chapter_12.pdf"),
        upload_path=Path("/dragon-ball/dragon-ball_chapter_12.pdf"),
    )
    v1.pages = [(1, b"here", "success"), (2, b"bye", "success")]
    v2.pages = [(1, b"hello", "success"), (2, b"jimmy", "success")]
    expected = [
        v1,
        v2,
        v3,
        v4,
    ]
    assert manga.chapters == expected


def test_get_page_2_from_manga(manga):
    assert manga.chapters_dict["1"].page[2] == Page(2, b"bye")


def test_manga_repr(manga):
    expected = f"Manga(name={manga.name}, chapters={len(manga.chapters)})"
    assert manga.__repr__() == expected


def test_manga_str(manga):
    expected = f"Manga(name={manga.name}, chapters={len(manga.chapters)})"
    assert manga.__str__() == expected


def test_manga_iter(manga):
    generator = manga.__iter__()
    values = list(generator)
    assert values == manga.chapters


@pytest.mark.parametrize("inval", [["1", "2", "3"], None])
def test_mangabuilder_get_all_chapters(inval):
    parser = MockedSiteParser()
    builder = MangaBuilder(parser)
    manga = builder.get_manga_chapters(chapter_ids=inval)
    v1 = Chapter(
        number="1",
        file_path=Path("/tmp/dragon-ball/dragon-ball_chapter_1_1.pdf"),
        upload_path=Path("/dragon-ball/dragon-ball_chapter_1_1.pdf"),
    )
    v2 = Chapter(
        number="2",
        file_path=Path("/tmp/dragon-ball/dragon-ball_chapter_2_2.pdf"),
        upload_path=Path("/dragon-ball/dragon-ball_chapter_2_2.pdf"),
    )
    v3 = Chapter(
        number="3",
        file_path=Path("/tmp/dragon-ball/dragon-ball_chapter_3_3.pdf"),
        upload_path=Path("/dragon-ball/dragon-ball_chapter_3_3.pdf"),
    )
    img1 = open("tests/test_files/jpgs/test-manga_1_1.jpg", "rb")
    img2 = open("tests/test_files/jpgs/test-manga_1_2.jpg", "rb")
    pages = [(1, img1.read(), "success"), (2, img2.read(), "success")]
    v1.pages = pages
    v2.pages = pages
    v3.pages = pages
    assert manga.chapters_dict["2"].upload_path == v2.upload_path
    assert manga.chapters_dict["1"].page[1] == v1.page[1]
    assert manga.chapters_dict["3"].page[2] == v1.page[2]
    assert manga.chapters == [v1, v2, v3]


def test_mangabuilder_get_single_chapters(parser):
    parser = MockedSiteParser()
    builder = MangaBuilder(parser)
    manga = builder.get_manga_chapters(chapter_ids=["1"])
    v1 = Chapter(
        number="1",
        file_path=Path("/tmp/dragon-ball/dragon-ball_chapter_1_1.pdf"),
        upload_path=Path("/dragon-ball/dragon-ball_chapter_1_1.pdf"),
    )
    img1 = open("tests/test_files/jpgs/test-manga_1_1.jpg", "rb")
    img2 = open("tests/test_files/jpgs/test-manga_1_2.jpg", "rb")
    pages = [(1, img1.read(), "success"), (2, img2.read(), "success")]
    v1.pages = pages
    assert manga.chapters == [v1]
    assert manga.chapters_dict["1"] == v1
    assert manga.chapters_dict["1"].page[1] == v1.page[1]


def test_manga_builder_preferred_name(parser):
    parser = MockedSiteParser()
    builder = MangaBuilder(parser)
    manga = builder.get_manga_chapters(
        chapter_ids=["1"], preferred_name="smelly_pancakes"
    )
    v1 = Chapter(
        number="1",
        file_path=Path("/tmp/smelly_pancakes/smelly_pancakes_chapter_1_1.pdf"),
        upload_path=Path("/smelly_pancakes/smelly_pancakes_chapter_1_1.pdf"),
    )
    img1 = open("tests/test_files/jpgs/test-manga_1_1.jpg", "rb")
    img2 = open("tests/test_files/jpgs/test-manga_1_2.jpg", "rb")
    pages = [(1, img1.read(), "success"), (2, img2.read(), "success")]
    v1.pages = pages
    assert manga.chapters_dict["1"] == v1


def test_builder_assembles_pages_from_worker_return_value(monkeypatch):
    # Characterization of the B2 fix: the parent Manga's pages must come from
    # what _get_chapters_data RETURNS, not from worker-side mutation of
    # self.manga. Under a spawn Pool the child mutates a private copy that never
    # reaches the parent, so only the return value can be trusted. We stub
    # _get_chapters_data to return canned ChapterDownloads and assert the parent
    # assembled the pages from them -- independent of any real pool.
    builder = MangaBuilder(MockedSiteParser())
    img1 = open("tests/test_files/jpgs/test-manga_1_1.jpg", "rb").read()
    img2 = open("tests/test_files/jpgs/test-manga_1_2.jpg", "rb").read()
    canned_pages = [(1, img1, "success"), (2, img2, "success")]

    def fake_get_chapters_data(chapter_ids):
        # mimic the worker: return a ChapterDownload per requested chapter,
        # WITHOUT touching builder.manga (the spawn-process reality)
        return [
            ChapterDownload(chapter_id, index, pages=canned_pages, complete=True)
            for index, chapter_id in enumerate(chapter_ids, start=1)
        ]

    monkeypatch.setattr(builder, "_get_chapters_data", fake_get_chapters_data)
    manga = builder.get_manga_chapters(chapter_ids=["1"])

    assert [p.number for p in manga.chapters_dict["1"].pages] == [1, 2]
    assert manga.chapters_dict["1"].page[1].img == img1


def test_builder_skips_pages_for_chapters_worker_returned_none(monkeypatch):
    # A worker returns pages=None for a skipped chapter (already on disk / no
    # pages). The parent still lists the chapter (metadata) but with no pages --
    # it must not invent pages for a skipped result.
    builder = MangaBuilder(MockedSiteParser())

    def fake_get_chapters_data(chapter_ids):
        return [
            ChapterDownload(chapter_id, index, pages=None, complete=False)
            for index, chapter_id in enumerate(chapter_ids, start=1)
        ]

    monkeypatch.setattr(builder, "_get_chapters_data", fake_get_chapters_data)
    manga = builder.get_manga_chapters(chapter_ids=["1"])

    assert "1" in manga.chapters_dict
    assert manga.chapters_dict["1"].pages == []


def _stub_chapters_data(builder, monkeypatch):
    """Stub the parallel download so author tests don't touch a pool."""
    monkeypatch.setattr(
        builder,
        "_get_chapters_data",
        lambda chapter_ids: [
            ChapterDownload(chapter_id, i, pages=None, complete=False)
            for i, chapter_id in enumerate(chapter_ids, start=1)
        ],
    )


def test_builder_sets_author_from_parser_hook(monkeypatch):
    # E1: the parent reads the parser's author() hook into manga.author.
    builder = MangaBuilder(MockedSiteParser())
    _stub_chapters_data(builder, monkeypatch)
    monkeypatch.setattr(
        builder.parser.manga, "author", lambda: "Mihachi Kagano", raising=False
    )
    manga = builder.get_manga_chapters(chapter_ids=["1"])
    assert manga.author == "Mihachi Kagano"


def test_builder_author_failure_is_swallowed(monkeypatch):
    # author extraction is best-effort: a failing hook must not abort the
    # download; manga.author just stays None.
    builder = MangaBuilder(MockedSiteParser())
    _stub_chapters_data(builder, monkeypatch)

    def boom():
        raise RuntimeError("series page blocked")

    monkeypatch.setattr(builder.parser.manga, "author", boom, raising=False)
    manga = builder.get_manga_chapters(chapter_ids=["1"])
    assert manga.author is None


@pytest.mark.real_pool
def test_builder_populates_pages_under_real_pool():
    # B2-redesign: with the REAL multiprocessing Pool (mocked_pool_imap opted
    # out via the real_pool marker), the parent's Manga must still have its pages
    # populated. The worker runs in a separate (spawned) process on a private
    # copy of the builder, so this only passes because the parent assembles the
    # Manga from the worker RETURN values -- the exact bug B2 fixed. Pre-redesign
    # this would show 0 pages (data only on disk).
    builder = MangaBuilder(MockedSiteParser())
    manga = builder.get_manga_chapters(chapter_ids=["1", "2"])

    for chapter_id in ("1", "2"):
        pages = manga.chapters_dict[chapter_id].pages
        assert pages, f"chapter {chapter_id} has no pages in the parent Manga"
        assert all(p.img for p in pages)


def test_builder_removes_stale_incomplete_file_when_chapter_completes(monkeypatch):
    # F2: a prior partial run left "<...>-incomplete.pdf" on disk. When the
    # chapter now downloads completely, the parent must delete that stale
    # incomplete sibling (chapter_exists only ever checks the complete name, so
    # otherwise it lingers forever and you get both variants side by side).
    img = open("tests/test_files/jpgs/test-manga_1_1.jpg", "rb").read()
    canned = [(1, img, "success")]

    def fake_get_chapters_data(chapter_ids):
        return [
            ChapterDownload(chapter_id, index, pages=canned, complete=True)
            for index, chapter_id in enumerate(chapter_ids, start=1)
        ]

    builder = MangaBuilder(MockedSiteParser())
    monkeypatch.setattr(builder, "_get_chapters_data", fake_get_chapters_data)

    # pre-create the stale incomplete file the would-be complete path maps to
    builder.manga = Manga("dragon-ball", "pdf")
    complete_path = builder.manga._chapter_path("1_1")
    incomplete_path = MangaBuilder._incomplete_path(complete_path)
    incomplete_path.parent.mkdir(parents=True, exist_ok=True)
    incomplete_path.write_bytes(b"partial")
    assert incomplete_path.exists()

    builder.get_manga_chapters(chapter_ids=["1"])

    # the completed chapter's clean file exists; the stale incomplete is gone
    assert complete_path.exists()
    assert not incomplete_path.exists()


def test_incomplete_path_inserts_suffix_before_extension():
    p = Path("/tmp/dragon-ball/dragon-ball_chapter_1_1.pdf")
    assert (
        MangaBuilder._incomplete_path(p).name
        == "dragon-ball_chapter_1_1-incomplete.pdf"
    )


class _GappyDecimalParser(MockedSiteParser):
    """Site whose chapters have a gap (no 11) and a decimal (9.22).

    Overrides the manga parser's listing + page methods so the test isolates
    *selection* behaviour from the mock's url-based page parsing.
    """

    def __init__(self, manga_url="dragon-ball"):
        super().__init__(manga_url=manga_url)
        chapters = ["9", "9.22", "10", "12"]
        img = open("tests/test_files/jpgs/test-manga_1_1.jpg", "rb").read()
        self._manga.all_chapter_ids = lambda: list(chapters)
        self._manga.page_urls = lambda chapter: [(1, f"u/{chapter}/1")]
        self._manga.page_data = lambda page_url: (1, img, "success")


def test_builder_selection_is_chapter_number_based_with_gaps_and_decimals():
    """
    --chapters "9-10" must select chapters 9, 9.22 and 10 by *number* (a range
    over a decimal chapter), not by list index, and must skip the gap at 11.
    """
    builder = MangaBuilder(_GappyDecimalParser())
    manga = builder.get_manga_chapters(chapter_ids=["9-10", "12"])
    # selected the decimal chapter inside the range + the explicit 12
    assert set(manga.chapters_dict.keys()) == {"9", "9.22", "10", "12"}


def test_builder_unmatched_selector_skipped_not_indexed():
    """
    Selecting chapter "3" when only 9/9.22/10/12 exist returns nothing for that
    token (old code would have indexed into the list and grabbed the 3rd item).
    """
    builder = MangaBuilder(_GappyDecimalParser())
    manga = builder.get_manga_chapters(chapter_ids=["3"])
    assert manga.chapters_dict == {}


def teardown_function():
    """
    Remove directories after every test, if present

    Fixtures only work before a test is executed, hence
    the need for this module teardown.
    """
    directories = ["/tmp/smelly_pancakes/", "/tmp/dragon-ball/"]
    for directory in directories:
        if Path(directory).exists():
            shutil.rmtree(directory)

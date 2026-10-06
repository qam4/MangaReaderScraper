import json
import os
import shutil
from pathlib import Path
from unittest import mock

import pytest

from scraper.bundle import Bundle
from scraper.exceptions import (
    ChapterAlreadyPresent,
    OfflineSeriesNotFound,
    PageAlreadyPresent,
)
from scraper.manga import (
    SERIES_FILE,
    Chapter,
    ChapterDownload,
    DownloadSummary,
    Manga,
    MangaBuilder,
    Page,
    load_offline_manga,
    summarize_downloads,
)
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
        file_path=Path("/tmp/dragon-ball/dragon-ball_chapter_1.pdf"),
        upload_path=Path("/dragon-ball/dragon-ball_chapter_1.pdf"),
        order=1,
    )
    v2 = Chapter(
        number="2",
        file_path=Path("/tmp/dragon-ball/dragon-ball_chapter_2.pdf"),
        upload_path=Path("/dragon-ball/dragon-ball_chapter_2.pdf"),
        order=2,
    )
    v3 = Chapter(
        number="3",
        file_path=Path("/tmp/dragon-ball/dragon-ball_chapter_3.pdf"),
        upload_path=Path("/dragon-ball/dragon-ball_chapter_3.pdf"),
        order=3,
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
        file_path=Path("/tmp/dragon-ball/dragon-ball_chapter_1.pdf"),
        upload_path=Path("/dragon-ball/dragon-ball_chapter_1.pdf"),
        order=1,
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
        file_path=Path("/tmp/smelly_pancakes/smelly_pancakes_chapter_1.pdf"),
        upload_path=Path("/smelly_pancakes/smelly_pancakes_chapter_1.pdf"),
        order=1,
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
    # B2-redesign: with the REAL pool (mocked_pool_imap opted out via the
    # real_pool marker), the parent's Manga must still have its pages populated.
    # The worker returns a ChapterDownload and the parent assembles the Manga
    # from those RETURN values -- the exact contract B2 fixed (pre-redesign this
    # showed 0 pages, data only on disk). The contract holds under the thread
    # pool too; keeping a real-pool test guards it against regressions if the
    # worker is ever moved back behind a process boundary.
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
    complete_path = builder.manga._chapter_path("1")
    incomplete_path = MangaBuilder._incomplete_path(complete_path)
    incomplete_path.parent.mkdir(parents=True, exist_ok=True)
    incomplete_path.write_bytes(b"partial")
    assert incomplete_path.exists()

    builder.get_manga_chapters(chapter_ids=["1"])

    # the completed chapter's clean file exists; the stale incomplete is gone
    assert complete_path.exists()
    assert not incomplete_path.exists()


def test_incomplete_path_inserts_suffix_before_extension():
    p = Path("/tmp/dragon-ball/dragon-ball_chapter_700.5.pdf")
    assert (
        MangaBuilder._incomplete_path(p).name
        == "dragon-ball_chapter_700.5-incomplete.pdf"
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


def test_chapter_file_index_is_stable_across_selections():
    # A chapter's saved name must NOT depend on what else was selected this run.
    # "12" is the 4th chapter in the full list (9, 9.22, 10, 12), so it is index
    # 4 whether downloaded alone or among all -- otherwise the already-on-disk
    # de-dup rebuilds a different path and re-downloads it under a new name.
    solo = MangaBuilder(_GappyDecimalParser()).get_manga_chapters(chapter_ids=["12"])
    every = MangaBuilder(_GappyDecimalParser()).get_manga_chapters(chapter_ids=None)
    assert solo.chapters_dict["12"].file_path.name == "dragon-ball_chapter_12.pdf"
    assert (
        solo.chapters_dict["12"].file_path.name
        == every.chapters_dict["12"].file_path.name
    )


# ------------------- chapter identity is the id (item 3) -------------------
#
# Files used to be named "<name>_chapter_<position in the site's list>_<id>".
# Positions shift whenever the site inserts or removes a chapter, so every
# chapter after that point was downloaded AGAIN under a new name, with both
# copies left on disk (reproduced: inserting 9.5 re-fetched 10, 11 and 12).


class _ListSite:
    """A site whose chapter list we control; records every chapter fetched."""

    def __init__(self, chapters, fetched, author=None):
        img = open("tests/test_files/jpgs/test-manga_1_1.jpg", "rb").read()

        class _Parser:
            manga_url = "series"

            def all_chapter_ids(self_inner):
                return list(chapters)

            def chapter_url(self_inner, chapter):
                return f"u/{chapter}"

            def page_urls(self_inner, chapter):
                fetched.append(chapter)
                return [(1, f"u/{chapter}/1")]

            def page_data(self_inner, page_url):
                return (1, img, "success")

            def author(self_inner):
                return author

        self.manga = _Parser()


def _cfg(tmp_path):
    return {
        "config": {
            "manga_directory": str(tmp_path),
            "manga_bundle_directory": str(tmp_path / "out"),
            "upload_root": "/",
        }
    }


def _download(
    tmp_path,
    chapters,
    fetched,
    filetype="cbz",
    author=None,
    ids=None,
    title=None,
    preferred_name=None,
):
    with mock.patch("scraper.manga.settings", return_value=_cfg(tmp_path)):
        site = _ListSite(chapters, fetched, author=author)
        builder = MangaBuilder(site, filetype=filetype)
        return builder.get_manga_chapters(
            chapter_ids=ids, title=title, preferred_name=preferred_name
        )


def test_a_chapter_inserted_mid_series_downloads_only_that_chapter(tmp_path):
    fetched: list = []
    _download(tmp_path, ["9", "10", "11", "12"], fetched)
    fetched.clear()

    _download(tmp_path, ["9", "9.5", "10", "11", "12"], fetched)

    assert fetched == ["9.5"]
    assert sorted(p.name for p in (tmp_path / "series").glob("*.cbz")) == [
        "series_chapter_10.cbz",
        "series_chapter_11.cbz",
        "series_chapter_12.cbz",
        "series_chapter_9.5.cbz",
        "series_chapter_9.cbz",
    ]


def test_old_style_chapter_files_are_renamed_not_downloaded_again(tmp_path):
    # files from before this change, named by list position
    folder = tmp_path / "series"
    folder.mkdir()
    (folder / "series_chapter_1_9.cbz").write_bytes(b"chapter 9")
    (folder / "series_chapter_2_10.cbz").write_bytes(b"chapter 10")
    fetched: list = []

    _download(tmp_path, ["9", "10"], fetched)

    assert fetched == []
    assert (folder / "series_chapter_9.cbz").read_bytes() == b"chapter 9"
    assert (folder / "series_chapter_10.cbz").read_bytes() == b"chapter 10"
    assert not (folder / "series_chapter_1_9.cbz").exists()


def test_duplicate_old_style_files_keep_the_newest_and_set_the_rest_aside(tmp_path):
    # the duplicates the position bug left behind: same chapter, two positions
    folder = tmp_path / "series"
    folder.mkdir()
    old = folder / "series_chapter_2_10.cbz"
    new = folder / "series_chapter_3_10.cbz"
    old.write_bytes(b"older copy")
    new.write_bytes(b"newer copy")
    os.utime(old, (1_000_000_000, 1_000_000_000))
    os.utime(new, (2_000_000_000, 2_000_000_000))
    fetched: list = []

    _download(tmp_path, ["10"], fetched)

    assert fetched == []
    assert (folder / "series_chapter_10.cbz").read_bytes() == b"newer copy"
    # moved, never deleted
    assert (folder / ".superseded" / "series_chapter_2_10.cbz").read_bytes() == (
        b"older copy"
    )


def test_old_style_file_is_set_aside_when_the_new_name_already_exists(tmp_path):
    folder = tmp_path / "series"
    folder.mkdir()
    (folder / "series_chapter_10.cbz").write_bytes(b"current")
    (folder / "series_chapter_2_10.cbz").write_bytes(b"leftover")
    fetched: list = []

    _download(tmp_path, ["10"], fetched)

    assert fetched == []
    assert (folder / "series_chapter_10.cbz").read_bytes() == b"current"
    assert (folder / ".superseded" / "series_chapter_2_10.cbz").exists()


def test_chapters_keep_the_site_order_even_when_ids_sort_differently(tmp_path):
    # opaque slugs: the site's order is the only order there is
    fetched: list = []
    manga = _download(tmp_path, ["zeta-intro", "alpha-main", "beta-end"], fetched)
    assert [c.number for c in manga.chapters] == [
        "zeta-intro",
        "alpha-main",
        "beta-end",
    ]


# --------------------- offline bundling (item 4) ---------------------------
#
# Bundling is a purely local step, but every run used to fetch the chapter list
# and the author first -- so volumes couldn't be rebuilt with the site down,
# blocking, or out of reach. A download now records what bundling needs.


def test_download_records_the_series_for_offline_use(tmp_path):
    _download(tmp_path, ["1", "2", "10"], [], author="Masashi Kishimoto")

    record = json.loads((tmp_path / "series" / SERIES_FILE).read_text("utf-8"))
    assert record["name"] == "series"
    assert record["manga_url"] == "series"
    assert record["author"] == "Masashi Kishimoto"
    # the FULL list, in the site's order -- not just this run's selection
    assert record["chapters"] == ["1", "2", "10"]


def test_offline_manga_matches_the_online_one(tmp_path):
    online = _download(tmp_path, ["1", "2", "10"], [], author="Masashi Kishimoto")

    with mock.patch("scraper.manga.settings", return_value=_cfg(tmp_path)):
        offline = load_offline_manga("series", "cbz")

    def shape(manga):
        return [(c.number, c.order, c.file_path) for c in manga.chapters]

    assert shape(offline) == shape(online)
    assert offline.author == online.author == "Masashi Kishimoto"


def test_offline_applies_the_chapter_selection(tmp_path):
    _download(tmp_path, ["1", "2", "3"], [])

    with mock.patch("scraper.manga.settings", return_value=_cfg(tmp_path)):
        offline = load_offline_manga("series", "cbz", chapter_ids=["2-3"])

    assert [c.number for c in offline.chapters] == ["2", "3"]
    assert [c.order for c in offline.chapters] == [2, 3]  # full-list positions


def test_offline_registers_a_chapter_with_no_file_as_incomplete(tmp_path):
    # mirrors online, where a chapter that failed to download is still listed:
    # same volume boundaries, and that volume is skipped rather than renumbered
    _download(tmp_path, ["1", "2", "3"], [])
    (tmp_path / "series" / "series_chapter_2.cbz").unlink()

    with mock.patch("scraper.manga.settings", return_value=_cfg(tmp_path)):
        offline = load_offline_manga("series", "cbz")

    assert [c.number for c in offline.chapters] == ["1", "2", "3"]
    assert offline.chapters_dict["2"].file_path.name == (
        "series_chapter_2-incomplete.cbz"
    )


def test_offline_without_a_record_says_how_to_get_one(tmp_path):
    (tmp_path / "series").mkdir()
    with mock.patch("scraper.manga.settings", return_value=_cfg(tmp_path)):
        with pytest.raises(OfflineSeriesNotFound, match="once without --offline"):
            load_offline_manga("series", "cbz")


def test_offline_rebuild_of_an_up_to_date_series_changes_nothing(tmp_path):
    """The strongest check that offline == online: bundling a series offline
    right after bundling it online must find every volume already current."""
    online = _download(tmp_path, ["1", "2", "3"], [], author="Masashi Kishimoto")

    def bundle(manga):
        def fake_convert(cbz_path, mobi_path):
            Path(mobi_path).write_bytes(b"mobi")

        verdicts = []
        real_is_stale = Bundle._volume_is_stale

        def recording_is_stale(self, *args):
            verdicts.append(real_is_stale(self, *args))
            return verdicts[-1]

        with (
            mock.patch("scraper.bundle.settings", return_value=_cfg(tmp_path)),
            mock.patch.object(Bundle, "_volume_is_stale", recording_is_stale),
        ):
            b = Bundle(manga, chapters_per_volume=2, jobs=1)
            with mock.patch.object(
                b, "_convert_to_mobi", side_effect=fake_convert
            ) as convert:
                b.create_volume(0, 1)
                b.create_volume(1, 1)
        return convert, verdicts

    _, built = bundle(online)
    assert built == [True, True]  # online: both volumes built
    volumes = sorted((tmp_path / "out" / "series" / "cbz").iterdir())
    before = [(p.name, p.stat().st_mtime_ns) for p in volumes]

    with mock.patch("scraper.manga.settings", return_value=_cfg(tmp_path)):
        offline = load_offline_manga("series", "cbz")
    convert, verdicts = bundle(offline)

    # Each volume was actually EVALUATED and found current. Without this, a
    # broken offline Manga would also leave the files untouched (create_volume
    # quietly skips a volume it can't build) and the checks below would pass.
    assert verdicts == [False, False]
    after = [(p.name, p.stat().st_mtime_ns) for p in volumes]
    assert after == before  # no volume rebuilt
    convert.assert_not_called()  # no MOBI rebuilt


# ------------------- series folder identity (item 6) -----------------------
#
# The series folder is named after the search-result title when you search, but
# after the url slug when you pass --manga. So the same series downloaded both
# ways ended up in two folders, and the second run silently fetched everything
# again. Each folder's .series.json records the source + url it came from.


def test_same_series_by_slug_reuses_the_folder_a_search_created(tmp_path):
    fetched: list = []
    # first run came from a search: folder named after the result's title
    _download(tmp_path, ["1", "2"], fetched, title="Series Title")
    fetched.clear()

    # later run passes the slug instead (no title)
    manga = _download(tmp_path, ["1", "2"], fetched)

    assert fetched == []  # nothing downloaded again
    assert manga.name == "Series Title"
    assert not (tmp_path / "series").exists()  # no second folder


def test_override_name_is_respected_even_when_a_folder_matches(tmp_path):
    fetched: list = []
    _download(tmp_path, ["1"], fetched, title="Series Title")
    manga = _download(tmp_path, ["1"], fetched, preferred_name="My Name")
    assert manga.name == "My Name"


def test_a_folder_recorded_for_a_different_url_is_not_reused(tmp_path):
    other = tmp_path / "Series Title"
    other.mkdir()
    (other / SERIES_FILE).write_text(
        json.dumps({"source": None, "manga_url": "another-series", "chapters": []}),
        encoding="utf-8",
    )
    manga = _download(tmp_path, ["1"], [])
    assert manga.name == "series"


def test_lookalike_folder_without_a_record_is_flagged_not_guessed(tmp_path, caplog):
    # old downloads have no record, so a name match is only a hint: say so and
    # point at --override_name, but don't silently adopt a folder on a guess.
    # (A space, not just case: Windows paths ignore case, so "Series" vs
    # "series" is already the same folder there and nothing forks.)
    (tmp_path / "Se ries").mkdir()
    with caplog.at_level("WARNING"):
        manga = _download(tmp_path, ["1"], [])
    assert manga.name == "series"
    assert "'Se ries'" in caplog.text
    assert "--override_name" in caplog.text


def test_an_unreadable_folder_does_not_break_the_folder_lookup(tmp_path):
    # Found on the Linux CI runner: manga_directory held system folders the user
    # can't enter (/tmp/systemd-private-*), and stat() of a path inside one
    # raises PermissionError, which Path.is_file() does NOT swallow (it only
    # ignores not-found-type errors). The lookup is a convenience; one unreadable
    # folder must not fail the download, nor hide the folder that does match.
    fetched: list = []
    _download(tmp_path, ["1"], fetched, title="Series Title")
    (tmp_path / "locked").mkdir()
    real_stat = Path.stat

    def stat(self, *args, **kwargs):
        if self.parent.name == "locked":
            raise PermissionError(13, "Permission denied", str(self))
        return real_stat(self, *args, **kwargs)

    fetched.clear()
    with mock.patch.object(Path, "stat", autospec=True, side_effect=stat):
        manga = _download(tmp_path, ["1"], fetched)

    assert manga.name == "Series Title"  # the recorded folder is still found
    assert fetched == []


# ----------------------------- download summary ----------------------------


def test_summarize_downloads_categorizes_results():
    downloads = [
        ChapterDownload("2", 2, pages=[(1, b"x", "success")], complete=True),
        ChapterDownload("1", 1, pages=None, complete=True),  # already on disk
        ChapterDownload("3", 3, pages=[(1, b"x", "missing")], complete=False),
        ChapterDownload("4", 4, pages=None, complete=False),  # couldn't fetch
    ]
    s = summarize_downloads(downloads)
    assert s.downloaded == ["2"]
    assert s.already_present == ["1"]
    assert s.incomplete == ["3"]
    assert s.failed == ["4"]
    assert s.requested == 4
    assert not s.ok  # has incomplete + failed


def test_summarize_downloads_sorts_ids_in_chapter_order():
    # ids are returned in canonical chapter order, not arrival order
    downloads = [
        ChapterDownload("10", 3, pages=[(1, b"x", "success")], complete=True),
        ChapterDownload("2", 1, pages=[(1, b"x", "success")], complete=True),
        ChapterDownload("9.5", 2, pages=[(1, b"x", "success")], complete=True),
    ]
    s = summarize_downloads(downloads)
    assert s.downloaded == ["2", "9.5", "10"]


def test_summarize_downloads_all_ok():
    downloads = [
        ChapterDownload("1", 1, pages=[(1, b"x", "success")], complete=True),
        ChapterDownload("2", 2, pages=None, complete=True),
    ]
    s = summarize_downloads(downloads)
    assert s.ok
    assert isinstance(s, DownloadSummary)


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

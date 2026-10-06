"""
Tests for scraper.bundle: volume naming (an external Calibre contract), archive
layout, the ComicInfo.xml that KCC turns into the Kindle's title / author /
table of contents, the kcc-c2e command line and its ini passthrough, and the
make-style rebuild check.

Kept hermetic: jobs=1 so resolve_jobs() doesn't read settings, settings() is
patched wherever a path is built, and _convert_to_mobi is mocked so no kcc-c2e
runs. Real chapter .cbz inputs are built from the committed sample jpgs in
tests/test_files/jpgs. The two tests that use KCC's own code skip unless the
optional `bundle` extra is installed (it isn't on CI).
"""

import os
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from unittest import mock
from xml.dom import minidom

import pytest

from scraper.bundle import (
    _KCC_IMAGE_TYPES,
    Bundle,
    _chapter_label,
    _chapter_range,
    _comic_info_xml,
    _kcc_args,
)
from scraper.manga import Manga


def test_bundle_writer_prefers_manga_author():
    # E1: an extracted author becomes the ComicInfo <Writer>.
    manga = Manga("some-series", "cbz", author="Mihachi Kagano")
    bundle = Bundle(manga, chapters_per_volume=1, jobs=1)
    assert bundle.writer == "Mihachi Kagano"


def test_bundle_writer_falls_back_to_configured_default():
    # No author on the manga -> the neutral/ini-configured writer, never a
    # hardcoded person.
    manga = Manga("some-series", "cbz")  # author defaults to None
    with mock.patch("scraper.bundle._configured_writer", return_value="Unknown"):
        bundle = Bundle(manga, chapters_per_volume=1, jobs=1)
    assert bundle.writer == "Unknown"


# ====================== kcc-c2e MOBI conversion (R3) =====================


def _bundle():
    return Bundle(Manga("x", "cbz"), chapters_per_volume=1, jobs=1)


def test_convert_to_mobi_raises_when_kcc_missing():
    bundle = _bundle()
    with mock.patch("scraper.bundle.shutil.which", return_value=None):
        with pytest.raises(RuntimeError, match="kcc-c2e not found"):
            bundle._convert_to_mobi("v.cbz", "v.mobi")


def test_convert_to_mobi_raises_on_nonzero_exit():
    bundle = _bundle()
    fail = mock.Mock(returncode=1, stdout="", stderr="kindlegen: not found")
    with mock.patch("scraper.bundle.shutil.which", return_value="/usr/bin/kcc-c2e"):
        with mock.patch("scraper.bundle.subprocess.run", return_value=fail):
            # even if a file somehow exists, a non-zero exit must raise
            with mock.patch("scraper.bundle.os.path.exists", return_value=True):
                with pytest.raises(RuntimeError, match="kcc-c2e failed"):
                    bundle._convert_to_mobi("v.cbz", "v.mobi")


def test_convert_to_mobi_surfaces_stdout_in_error():
    # KCC prints its errors to stdout (e.g. 'ERROR: 7z is missing!'), so a
    # failure diagnostic must include stdout, not only stderr.
    bundle = _bundle()
    fail = mock.Mock(returncode=1, stdout="ERROR: 7z is missing!", stderr="")
    with mock.patch("scraper.bundle.shutil.which", return_value="/usr/bin/kcc-c2e"):
        with mock.patch("scraper.bundle.subprocess.run", return_value=fail):
            with mock.patch("scraper.bundle.os.path.exists", return_value=False):
                with pytest.raises(RuntimeError, match="7z is missing"):
                    bundle._convert_to_mobi("v.cbz", "v.mobi")


def test_convert_to_mobi_raises_when_output_missing():
    bundle = _bundle()
    ok = mock.Mock(returncode=0, stdout="", stderr="")
    with mock.patch("scraper.bundle.shutil.which", return_value="/usr/bin/kcc-c2e"):
        with mock.patch("scraper.bundle.subprocess.run", return_value=ok):
            # exit 0 but no MOBI produced -> still an error (don't claim success)
            with mock.patch("scraper.bundle.os.path.exists", return_value=False):
                with pytest.raises(RuntimeError, match="expected"):
                    bundle._convert_to_mobi("v.cbz", "v.mobi")


def test_convert_to_mobi_succeeds_when_output_present():
    bundle = _bundle()
    ok = mock.Mock(returncode=0, stdout="", stderr="")
    with mock.patch("scraper.bundle.shutil.which", return_value="/usr/bin/kcc-c2e"):
        with mock.patch("scraper.bundle.subprocess.run", return_value=ok) as run:
            with mock.patch("scraper.bundle.os.path.exists", return_value=True):
                bundle._convert_to_mobi("v.cbz", "out/v.mobi")
    # invoked kcc-c2e with the output dir + cbz
    cmd = run.call_args[0][0]
    assert cmd[0] == "kcc-c2e"
    assert "out" in cmd
    assert "v.cbz" in cmd
    # --tempdir keeps concurrent conversions from wiping each other's work dirs
    assert "--tempdir" in cmd


def test_convert_to_mobi_passes_configured_args_before_output():
    # the rendering flags are whatever _kcc_args() resolved; -o/input stay ours
    bundle = _bundle()
    ok = mock.Mock(returncode=0, stdout="", stderr="")
    with (
        mock.patch("scraper.bundle.shutil.which", return_value="/usr/bin/kcc-c2e"),
        mock.patch("scraper.bundle._kcc_args", return_value=["-p", "KPW34", "-s"]),
        mock.patch("scraper.bundle.subprocess.run", return_value=ok) as run,
        mock.patch("scraper.bundle.os.path.exists", return_value=True),
    ):
        bundle._convert_to_mobi("v.cbz", "out/v.mobi")
    assert run.call_args[0][0] == [
        "kcc-c2e",
        "-p",
        "KPW34",
        "-s",
        "-o",
        "out",
        "v.cbz",
    ]


# ========================= volume naming / layout ========================


def _write_chapter_cbz(path, chapter_number):
    """A real chapter .cbz, built from the committed sample jpgs."""
    jpgs = sorted(Path("tests/test_files/jpgs").glob("*.jpg"))
    assert jpgs, "sample jpgs missing"
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as z:
        for page, jpg in enumerate(jpgs, start=1):
            z.writestr(f"{page:03d}_{chapter_number}.jpg", jpg.read_bytes())


def _bundled(
    tmp_path, chapters_per_volume=2, name="Naruto", chapters=(("1", 1), ("2", 2))
):
    """Build a Manga with real chapter .cbz files on disk, plus its Bundle.

    ``chapters`` is ``((site_chapter_id, stable_file_index), ...)``. Passing ids
    that differ from the indexes (as real series do: "700.5" sits at index 749)
    is what proves a label comes from the chapter id and not the file index.
    """
    download = tmp_path / "dl"
    out = tmp_path / "out"
    cfg = {
        "config": {
            "manga_directory": str(download),
            "manga_bundle_directory": str(out),
            "upload_root": "/",  # Manga builds an upload path for every chapter
        }
    }
    with mock.patch("scraper.manga.settings", return_value=cfg):
        manga = Manga(name, "cbz", author="Masashi Kishimoto")
        for chapter_id, index in chapters:
            manga.add_chapter(chapter_id, chapter_index=index)
        for chapter in manga.chapters:
            _write_chapter_cbz(Path(chapter.file_path), chapter.number)
    return manga, out, cfg, chapters_per_volume


def _comic_info(volume_path):
    with zipfile.ZipFile(volume_path) as z:
        return ET.fromstring(z.read("ComicInfo.xml"))


def test_create_volume_filename_is_the_calibre_contract(tmp_path):
    """The volume filename is an EXTERNAL contract, not cosmetic.

    Calibre reads .cbz metadata from the filename (it ignores ComicInfo.xml on
    import), so the "<series> - <title>.cbz" shape is what feeds its series /
    title columns. The duplicated series name looks redundant and has already
    been proposed for "cleanup" once; this test makes that fail loudly.
    """
    manga, out, cfg, per_volume = _bundled(tmp_path)
    with mock.patch("scraper.bundle.settings", return_value=cfg):
        bundle = Bundle(manga, chapters_per_volume=per_volume, jobs=1)
        with mock.patch.object(bundle, "_convert_to_mobi") as convert:
            bundle.create_volume(0, 1)

    expected = out / "Naruto" / "cbz" / "Naruto - Naruto vol1 ch1-2.cbz"
    assert expected.is_file(), sorted(
        p.name for p in (out / "Naruto" / "cbz").iterdir()
    )
    # the mobi conversion targets the same stem under mobi/
    assert convert.call_args[0][1].endswith(
        os.path.join("mobi", "Naruto - Naruto vol1 ch1-2.mobi")
    )


def test_create_volume_single_chapter_omits_the_range(tmp_path):
    manga, out, cfg, _ = _bundled(tmp_path)
    with mock.patch("scraper.bundle.settings", return_value=cfg):
        bundle = Bundle(manga, chapters_per_volume=1, jobs=1)
        with mock.patch.object(bundle, "_convert_to_mobi"):
            bundle.create_volume(0, 1)
    assert (out / "Naruto" / "cbz" / "Naruto - Naruto vol1 ch1.cbz").is_file()


def test_create_volume_archive_layout_and_comicinfo(tmp_path):
    # one folder per chapter named after the chapter file stem, plus ComicInfo
    manga, out, cfg, per_volume = _bundled(tmp_path)
    with mock.patch("scraper.bundle.settings", return_value=cfg):
        bundle = Bundle(manga, chapters_per_volume=per_volume, jobs=1)
        with mock.patch.object(bundle, "_convert_to_mobi"):
            bundle.create_volume(0, 1)

    volume = out / "Naruto" / "cbz" / "Naruto - Naruto vol1 ch1-2.cbz"
    with zipfile.ZipFile(volume) as z:
        names = z.namelist()

    assert "ComicInfo.xml" in names
    # chapter folders carry the chapter file stem (<name>_chapter_<index>_<id>)
    assert any(n.startswith("Naruto_chapter_1_1/") for n in names)
    assert any(n.startswith("Naruto_chapter_2_2/") for n in names)
    # page entries keep the writer's zero-padded <page>_<chapter>.jpg naming
    assert any(n.endswith("001_1.jpg") for n in names)

    info = _comic_info(volume)
    # Series is the SERIES name: KCC uses it as the book title. It used to hold
    # the volume title, which is why the device read "Naruto vol1 ch1-2".
    assert info.findtext("Series") == "Naruto"
    assert info.findtext("Volume") == "1"
    assert info.findtext("Writer") == "Masashi Kishimoto"
    # Number would be appended to the device title unconditionally
    assert info.find("Number") is None


def test_create_volume_bookmarks_each_chapter_at_its_first_page(tmp_path):
    """Chapter bookmarks are what label the Kindle's table of contents.

    Without them KCC names TOC entries after the archive folders, i.e.
    "Naruto_chapter_748_700". Ids deliberately differ from the stable file
    indexes, as in a real series, so a label built from Chapter.number (the
    index) would read "Chapter 748" and fail here.
    """
    manga, out, cfg, per_volume = _bundled(
        tmp_path, chapters=(("700", 748), ("700.5", 749))
    )
    with mock.patch("scraper.bundle.settings", return_value=cfg):
        bundle = Bundle(manga, chapters_per_volume=per_volume, jobs=1)
        with mock.patch.object(bundle, "_convert_to_mobi"):
            bundle.create_volume(0, 1)

    info = _comic_info(out / "Naruto" / "cbz" / "Naruto - Naruto vol1 ch1-2.cbz")
    pages = [(p.get("Image"), p.get("Bookmark")) for p in info.iter("Page")]
    # 2 sample jpgs per chapter: chapter 700 starts at page 0, 700.5 at page 2
    assert pages == [("0", "Chapter 700"), ("2", "Chapter 700.5")]
    # Title carries the REAL chapter numbers, not the filename's positional ch1-2
    assert info.findtext("Title") == "Chapters 700-700.5"


def test_create_volume_counts_only_image_files_as_pages(tmp_path):
    """A bookmark index must count exactly what KCC counts as a page.

    KCC's page list holds images only. If a stray non-image (Thumbs.db, a
    per-chapter ComicInfo.xml) were counted, every later chapter's bookmark
    would land one page late.
    """
    manga, out, cfg, per_volume = _bundled(
        tmp_path, chapters=(("1", 1), ("2", 2), ("3", 3)), chapters_per_volume=3
    )
    with zipfile.ZipFile(Path(manga.chapters_dict["1"].file_path), "a") as z:
        z.writestr("Thumbs.db", b"not a page")
    # chapter 2 holds no images at all: an index for it would point at chapter
    # 3's first page (or, as the last chapter, past KCC's list -> IndexError)
    with zipfile.ZipFile(Path(manga.chapters_dict["2"].file_path), "w") as z:
        z.writestr("readme.txt", b"no pages here")
    with mock.patch("scraper.bundle.settings", return_value=cfg):
        bundle = Bundle(manga, chapters_per_volume=per_volume, jobs=1)
        with mock.patch.object(bundle, "_convert_to_mobi"):
            bundle.create_volume(0, 1)

    info = _comic_info(out / "Naruto" / "cbz" / "Naruto - Naruto vol1 ch1-3.cbz")
    pages = [(p.get("Image"), p.get("Bookmark")) for p in info.iter("Page")]
    # chapter 1: 2 jpgs (+ Thumbs.db, not counted); chapter 2: skipped
    assert pages == [("0", "Chapter 1"), ("2", "Chapter 3")]


# ============================ ComicInfo builder ==========================


def test_comic_info_xml_emits_only_fields_kcc_reads():
    info = ET.fromstring(
        _comic_info_xml("Naruto", 3, "Chapters 21-30", "Masashi Kishimoto")
    )
    assert info.findtext("Series") == "Naruto"
    assert info.findtext("Volume") == "3"
    assert info.findtext("Title") == "Chapters 21-30"
    assert info.findtext("Writer") == "Masashi Kishimoto"
    assert info.find("Number") is None
    assert info.find("Pages") is None  # no bookmarks given


def test_comic_info_xml_escapes_markup_in_site_supplied_text():
    # author names now come from the sites; raw interpolation produced invalid
    # XML for an "&", which KCC silently discards wholesale
    xml = _comic_info_xml("Tom & Jerry", 1, "a <b> c", "Smith & Wesson")
    info = ET.fromstring(xml)  # would raise on invalid XML
    assert info.findtext("Series") == "Tom & Jerry"
    assert info.findtext("Writer") == "Smith & Wesson"
    assert info.findtext("Title") == "a <b> c"


def test_comic_info_xml_never_writes_an_empty_element():
    """KCC reads every field via ``firstChild.nodeValue``; an empty tag has no
    firstChild, raises, and KCC's bare ``except`` then throws away the whole
    ComicInfo. Mirror exactly that access pattern over every element."""
    xml = _comic_info_xml("Naruto", 1, "", "Writer", bookmarks=[(0, ""), (2, "Ch")])
    doc = minidom.parseString(xml)
    for tag in ("Series", "Volume", "Title", "Writer", "Number"):
        for node in doc.getElementsByTagName(tag):
            assert node.firstChild is not None, f"empty <{tag}>"
    # the empty-label bookmark is dropped rather than emitted
    assert [p.getAttribute("Bookmark") for p in doc.getElementsByTagName("Page")] == [
        "Ch"
    ]


def test_comic_info_xml_round_trips_through_kccs_own_parser(tmp_path):
    """The strongest check available offline: KCC's real MetadataParser.

    Skipped where the optional `bundle` extra (KCC) isn't installed, i.e. CI.
    """
    metadata = pytest.importorskip("kindlecomicconverter.metadata")
    path = tmp_path / "ComicInfo.xml"
    path.write_text(
        _comic_info_xml(
            "Naruto",
            1,
            "Chapters 700-700.5",
            "Masashi Kishimoto",
            bookmarks=[(0, "Chapter 700"), (2, "Chapter 700.5")],
        ),
        encoding="utf-8",
    )
    data = metadata.MetadataParser(str(path)).data
    assert data["Series"] == "Naruto"
    assert data["Volume"] == "1"
    assert data["Title"] == "Chapters 700-700.5"
    assert data["Writers"] == ["Masashi Kishimoto"]
    assert data["Bookmarks"] == [(0, "Chapter 700"), (2, "Chapter 700.5")]


def test_kcc_image_types_match_installed_kcc():
    # bookmark indices must count exactly what KCC counts as a page; this
    # catches the mirrored tuple drifting on a KCC bump
    shared = pytest.importorskip("kindlecomicconverter.shared")
    assert set(_KCC_IMAGE_TYPES) == set(shared.IMAGE_TYPES)


@pytest.mark.parametrize(
    "chapter_id,expected",
    [
        ("700.5", "Chapter 700.5"),
        ("12", "Chapter 12"),
        ("vol-54-chapter-name", "vol-54-chapter-name"),  # opaque slug, as-is
        (None, ""),
        ("", ""),
    ],
)
def test_chapter_label(chapter_id, expected):
    assert _chapter_label(chapter_id) == expected


@pytest.mark.parametrize(
    "ids,expected",
    [
        (["700", "700.5", "701"], "Chapters 700-701"),
        (["5"], "Chapter 5"),
        ([None, "3", None], "Chapter 3"),
        ([], ""),
    ],
)
def test_chapter_range(ids, expected):
    assert _chapter_range(ids) == expected


def test_create_volume_skips_rebuild_when_volume_is_up_to_date(tmp_path):
    manga, out, cfg, per_volume = _bundled(tmp_path)
    volume = out / "Naruto" / "cbz" / "Naruto - Naruto vol1 ch1-2.cbz"
    volume.parent.mkdir(parents=True)
    volume.write_bytes(b"already built and newer than its chapters")

    with mock.patch("scraper.bundle.settings", return_value=cfg):
        bundle = Bundle(manga, chapters_per_volume=per_volume, jobs=1)
        with mock.patch.object(bundle, "_convert_to_mobi"):
            bundle.create_volume(0, 1)

    # untouched: the mtime check short-circuited the rebuild
    assert volume.read_bytes() == b"already built and newer than its chapters"


# ======================= is_obsolete / missing chapters ==================


def test_is_obsolete_true_when_target_missing(tmp_path):
    dep = tmp_path / "chapter.cbz"
    dep.write_bytes(b"x")
    assert _bundle().is_obsolete(str(tmp_path / "nope.cbz"), [str(dep)]) is True


def test_is_obsolete_false_when_target_newer(tmp_path):
    dep = tmp_path / "chapter.cbz"
    dep.write_bytes(b"x")
    target = tmp_path / "vol.cbz"
    target.write_bytes(b"y")
    os.utime(dep, (1, 1))  # dependency far in the past
    assert _bundle().is_obsolete(str(target), [str(dep)]) is False


def test_is_obsolete_true_when_dependency_newer(tmp_path):
    target = tmp_path / "vol.cbz"
    target.write_bytes(b"y")
    dep = tmp_path / "chapter.cbz"
    dep.write_bytes(b"x")
    os.utime(target, (1, 1))  # target far in the past
    assert _bundle().is_obsolete(str(target), [str(dep)]) is True


def test_is_obsolete_does_not_raise_when_a_dependency_is_missing(tmp_path):
    """A chapter that failed to download is registered in the Manga with an
    ``-incomplete`` path but no file on disk. os.path.getmtime on it used to
    raise FileNotFoundError from OUTSIDE create_volume's try block, so it
    escaped through pool.imap and aborted every remaining volume. Treat a
    missing input as "needs rebuilding" instead; the build itself then fails
    cleanly and skips just that volume.
    """
    target = tmp_path / "vol.cbz"
    target.write_bytes(b"y")
    missing = str(tmp_path / "never-downloaded-incomplete.cbz")
    assert _bundle().is_obsolete(str(target), [missing]) is True


def test_create_volume_skips_volume_with_missing_chapter_instead_of_aborting(tmp_path):
    # end-to-end guard: the exception must not escape create_volume
    download = tmp_path / "dl"
    (download / "x").mkdir(parents=True)
    out = tmp_path / "out"
    out.mkdir()

    manga = Manga("x", "cbz")
    manga.add_chapter("1", chapter_index=1, complete=False)  # no file written
    bundle = Bundle(manga, chapters_per_volume=1, jobs=1)

    # a pre-existing volume file is what made is_obsolete reach getmtime
    volume_dir = out / "x" / "cbz"
    volume_dir.mkdir(parents=True)
    (volume_dir / "x - x vol1 ch1.cbz").write_bytes(b"stale")

    with (
        mock.patch.object(
            bundle, "_get_manga_download_dir", return_value=str(download)
        ),
        mock.patch.object(bundle, "_get_manga_bundle_dir", return_value=str(out)),
        mock.patch.object(bundle, "_convert_to_mobi") as convert,
    ):
        bundle.create_volume(0, 1)  # must return, not raise

    convert.assert_not_called()  # never claims a MOBI for a volume it couldn't build


# ==================== kcc arg resolution (ini passthrough) ================


def _settings(**config):
    """Fake settings() so these tests never read the real user ini."""
    return mock.patch("scraper.bundle.settings", return_value={"config": config})


DEFAULT_ARGS = ["-u", "--hq", "-g", "1.8", "--metadatatitle", "1", "--tempdir"]


def test_kcc_args_default_requests_panel_view_gamma_and_chapter_title():
    """Pin the defaults.

    --hq and -g 1.8 compensate for KCC 10.x behaviour changes: KCC 5.x (the old
    submodule fork) enabled Panel View by default and took gamma 1.8 from the
    KV device profile, while 10.x disables Panel View unless --hq/-2 is given
    and sets every Kindle profile's gamma to 1.0 -- so dropping them silently
    costs tap-to-zoom and washes the pages out.

    --metadatatitle 1 puts the chapter range in the device title
    ("Naruto Vol. 01: Chapters 700-710"); without it every volume of a series
    shows as just "Naruto Vol. NN".
    """
    with _settings():  # no kcc_args key
        assert _kcc_args() == DEFAULT_ARGS


def test_kcc_args_ini_override_is_passed_through_verbatim():
    # arbitrary KCC flags work without this module knowing about them
    with _settings(kcc_args="-p KPW34 -s --colorautocontrast"):
        assert _kcc_args() == [
            "-p",
            "KPW34",
            "-s",
            "--colorautocontrast",
            "--tempdir",
        ]


def test_kcc_args_respects_shell_quoting():
    with _settings(kcc_args='-p "Kindle Custom" -u'):
        assert _kcc_args() == ["-p", "Kindle Custom", "-u", "--tempdir"]


def test_kcc_args_does_not_duplicate_required_tempdir():
    with _settings(kcc_args="--tempdir -u"):
        assert _kcc_args() == ["--tempdir", "-u"]


def test_kcc_args_empty_value_means_no_rendering_flags():
    # `kcc_args =` is an explicit "run plain kcc-c2e", distinct from an absent
    # key (which takes the defaults) -- but --tempdir is still required.
    with _settings(kcc_args=""):
        assert _kcc_args() == ["--tempdir"]


def test_kcc_args_injects_tempdir_even_when_overridden():
    # parallel-safety is not a preference: dropping it corrupts concurrent runs
    with _settings(kcc_args="-s"):
        assert "--tempdir" in _kcc_args()


def test_kcc_args_falls_back_to_default_when_settings_unavailable():
    with mock.patch("scraper.bundle.settings", side_effect=Exception("no ini")):
        assert _kcc_args() == DEFAULT_ARGS

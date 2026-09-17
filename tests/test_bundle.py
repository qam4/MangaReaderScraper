"""
Tests for Bundle's ComicInfo <Writer> source (E1).

Kept hermetic: pass jobs=1 so resolve_jobs() doesn't read settings, and mock
_configured_writer for the fallback case.
"""

import os
from unittest import mock

import pytest

from scraper.bundle import Bundle, _kcc_args
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


def test_kcc_args_default_requests_panel_view_and_gamma():
    """Pin the defaults that compensate for KCC 10.x behaviour changes.

    KCC 5.x (the old submodule fork) enabled Panel View by default and took
    gamma 1.8 from the KV device profile. Upstream 10.x disables Panel View
    unless --hq/-2 is given, and sets every Kindle profile's gamma to 1.0 -- so
    dropping these silently costs tap-to-zoom and washes the pages out.
    """
    with _settings():  # no kcc_args key
        assert _kcc_args() == ["-u", "--hq", "-g", "1.8", "--tempdir"]


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
        assert _kcc_args() == ["-u", "--hq", "-g", "1.8", "--tempdir"]

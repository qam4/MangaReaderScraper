"""
Bundle manga into volumes with multiple chapters
"""

import logging
import os
import shutil
import subprocess
import time
import zipfile
from itertools import repeat
from logging import LoggerAdapter
from multiprocessing.pool import Pool
from typing import List

from scraper.manga import Manga
from scraper.utils import configure_logging, get_adapter, get_console, settings

logger = logging.getLogger(__name__)

WRITER_DEFAULT = "Fred Marchais"


def extract_cbz(archive_path, cbz_output_path):
    if os.path.exists(cbz_output_path):
        logger.debug(f"{archive_path} already unzipped")
        shutil.rmtree(cbz_output_path)

    logger.debug(f"Unzipping {archive_path} to {cbz_output_path}")
    try:
        with zipfile.ZipFile(archive_path, "r") as zip:
            zip.extractall(cbz_output_path)
    except zipfile.BadZipFile as e:
        logger.error(f"Invalid zip file {archive_path}")
        raise e
    except FileNotFoundError as e:
        logger.error(f"Zip file not found {archive_path}")
        raise e
    except Exception as e:
        logger.error("An error occurred:", e)
        raise e


def ceiling_division(n, d):
    """
    Ceiling division
    """
    return -(n // -d)


class Bundle:
    """
    Bundles the manga into volumes with multiple chapters
    Also convert to MOBI
    """

    def __init__(self, manga: Manga, chapters_per_volume: int) -> None:
        self.manga: Manga = manga
        self.chapters_per_volume: int = chapters_per_volume
        self.adapter: LoggerAdapter = get_adapter(logger, manga.name)
        self.writer = WRITER_DEFAULT
        self.comic_info_template = """<?xml version="1.0" encoding="utf-8"?>
        <ComicInfo xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
        <Series>{series}</Series>
        <Writer>{writer}</Writer>
        </ComicInfo>
        """

    def _get_manga_download_dir(self) -> str:
        """
        Get path to manga download dir.
        """
        return settings()["config"]["manga_directory"]

    def _get_manga_bundle_dir(self) -> str:
        """
        Get path to manga bundle dir.
        """
        config = settings()["config"]
        return config.get(
            "manga_bundle_directory", config.get("manga_directory", os.getcwd())
        )

    def is_obsolete(self, target: str, dependencies: List[str]) -> bool:
        if not os.path.isfile(target):
            return True
        for dependency in dependencies:
            if os.path.getmtime(target) - os.path.getmtime(dependency) < 0:
                return True
        return False

    def create_volume_wrapped(self, arg):
        return self.create_volume(*arg)  # Unpacks args

    def create_volume(self, volume_index: int, volume_digits: int):
        """
        Create a bundled volume
        """
        start_time = time.time()

        input_root_path = self._get_manga_download_dir()
        output_root_path = self._get_manga_bundle_dir()

        # the original Manga class calls chapters volumes
        manga_chapters = self.manga.volumes
        manga_title = self.manga.name
        writer = self.writer
        manga_folder = os.path.join(input_root_path, manga_title)
        output_folder = os.path.join(output_root_path, manga_title)
        os.makedirs(output_folder, exist_ok=True)
        os.makedirs(os.path.join(output_folder, "cbz"), exist_ok=True)
        os.makedirs(os.path.join(output_folder, "mobi"), exist_ok=True)

        cbz_files = [os.path.basename(chapter.file_path) for chapter in manga_chapters]

        # Create the volume .cbz file
        volume = volume_index + 1

        chapter_start = volume_index * self.chapters_per_volume
        chapter_end = min(
            len(cbz_files) - 1, chapter_start + self.chapters_per_volume - 1
        )
        num_chapters = chapter_end - chapter_start + 1

        series = manga_title
        if num_chapters == 1:
            title = f"{manga_title} vol{volume:0{volume_digits}} ch{chapter_start + 1}"
        else:
            title = f"{manga_title} vol{volume:0{volume_digits}} ch{chapter_start + 1}-{chapter_end + 1}"
        volume_cbz_path = os.path.join(output_folder, "cbz", f"{series} - {title}.cbz")

        # check if the cbz archive needs an update
        dependencies = [
            str(manga_chapters[chapter_start + chapter].file_path)
            for chapter in range(num_chapters)
        ]
        if self.is_obsolete(volume_cbz_path, dependencies):
            logger.info(f"Creating {volume_cbz_path}...")
            z = zipfile.ZipFile(volume_cbz_path, "w")

            # Add metadata info file
            comic_info_str = self.comic_info_template.format(
                series=title, writer=writer
            )
            comic_info_path = os.path.join(output_folder, f"ComicInfo{volume}.xml")
            with open(comic_info_path, "w") as the_file:
                the_file.write(comic_info_str)
            cbz_output_path = os.path.basename(comic_info_path.replace(str(volume), ""))
            z.write(comic_info_path, cbz_output_path)
            os.remove(comic_info_path)

            chapter = 0
            while chapter < num_chapters:
                cbz_file = cbz_files[chapter_start + chapter]
                file_root, _ = os.path.splitext(cbz_file)
                logger.debug(f"file_root={file_root}")

                archive_path = os.path.join(manga_folder, cbz_file)
                logger.debug(f"archive_path={archive_path}")
                folder = os.path.join(manga_folder, file_root)
                logger.debug(f"folder={folder}")
                try:
                    extract_cbz(archive_path, folder)
                except Exception:
                    # do not go further if extracting the cbz failed
                    return

                # Add every file in the current folder to the volume
                for root, _dirs, files in os.walk(folder):
                    for filename in files:
                        cbz_input_path = os.path.join(folder, filename)
                        cbz_output_path = os.path.join(file_root, filename)
                        z.write(cbz_input_path, cbz_output_path)

                # remove the unzipped chapter
                shutil.rmtree(folder)

                chapter += 1

            # Close the volume .cbz file
            z.close()

        # Convert the .cbz to .mobi
        volume_mobi_path = volume_cbz_path.replace("cbz", "mobi")
        # check if the mobi file needs an update
        if self.is_obsolete(volume_mobi_path, [volume_cbz_path]):
            logger.info(f"Creating {volume_mobi_path}...")
            if shutil.which("kcc-c2e") is None:
                raise RuntimeError(
                    "kcc-c2e not found on PATH -- MOBI bundling needs the KCC "
                    "fork installed. See the README 'Bundling to MOBI' section: "
                    "`git submodule update --init` then `uv pip install -e kcc/` "
                    "(and the vendored kindlegen for the MOBI step)."
                )
            command = [
                "kcc-c2e",
                "-u",
                "-o",
                os.path.dirname(volume_mobi_path),
                volume_cbz_path,
            ]
            logger.info(f"command={command}")
            subprocess.run(command)

        elapsed_time = time.time() - start_time
        time_str = time.strftime("%H:%M:%S", time.gmtime(elapsed_time))
        logger.debug(f"{title} creation duration: {time_str}")

    def bundle(self):
        manga_chapters = self.manga.volumes
        num_volumes = ceiling_division(len(manga_chapters), self.chapters_per_volume)
        volume_digits = len(str(num_volumes))

        logger.info(f"Bundling {num_volumes} volumes...")
        # rich.progress.Progress sharing the logging Console (see
        # utils.get_console) keeps this bar pinned at the bottom while logs
        # scroll above -- one coordinated rich Live region.
        from rich.progress import (
            BarColumn,
            MofNCompleteColumn,
            Progress,
            TextColumn,
            TimeElapsedColumn,
        )

        with Pool(initializer=configure_logging) as pool:
            with Progress(
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                MofNCompleteColumn(),
                TimeElapsedColumn(),
                console=get_console(),
                transient=False,
            ) as progress:
                task = progress.add_task("Bundling volumes", total=num_volumes)
                for _ in pool.imap(
                    self.create_volume_wrapped,
                    zip(range(num_volumes), repeat(volume_digits)),
                ):
                    progress.advance(task)

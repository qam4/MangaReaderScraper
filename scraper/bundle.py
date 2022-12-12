"""
Bundle manga into volumes with multiple chapters
"""

import logging
import os
import shutil
import re
import zipfile
import time
import subprocess
from multiprocessing.pool import Pool
from scraper.manga import Manga
from scraper.utils import get_adapter, settings
from logging import LoggerAdapter
import tqdm  # type: ignore
from typing import Any, List

logger = logging.getLogger(__name__)

WRITER_DEFAULT = "Fred Marchais"


def extract_cbz(archive_path, cbz_output_path):
    if not os.path.exists(cbz_output_path):
        logger.debug("Unzipping {} to {}".format(archive_path, cbz_output_path))
        with zipfile.ZipFile(archive_path, "r") as zip:
            zip.extractall(cbz_output_path)
    else:
        logger.debug("{} already unzipped".format(archive_path))


def ceiling_division(n, d):
    """
    Ceiling division
    """
    return -(n // -d)


def natural_sort(_list, key=lambda s: s) -> List[Any]:
    """
    Sort the list into natural alphanumeric order.
    """

    def convert_text(text: str):
        return int(text) if text.isdigit() else text.lower()

    def get_alphanum_key_func(key):
        return lambda s: [convert_text(c) for c in re.split("([0-9]+)", key(s))]

    sort_key = get_alphanum_key_func(key)
    return sorted(_list, key=sort_key)


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
        return settings()["config"]["manga_bundle_directory"]

    def is_obsolete(self, target: str, dependencies: List[str]) -> bool:
        if not os.path.isfile(target):
            return True
        for dependency in dependencies:
            if os.path.getmtime(target) - os.path.getmtime(dependency) < 0:
                return True
        return False

    def create_volume(self, volume_index: int):
        """
        Create a bundled volume
        """
        logger.info(f"create_volume: {volume_index}")

        start_time = time.time()

        input_root_path = self._get_manga_download_dir()
        output_root_path = self._get_manga_bundle_dir()

        # the original Manga class calls chapters volumes
        manga_chapters = self.manga.volumes
        manga_title = self.manga.name
        writer = self.writer
        manga_folder = os.path.join(input_root_path, manga_title)
        output_folder = os.path.join(output_root_path, manga_title)
        if not os.path.exists(output_folder):
            os.makedirs(output_folder)

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
            title = "{} vol{} ch{}".format(
                manga_title, volume, manga_chapters[chapter_start].number
            )
        else:
            title = "{} vol{} ch{}-{}".format(
                manga_title,
                volume,
                manga_chapters[chapter_start].number,
                manga_chapters[chapter_end].number,
            )
        volume_cbz_path = os.path.join(
            output_folder, "{} - {}.cbz".format(series, title)
        )
        logger.info("Creating {}...".format(volume_cbz_path))

        # check if the cbz archive needs an update
        dependencies = [
            str(manga_chapters[chapter_start + chapter - 1].file_path)
            for chapter in range(num_chapters)
        ]
        if self.is_obsolete(volume_cbz_path, dependencies):
            z = zipfile.ZipFile(volume_cbz_path, "w")

            # Add metadata info file
            comic_info_str = self.comic_info_template.format(
                series=title, writer=writer
            )
            comic_info_path = os.path.join(
                output_folder, "ComicInfo{}.xml".format(volume)
            )
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
                extract_cbz(archive_path, folder)

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
            logger.info("Creating {}...".format(volume_mobi_path))
            command = ["kcc-c2e", "-u", volume_cbz_path]
            subprocess.run(command)

        elapsed_time = time.time() - start_time
        time_str = time.strftime("%H:%M:%S", time.gmtime(elapsed_time))
        logger.info("{} creation time: {}".format(title, time_str))

    def bundle(self):
        manga_chapters = self.manga.volumes
        num_volumes = ceiling_division(len(manga_chapters), self.chapters_per_volume)

        logger.info(f"Bundling {num_volumes} volumes...")
        multi_process = True  # kcc does not work well multi-process
        if multi_process:
            with Pool(4) as pool:
                list(
                    tqdm.tqdm(
                        pool.imap(self.create_volume, range(num_volumes)),
                        total=num_volumes,
                        unit="volumes",
                    )
                )
        else:
            list(
                tqdm.tqdm(
                    map(self.create_volume, range(num_volumes)), total=num_volumes
                )
            )

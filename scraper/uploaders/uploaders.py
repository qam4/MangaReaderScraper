import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import dropbox
from dropbox.files import FileMetadata
from pcloud import PyCloud

from scraper.manga import Chapter
from scraper.uploaders.base import BaseUploader

logger = logging.getLogger(__name__)


class DropboxUploader(BaseUploader):
    """
    Uploads manga chapters to Dropbox
    """

    def __init__(self) -> None:
        super().__init__(service="dropbox")

    def _get_api_object(self) -> dropbox.Dropbox:
        return dropbox.Dropbox(self.config["token"])

    def chapter_exists(self, chapter: Chapter) -> bool:
        try:
            chapter_search = self.api.files_search(
                path=str(chapter.upload_path.parent),
                query=str(chapter.upload_path.name),
            )
            return True if chapter_search.matches else False
        except dropbox.exceptions.ApiError as e:
            actual_error = str(e.error._value)
            if "not_found" in actual_error:
                return False
            raise e

    def upload_chapter(self, chapter: Chapter) -> Optional[FileMetadata]:
        if self.chapter_exists(chapter):
            self.adapter.warning(f"Chapter {chapter.number} already exists in Dropbox")
            return None
        with open(chapter.file_path, "rb") as cbz:
            response = self.api.files_upload(cbz.read(), str(chapter.upload_path))
            self.adapter.info(f"Uploaded to {response.path_lower}")
            return response


class PcloudUploader(BaseUploader):
    """
    Uploads manga chapters to pCloud
    """

    def __init__(self) -> None:
        super().__init__(service="pcloud")

    def _get_api_object(self) -> PyCloud:
        return PyCloud(self.config["email"], self.config["password"])

    def create_directory(self, dirname: str) -> Dict[str, Any]:
        """
        Creates directory in pCloud
        """
        res = self.api.listfolder(path=dirname)
        if res.get("error"):
            if res["result"] == 2005:
                self.adapter.info(f"Creating directory {dirname}")
                response = self.api.createfolder(path=dirname)
                return response
        return {}

    def create_directories_recursively(self, filename: Path) -> List[Dict[str, Any]]:
        """
        Splits a path up and creates each subdirectory down the path tree
        """
        responses: List[Dict[str, Any]] = []
        for i in reversed(range(1, len(filename.parts) - 1)):
            directory = filename.parents[i - 1]
            res = self.create_directory(str(directory))
            if res.get("error"):
                raise IOError(res.get("error"))
            responses.append(res)
        return responses

    def upload_chapter(self, chapter: Chapter) -> Dict[str, Any]:
        self.create_directories_recursively(chapter.upload_path)
        parent_dir = str(chapter.upload_path.parent)
        response = self.api.uploadfile(
            data=chapter.file_path.read_bytes(),
            filename=str(chapter.upload_path),
            path=parent_dir,
        )
        if response.get("error"):
            raise IOError(response.get("error"))
        self.adapter.info(f"Chapter {chapter.number} uploaded to {chapter.upload_path}")
        return response

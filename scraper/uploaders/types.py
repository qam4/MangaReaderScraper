from typing import Union

from scraper.uploaders.uploaders import DropboxUploader, PcloudUploader

Uploader = Union[DropboxUploader, PcloudUploader]

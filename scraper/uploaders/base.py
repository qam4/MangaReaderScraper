import abc
import logging
from multiprocessing.pool import ThreadPool
from typing import Any

from scraper.manga import Manga, Volume
from scraper.utils import settings

logger = logging.getLogger(__name__)


class BaseUploader:
    """
    Base class for uploading to cloud storage services
    """

    def __init__(self, service: str) -> None:
        self.service = service
        self.config = settings()[service]
        self.api = self._get_api_object()

    def __call__(self, manga: Manga):
        return self.upload(manga)

    @abc.abstractmethod
    def _get_api_object(self) -> Any:
        """
        Returns an object used for uploading data to the service
        """
        pass

    @abc.abstractmethod
    def upload_volume(self, volume: Volume) -> None:
        """
        Uploads a given volume
        """
        pass

    def upload(self, manga: Manga) -> None:
        """
        Uploads all volumes in a given Manga object
        """
        logger.info(f"Uploading to {self.service.title()}")
        with ThreadPool() as pool:
            pool.map(self.upload_volume, manga.volumes)

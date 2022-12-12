import argparse
import logging
import sys
from typing import Dict, List, Optional, Tuple, Type

from scraper.download import Download
from scraper.exceptions import MangaDoesNotExist
from scraper.manga import Manga
from scraper.menu import SearchMenu
from scraper.parsers.mangafast import MangaFast
from scraper.parsers.mangakaka import MangaKaka
from scraper.parsers.manganelo import Manganelo
from scraper.parsers.mangareader import MangaReader
from scraper.parsers.types import SiteParserClass
from scraper.uploaders.types import Uploader
from scraper.bundle import Bundle
from scraper.uploaders.uploaders import DropboxUploader, PcloudUploader
from scraper.utils import menu_input, settings

CONFIG = settings()["config"]

logging.basicConfig(
    level=logging.WARN,
    format="%(asctime)s %(process)s %(levelname)s %(message)s",
)


logger = logging.getLogger(__name__)


def get_volume_values(volume: str) -> List[str]:
    """
    Transform a string digit into a list of floats
    """
    if "-" in volume:
        start, end = volume.split("-")
        return [str(x) for x in range(int(start), int(end) + 1)]
    return [x for x in volume.split(",")]


def manga_search(
    query: List[str], parser: SiteParserClass
) -> Tuple[str, str, List[str]]:
    """
    Search for a manga and return the manga name and volumes
    selected by user input
    """
    menu = SearchMenu(query, parser)
    manga = menu.handle_options()
    logger.debug(f"[manga_search] manga={manga}")
    title = manga["title"]
    url = manga["manga_url"]
    msg = (
        "Which volume(s) do you want to download "
        "(Enter alone to download all volumes)?"
    )
    volumes = menu_input(msg)
    logger.debug(f"[manga_search] volumes={volumes}")
    return (title, url, volumes.split())


def get_manga_parser(source: str) -> SiteParserClass:
    """
    Use the string to return correct parser class
    """
    sources: Dict[str, SiteParserClass] = {
        "mangareader": MangaReader,
        "mangafast": MangaFast,
        "mangakaka": MangaKaka,
        "manganelo": Manganelo,
    }
    parser = sources.get(source)
    if not parser:
        raise ValueError(f"{source} is not supported try {', '.join(sources.keys())}")
    return parser


def download_manga(
    manga_url: str,
    manga_title: str,
    volumes: Optional[List[str]],
    filetype: str,
    parser: SiteParserClass,
    preferred_name: Optional[str] = None,
) -> Manga:
    """Download a manga"""
    downloader = Download(manga_url, filetype, parser)
    manga = downloader.download_volumes(volumes, manga_title, preferred_name)
    return manga


def upload(manga: Manga, service: str) -> Uploader:
    services: Dict[str, Type[Uploader]] = {
        "dropbox": DropboxUploader,
        # "mega": MegaUploader,
        "pcloud": PcloudUploader,
    }
    uploader = services[service]()
    return uploader(manga)


def bundle(manga: Manga, chapter_per_volume: int):
    bundle = Bundle(manga, chapter_per_volume)
    return bundle.bundle()


def cli(arguments: List[str]) -> dict:
    # logger.debug(f"arguments={arguments}")
    parser = get_parser()
    args = vars(parser.parse_args(arguments))
    # logger.debug(f"args={args}")
    manga_parser = get_manga_parser(args["source"])
    title = None

    if args["remove"] and not args["upload"]:
        raise IOError("Cannot use --remove without --upload")

    if args["search"]:
        title, args["manga"], args["volumes"] = manga_search(
            args["search"], manga_parser
        )

    elif args["manga"]:
        args["manga"] = " ".join(args["manga"])
    else:
        raise IOError("Missing argument --manga or --search")

    if args["volumes"]:
        volumes: List[str] = []
        for vol in args["volumes"]:
            volumes += get_volume_values(vol)
        args["volumes"] = volumes
    else:
        args["volumes"] = None

    logger.debug(f"args={args}")
    try:
        manga = download_manga(
            manga_url=args["manga"],
            manga_title=title,
            volumes=args["volumes"],
            filetype=args["filetype"],
            parser=manga_parser,
            preferred_name=args["override_name"],
        )
    except MangaDoesNotExist:
        logging.warning(
            f"No manga found for {args['manga']}. Searching for closest match."
        )
        updated_args = change_args_to_search(args)
        # logger.debug(f"updated_args={updated_args}")
        return cli(updated_args)

    if args["upload"]:
        upload(manga, args["upload"])

    if args["remove"]:
        for volume in manga.volumes:
            volume.file_path.unlink()

    if args["bundle"]:
        bundle(manga, args["bundle"])

    return args


def change_args_to_search(args: Dict[str, Optional[str]]) -> List[Optional[str]]:
    """
    Alters arguments to use --search
    """
    updated_args = []
    args.update(
        {"manga": None, "volumes": None, "search": args["manga"], "volumes": None}
    )

    flags = ["remove"]

    for k, v in args.items():
        if (k == "upload" and not v) or (k in flags and v is False) or v is None:
            continue
        if k in flags and v is True:
            updated_args.append(f"--{k}")
            continue
        updated_args.append(f"--{k}")
        updated_args.append(v)
    return updated_args


def cli_entry() -> None:
    """
    Required as entry_point in setup.py cannot take args,
    however, we need cli() to take args for unit testing
    purposes. Hence the need for this function.
    """
    cli(sys.argv[1:])


def get_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="downloads and converts manga volumes to pdf or cbz format"
    )
    parser.add_argument("--manga", "-m", type=str, help="manga series name", nargs="*")
    parser.add_argument(
        "--search", "-s", type=str, help="search manga reader", nargs="*"
    )
    parser.add_argument(
        "--volumes", "-q", nargs="+", type=str, help="manga volume to download"
    )
    parser.add_argument("--output", "-o", default=CONFIG["manga_directory"])
    parser.add_argument(
        "--filetype",
        "-f",
        type=str,
        choices={"pdf", "cbz"},
        default=CONFIG["filetype"],
        help="format to store manga as",
    )
    parser.add_argument(
        "--source",
        "-z",
        type=str,
        choices={"mangareader", "mangafast", "mangakaka", "manganelo"},
        default=CONFIG["source"],
        help="website to scrape data from",
    )
    parser.add_argument(
        "--upload",
        "-u",
        type=str,
        choices={"dropbox", "mega", "pcloud"},
        help="upload manga to a cloud storage service",
    )
    parser.add_argument(
        "--override_name",
        "-n",
        type=str,
        help="change manga name for all saved/uploaded files",
    )
    parser.add_argument(
        "--remove",
        "-r",
        action="store_true",
        help="delete downloaded volumes aftering uploading to a cloud service",
    )
    parser.add_argument(
        "--version",
        "-v",
        action="version",
        version="v0.50",
        help="display the installed version number of the application",
    )
    parser.add_argument(
        "--bundle",
        type=int,
        help="Specify the number of chapters per volume in the output manga",
    )
    return parser


if __name__ == "__main__":
    cli_entry()

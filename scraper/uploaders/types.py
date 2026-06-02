from typing import TYPE_CHECKING, Union

# Imported only for type checking so that merely referencing the `Uploader`
# type (e.g. in scraper.__main__) does not pull in the optional upload backends
# (dropbox/pcloud). At runtime the alias degrades to a plain object type, which
# is all annotations need. See refactoring-plan §3.6 / §5.4.
if TYPE_CHECKING:
    from scraper.uploaders.uploaders import DropboxUploader, PcloudUploader

    Uploader = Union[DropboxUploader, PcloudUploader]
else:
    Uploader = object

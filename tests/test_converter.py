import os
import zipfile
from unittest import mock

import pytest

from scraper.config import HERE
from scraper.converter import Conversion


@pytest.mark.parametrize("inval, output", [("pdf", "pdf"), ("cbz", "cbz")])
def test_type_setting(inval, output):
    converter = Conversion("something")
    converter.type = inval
    assert converter.type == output


def test_wrong_type_setting():
    with pytest.raises(ValueError):
        converter = Conversion("something")
        converter.type = "csv"


def test_get_volume_images(converter):
    converter._get_volume_images()
    expected = ["test-manga_1_1.jpg", "test-manga_1_2.jpg", "test-manga_1_3.jpg"]
    assert converter.images == expected


def test_extract_page_number(converter, test_jpg_dir):
    test_jpg = f"{test_jpg_dir}/test-manga_1_3.jpg"
    page_number = converter._get_page_number(test_jpg)
    assert page_number == 3


def test_sort_images(converter):
    converter._get_volume_images()
    converter._sort_images()
    expected = [
        f"{HERE}/tests/test_files/jpgs/test-manga_1_1.jpg",
        f"{HERE}/tests/test_files/jpgs/test-manga_1_2.jpg",
        f"{HERE}/tests/test_files/jpgs/test-manga_1_3.jpg",
    ]
    assert expected == converter.images


def test_set_filename(converter):
    converter.type = "pdf"
    converter._set_filename()
    expected = f"{HERE}/tests/test_files/jpgs/test-manga/test-manga_volume_1.pdf"
    assert expected == converter.filename


def test_get_conversion_method_pdf(converter):
    converter.type = "pdf"
    method = converter._get_conversion_method()
    assert method.__name__ == "_convert_to_pdf"


def test_get_conversion_method_cbz(converter):
    converter.type = "cbz"
    method = converter._get_conversion_method()
    assert method.__name__ == "_convert_to_cbz"


def test_convert_to_cbz(converter):
    converter.type = "cbz"
    converter.convert_volume(1)
    expected_file_path = (
        f"{HERE}/tests/test_files/jpgs/test-manga/test-manga_volume_1.cbz"
    )
    assert os.path.exists(expected_file_path)
    assert zipfile.is_zipfile(expected_file_path)


def test_convert_to_pdf(converter):
    converter.type = "pdf"
    converter.convert_volume(1)
    expected_file_path = (
        f"{HERE}/tests/test_files/jpgs/test-manga/test-manga_volume_1.pdf"
    )
    assert os.path.exists(expected_file_path)
    # check is a valid pdf file
    sliced_pdf = open(expected_file_path, "rb").read()[:10]
    assert "PDF" in sliced_pdf.decode("utf-8")


def teardown_module(module):
    files = [
        f"{HERE}/tests/test_files/jpgs/test-manga/test-manga_volume_1.cbz",
        f"{HERE}/tests/test_files/jpgs/test-manga/test-manga_volume_1.pdf",
    ]
    for f in files:
        os.remove(f)

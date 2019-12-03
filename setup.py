"""
To install locally:
    python setup.py install

To upload to PyPi:
    python setup.py sdist
    pip install twine
    twine upload dist/*
"""

import setuptools

with open("README.md", "r") as fh:
    long_description = fh.read()

setuptools.setup(
    name="MangaReaderScraper",
    # should correlate with git tag
    version="0.1",
    author="superDross",
    author_email="dross78375@gmail.com",
    description="Manga scrapier",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/superDross/MangaReaderScraper",
    packages=setuptools.find_packages(),
    python_requires=">=3.6",
    classifiers=[
        "Programming Language :: Python :: 3",
        "Operating System :: POSIX :: Linux",
        "License :: OSI Approved :: GNU General Public License v3 (GPLv3)",
    ],
    entry_points={"console_scripts": ["manga-scraper = scraper.__main__:cli"]},
    install_requires=[
        "bs4>=0.0.1",
        "lxml>=4.2.5",
        "Pillow>=3.1.2",
        "PyQt5-sip>=12.7.0",
        "PyQt5>=5.13.0",
        "reportlab>=3.5.23",
        "requests>=2.18.4",
        "tabulate>=0.8.1",
    ],
)

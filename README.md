# MangaReaderScraper

**Deprecated project, do not use**

Search & download mangas from the command line.

![](docs/demo.gif)

## Install

Requires Python3.7+

To install:

```bash
pip3 install --user MangaReaderScraper
```

For development:

```bash
git clone https://github.com/superDross/MangaReaderScraper
pip install -r MangaReaderScraper/dev_requirements.txt
export PYTHONPATH=$PYTHONPATH:/path/to/MangaReaderScraper/
```
## usage
$ manga-scraper --help
usage: manga-scraper [-h] [--manga [MANGA [MANGA ...]]] [--search [SEARCH [SEARCH ...]]] [--volumes VOLUMES [VOLUMES ...]] [--output OUTPUT] [--filetype {pdf,cbz}] [--source {manganelo,mangareader,mangafast,mangakaka}]
                     [--upload {mega,dropbox,pcloud}] [--override_name OVERRIDE_NAME] [--remove] [--version] [--bundle BUNDLE]

downloads and converts manga volumes to pdf or cbz format

optional arguments:
  -h, --help            show this help message and exit
  --manga [MANGA [MANGA ...]], -m [MANGA [MANGA ...]]
                        manga series name
  --search [SEARCH [SEARCH ...]], -s [SEARCH [SEARCH ...]]
                        search manga reader
  --volumes VOLUMES [VOLUMES ...], -q VOLUMES [VOLUMES ...]
                        manga volume to download
  --output OUTPUT, -o OUTPUT
  --filetype {pdf,cbz}, -f {pdf,cbz}
                        format to store manga as
  --source {manganelo,mangareader,mangafast,mangakaka}, -z {manganelo,mangareader,mangafast,mangakaka}
                        website to scrape data from
  --upload {mega,dropbox,pcloud}, -u {mega,dropbox,pcloud}
                        upload manga to a cloud storage service
  --override_name OVERRIDE_NAME, -n OVERRIDE_NAME
                        change manga name for all saved/uploaded files
  --remove, -r          delete downloaded volumes aftering uploading to a cloud service
  --version, -v         display the installed version number of the application
  --bundle BUNDLE       Specify the number of chapters per volume in the output manga

## Options

`--search` Search mangareader.net for a given query and select to download one of the mangas from the parsed searched results. <br />
`--manga` Manga series name to download. <br />
`--volumes` Manga series volume number to download. <br />
`--filetype` Format to store manga as {PDF/CBZ}. <br />
`--output` Directory to save downloads (defaults to `~/Downloads`) <br />
`--source` Website to scrape from {mangareader/mangafast} - __mangakaka has been deprecated__<br />
`--upload` Upload mangas to a cloud storage service <br />
`--override_name` Change manga name used to store volume(s) locally or in the cloud <br />
`--remove` Delete the manga(s) after they have downloaded & uploaded <br />

## Config

The default config file lives in `$HOME/.config/mangascraper.ini` and is as below:

```ini
[config]

# directory to save downloaded files to
manga_directory = /home/dir/Download

# directory to save bundled files to
manga_bundle_directory = /home/dir/Manga

# default website to download from
source = mangareader

# defaulta filetype to store mangas as
filetype = pdf

# root cloud directory to upload the manga to
upload_root = /
```

## Uploading

### Dropbox

Follow this [guide](https://blogs.dropbox.com/developers/2014/05/generate-an-access-token-for-your-own-account/) to create a token. Then place the token into your config (`~/.config/mangarscraper.ini`):

```ini
[dropbox]
token = hdkd87799jjjj
```

### Mega

Add your email and password to the config file:

```ini
[mega]
email = email@email.com
password = notapassword123
```

### pCloud

Add you email and password to the config file:

```ini
[pcloud]
email = email@email.con
password = notapassword123
```

## Example Usage

After using the search function, a table will appear and you will be asked to select a specific manga (type a number in the first column). You will subsequently be asked to download a specific volume. In the example below, Dragon Ball Super volume 1 has been selected for download.

```
$ manga-scraper --search dragon ball

+----+---------------------------------+-----------+--------+
|    | Title                           |   Volumes | Type   |
|----+---------------------------------+-----------+--------|
|  0 | Dragon Ball: Episode of Bardock |         3 | Manga  |
|  1 | Dragon Ball SD                  |        20 | Manga  |
|  2 | DragonBall Next Gen             |         4 | Manga  |
|  3 | Dragon Ball                     |       520 | Manga  |
|  4 | Dragon Ball Z - Rebirth of F    |         3 | Manga  |
|  5 | Dragon Ball Super               |        29 | Manga  |
+----+---------------------------------+-----------+--------+
Select manga number

>> 5

Dragon Ball Super has been selected for download.
Which volume do you want to download (Enter alone to download all volumes)?

>> 1-25 33 56
```

To download a manga directly:

```bash
# Download all Dragon Ball volumes & upload to dropbox
manga-scraper --manga dragon-ball --upload dropbox

# Download volume 2 of the Final Fantasy XII manga
manga-scraper --manga final-fantasy-xii --volumes 2

# Download Dragon Ball Super volumes 3-7 & 23
manga-scraper --manga dragon-ball-super --volumes 3-7 23
```

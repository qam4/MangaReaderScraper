# MangaReaderScraper

Search and download manga from the command line, save as PDF or CBZ, and
optionally bundle into Kindle-ready MOBI volumes.

![](docs/demo.gif)

> **A note on this fork.** This is a personal, actively-maintained fork of
> [superDross/MangaReaderScraper](https://github.com/superDross/MangaReaderScraper)
> (which is itself deprecated). Manga sites change their markup, move behind
> Cloudflare, or shut down without warning, so **any given source can break at
> any time**. When one does, the [probe tooling](docs/adding-a-source.md) is
> there to help you re-point or rewrite its parser. Use responsibly and at a
> polite request rate.

## Supported sources

`--source` accepts any of the registered sites:

```
mangabuddy  mangafire  mangago  mangakaka
manganato   manganelo
```

Availability varies over time (see the note above). `mangafire` is the most
actively maintained parser. Adding a new source is documented in
[docs/adding-a-source.md](docs/adding-a-source.md).

### Known source limitations

- **kakalot family (`manganelo`, `manganato`, `mangakaka`)** — search and
  chapter listing work, but **page-image download does not**. The reader page
  is gated behind an *interactive* Cloudflare Turnstile (a human must click the
  "Verify you are human" checkbox on every chapter) and the image CDN serves
  bytes only to that live browser session, so there is no automatable download
  path. These sources are still useful for searching and browsing chapter
  lists; a download attempt fails fast with an explanatory message rather than
  hanging on a browser. Their chapter list is infinite-scroll, which the
  browser fetcher handles by scrolling to the bottom before parsing.

## Install

This fork is developed with [uv](https://docs.astral.sh/uv/) and pinned to
Python 3.13 (via `.python-version`).

```bash
git clone https://github.com/qam4/MangaReaderScraper
cd MangaReaderScraper

# Create the venv and install runtime + dev deps.
uv sync
```

Run the CLI through uv:

```bash
uv run manga-scraper --help
```

`uv run` isn't required — it just runs the command inside the project's venv
without you having to activate it. If you prefer, activate the venv and call
`manga-scraper` directly (`.venv\Scripts\activate` on Windows, then
`manga-scraper --help`).

Common dev commands:

```bash
uv run pytest                          # tests
uv run mypy scraper                    # type check
uv run ruff check ./scraper ./tests    # lint
uv run ruff format ./scraper ./tests   # format
```

## Quick start

Search for a series, pick it from the table, then choose what to download:

```
$ uv run manga-scraper --search dragon ball

+----+---------------------------------+------------------+------------+
|    | Title                           |   Latest Chapter | Source     |
|----+---------------------------------+------------------+------------|
|  1 | Dragon Ball: Episode of Bardock |                3 | mangabuddy |
|  2 | Dragon Ball SD                  |               35 | mangabuddy |
|  3 | DragonBall Next Gen             |                4 | mangabuddy |
|  4 | Dragon Ball                     |              520 | mangabuddy |
|  5 | Dragon Ball Z - Rebirth of F    |                3 | mangabuddy |
|  6 | Dragon Ball Super               |               62 | mangabuddy |
+----+---------------------------------+------------------+------------+
Select manga number

>> 6

Dragon Ball Super has been selected for download.
Which chapter(s) do you want to download (Enter alone to download all chapters)?

>> 1-25 33 56
```

Or skip the menu and download a series directly by its url slug:

```bash
# All chapters of Dragon Ball
uv run manga-scraper --manga dragon-ball

# Just chapter 2 of Final Fantasy XII
uv run manga-scraper --manga final-fantasy-xii --chapters 2

# Dragon Ball Super chapters 3-7 and 23, from a specific source, as CBZ
uv run manga-scraper --manga dragon-ball-super --chapters 3-7 23 --source mangafire --filetype cbz
```

If `--manga <slug>` finds nothing, the tool automatically falls back to a
`--search` for that term so you can pick the right entry.

## Selecting chapters

The `--chapters` argument (and the interactive prompt) is **chapter-number
based**, not positional. You can mix single chapters and ranges:

```
1-25 33 56     # chapters 1 through 25, plus 33 and 56
```

- Ranges are inclusive and resolved against the series' real chapter list, so
  they tolerate gaps and span decimal chapters (e.g. a `10-12` range will pick
  up a `10.5` if it exists).
- Press **Enter** alone at the prompt to download **all** chapters.
- A selector that matches nothing is skipped with a warning rather than
  aborting the run.

Some sites use opaque chapter slugs (no number in the url). For those, selection
falls back to **positional** order (1 = first listed chapter), since there is no
chapter number to match against.

## CLI reference

```
usage: manga-scraper [-h] [--manga [MANGA ...]] [--search [SEARCH ...]]
                     [--chapters CHAPTERS [CHAPTERS ...]] [--output OUTPUT]
                     [--filetype {pdf,cbz}]
                     [--source {mangabuddy,mangafire,mangago,mangakaka,manganato,manganelo}]
                     [--override_name OVERRIDE_NAME] [--version]
                     [--bundle BUNDLE]
```

| Flag | Short | Description |
|------|-------|-------------|
| `--manga` | `-m` | Manga series name / url slug to download |
| `--search` | `-s` | Search the source and pick from a results table |
| `--chapters` | `-q` | Chapters to download (single, ranges, or a mix); omit for all |
| `--output` | `-o` | Directory to save downloads (defaults to config `manga_directory`) |
| `--filetype` | `-f` | `pdf` or `cbz` (defaults to config `filetype`) |
| `--source` | `-z` | Site to scrape from (defaults to config `source`) |
| `--override_name` | `-n` | Rename the manga for all saved files |
| `--bundle` | | Chapters per volume; bundles the download into MOBI (forces `cbz`) |
| `--version` | `-v` | Print the installed version |

> **Note:** the CLI also exposes `--upload` / `--remove` flags for cloud storage,
> but those backends are unmaintained and partially broken. They are intentionally
> left undocumented here.

## Bundling to MOBI (`--bundle`)

The `--bundle` option groups chapters into volumes and converts each to **MOBI**
(preferred over EPUB for Kindle manga quality). This requires extra setup beyond
the base install, because it shells out to [Kindle Comic Converter
(KCC)](https://github.com/ciromattia/kcc):

1. **Install KCC** via the `bundle` extra, which puts the `kcc-c2e` CLI on your
   PATH along with all eleven of its runtime dependencies:
   ```bash
   uv sync --extra bundle
   ```
   KCC is pinned in the lockfile to upstream tag `v10.2.0`, so this is
   reproducible. It is an extra rather than a base dependency because KCC
   requires PySide6 (a full Qt stack) even for its CLI.

   Do **not** use `uv pip install -e kcc/` (what older revisions of this README
   said). That installs outside the lockfile, so a later venv rebuild leaves
   `kcc-c2e` on PATH with its imports broken — `natsort`, `psutil`, `pymupdf`,
   `numpy`, `mozjpeg_lossless_optimization`, `distro` and friends all disappear.

   The `kcc/` git submodule is no longer needed to *run* bundling; it is kept
   only for reading KCC's source when diagnosing output changes. If you want it,
   `git submodule update --init` (or clone with `--recurse-submodules`). Its
   commit and the lockfile's tag must be bumped together.
2. **7-Zip** (`7z` on your PATH) is required by KCC for archive handling.
   Install it from [7-zip.org](https://www.7-zip.org/) (Windows users: add the
   install dir, e.g. `C:\Program Files\7-Zip`, to PATH).
3. **kindlegen** does the final CBZ/EPUB -> MOBI step. Amazon folded it into the
   Kindle Previewer app and no longer ships the standalone binary, so a Windows
   build (`kindlegen.exe`) is vendored at the repo root. On other platforms you
   must supply your own `kindlegen` on PATH.

Bundling runs conversions in parallel, so the scraper passes KCC's `--tempdir`
flag: this keeps each conversion's temporary files on the source drive instead
of the shared system temp dir, which is what makes concurrent runs safe (a
plain parallel KCC would otherwise wipe its siblings' in-progress work dirs at
startup). This is why no patched KCC fork is needed.

Without these, `--bundle` raises a clear error (it never silently produces
nothing). Plain PDF/CBZ downloads need none of this.

Example — download a series as CBZ and bundle every 10 chapters into a MOBI:

```bash
uv run manga-scraper --manga dragon-ball-super --bundle 10
```

## Config

The default config file lives at `$HOME/.config/mangascraper.ini` and is created
on first run:

```ini
[config]

# directory to save downloaded files to
manga_directory = /home/dir/Download

# directory to save bundled files to
manga_bundle_directory = /home/dir/Manga

# default website to download from
source = mangabuddy

# default filetype to store mangas as
filetype = pdf
```

These optional keys are not written on first run, but are read if you add them:

```ini
[config]

# ComicInfo <Writer> fallback, used when the source exposes no author
# (MangaFire and mangabuddy do; the others don't). Defaults to "Unknown".
writer = Jane Doe

# parallel download/bundle workers. Defaults to min(4, CPU count).
jobs = 4

# flags passed through to kcc-c2e when bundling to MOBI. Defaults to
# "-u --hq -g 1.8". Any KCC flag works -- this is a verbatim passthrough, so
# `kcc-c2e --help` is the reference. `--tempdir` is always added (it keeps
# parallel conversions from wiping each other's work dirs), and `-o` plus the
# input path are always supplied by the scraper. An empty value means "no
# rendering flags at all", which is different from omitting the key.
#
# Useful ones:
#   -p KPW34     target a specific device profile (default is KV, 1072x1448;
#                `kcc-c2e --help` lists them all)
#   --hq         HQ Panel View -- tap-to-zoom on Kindle. In the default because
#                KCC 10.x disables Panel View unless this or -2 is passed.
#   -g 1.8       gamma. In the default because KCC 10.x sets every Kindle
#                profile's gamma to 1.0, which renders pages lighter/flatter
#                than KCC 5.x did.
#   -s           stretch to the device resolution instead of fitting; avoids
#                KCC's auto-crop trimming a sliver off pages whose aspect ratio
#                is close to the device's.
#   --colorautocontrast   apply autocontrast to colour pages (10.x skips them)
kcc_args = -u --hq -g 1.8
```

## How fetching works (briefly)

Network access goes through pluggable fetcher backends
(`scraper/fetchers.py`):

- **curl_cffi** (default) — speaks the `requests` API but presents a real Chrome
  TLS fingerprint, which transparently clears the fingerprint-based blocking
  that plain `requests` trips on many sites.
- **requests** — a lightweight fallback.
- **cloudscraper** — for Cloudflare's JS interstitial.
- **nodriver** (real browser) — for JS-rendered pages and search results that
  only populate after scripts run.

A parser declares which backend it needs. When a site stops working or you are
adding a new one, the **probe tool** can tell you which backend a page requires
and surface candidate selectors/endpoints — see
[docs/adding-a-source.md](docs/adding-a-source.md).

The browser backend runs **one shared session** for the whole process (a single
Chrome instance reused across search, chapter listing, and reading), launched
off-screen so it doesn't cover the terminal. If a site shows an interactive
Cloudflare check ("Verify you are human"), the window comes to the front and the
run waits for you to click it.

### Persisting a manual Cloudflare solve across runs

By default the browser uses a throwaway profile, so a challenge you solve by
hand has to be solved again next run. To keep a solved challenge (and its
`cf_clearance` cookie) between runs, point `MANGASCRAPER_BROWSER_PROFILE` at a
directory:

```bash
# Windows (cmd)
set MANGASCRAPER_BROWSER_PROFILE=%LOCALAPPDATA%\mangascraper-chrome
# macOS / Linux
export MANGASCRAPER_BROWSER_PROFILE="$HOME/.cache/mangascraper-chrome"
```

The shared session reuses that profile, so once you clear a site's check it
usually stays cleared until the cookie expires. (Safe because there's only ever
one browser instance — no profile-lock conflicts.)

### Limiting download concurrency

Downloads run in parallel (chapters × pages), capped at **8 concurrent CDN
requests** process-wide by default so an aggressive fan-out doesn't get your IP
throttled or banned. Tune it with `MANGASCRAPER_MAX_CONCURRENT_DOWNLOADS`:

```bash
# Windows (cmd)
set MANGASCRAPER_MAX_CONCURRENT_DOWNLOADS=4
# macOS / Linux
export MANGASCRAPER_MAX_CONCURRENT_DOWNLOADS=4
```

On top of this, the downloader honors a server's `Retry-After` on 429/503 and,
when any download is rate-limited, briefly eases off *all* download threads.

## Adding or fixing a source

See [docs/adding-a-source.md](docs/adding-a-source.md) for the full workflow,
including the `python -m scraper.probe` helper for inspecting a site's structure
and deciding how to fetch it.

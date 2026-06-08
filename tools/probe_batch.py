#!/usr/bin/env python3
"""Batch-drive ``scraper.probe`` across many sources/stages, then aggregate
every ``recommendation.txt`` into one matrix.

This is a *thin driver* around the existing probe -- it shells out to
``python -m scraper.probe`` and reads the artifacts the probe writes under
``probe_out/``. It does not import or modify probe internals beyond two pure
helpers (``site_name_from_url`` / ``stage_from_url``) used to predict where a
run lands.

Workflow (run on a machine with a browser -- the probe drives nodriver headful):

    uv run python tools/probe_batch.py run         # probe every filled target
    uv run python tools/probe_batch.py summary      # matrix of what was found
    uv run python tools/probe_batch.py list         # show targets, no probing

``run`` skips targets whose output already exists (resume-friendly); pass
``--force`` to re-probe them. ``run`` finishes by printing the summary.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

REPO = Path(__file__).resolve().parent.parent
PROBE_OUT = REPO / "probe_out"
DEFAULT_TARGETS = Path(__file__).resolve().parent / "probe_targets.toml"

STAGES = ("chapters", "images", "search")


@dataclass
class Target:
    site: str
    stage: str  # one of STAGES
    url: str
    search_query: Optional[str] = None

    @property
    def skip_reason(self) -> Optional[str]:
        if not self.url:
            return "no url (blank)"
        if "TODO" in self.url:
            return "url still has a TODO placeholder"
        return None

    @property
    def out_dir(self) -> Path:
        """Where scraper.probe writes this run. We force ``--site
        <source>/<stage>`` (see probe_command), so each (source, stage) gets a
        distinct, correctly-labelled folder regardless of the probe's URL-based
        stage heuristics -- which misclassify some schemes (e.g. mangago's
        ``/read-manga/<slug>`` would otherwise collide chapters+images into one
        ``home`` folder)."""
        return PROBE_OUT / self.site / self.stage


def load_targets(path: Path) -> List[Target]:
    with open(path, "rb") as fh:
        data = tomllib.load(fh)
    targets: List[Target] = []
    for site in data.get("site", []):
        name = site.get("name", "?")
        for stage in STAGES:
            url = site.get(stage, "") or ""
            targets.append(
                Target(
                    site=name,
                    stage=stage,
                    url=url,
                    search_query=site.get("search_query")
                    if stage == "search"
                    else None,
                )
            )
    return targets


def probe_command(t: Target, force: bool) -> List[str]:
    # Force the output subfolder to <source>/<stage> so stages never collide and
    # the folder name is the registry source name (what the user thinks in).
    cmd = [
        sys.executable,
        "-m",
        "scraper.probe",
        t.url,
        "--site",
        f"{t.site}/{t.stage}",
    ]
    if t.stage == "search" and t.search_query:
        cmd += ["--search", t.search_query]
    if force:
        cmd.append("--force")
    return cmd


def run(targets: List[Target], force: bool, timeout: int) -> None:
    runnable = [t for t in targets if t.skip_reason is None]
    skipped = [t for t in targets if t.skip_reason is not None]
    for t in skipped:
        print(f"[skip ] {t.site:12} {t.stage:8} -- {t.skip_reason}")

    for i, t in enumerate(runnable, 1):
        already = (t.out_dir / "recommendation.txt").exists()
        if already and not force:
            print(
                f"[done ] {t.site:12} {t.stage:8} -- already captured ({t.out_dir.relative_to(REPO)}); --force to redo"
            )
            continue
        cmd = probe_command(t, force)
        print(f"[run {i:2}/{len(runnable)}] {t.site:12} {t.stage:8} -> {t.url}")
        try:
            proc = subprocess.run(cmd, cwd=REPO, timeout=timeout)
            status = "ok" if proc.returncode == 0 else f"exit {proc.returncode}"
        except subprocess.TimeoutExpired:
            status = f"TIMEOUT after {timeout}s"
        except Exception as e:  # keep going across the whole sweep
            status = f"ERROR {e}"
        print(f"          -> {status}")
    print()
    summary()


# --------------------------- summary aggregation ---------------------------


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _field(text: str, label: str) -> str:
    """Pull the value after ``label:`` from a recommendation.txt line."""
    for line in text.splitlines():
        if line.lower().startswith(label.lower()):
            return line.split(":", 1)[1].strip()
    return "-"


@dataclass
class StageResult:
    site: str
    stage: str
    present: bool
    fetcher: str = "-"
    api_open: str = "-"
    challenge: bool = False
    title: str = "-"
    size: int = 0
    verdict: str = "-"


# Hosts that mean the domain has been dropped to a parking / for-sale / spam
# redirect service -- the captured page is NOT the manga site, even though it
# "loaded". Add more as you see them in `recommendation.txt`'s "host(s) seen".
_PARKED_HOSTS = (
    "parklogic",
    "sedoparking",
    "bodis",
    "dan.com",
    "afternic",
    "hugedomains",
    "above.com",
    "parkingcrew",
    "/cashparking",
    "uniregistry",
)
# Off-site hosts that indicate a spam/shop redirect rather than the manga site.
# Kept deliberately narrow: a mere host *change* is NOT a problem (mangabuddy
# legitimately serves from mangak.io; sites migrate domains), so we only flag
# hosts that are unambiguously not a manga site. Extend as you actually see them.
_SPAM_HOSTS = ("dhgate", "aliexpress")

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


def _page_title_size(page_html: str) -> tuple[str, int]:
    """Page <title> + byte size -- the single most diagnostic signal (separates
    '404' / 'Just a moment' / 'Privacy error' / 'Redirecting' / a real title).
    Empty title (JS apps) -> '(js/no title)'."""
    if not page_html:
        return "(no page.html)", 0
    m = _TITLE_RE.search(page_html)
    title = " ".join(m.group(1).split()) if m else ""
    return (title or "(js/no title)"), len(page_html)


def _hosts_seen(rec: str) -> str:
    for line in rec.splitlines():
        if line.lower().startswith("host(s) seen:"):
            return line.split(":", 1)[1].strip().lower()
    return ""


def _classify(title: str, size: int, hosts: str, challenge: bool) -> str:
    """Turn the capture into a fix/retire verdict. Order matters: a parked/spam
    landing or a privacy/redirect interstitial means the capture is NOT the
    manga site, regardless of how cleanly it 'loaded'.

      PARKED   - domain dropped to a parking service (parklogic, sedo, ...)
      OFFSITE  - redirected to an unambiguous spam/shop host (dhgate, ...)
      DEAD     - Chrome 'Privacy error' / connection interstitial (cert/DNS)
      REDIRECT - a 'Redirecting...' shim (inspect where it landed)
      WALL     - Cloudflare challenge wall not cleared ('Just a moment...')
      404      - page loaded but is a not-found (often just a stale sample slug)
      LIVE     - a real page

    NOTE: a host *change* alone is never a verdict -- a legit migration
    (mangabuddy -> mangak.io) lands on a non-parked, non-spam host with real
    content and is correctly reported LIVE. Only the kind of landing host
    (parking/spam) or an interstitial title condemns a capture.
    """
    t = title.lower()
    h = hosts.lower()
    if any(p in h for p in _PARKED_HOSTS):
        return "PARKED"
    if any(s in h for s in _SPAM_HOSTS) or any(s in t for s in ("dhgate", "wholesale")):
        return "OFFSITE"
    if "privacy error" in t or "your connection" in t:
        return "DEAD"
    if "redirect" in t:
        return "REDIRECT"
    if challenge or "just a moment" in t or "attention required" in t:
        return "WALL"
    if "404" in t or "not found" in t:
        return "404?slug"
    return "LIVE"


def collect(targets: List[Target]) -> List[StageResult]:
    results: List[StageResult] = []
    for t in targets:
        if not t.url or "TODO" in t.url:
            continue
        d = t.out_dir
        rec = _read(d / "recommendation.txt")
        cand = _read(d / "candidates.txt")
        title, size = _page_title_size(_read(d / "page.html"))
        hosts = _hosts_seen(rec)
        challenge = "CHALLENGE WALL" in cand
        results.append(
            StageResult(
                site=t.site,
                stage=t.stage,
                present=bool(rec or cand or size),
                fetcher=_field(rec, "suggested default fetcher") if rec else "-",
                api_open=_field(rec, "open JSON API found") if rec else "-",
                challenge=challenge,
                title=title,
                size=size,
                verdict=_classify(title, size, hosts, challenge),
            )
        )
    return results


def summary(targets: Optional[List[Target]] = None) -> None:
    if targets is None:
        targets = load_targets(DEFAULT_TARGETS)
    results = collect(targets)
    if not results:
        print(
            "No probe_out captures found yet. Run: uv run python tools/probe_batch.py run"
        )
        return
    hdr = f"{'site':12} {'stage':8} {'verdict':9} {'fetcher':9} {'api':4} {'size':>7}  title"
    print(hdr)
    print("-" * (len(hdr) + 18))
    for r in results:
        print(
            f"{r.site:12} {r.stage:8} {r.verdict:9} {r.fetcher[:9]:9} "
            f"{r.api_open[:4]:4} {r.size:>7}  {r.title[:38]}"
        )


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("action", choices=("run", "summary", "list"), help="what to do")
    p.add_argument(
        "--targets",
        type=Path,
        default=DEFAULT_TARGETS,
        help="targets TOML (default: tools/probe_targets.toml)",
    )
    p.add_argument(
        "--force", action="store_true", help="re-probe targets even if already captured"
    )
    p.add_argument(
        "--timeout",
        type=int,
        default=180,
        help="per-probe timeout in seconds (default 180)",
    )
    args = p.parse_args(argv)

    targets = load_targets(args.targets)

    if args.action == "list":
        for t in targets:
            note = t.skip_reason or f"-> {t.out_dir.relative_to(REPO)}"
            q = (
                f" (search='{t.search_query}')"
                if t.stage == "search" and t.search_query
                else ""
            )
            print(f"{t.site:12} {t.stage:8} {t.url}{q}  {note}")
        return 0
    if args.action == "summary":
        summary(targets)
        return 0
    run(targets, force=args.force, timeout=args.timeout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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


def _strategy(text: str) -> str:
    """Pull ``-> strategy: X`` from fetch_recommendation.txt (the ``->`` result
    line, not the ``# Fetch strategy:`` header)."""
    for line in text.splitlines():
        if "-> strategy:" in line:
            return line.split("strategy:", 1)[1].strip()
    return "-"


@dataclass
class StageResult:
    site: str
    stage: str
    present: bool
    fetcher: str = "-"
    api_open: str = "-"
    strategy: str = "-"
    challenge: bool = False


def collect(targets: List[Target]) -> List[StageResult]:
    results: List[StageResult] = []
    for t in targets:
        if not t.url or "TODO" in t.url:
            continue
        d = t.out_dir
        rec = _read(d / "recommendation.txt")
        fetch = _read(d / "fetch_recommendation.txt")
        cand = _read(d / "candidates.txt")
        present = bool(rec or fetch or cand)
        results.append(
            StageResult(
                site=t.site,
                stage=t.stage,
                present=present,
                fetcher=_field(rec, "suggested default fetcher") if rec else "-",
                api_open=_field(rec, "open JSON API found") if rec else "-",
                strategy=_strategy(fetch) if fetch else "-",
                challenge="CHALLENGE WALL" in cand,
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
    hdr = f"{'site':12} {'stage':8} {'cap':3} {'fetcher':10} {'api':4} {'strategy':18} chal"
    print(hdr)
    print("-" * len(hdr))
    for r in results:
        cap = "yes" if r.present else " - "
        chal = "WALL" if r.challenge else ""
        print(
            f"{r.site:12} {r.stage:8} {cap:3} {r.fetcher[:10]:10} "
            f"{r.api_open[:4]:4} {r.strategy[:18]:18} {chal}"
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

"""
Chapter identifiers, ordering, and selection.

One place that defines (a) how chapter ids are ordered and (b) how a user's
``--volumes`` selection -- single ids and ``start-end`` ranges -- maps onto the
chapter ids a site actually offers.

This replaces the ad-hoc ``int()`` / ``float()`` / lexicographic-string sorts
that were scattered across the CLI range parser, the volume sorter, and the
individual parsers (refactoring-plan sec 3.1 / 5.1). Chapter numbering varies
per site -- plain integers, decimals like ``9.22``, gaps -- so every site that
numbered things differently used to force a patch in whichever spot broke.

Selection is **chapter-number based** when chapter ids carry numbers:
``--volumes 9-12`` means "every chapter whose number is between 9 and 12
(inclusive)", which naturally includes decimal chapters like ``9.22`` and
tolerates gaps, and ``--volumes 40`` means "chapter 40", not "the 40th chapter".

Some sites, though, use **opaque chapter slugs** with no reliable number
(mangabuddy ids are whatever the uploader chose: ``vol-54-chapter-name``,
``chapter-3000``, ...). There is no general rule to turn those into chapter
numbers -- this is the plan's "Problem A". For such sites, selection and
ordering fall back to **positional** (the site's own order + 1-based index),
which is what those sites relied on before number-based selection existed.
"""

import logging
import re
from dataclasses import dataclass
from functools import total_ordering
from typing import Iterable, List

logger = logging.getLogger(__name__)

# leading numeric portion of a chapter id, e.g. "28.22" -> 28.22, "7b" -> 7
_NUM_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)")
# a "start-end" range token, e.g. "9-12" or "9.22-9.25"
_RANGE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*$")


@total_ordering
@dataclass(frozen=True)
class ChapterId:
    """
    A chapter identifier that keeps its original string form (so urls round-trip
    verbatim, e.g. ``chapter-28.22``) while ordering numerically.

    Numeric ids order by value (``9`` < ``10``, ``28`` < ``28.22`` < ``29``).
    Non-numeric ids (rare/defensive) sort after all numeric ones, alphabetically,
    so a weird id never crashes a sort.
    """

    raw: str

    @property
    def value(self) -> float | None:
        """Numeric value of the leading number, or ``None`` if non-numeric."""
        match = _NUM_RE.match(self.raw)
        return float(match.group(1)) if match else None

    @property
    def _sort_key(self) -> tuple:
        value = self.value
        if value is not None:
            return (0, value, self.raw)
        return (1, 0.0, self.raw.lower())

    def __lt__(self, other: "ChapterId") -> bool:
        if not isinstance(other, ChapterId):
            return NotImplemented
        return self._sort_key < other._sort_key

    def __str__(self) -> str:
        return self.raw


def sort_chapter_ids(ids: Iterable[str]) -> List[str]:
    """
    Return the chapter ids de-duplicated in canonical order.

    When the ids carry numbers, that means ascending chapter order
    (``9`` < ``10``, ``28`` < ``28.22`` < ``29``); any stray non-numeric ids
    sort last. When **no** id carries a number -- i.e. the site uses opaque
    slugs (mangabuddy: ``vol-54-chapter-name``, ``chapter-3000``) -- there is no
    meaningful numeric order, so the site's own order is preserved (just
    de-duplicated). This matters because such sites order chapters themselves
    and selection then falls back to positional (see ``select_chapters``).
    """
    seen: set[str] = set()
    deduped: List[str] = []
    for raw in ids:
        if raw not in seen:
            seen.add(raw)
            deduped.append(raw)

    if not any(ChapterId(raw).value is not None for raw in deduped):
        # opaque slugs: keep the site's order
        return deduped
    return [chapter.raw for chapter in sorted(ChapterId(raw) for raw in deduped)]


def _match_token(token: str, available: List[ChapterId]) -> List[str]:
    """
    Resolve a single selector token against the available (sorted) chapters.

    ``available`` must already be sorted ascending so range matches come back in
    order.
    """
    range_match = _RANGE_RE.match(token)
    if range_match:
        low, high = float(range_match.group(1)), float(range_match.group(2))
        if low > high:
            low, high = high, low
        return [
            chapter.raw
            for chapter in available
            if chapter.value is not None and low <= chapter.value <= high
        ]

    selector = ChapterId(token)
    if selector.value is not None:
        return [chapter.raw for chapter in available if chapter.value == selector.value]
    # non-numeric selector: fall back to exact string match
    return [chapter.raw for chapter in available if chapter.raw == token]


def _select_by_index(tokens: Iterable[str], available: List[str]) -> List[str]:
    """
    Positional (1-based) selection, used as a fallback for sites whose chapter
    ids are opaque slugs (e.g. mangabuddy's ``vol-54-chapter-name`` or
    ``chapter-3000``) where no chapter *number* can be reliably extracted.

    ``--volumes 1`` -> the 1st chapter in the list, ``--volumes 1-3`` -> the
    first three. This preserves the behaviour these sites relied on before
    chapter-number selection existed. Out-of-range positions are warned and
    skipped rather than raising.
    """
    selected: List[str] = []
    seen: set[str] = set()

    def take(pos: int) -> None:
        # pos is 1-based
        if 1 <= pos <= len(available):
            raw = available[pos - 1]
            if raw not in seen:
                seen.add(raw)
                selected.append(raw)
        else:
            logger.warning(
                "Chapter position %d out of range (1..%d)", pos, len(available)
            )

    for token in tokens:
        token = token.strip()
        if not token:
            continue
        range_match = _RANGE_RE.match(token)
        if range_match:
            low, high = int(float(range_match.group(1))), int(
                float(range_match.group(2))
            )
            if low > high:
                low, high = high, low
            for pos in range(low, high + 1):
                take(pos)
        elif token.isdigit():
            take(int(token))
        else:
            logger.warning("Cannot select %r positionally (expected a number)", token)
    return selected


def select_chapters(tokens: Iterable[str], available: Iterable[str]) -> List[str]:
    """
    Map user selector tokens onto the chapter ids a site offers.

    ``tokens`` are raw selectors such as ``["9-12", "28.22", "40"]`` (single ids
    and ``start-end`` ranges). ``available`` is the chapter ids the site has, in
    any order. Returns the matching chapter ids, de-duplicated, preserving the
    order the user asked for (and ascending order within a range).

    Selection is chapter-number based when the site's chapter ids carry numbers.
    For sites whose ids are opaque slugs with no extractable number, it falls
    back to **positional** (1-based index) selection -- the only meaningful
    interpretation when chapters aren't numbered (see ``_select_by_index``).

    Tokens that match nothing are warned about and skipped rather than raising,
    so one bad selector doesn't abort a multi-chapter download.
    """
    available = list(available)
    chapter_ids = [ChapterId(raw) for raw in available]

    # If no id carries a number, the site uses opaque slugs -> select by position.
    if not any(chapter.value is not None for chapter in chapter_ids):
        return _select_by_index(tokens, available)

    available_sorted = sorted(set(chapter_ids))
    selected: List[str] = []
    seen: set[str] = set()
    for token in tokens:
        token = token.strip()
        if not token:
            continue
        matches = _match_token(token, available_sorted)
        if not matches:
            logger.warning("No chapters matched selector %r", token)
        for raw in matches:
            if raw not in seen:
                seen.add(raw)
                selected.append(raw)
    return selected

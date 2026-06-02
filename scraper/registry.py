"""
Source registry: maps a ``--source`` name to its site-parser class.

Adding a site becomes "write the parser, decorate it with
``@register_source("name")``" instead of editing the ``get_manga_parser`` dict,
the ``--source`` argparse choices, and the ``types.py`` Union blocks separately
(refactoring-plan §3.3 / §5.3).

Usage:

    from scraper.registry import register_source
    from scraper.parsers.base import BaseSiteParser

    @register_source("mangafire")
    class Mangafire(BaseSiteParser): ...

Then ``get_source("mangafire")`` returns the class and ``available_sources()``
lists every registered name (used for the argparse ``choices``).

Registration happens at class-definition time, so the parser modules must be
imported first; ``load_sources()`` imports them all (idempotent).
"""

from __future__ import annotations

import importlib
import logging
from typing import Dict, List, Optional, Type

from scraper.parsers.base import BaseSiteParser

logger = logging.getLogger(__name__)

_REGISTRY: Dict[str, Type[BaseSiteParser]] = {}

# Parser modules to import so their @register_source decorators run. Adding a
# new site means adding its module here (one line) -- still far less than the
# previous four edit sites, and this list is the single source of truth.
_SOURCE_MODULES = [
    "scraper.parsers.mangareader",
    "scraper.parsers.mangakaka",
    "scraper.parsers.manganelo",
    "scraper.parsers.manganato",
    "scraper.parsers.mangapark",
    "scraper.parsers.mangago",
    "scraper.parsers.mangabuddy",
    "scraper.parsers.mangafast",
    "scraper.parsers.mangafire",
]

_loaded = False


def register_source(name: str):
    """Class decorator registering a ``BaseSiteParser`` subclass under ``name``."""

    def decorator(cls: Type[BaseSiteParser]) -> Type[BaseSiteParser]:
        if name in _REGISTRY and _REGISTRY[name] is not cls:
            logger.warning("Overriding already-registered source %r", name)
        _REGISTRY[name] = cls
        cls.source_name = name  # type: ignore[attr-defined]
        return cls

    return decorator


def load_sources() -> None:
    """Import all source modules so their decorators populate the registry.

    Idempotent: safe to call repeatedly.
    """
    global _loaded
    if _loaded:
        return
    for module in _SOURCE_MODULES:
        importlib.import_module(module)
    _loaded = True


def get_source(name: str) -> Optional[Type[BaseSiteParser]]:
    """Return the site-parser class for ``name``, or ``None`` if unknown."""
    load_sources()
    return _REGISTRY.get(name)


def available_sources() -> List[str]:
    """All registered source names, sorted (used for ``--source`` choices)."""
    load_sources()
    return sorted(_REGISTRY)

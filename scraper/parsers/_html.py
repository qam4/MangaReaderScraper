"""
Small bs4 typing helpers.

BeautifulSoup types ``Tag.get(...)`` / ``Tag["..."]`` as ``str |
AttributeValueList`` because multi-valued attributes (``class``, ``rel``, ...)
come back as a list. For the single-valued attributes we read (``href``,
``src``, ``data-src``, ``alt``, ...) the value is always a ``str`` at runtime.

``attr`` narrows that union to ``str`` in one place so parsers don't each carry
``# type: ignore`` noise. If an attribute is genuinely missing it returns "";
if it's multi-valued it joins with a space (matching how a browser serializes
e.g. a class list), so the return type is always ``str``.
"""

from typing import Optional

from bs4.element import Tag


def attr(tag: Optional[Tag], name: str, default: str = "") -> str:
    """Return ``tag[name]`` as a ``str`` (joined if multi-valued), or ``default``."""
    if tag is None:
        return default
    value = tag.get(name)
    if value is None:
        return default
    if isinstance(value, str):
        return value
    # multi-valued (list) attribute -> join, so the result is always a str
    return " ".join(value)


def text(tag: Optional[Tag], default: str = "") -> str:
    """Return ``tag.text`` as a ``str``, or ``default`` if the tag is None."""
    return tag.text if tag is not None else default

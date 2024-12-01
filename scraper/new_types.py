"""
Custom type hints & aliases
"""

from typing import Dict, Iterable, Optional, Tuple

PageData = Tuple[int, bytes, str]
VolumeData = Tuple[str, Optional[Iterable[PageData]]]
SearchResults = Dict[str, Dict[str, str]]

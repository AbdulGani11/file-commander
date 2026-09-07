"""
Launcher Data Types - The Query and Result that every plugin speaks

A Path can only answer "which file?". A Result carries its own action, so a
plugin can return something with no file behind it at all.
"""

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional


# Plugins registered under this keyword run on every keyword-less search.
# Flow Launcher calls this the global plugin wildcard sign.
GLOBAL_WILDCARD = "*"


@dataclass(frozen=True)
class Query:
    """A user's search text, split into an action keyword and the search part."""

    raw: str            # Exactly what was typed, with surrounding spaces removed
    keyword: str = ""   # Empty unless the first word is a registered keyword
    search: str = ""    # The part a plugin should actually search

    @property
    def is_empty(self) -> bool:
        """True when there is nothing to search for."""
        return not self.search.strip()

    @property
    def search_terms(self) -> tuple:
        """The search text split into words, without the action keyword."""
        return tuple(self.search.split())

    @staticmethod
    def parse(text: str, keywords: Optional[set] = None) -> "Query":
        """Split raw input into an optional action keyword plus search text.

        "sp hoshino" with "sp" registered gives keyword="sp", search="hoshino".
        Plugins read `search`, never `raw`.
        """
        raw = (text or "").strip()
        if not raw:
            return Query(raw="", keyword="", search="")

        keywords = keywords or set()

        parts = raw.split(None, 1)
        first_word = parts[0].lower()

        if first_word in keywords:
            rest = parts[1].strip() if len(parts) > 1 else ""
            return Query(raw=raw, keyword=first_word, search=rest)

        return Query(raw=raw, keyword="", search=raw)


@dataclass
class Result:
    """One selectable row: what to show, how to rank it, and what to do."""

    title: str                                          # Main line
    subtitle: str = ""                                  # Dim second line
    score: int = 0                                      # Higher sorts first
    action: Optional[Callable[[], Optional[bool]]] = None
    icon: Optional[str] = None                          # Fallback glyph name
    source: str = ""                                    # Plugin that produced it
    context: Dict[str, Any] = field(default_factory=dict)   # Extra data, e.g. path

    def run(self) -> bool:
        """Run the action. True means the launcher window should hide.

        False keeps it open so a plugin can replace the results in place.
        """
        if self.action is None:
            return False

        try:
            outcome = self.action()
        except Exception:
            # A broken action must never crash the user interface
            return False

        return True if outcome is None else bool(outcome)

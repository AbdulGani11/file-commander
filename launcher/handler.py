"""
Plugin Contract - What every search provider must implement

A plugin is anything that turns a Query into a list of Results. Keeping the
contract this small is what lets file search, application search, and any
future plugin sit side by side in the same dispatcher.
"""

import threading
from typing import List, Protocol, runtime_checkable

from .models import Query, Result


class QueryCancelled(Exception):
    """Raised by a plugin that noticed its query is no longer wanted."""


class CancellationToken:
    """Signals that a plugin's answer is obsolete because the user typed again.

    Cooperative: a running thread cannot be killed safely, so plugins must poll.
    """

    __slots__ = ("_event",)

    def __init__(self):
        # Event is a thread-safe flag: one thread sets it, others read it
        self._event = threading.Event()

    @property
    def cancelled(self) -> bool:
        """True once this query has been superseded."""
        return self._event.is_set()

    def cancel(self):
        """Mark the query obsolete."""
        self._event.set()

    def raise_if_cancelled(self):
        """Abort by raising, for plugins that prefer exceptions to checks."""
        if self._event.is_set():
            raise QueryCancelled()

    def wait(self, timeout: float) -> bool:
        """Sleep up to timeout seconds. True if cancelled while waiting.

        Waiting on the flag lets an obsolete query abort without paying the delay.
        """
        return self._event.wait(timeout)


@runtime_checkable
class Plugin(Protocol):
    """Anything that turns a Query into Results."""

    name: str             # Stamped onto each Result so its origin is known
    keyword: str          # Action keyword, or GLOBAL_WILDCARD to always run
    search_delay: float   # Seconds to wait before working (0 for in-memory work)

    def query(self, q: Query, token: CancellationToken) -> List[Result]: ...


class BasePlugin:
    """Optional base class supplying the standard attributes and defaults."""

    name = "plugin"
    keyword = "*"          # Global by default
    search_delay = 0.0

    def init(self):
        """Called once when the plugin is registered. Override to load data."""

    def query(self, q: Query, token: CancellationToken) -> List[Result]:
        raise NotImplementedError

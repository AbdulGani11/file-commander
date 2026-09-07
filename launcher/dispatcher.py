"""
Query Dispatcher - Sends a query to the right plugins and merges the answers

Two behaviours matter as much as the routing: starting a query cancels the
previous one, and a plugin that raises is dropped rather than breaking the
whole result list.
"""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional

from .handler import CancellationToken, Plugin, QueryCancelled
from .models import GLOBAL_WILDCARD, Query, Result


# CONSTANTS - Centralized configuration for easy maintenance

DEFAULT_LIMIT = 20      # Maximum results handed back to the user interface
DEFAULT_WORKERS = 8     # Plugins queried at the same time


class Dispatcher:
    """Holds the registered plugins and runs queries against them."""

    def __init__(self, max_workers: int = DEFAULT_WORKERS):
        self._plugins: List[Plugin] = []

        # Keyword -> plugins, so routing is a dictionary lookup rather than a
        # scan through every plugin on each keystroke
        self._by_keyword: Dict[str, List[Plugin]] = {}

        # Plugins run in parallel so one slow plugin cannot hold up the rest
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="launcher-query"
        )

        # The token for the query currently running, so it can be cancelled
        self._token: Optional[CancellationToken] = None

        # Diagnostics from the most recent query
        self.last_timings: Dict[str, float] = {}   # Plugin name -> seconds
        self.last_errors: Dict[str, str] = {}      # Plugin name -> error message

    # REGISTRATION

    def register(self, plugin: Plugin):
        """Add a plugin under its action keyword, or the global wildcard."""
        keyword = (getattr(plugin, "keyword", "") or GLOBAL_WILDCARD).lower()
        self._by_keyword.setdefault(keyword, []).append(plugin)
        self._plugins.append(plugin)

        # Give the plugin its chance to load data (application list, etc.)
        init = getattr(plugin, "init", None)
        if callable(init):
            init()

    @property
    def plugins(self) -> List[Plugin]:
        return list(self._plugins)

    @property
    def keywords(self) -> set:
        """Registered action keywords, excluding the global wildcard."""
        return {k for k in self._by_keyword if k != GLOBAL_WILDCARD}

    # RUNNING A QUERY

    def cancel(self):
        """Tell the query that is currently running to stop."""
        if self._token is not None:
            self._token.cancel()

    def query(self, text: str, limit: int = DEFAULT_LIMIT) -> List[Result]:
        """Cancel the previous query, run this one, return ranked Results."""
        self.cancel()

        token = CancellationToken()
        self._token = token

        self.last_timings = {}
        self.last_errors = {}

        q = Query.parse(text, self.keywords)
        if not q.raw:
            return []

        targets = self._route(q)
        if not targets:
            return []

        # Hand every selected plugin to the thread pool at once
        futures = {
            self._pool.submit(self._run_plugin, plugin, q, token): plugin
            for plugin in targets
        }

        merged: List[Result] = []
        for future in as_completed(futures):
            if token.cancelled:
                return []       # A newer query replaced this one
            merged.extend(future.result())

        # Sorting is stable, so plugins keep their own order among equal scores
        merged.sort(key=lambda r: r.score, reverse=True)
        return merged[:limit]

    def _route(self, q: Query) -> List[Plugin]:
        """Pick which plugins see this query.

        A known action keyword routes to that plugin alone.
        """
        if q.keyword:
            return list(self._by_keyword.get(q.keyword, ()))
        return list(self._by_keyword.get(GLOBAL_WILDCARD, ()))

    def _run_plugin(self, plugin, q: Query, token: CancellationToken) -> List[Result]:
        """Run one plugin with timing, search delay, and error handling."""
        name = getattr(plugin, "name", plugin.__class__.__name__)
        started = time.perf_counter()

        try:
            # Lets a fast typist cancel an expensive plugin before it works
            delay = float(getattr(plugin, "search_delay", 0.0) or 0.0)
            if delay > 0 and token.wait(delay):
                return []

            if token.cancelled:
                return []

            produced = plugin.query(q, token) or []

            if token.cancelled:
                return []

            for result in produced:
                result.source = name
            return produced

        except QueryCancelled:
            return []
        except Exception as exc:
            # Drop this plugin's results, keep everyone else's
            self.last_errors[name] = "%s: %s" % (type(exc).__name__, exc)
            return []
        finally:
            self.last_timings[name] = time.perf_counter() - started

    def shutdown(self):
        """Stop in-flight work and release the worker threads."""
        self.cancel()
        self._pool.shutdown(wait=False)

    # DIAGNOSTICS

    def last_query_ms(self) -> float:
        """Previous query time in ms. Plugins run in parallel, so the slowest
        one is the real elapsed time."""
        if not self.last_timings:
            return 0.0
        return max(self.last_timings.values()) * 1000.0

    def describe_timings(self) -> str:
        """Readable per-plugin timing breakdown for the previous query."""
        if not self.last_timings:
            return "no query executed"

        parts = [
            "%s %.2fms" % (name, seconds * 1000.0)
            for name, seconds in sorted(
                self.last_timings.items(), key=lambda kv: kv[1], reverse=True
            )
        ]
        return "slowest %.2fms  (%s)" % (self.last_query_ms(), ", ".join(parts))

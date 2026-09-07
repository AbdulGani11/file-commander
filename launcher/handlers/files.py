"""
File Search - Adapts FileFind's search engine to the plugin contract

This is an adapter, not a second search engine. All the indexing, caching, live
updating and relevance work stays in FileSearchIndex. The only job here is
turning the Paths it returns into Results that know how to open themselves.
"""

import os
from pathlib import Path
from typing import List

from .. import matcher
from ..handler import BasePlugin, CancellationToken
from ..models import GLOBAL_WILDCARD, Query, Result


# Pull extra candidates from the engine, since re-ranking may reorder them
CANDIDATE_LIMIT = 80

# Points per recorded open, and the cap. Mirrors FileFind's own usage-adaptive
# bonus, which is lost when results are re-scored here and so is reapplied.
ACCESS_SCORE_WEIGHT = 5
ACCESS_SCORE_MAX = 40


class FilePlugin(BasePlugin):
    """Searches files and folders using FileFind's index."""

    name = "files"
    keyword = GLOBAL_WILDCARD   # Runs on every search
    search_delay = 0.0          # The index is in memory, so no delay is needed

    def __init__(self, search_index, max_results: int = 30):
        self._index = search_index
        self._max_results = max_results

    def query(self, q: Query, token: CancellationToken) -> List[Result]:
        """Search the index, re-score each hit, and wrap it in a Result.

        The engine does retrieval; scoring is redone here with the shared
        matcher so file and application scores mean the same thing.
        """
        if q.is_empty:
            return []

        paths = self._index.search(q.search, CANDIDATE_LIMIT)
        access_counts = getattr(self._index, "access_counts", {})

        results = []
        for path in paths:
            # Cheap per item, but a large candidate list is still worth
            # abandoning if the user has already typed another character
            if token.cancelled:
                return []

            hit = matcher.match(q.search, path.name)
            if not hit:
                continue

            score = hit.score

            # Files the user opens often should surface sooner
            opens = access_counts.get(str(path), 0)
            score += min(opens * ACCESS_SCORE_WEIGHT, ACCESS_SCORE_MAX)

            results.append(
                Result(
                    title=path.name,
                    subtitle=str(path.parent),
                    score=score,
                    action=self._make_open_action(path),
                    icon="folder" if self._is_dir(path) else "file",
                    context={"path": path, "match": hit.indices},
                )
            )

        results.sort(key=lambda r: r.score, reverse=True)
        return results[: self._max_results]

    def _make_open_action(self, path: Path):
        """Build the Enter action that opens this path."""

        def _open() -> bool:
            # os.startfile works for both files and folders on Windows, and
            # builds no command string, so nothing in the name can be run
            os.startfile(str(path))

            # Feeds FileFind's usage-adaptive ranking on later searches
            record = getattr(self._index, "record_access", None)
            if record is not None:
                record(path)

            return True     # Hide the launcher after opening
        return _open

    @staticmethod
    def _is_dir(path: Path) -> bool:
        """Check whether a path is a folder, treating errors as 'not a folder'."""
        try:
            return path.is_dir()
        except OSError:
            return False

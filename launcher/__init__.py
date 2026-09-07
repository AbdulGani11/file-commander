"""Launcher core: query routing, plugin contract, and result model.

Wraps the FileFind search engine rather than replacing it.
"""

from .dispatcher import Dispatcher
from .handler import BasePlugin, CancellationToken, Plugin, QueryCancelled
from .models import GLOBAL_WILDCARD, Query, Result

__all__ = [
    "Dispatcher",
    "BasePlugin",
    "Plugin",
    "CancellationToken",
    "QueryCancelled",
    "Query",
    "Result",
    "GLOBAL_WILDCARD",
]

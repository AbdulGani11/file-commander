#!/usr/bin/env python3
"""
FileFind - File Search Engine

A Trie, an inverted word index and an exact-match dictionary over filenames,
persisted to SQLite and kept current by a filesystem watcher. Built for Windows.

This module is the engine only. The user interface is the Qt overlay in
`launcher/`, started by `run_launcher.py`; there is no terminal application and
nothing here prints beyond a few lines of indexing progress.
"""

import json
import os
import queue
import sqlite3
import threading
from collections import defaultdict
from pathlib import Path
from typing import List, Optional

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler as _FileSystemEventHandler
    WATCHDOG_AVAILABLE = True
except ImportError:
    WATCHDOG_AVAILABLE = False
    _FileSystemEventHandler = object  # Fallback base class when watchdog not installed

try:
    from rapidfuzz import process as rf_process, fuzz as rf_fuzz
    RAPIDFUZZ_AVAILABLE = True
except ImportError:
    RAPIDFUZZ_AVAILABLE = False
    rf_process = None
    rf_fuzz = None

# CONSTANTS - Centralized configuration for easy maintenance

# Directories to skip during indexing (improves performance and security).
# Matched per path segment, so a name at any depth prunes the whole subtree.
SKIP_DIRECTORIES = {
    # Operating system
    "system32",
    "windows",
    "programdata",
    "$recycle",
    "appdata",

    # Version control and editor state
    ".git",
    ".claude",
    ".idea",
    ".vs",

    # Python virtual environments ("site-packages" catches any env name)
    "venv",
    ".venv",
    "site-packages",

    # Dependency and build caches
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
}

# Search configuration constants
MIN_WORD_LENGTH = 2  # Minimum word length to index (skip short words like 'a', 'of')
MAX_FILENAME_SCORE_BONUS = 30  # Maximum bonus for shorter filenames in relevance scoring

# Relevance scoring weights (higher = more relevant)
SCORE_EXACT_MATCH = 100  # Query exactly matches filename
SCORE_STARTS_WITH = 80   # Filename starts with query (autocomplete-style)
SCORE_CONTAINS = 50      # Filename contains query somewhere

# Persistent index cache
CACHE_DB_PATH = Path.home() / ".filefind_cache.db"
CACHE_SCHEMA_VERSION = 2  # Bump when the schema OR what gets indexed changes

# Usage-adaptive scoring
HISTORY_PATH = Path.home() / ".filefind_history.json"
ACCESS_SCORE_WEIGHT = 5   # Points added per recorded open (e.g. opened 3 times = +15)
ACCESS_SCORE_MAX = 40     # Cap so a heavily-used file doesn't bury everything else

# Fuzzy matching (Strategy 5)
FUZZY_SCORE_CUTOFF = 75   # Minimum rapidfuzz WRatio score to accept a fuzzy match (0–100)
FUZZY_MIN_RESULTS = 5     # Only run fuzzy pass when strategies 1–4 return fewer than this

# Live update writer. Filesystem events are applied in batches under one commit,
# because a commit per event capped the writer near 1,000 events a second:
# extracting an archive or switching a branch left the index stale for seconds
# while the queue drained. The batch only fills when events are already waiting,
# so a single file change is still applied at once.
WRITER_BATCH_SIZE = 500      # Events applied per commit at most
WRITER_POLL_SECONDS = 1.0    # How long an idle writer waits before re-checking stop



# UTILITY CLASSES - Reusable components for common operations


class PathUtils:
    """Safe path operations with security validation and drive detection."""

    @staticmethod
    def get_drive_path(drive_letter: str) -> Path:
        """Convert drive letter to Path object (e.g., 'D' -> 'D:/')"""
        return Path(f"{drive_letter.upper()}:/")

    @staticmethod
    def get_available_drives() -> List[str]:
        """Return list of accessible drive letters (C, D, E, etc.)."""
        drives = []
        for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
            if PathUtils.get_drive_path(letter).exists():
                drives.append(letter)
        return drives

    @staticmethod
    def is_valid_folder(path: Path) -> bool:
        """Check if path exists and is actually a directory (not a file)"""
        return path.exists() and path.is_dir()

    @staticmethod
    def should_skip_directory(path: Path) -> bool:
        """Return True if directory should be skipped (system folders, node_modules, etc.)."""
        path_parts = [p.lower() for p in path.parts]
        return any(skip in path_parts for skip in SKIP_DIRECTORIES)

    @staticmethod
    def is_safe_filename(name: str) -> bool:
        """Validate filename blocks directory traversal and Windows reserved names."""
        # Check for empty or whitespace-only names
        if not name or not name.strip():
            return False

        # Directory traversal protection - these patterns can escape intended directories
        if ".." in name or "\\" in name:
            return False

        # Windows file system restrictions - these characters cause errors
        # '/' is included to block path traversal during rename (e.g. "sub/file")
        invalid_chars = '<>:"|?*/'
        if any(char in name for char in invalid_chars):
            return False

        # Windows reserved names (cannot be used as file/folder names)
        reserved_names = {
            "con", "prn", "aux", "nul",
            "com1", "com2", "com3", "com4", "com5", "com6", "com7", "com8", "com9",
            "lpt1", "lpt2", "lpt3", "lpt4", "lpt5", "lpt6", "lpt7", "lpt8", "lpt9",
        }
        base_name = name.split(".")[0].lower()
        if base_name in reserved_names:
            return False

        # Windows doesn't allow names ending with periods or spaces
        if name.endswith(".") or name.endswith(" "):
            return False

        # Windows filename length limit (255 characters)
        if len(name) > 255:
            return False

        return True

    @staticmethod
    def get_item_type(path: Path) -> str:
        """Get simple item type string: 'folder' or 'file'"""
        return "folder" if path.is_dir() else "file"




# FILE SYSTEM WATCHER


# FILE SYSTEM WATCHER

# FILE SYSTEM WATCHER - Real-time index delta updates


class IndexEventHandler(_FileSystemEventHandler):
    """Pushes filesystem events onto a queue for the dedicated writer thread.
    Callbacks return immediately — all index work happens off the watcher thread."""

    def __init__(self, event_queue: queue.Queue):
        self._queue = event_queue

    def on_created(self, event):
        self._queue.put(("insert", Path(event.src_path), None))

    def on_deleted(self, event):
        self._queue.put(("delete", Path(event.src_path), None))

    def on_moved(self, event):
        self._queue.put(("move", Path(event.src_path), Path(event.dest_path)))



# SEARCH ENGINE - Fast file indexing and retrieval system


class TrieNode:
    """Trie node storing children (char -> TrieNode) and files matching this prefix."""

    def __init__(self):
        self.children = {}  # Dictionary mapping characters to child nodes
        self.files = []  # Files that contain this prefix


class Trie:
    """Prefix tree for O(m) prefix matching where m = query length."""

    def __init__(self):
        self.root = TrieNode()

    def insert(self, word: str, file_path: Path):
        """Insert word into trie. Adds file_path to every prefix node for partial matching."""
        node = self.root
        for char in word.lower():
            if char not in node.children:
                node.children[char] = TrieNode()
            node = node.children[char]
            # Add file to this prefix - enables partial matching
            node.files.append(file_path)

    def search_prefix(self, prefix: str, max_results: int = 20) -> List[Path]:
        """Find unique files matching prefix. Returns up to max_results."""
        node = self.root
        for char in prefix.lower():
            if char not in node.children:
                return []  # Prefix not found
            node = node.children[char]

        # Remove duplicates while preserving order (dict.fromkeys trick)
        unique_files = list(dict.fromkeys(node.files))
        return unique_files[:max_results]


class FileMetadata:
    """Cached file info (path, name, suffix, is_dir) to avoid repeated Path operations."""

    def __init__(self, path: Path):
        self.path = path
        self.name = path.name
        self.suffix = path.suffix.lower()  # File extension for type filtering
        self.is_dir = path.is_dir()


class FileSearchIndex:
    """Multi-strategy search: exact match (O(1)), Trie prefix (O(m)), word index, substring."""

    def __init__(self):
        # Trie for fast prefix search (like autocomplete)
        self.trie = Trie()

        # Hash map for instant exact filename lookup
        self.exact_match = {}  # filename -> [FileMetadata]

        # Inverted index: word -> set of files containing that word
        # Enables searching for "intern the" to find "The Intern.mp4"
        self.word_index = defaultdict(set)

        # Track indexed files to avoid duplicates
        self.indexed_paths = set()

        # Statistics for user feedback
        self.total_items = 0

        # Usage-adaptive scoring: path string -> open count
        self.access_counts = FileSearchIndex._load_access_counts()

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        """Split text into searchable words on dots, underscores, and dashes."""
        return text.replace(".", " ").replace("_", " ").replace("-", " ").split()

    @staticmethod
    def _acronym(filename: str) -> Optional[str]:
        """Initials of a multi-word filename, ignoring the extension.

        "quarterly_business_review.docx" -> "qbr", so typing "qbr" finds it.
        """
        stem = filename.rsplit(".", 1)[0] if "." in filename else filename
        words = FileSearchIndex._tokenize(stem)

        if len(words) < 2:
            # A single word has no acronym worth storing
            return None
        return "".join(word[0] for word in words if word)

    def add_file(self, file_path: Path):
        """Index file/folder in Trie, exact_match, and word_index. Skips duplicates."""
        # Avoid duplicate indexing (important for performance)
        if str(file_path).lower() in self.indexed_paths:
            return

        try:
            metadata = FileMetadata(file_path)
            filename = metadata.name.lower()

            # 1. Add to trie for prefix search
            self.trie.insert(filename, file_path)

            # 2. Add to exact match lookup
            if filename not in self.exact_match:
                self.exact_match[filename] = []
            self.exact_match[filename].append(metadata)

            # 3. Add to word index for flexible search
            # Split filename into searchable words (handle dots, underscores, dashes)
            words = FileSearchIndex._tokenize(filename)
            for word in words:
                if len(word) > MIN_WORD_LENGTH:  # Skip very short words (the, of, a, etc.)
                    self.word_index[word].add(file_path)

            # 4. Add initials as a token; the Trie copy lets "qb" match too
            acronym = FileSearchIndex._acronym(filename)
            if acronym:
                self.word_index[acronym].add(file_path)
                self.trie.insert(acronym, file_path)

            # Track this file as indexed
            self.indexed_paths.add(str(file_path).lower())
            self.total_items += 1

        except (OSError, PermissionError):
            # Skip files we can't access (common in system directories)
            pass

    def remove_file(self, file_path: Path):
        """Remove file from exact_match, word_index, and indexed_paths.
        Trie does not support deletion; stale trie entries are filtered in search()."""
        path_key = str(file_path).lower()
        if path_key not in self.indexed_paths:
            return

        filename = file_path.name.lower()
        self.indexed_paths.discard(path_key)
        self.total_items = max(0, self.total_items - 1)

        if filename in self.exact_match:
            self.exact_match[filename] = [
                m for m in self.exact_match[filename]
                if str(m.path).lower() != path_key
            ]
            if not self.exact_match[filename]:
                del self.exact_match[filename]

        words = FileSearchIndex._tokenize(filename)
        acronym = FileSearchIndex._acronym(filename)
        evict = [w for w in words if len(w) > MIN_WORD_LENGTH]
        if acronym:
            evict.append(acronym)

        for word in evict:
            if word in self.word_index:
                self.word_index[word].discard(file_path)
                if not self.word_index[word]:
                    del self.word_index[word]

    @staticmethod
    def _load_access_counts() -> dict:
        """Load open-count history from JSON. Returns empty dict on any failure."""
        if HISTORY_PATH.exists():
            try:
                with open(HISTORY_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def record_access(self, file_path: Path):
        """Increment the open count for a file and persist to disk."""
        key = str(file_path)
        self.access_counts[key] = self.access_counts.get(key, 0) + 1
        try:
            with open(HISTORY_PATH, "w", encoding="utf-8") as f:
                json.dump(self.access_counts, f)
        except OSError:
            pass  # History loss is acceptable; never crash on a write failure

    def index_folder(self, folder_path: Path) -> int:
        """Recursively index all files/folders in directory. Returns count of items indexed."""
        items_added = 0

        if not PathUtils.is_valid_folder(folder_path):
            return items_added

        try:
            # rglob("*") recursively finds all files AND folders in subdirectories
            for item in folder_path.rglob("*"):
                # Skip symlinks and NTFS junctions to avoid traversing outside
                # the intended scope (e.g. OneDrive junctions, dev env mounts)
                if item.is_symlink():
                    continue

                # Check the item, not its parent, so the excluded folder
                # itself is skipped too
                if PathUtils.should_skip_directory(item):
                    continue

                # Index both files AND folders for comprehensive search
                self.add_file(item)  # Works for both files and directories
                items_added += 1

        except (OSError, PermissionError):
            # Skip inaccessible directories (network drives, system folders, etc.)
            pass

        return items_added

    def save_index(self, db_path: Path) -> bool:
        """Persist in-memory index to a single SQLite file on disk.

        Returns False if the cache could not be written. The cache is only a
        speed optimisation, so a failure here must not discard a completed
        index build.
        """
        try:
            conn = sqlite3.connect(db_path)
        except sqlite3.Error:
            return False

        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS files "
                "(path TEXT PRIMARY KEY, name TEXT NOT NULL, is_dir INTEGER NOT NULL)"
            )
            conn.execute("DELETE FROM schema_version")
            conn.execute("INSERT INTO schema_version VALUES (?)", (CACHE_SCHEMA_VERSION,))
            conn.execute("DELETE FROM files")
            rows = [
                (str(meta.path), meta.name, int(meta.is_dir))
                for meta_list in self.exact_match.values()
                for meta in meta_list
            ]
            conn.executemany("INSERT OR IGNORE INTO files VALUES (?, ?, ?)", rows)
            conn.commit()
            return True
        except (sqlite3.Error, OSError):
            return False
        finally:
            conn.close()

    def load_index(self, db_path: Path) -> bool:
        """Rebuild in-memory index from SQLite cache. No filesystem I/O — fast startup.
        Returns False if cache is missing, corrupt, or schema version mismatch."""
        if not db_path.exists():
            return False

        try:
            conn = sqlite3.connect(db_path)
        except sqlite3.Error:
            return False

        try:
            conn.execute("PRAGMA journal_mode=WAL")
            version_row = conn.execute(
                "SELECT version FROM schema_version"
            ).fetchone()
            if not version_row or version_row[0] != CACHE_SCHEMA_VERSION:
                return False
            rows = conn.execute(
                "SELECT path, name, is_dir FROM files"
            ).fetchall()
        except sqlite3.Error:
            # sqlite3.Error is the base class. Catching OperationalError alone
            # missed the DatabaseError raised by a corrupt file, so a damaged
            # cache crashed startup instead of triggering a rebuild.
            return False
        finally:
            conn.close()

        for path_str, name, is_dir in rows:
            file_path = Path(path_str)
            path_key = path_str.lower()
            if path_key in self.indexed_paths:
                continue
            # Bypass FileMetadata.__init__ to avoid is_dir() filesystem calls on load
            meta = FileMetadata.__new__(FileMetadata)
            meta.path = file_path
            meta.name = name
            meta.suffix = file_path.suffix.lower()
            meta.is_dir = bool(is_dir)
            filename = name.lower()
            self.trie.insert(filename, file_path)
            if filename not in self.exact_match:
                self.exact_match[filename] = []
            self.exact_match[filename].append(meta)
            words = FileSearchIndex._tokenize(filename)
            for word in words:
                if len(word) > MIN_WORD_LENGTH:
                    self.word_index[word].add(file_path)
            acronym = FileSearchIndex._acronym(filename)
            if acronym:
                self.word_index[acronym].add(file_path)
                self.trie.insert(acronym, file_path)
            self.indexed_paths.add(path_key)
            self.total_items += 1

        return True

    def search(self, query: str, max_results: int = 20) -> List[Path]:
        """Search using 4 strategies: exact, prefix, word, substring. Returns top results by relevance."""
        if not query.strip():
            return []

        query = query.lower().strip()
        results = set()  # Use set to automatically handle duplicates

        # Strategy 1: Exact filename match (fastest possible)
        if query in self.exact_match:
            for metadata in self.exact_match[query]:
                results.add(metadata.path)

        # Strategy 2: Prefix search using Trie (autocomplete-style)
        prefix_results = self.trie.search_prefix(query, max_results * 2)
        results.update(prefix_results)

        # Strategy 3: Word-based search (handles different word orders)
        # Splits "the intern" into ["the", "intern"] for flexible matching
        query_words = FileSearchIndex._tokenize(query)
        for word in query_words:
            if word in self.word_index:
                results.update(self.word_index[word])

        # Strategy 4: Substring search (broadest, slowest)
        # Only use if we don't have enough results yet
        if len(results) < max_results:
            for filename, metadata_list in self.exact_match.items():
                if query in filename:
                    for metadata in metadata_list:
                        results.add(metadata.path)

        # Strategy 5: Fuzzy fallback — only when the first four strategies are sparse
        if RAPIDFUZZ_AVAILABLE and len(results) < FUZZY_MIN_RESULTS:
            candidates = list(self.exact_match.keys())
            matches = rf_process.extract(
                query, candidates, scorer=rf_fuzz.WRatio,
                limit=10, score_cutoff=FUZZY_SCORE_CUTOFF,
            )
            for match_name, *_ in matches:
                for meta in self.exact_match[match_name]:
                    results.add(meta.path)

        # Filter stale trie entries — files removed by the watcher since last add
        results = {p for p in results if str(p).lower() in self.indexed_paths}

        # Sort results by relevance and return top matches
        return self._sort_by_relevance(list(results), query)[:max_results]

    def _sort_by_relevance(self, results: List[Path], query: str) -> List[Path]:
        """Sort by score: exact match > starts with > contains > shorter names > common dirs."""

        def score(path: Path) -> int:
            filename = path.name.lower()
            relevance_score = 0

            # Exact match gets highest priority
            if query == filename:
                relevance_score += SCORE_EXACT_MATCH
            # Starts with query (like autocomplete)
            elif filename.startswith(query):
                relevance_score += SCORE_STARTS_WITH
            # Contains query somewhere
            elif query in filename:
                relevance_score += SCORE_CONTAINS

            # Shorter filenames often more relevant (less clutter)
            relevance_score += max(0, MAX_FILENAME_SCORE_BONUS - len(filename))

            # Bonus for files in commonly-accessed directories
            parent_name = path.parent.name.lower()
            if any(
                common in parent_name
                for common in ["documents", "desktop", "downloads"]
            ):
                relevance_score += 10

            # Usage-adaptive bonus: files the user opens frequently surface higher
            opens = self.access_counts.get(str(path), 0)
            relevance_score += min(opens * ACCESS_SCORE_WEIGHT, ACCESS_SCORE_MAX)

            return relevance_score

        return sorted(results, key=score, reverse=True)



# INDEX LIFECYCLE - Builds, caches and keeps the index current


class FileCommander:
    """Owns the index: builds or loads it, then keeps it current.

    Progress goes to stdout with plain print. This used to be a Rich terminal
    application; the launcher is the only front end now, and its console output
    is a handful of startup lines, which is not worth a dependency.
    """

    def __init__(self):
        self.search_index = FileSearchIndex()
        self._index_built = False
        self._observer = None
        self._writer_thread = None
        self._event_queue: queue.Queue = queue.Queue()
        self._stop_event = threading.Event()

    def load_or_build_index(self):
        """Load from SQLite cache if available, otherwise run full build and save cache."""
        if self.search_index.load_index(CACHE_DB_PATH):
            print(
                "Index loaded from cache (%s items)"
                % format(self.search_index.total_items, ",")
            )
            self._index_built = True
            return
        # No cache — full indexing pass
        self._build_fresh_index()
        print("Saving index to cache for next startup...")
        if self.search_index.save_index(CACHE_DB_PATH):
            print("Cache saved; next startup will be fast")
        else:
            print(
                "Warning: could not write the cache. Searching still works, "
                "but the next startup will re-index"
            )

    def _build_fresh_index(self):
        """Full filesystem indexing pass (C: user folders + other drives complete)."""
        print("Indexing. C: is limited to user folders, other drives are complete.")

        c_drive_folders = [
            Path.home() / "Downloads",
            Path.home() / "Documents",
            Path.home() / "Desktop",
            Path.home() / "Videos",
            Path.home() / "Pictures",
            Path.home() / "Pictures" / "Samsung Flow",
        ]

        for folder in c_drive_folders:
            if PathUtils.is_valid_folder(folder):
                items_added = self.search_index.index_folder(folder)
                if items_added > 0:
                    print("   %s: %d items" % (folder.name, items_added))

        drives = PathUtils.get_available_drives()
        other_drives = [drive for drive in drives if drive.upper() != "C"]

        for drive in other_drives:
            items_added = self.search_index.index_folder(
                PathUtils.get_drive_path(drive)
            )
            if items_added > 0:
                print("   %s: drive: %d items" % (drive, items_added))
            else:
                print("   %s: drive: no accessible items" % drive)

        print("Indexing complete: %s items" % format(self.search_index.total_items, ","))
        self._index_built = True

    def _start_watcher(self):
        """Start watchdog observer + writer thread for real-time index delta updates."""
        if not WATCHDOG_AVAILABLE:
            print(
                "Warning: watchdog is not installed, so live index updates are "
                "disabled. Run: pip install watchdog"
            )
            return

        watch_paths = [
            Path.home() / "Downloads",
            Path.home() / "Documents",
            Path.home() / "Desktop",
            Path.home() / "Videos",
            Path.home() / "Pictures",
        ]
        for drive in PathUtils.get_available_drives():
            if drive.upper() != "C":
                watch_paths.append(PathUtils.get_drive_path(drive))

        handler = IndexEventHandler(self._event_queue)
        self._observer = Observer()
        for path in watch_paths:
            if PathUtils.is_valid_folder(path):
                self._observer.schedule(handler, str(path), recursive=True)

        self._observer.start()

        self._writer_thread = threading.Thread(
            target=FileCommander._writer_loop,
            args=(
                self.search_index,
                CACHE_DB_PATH,
                self._event_queue,
                self._stop_event,
            ),
            daemon=True,
            name="filefind-writer",
        )
        self._writer_thread.start()

    def _stop_watcher(self):
        """Stop filesystem watcher and writer thread gracefully."""
        self._stop_event.set()
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=3)
        if self._writer_thread:
            self._writer_thread.join(timeout=3)

    @staticmethod
    def _collect_batch(event_queue: queue.Queue) -> list:
        """Take one event, then everything already waiting behind it.

        Returns as soon as the queue runs dry, so a single file change is still
        applied immediately. Under a burst the queue is never dry and this fills
        to WRITER_BATCH_SIZE, which is what makes one commit cover many events.
        """
        try:
            batch = [event_queue.get(timeout=WRITER_POLL_SECONDS)]
        except queue.Empty:
            return []

        while len(batch) < WRITER_BATCH_SIZE:
            try:
                batch.append(event_queue.get_nowait())
            except queue.Empty:
                break
        return batch

    @staticmethod
    def _apply_event(search_index: FileSearchIndex, conn, op, src, dest):
        """Apply one filesystem event to the index and the open transaction.

        Deliberately does not commit: the caller commits once per batch.
        """
        if op == "insert":
            if not PathUtils.should_skip_directory(src) and src.exists():
                search_index.add_file(src)
                conn.execute(
                    "INSERT OR REPLACE INTO files VALUES (?, ?, ?)",
                    (str(src), src.name, int(src.is_dir())),
                )

        elif op == "delete":
            search_index.remove_file(src)
            conn.execute(
                "DELETE FROM files WHERE LOWER(path) = ?",
                (str(src).lower(),),
            )

        elif op == "move":
            if dest is not None and dest.is_dir():
                # Directory move: remove all stale entries, re-index destination
                src_prefix = str(src).lower() + os.sep.lower()
                stale = [
                    p for p in list(search_index.indexed_paths)
                    if p.startswith(src_prefix) or p == str(src).lower()
                ]
                for stale_lower in stale:
                    search_index.remove_file(Path(stale_lower))
                    conn.execute(
                        "DELETE FROM files WHERE LOWER(path) = ?",
                        (stale_lower,),
                    )
                if dest.exists():
                    search_index.index_folder(dest)
                    for meta_list in search_index.exact_match.values():
                        for meta in meta_list:
                            if str(meta.path).lower().startswith(str(dest).lower()):
                                conn.execute(
                                    "INSERT OR REPLACE INTO files VALUES (?, ?, ?)",
                                    (str(meta.path), meta.name, int(meta.is_dir)),
                                )
            else:
                # File move: swap old path for new
                search_index.remove_file(src)
                conn.execute(
                    "DELETE FROM files WHERE LOWER(path) = ?",
                    (str(src).lower(),),
                )
                if dest is not None and dest.exists():
                    search_index.add_file(dest)
                    conn.execute(
                        "INSERT OR REPLACE INTO files VALUES (?, ?, ?)",
                        (str(dest), dest.name, int(dest.is_dir())),
                    )

    @staticmethod
    def _writer_loop(
        search_index: FileSearchIndex,
        db_path: Path,
        event_queue: queue.Queue,
        stop_event: threading.Event,
    ):
        """Drain the event queue and apply delta adds/removes to index + SQLite.

        Events are applied in batches under a single commit. Committing per
        event held the writer to about 1,000 events a second, so a burst of
        15,000 files backed the queue up 8,700 deep and left the index stale for
        ten seconds while it caught up.

        This loop must never crash: one bad event is skipped, and a failed
        commit costs the cache a batch rather than the thread.
        """
        try:
            conn = sqlite3.connect(db_path)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
        except sqlite3.Error:
            # Without a connection there is nothing to sync; leave the in-memory
            # index alone rather than killing the thread with an exception
            return

        while not stop_event.is_set():
            batch = FileCommander._collect_batch(event_queue)
            if not batch:
                continue

            for op, src, dest in batch:
                try:
                    FileCommander._apply_event(search_index, conn, op, src, dest)
                except Exception:
                    pass  # Skip this event, keep the rest of the batch

            try:
                conn.commit()
            except sqlite3.Error:
                # The in-memory index is already correct; only the cache is
                # behind, and a rebuild fixes that. Never worth dying for.
                pass

        conn.close()

        conn.close()

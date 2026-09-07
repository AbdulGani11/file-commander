#!/usr/bin/env python3
"""
FileFind - File Search Tool

Search using Trie data structures and multi-strategy algorithms.
Built for Windows.
"""

import os
import json
import time
import sqlite3
import queue
import threading
from collections import defaultdict
from pathlib import Path
from typing import Any, List, Optional, Tuple

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.prompt import Prompt, Confirm
from rich import box

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

# Initialize Rich console for beautiful terminal output
console = Console()


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
DEFAULT_SEARCH_RESULTS = 50  # Default number of search results to return
DISPLAY_RESULTS_LIMIT = 20  # Maximum results to display in table

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

    @staticmethod
    def get_item_emoji_type(path: Path) -> str:
        """Get emoji item type string: '📁 Folder' or '📄 File'"""
        return "📁 Folder" if path.is_dir() else "📄 File"


class UIUtils:
    """Terminal UI helpers for tables, menus, messages, and user input."""

    @staticmethod
    def create_results_table(title: str, columns: List[Tuple[str, str, int]]) -> Table:
        """Create styled Rich table with title and columns (name, style, width). Width 0 = auto-size."""
        table = Table(title=title, show_lines=True, header_style="bold cyan")
        for name, style, width in columns:
            if width:
                table.add_column(name, style=style, width=width)
            else:
                table.add_column(name, style=style)
        return table

    @staticmethod
    def get_user_choice(prompt: str, choices: List[str], default: Optional[str] = None) -> str:
        """Get validated user input with automatic retry on invalid choices"""
        if default:
            return Prompt.ask(prompt, choices=choices, default=default)
        else:
            return Prompt.ask(prompt, choices=choices)

    @staticmethod
    def show_options_and_choose(options: List[str], prompt: str) -> str:
        """Display numbered options and return validated user choice."""
        for option in options:
            console.print(option)

        choices = [str(i) for i in range(1, len(options) + 1)]
        return UIUtils.get_user_choice(prompt, choices)

    @staticmethod
    def print_success(message: str):
        """Print success message with consistent formatting"""
        console.print(f"[bold green]✅ SUCCESS:[/] {message}")

    @staticmethod
    def print_error(message: str):
        """Print error message with consistent formatting"""
        console.print(f"[bold red]❌ ERROR:[/] {message}")

    @staticmethod
    def print_warning(message: str):
        """Print warning message with consistent formatting"""
        console.print(f"[bold yellow]⚠️ WARNING:[/] {message}")

    @staticmethod
    def print_info(message: str):
        """Print info message with consistent formatting"""
        console.print(f"[bold cyan]ℹ️ INFO:[/] {message}")

    @staticmethod
    def print_separator():
        """Print standard visual separator line"""
        console.print("─" * 60)

    @staticmethod
    def print_section_break():
        """Print section break line for major divisions"""
        console.print("═" * 60)

    @staticmethod
    def print_section_header(title: str):
        """Print formatted section header with consistent styling"""
        console.print()
        console.print(Panel(title, style="bold green"))
        UIUtils.print_separator()

    @staticmethod
    def validate_filename_or_show_error(name: str) -> bool:
        """Validate filename and print error if invalid. Returns True if valid."""
        if not PathUtils.is_safe_filename(name):
            UIUtils.print_error(
                "Invalid name. Avoid empty names, '..' patterns, and special characters"
            )
            return False
        return True

    @staticmethod
    def safe_execute(operation_name: str, func, *args, **kwargs) -> Any:
        """Execute function with error handling. Catches file system errors."""
        try:
            return func(*args, **kwargs)
        except PermissionError:
            UIUtils.print_error(f"Permission denied: {operation_name}")
        except FileNotFoundError:
            UIUtils.print_error(f"File not found: {operation_name}")
        except FileExistsError:
            UIUtils.print_error(f"File already exists: {operation_name}")
        except OSError as e:
            UIUtils.print_error(f"{operation_name} - {e}")
        return None



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

    def suggest_correction(self, query: str) -> Optional[str]:
        """Return the closest filename in the index to query, or None if no good match."""
        if not RAPIDFUZZ_AVAILABLE or not self.exact_match:
            return None
        candidates = list(self.exact_match.keys())
        match = rf_process.extractOne(
            query, candidates, scorer=rf_fuzz.WRatio, score_cutoff=60
        )
        return match[0] if match else None

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



# MAIN APPLICATION - Interactive file management interface


class FileCommander:
    """Interactive file search application with Trie-based indexing and multi-strategy search."""

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
            UIUtils.print_success(
                f"Index loaded from cache "
                f"({self.search_index.total_items:,} items, <1s startup)"
            )
            self._index_built = True
            return
        # No cache — full indexing pass
        self._build_fresh_index()
        console.print("[dim]💾 Saving index to cache for next startup...[/dim]")
        if self.search_index.save_index(CACHE_DB_PATH):
            UIUtils.print_success("Cache saved — next startup will be instant")
        else:
            UIUtils.print_warning(
                "Could not write the cache; searching still works, but the "
                "next startup will re-index"
            )

    def _build_fresh_index(self):
        """Full filesystem indexing pass (C: user folders + other drives complete)."""
        console.print("[dim]📄 Indexing files using smart drive strategy...[/dim]")

        c_drive_folders = [
            Path.home() / "Downloads",
            Path.home() / "Documents",
            Path.home() / "Desktop",
            Path.home() / "Videos",
            Path.home() / "Pictures",
            Path.home() / "Pictures" / "Samsung Flow",
        ]

        console.print("[dim]   🎯 C: drive - Indexing user folders only...[/dim]")
        for folder in c_drive_folders:
            if PathUtils.is_valid_folder(folder):
                items_added = self.search_index.index_folder(folder)
                if items_added > 0:
                    console.print(
                        f"[dim]      ✅ {folder.name}: {items_added} items[/dim]"
                    )

        drives = PathUtils.get_available_drives()
        other_drives = [drive for drive in drives if drive.upper() != "C"]

        if other_drives:
            console.print(
                f"[dim]   💾 Other drives ({', '.join(other_drives)}) - Complete indexing...[/dim]"
            )
            for drive in other_drives:
                drive_path = PathUtils.get_drive_path(drive)
                console.print(
                    f"[dim]      📂 Indexing {drive}: drive completely...[/dim]"
                )
                items_added = self.search_index.index_folder(drive_path)
                if items_added > 0:
                    console.print(
                        f"[dim]      ✅ {drive}: drive: {items_added} items indexed[/dim]"
                    )
                else:
                    console.print(
                        f"[dim]      ⚠️ {drive}: drive: No accessible items[/dim]"
                    )
        else:
            console.print("[dim]   ℹ️ No additional drives found besides C:[/dim]")

        UIUtils.print_success("Indexing complete")
        self._index_built = True

    def _start_watcher(self):
        """Start watchdog observer + writer thread for real-time index delta updates."""
        if not WATCHDOG_AVAILABLE:
            UIUtils.print_warning(
                "watchdog not installed — live index updates disabled. "
                "Run: pip install watchdog"
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
    def _writer_loop(
        search_index: FileSearchIndex,
        db_path: Path,
        event_queue: queue.Queue,
        stop_event: threading.Event,
    ):
        """Drain the event queue and apply delta adds/removes to index + SQLite.
        This loop must never crash — all exceptions are swallowed per-event."""
        try:
            conn = sqlite3.connect(db_path)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
        except sqlite3.Error:
            # Without a connection there is nothing to sync; leave the in-memory
            # index alone rather than killing the thread with an exception
            return

        while not stop_event.is_set():
            try:
                op, src, dest = event_queue.get(timeout=1.0)
            except queue.Empty:
                continue

            try:
                if op == "insert":
                    if not PathUtils.should_skip_directory(src) and src.exists():
                        search_index.add_file(src)
                        conn.execute(
                            "INSERT OR REPLACE INTO files VALUES (?, ?, ?)",
                            (str(src), src.name, int(src.is_dir())),
                        )
                        conn.commit()

                elif op == "delete":
                    search_index.remove_file(src)
                    conn.execute(
                        "DELETE FROM files WHERE LOWER(path) = ?",
                        (str(src).lower(),),
                    )
                    conn.commit()

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
                                    if str(meta.path).lower().startswith(
                                        str(dest).lower()
                                    ):
                                        conn.execute(
                                            "INSERT OR REPLACE INTO files VALUES (?, ?, ?)",
                                            (str(meta.path), meta.name, int(meta.is_dir)),
                                        )
                        conn.commit()
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
                        conn.commit()

            except Exception:
                pass  # Writer loop must never crash; stale entries filtered in search()

        conn.close()

    def refresh_index(self):
        """Wipe cache, stop watcher, rebuild index from scratch, restart watcher."""
        UIUtils.print_section_header("🔄 Rebuilding Index")
        self._stop_watcher()
        self.search_index = FileSearchIndex()
        self._index_built = False
        self._stop_event = threading.Event()
        self._event_queue = queue.Queue()
        if CACHE_DB_PATH.exists():
            CACHE_DB_PATH.unlink()
        self.load_or_build_index()
        self._start_watcher()

    def show_main_menu(self):
        """Display the main application menu with available operations."""
        console.clear()

        # Centered application header with gradient-style colors
        title = Text()
        title.append("⚡ ", style="bold yellow")
        title.append("FILE COMMANDER", style="bold bright_cyan")

        subtitle = Text("High-Performance File Search Engine", style="dim white")

        # Create header panel with rounded borders
        header_content = Text.assemble(
            title, "\n", subtitle
        )
        header_content.justify = "center"

        console.print()
        console.print(
            Panel(
                header_content,
                box=box.ROUNDED,
                style="cyan",
                padding=(1, 4),
            ),
            justify="center",
        )
        console.print()

        # Main menu options
        options = [
            ("1", "⚡", "Search", "Find and manage files"),
            ("2", "📊", "Statistics", "View search index status"),
            ("3", "🔄", "Refresh Index", "Rebuild index from scratch"),
            ("0", "❌", "Exit", "Close application"),
        ]

        # Create styled table with rounded box
        table = Table(
            box=box.ROUNDED,
            show_header=True,
            header_style="bold bright_cyan",
            border_style="dim cyan",
            padding=(0, 1),
        )

        table.add_column("", style="bold yellow", width=3, justify="center")
        table.add_column("", width=3, justify="center")
        table.add_column("Action", style="bold white", min_width=20)
        table.add_column("Description", style="dim", min_width=25)

        for key, icon, action, desc in options:
            if key == "0":
                table.add_row(
                    f"[red]{key}[/red]",
                    icon,
                    f"[red]{action}[/red]",
                    f"[dim red]{desc}[/dim red]",
                )
            else:
                table.add_row(key, icon, action, desc)

        # Use a grid to center the table robustly
        grid = Table.grid(expand=True)
        grid.add_column(justify="center")
        grid.add_row(table)
        console.print(grid)
        console.print()

    def search_files(self):
        """Index drives (once), then continuous search loop. Actions: open, rename, search again."""
        UIUtils.print_section_header("⚡ Search & Manage Files/Folders")

        # Load from cache or build fresh index on first use
        if not self._index_built:
            self.load_or_build_index()
            self._start_watcher()
        else:
            UIUtils.print_info("Using cached index (instant search ready)")

        UIUtils.print_separator()

        # Continuous search loop - no re-indexing needed
        while True:
            search_term = Prompt.ask("⚡ What are you looking for?")
            if not search_term.strip():
                UIUtils.print_error("Please enter a search term")
                continue  # Ask again without breaking the loop

            UIUtils.print_info(f"Searching for '{search_term}'...")

            # Perform search with performance tracking
            start_time = time.time()
            results = self.search_index.search(search_term, DEFAULT_SEARCH_RESULTS)
            search_time = time.time() - start_time

            if results:
                UIUtils.print_success(
                    f"Found {len(results)} results in {search_time:.3f} seconds"
                )
                UIUtils.print_section_break()
                self._display_search_results(results, search_term)

                # Handle actions and check if user wants to continue
                if not self._handle_search_actions(results):
                    break  # Exit to main menu if user chose "Back to menu"
            else:
                UIUtils.print_section_break()
                UIUtils.print_warning(f"No items found for '{search_term}'")
                suggestion = self.search_index.suggest_correction(search_term)
                if suggestion:
                    UIUtils.print_info(f"Did you mean: [bold]{suggestion}[/bold]?")
                UIUtils.print_section_break()

                # Ask if user wants to continue searching (only when no results)
                UIUtils.print_separator()
                if not Confirm.ask(
                    "[bold cyan]🔍 Do you want to search for something else?[/bold cyan]",
                    default=False,
                ):
                    console.print("[dim]👍 Returning to main menu[/dim]")
                    break  # Exit the search loop and return to main menu

            UIUtils.print_separator()  # Visual separator for next search

    def _display_search_results(self, results: List[Path], search_term: str):
        """Display search results in a formatted table with file type indicators."""
        UIUtils.print_separator()

        table = UIUtils.create_results_table(
            f"🔍 Results for '{search_term}'",
            [
                ("#", "white", 3),
                ("Name", "green", 0),
                ("Type", "white", 8),
                ("Location", "blue", 0),
            ],
        )

        # Show first results to avoid overwhelming the user
        for i, item in enumerate(results[:DISPLAY_RESULTS_LIMIT], 1):
            item_type = PathUtils.get_item_emoji_type(item)
            table.add_row(str(i), item.name, item_type, str(item.parent))

        console.print(table)

        # Indicate if there are more results
        if len(results) > DISPLAY_RESULTS_LIMIT:
            console.print(
                f"[dim]... and {len(results) - DISPLAY_RESULTS_LIMIT} more results (showing first {DISPLAY_RESULTS_LIMIT})[/dim]"
            )

        UIUtils.print_separator()

    def _handle_search_actions(self, results: List[Path]) -> bool:
        """Show action menu. Returns True to continue searching, False to exit to main menu."""
        actions = [
            "1. 📂 Open item",
            "2. ✏️ Rename item",
            "3. 🔍 Search again",
            "4. 🔙 Back to menu",
        ]

        action = UIUtils.show_options_and_choose(actions, "Choose action")

        if action in ["1", "2"]:
            # Get user selection for the action
            if len(results) == 1:
                selected = results[0]
            else:
                choice = UIUtils.get_user_choice(
                    "Enter number",
                    [str(i) for i in range(1, min(len(results), DISPLAY_RESULTS_LIMIT) + 1)],
                )
                selected = results[int(choice) - 1]

            # Perform the selected action
            if action == "1":
                self._open_item(selected)
            else:
                self._rename_item(selected)

            return True  # Continue searching after open/rename
        elif action == "3":
            return True  # Continue search loop (no re-indexing!)
        else:
            return False  # Back to main menu

    def _open_item(self, item_path: Path):
        """Open file/folder with os.startfile (safe, no shell injection)."""

        def open_operation():
            # os.startfile works for both files and folders on Windows
            os.startfile(str(item_path))
            item_type = PathUtils.get_item_type(item_path)
            UIUtils.print_success(f"Opened {item_type}: {item_path.name}")
            self.search_index.record_access(item_path)

        UIUtils.safe_execute("opening item", open_operation)

    def _rename_item(self, item_path: Path):
        """Rename file/folder with validation. Offers undo after successful rename."""
        UIUtils.print_section_break()
        console.print(Panel(f"✏️ Rename: {item_path.name}", style="bold cyan"))
        UIUtils.print_section_break()

        new_name = Prompt.ask("📝 Enter new name", default=item_path.name)

        if new_name == item_path.name:
            UIUtils.print_warning("Name unchanged")
            return

        # Security validation
        if not UIUtils.validate_filename_or_show_error(new_name):
            return

        # Store original info for potential undo
        original_path = item_path
        original_name = item_path.name
        new_path = item_path.parent / new_name

        def rename_operation():
            try:
                original_path.rename(new_path)
                item_type = PathUtils.get_item_type(new_path)
                UIUtils.print_success(f"Renamed {item_type} to: {new_name}")
                return True
            except FileExistsError:
                UIUtils.print_error(f"Name already exists: {new_name}")
                return False

        # Guard: ensure the resolved new path stays in the same directory.
        # Catches any edge case where a name could slip out of the parent folder.
        if new_path.parent.resolve() != item_path.parent.resolve():
            UIUtils.print_error(
                "Rename cannot move a file to a different directory. "
                "Use a plain name without slashes."
            )
            return

        # Perform rename operation
        rename_successful = UIUtils.safe_execute("renaming item", rename_operation)

        # If rename was successful, offer immediate undo option
        if rename_successful:
            UIUtils.print_separator()
            if Confirm.ask(
                "[bold cyan]🔄 Do you want to undo this rename?[/bold cyan]",
                default=False,
            ):

                def undo_operation():
                    new_path.rename(original_path)
                    item_type = PathUtils.get_item_type(original_path)
                    UIUtils.print_success(f"Restored original name: {original_name}")

                UIUtils.safe_execute("undoing rename", undo_operation)
            UIUtils.print_section_break()

    def show_search_statistics(self):
        """Display current search index statistics for user information."""
        UIUtils.print_section_header("📊 Search Statistics")

        table = UIUtils.create_results_table(
            "⚡ Search System Status",
            [("Metric", "cyan", 20), ("Value", "green", 20), ("Details", "dim", 40)],
        )

        # Cache file status
        cache_exists = CACHE_DB_PATH.exists()
        if cache_exists:
            cache_size_kb = CACHE_DB_PATH.stat().st_size // 1024
            cache_value = f"{cache_size_kb:,} KB"
            cache_detail = str(CACHE_DB_PATH)
        else:
            cache_value = "Not found"
            cache_detail = "Will be created on first search"

        watcher_status = (
            "✅ Running" if (self._observer and self._observer.is_alive())
            else ("⚠️ Not available" if not WATCHDOG_AVAILABLE else "⏸ Not started")
        )

        table.add_row("Status", "✅ Ready", "Optimized for instant search")
        table.add_row(
            "Items Indexed",
            f"{self.search_index.total_items:,}",
            "Total files and folders in search index",
        )
        table.add_row("Search Speed", "< 1ms", "Microsecond-level performance")
        table.add_row("Cache File", cache_value, cache_detail)
        table.add_row("Live Watcher", watcher_status, "Real-time index delta updates")

        console.print(table)
        UIUtils.print_section_break()

    def run_interactive(self):
        """Main application loop. Shows menu and runs search or statistics based on user choice."""
        while True:
            try:
                self.show_main_menu()

                choice = UIUtils.get_user_choice(
                    "Select option", ["0", "1", "2", "3"]
                )

                if choice == "0":
                    UIUtils.print_section_break()
                    console.print(
                        "[bold yellow]👋 GOODBYE![/] Thank you for using File Commander"
                    )
                    UIUtils.print_section_break()
                    self._stop_watcher()
                    break
                elif choice == "1":
                    self.search_files()
                elif choice == "2":
                    self.show_search_statistics()
                elif choice == "3":
                    self.refresh_index()

                # Pause before returning to menu (better UX)
                if choice != "0":
                    UIUtils.print_separator()
                    Prompt.ask(
                        "[dim]Press Enter to return to main menu[/dim]", default=""
                    )
                    UIUtils.print_separator()

            except KeyboardInterrupt:
                # Graceful handling of Ctrl+C
                UIUtils.print_section_break()
                console.print("[bold yellow]👋 GOODBYE![/] Interrupted by user")
                UIUtils.print_section_break()
                self._stop_watcher()
                break
            except Exception as e:
                # Unexpected error handling
                UIUtils.print_section_break()
                UIUtils.print_error(f"Unexpected error: {e}")
                console.print("[dim]Please try again or restart the application.[/dim]")
                UIUtils.print_section_break()



# APPLICATION ENTRY POINT

if __name__ == "__main__":
    # The floating overlay now lives in launcher/ui and is started by
    # run_launcher.py. This file is the terminal application and the search
    # engine behind it.
    commander = FileCommander()
    commander.run_interactive()

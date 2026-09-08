"""
Application Search - Finds installed programs so they can be launched

FileFind's file index deliberately skips 'programdata' and 'appdata', which is
correct for finding documents but hides every installed application, because
that is exactly where Windows keeps its Start Menu shortcuts. This plugin
indexes applications separately, from the four places Windows registers them.

Three of those sources are cheap and read directly. The fourth, Store apps, has
to be asked for through PowerShell and costs over a second, so it is cached and
refreshed on a background thread rather than delaying startup.
"""

import json
import os
import subprocess
import threading
import winreg
from pathlib import Path
from typing import Dict, List, Optional

from .. import matcher
from ..handler import BasePlugin, CancellationToken
from ..models import GLOBAL_WILDCARD, Query, Result


# CONSTANTS - Centralized configuration for easy maintenance

# Shortcut file types found in the Start Menu.
# .lnk is a normal program shortcut, .url is an internet shortcut.
SHORTCUT_SUFFIXES = {".lnk", ".url"}

# Executable types worth indexing from folders on the PATH
EXECUTABLE_SUFFIXES = {".exe", ".bat", ".cmd"}

# Where Windows records programs that registered themselves at install time
APP_PATHS_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"

# PATH folders holding dependency tooling rather than launchable programs.
# Deliberately narrower than FileFind's SKIP_DIRECTORIES, which also excludes
# system32 -- correct for files, wrong for apps, since cmd, calc, taskmgr and
# regedit all live there.
SKIP_PATH_DIRS = {
    "venv", ".venv", "site-packages", "node_modules",
    ".tox", ".nox", "__pycache__",
}

# Match quality comes from the shared matcher, so applications and files are
# scored on one scale. These two values position applications on that scale.

# Applications outrank files of equal match quality: typing "chrome" almost
# always means "launch Chrome", not "find a file named chrome".
APP_SCORE_BONUS = 100

# Command line utilities on the PATH are rarely the intended target of a search
# box, so they rank below properly installed applications.
PATH_SCORE_PENALTY = 150

# SOURCE 4 - Store applications.
#
# Store apps have no ordinary executable on disk, so none of the three sources
# above can see them: Calculator, Clock, Photos, Paint and Terminal are all
# missing without this. Windows will list them, but only through PowerShell.
UWP_QUERY = "Get-StartApps | ConvertTo-Json -Compress"

# Measured at 1.2 seconds on the development machine, against 0.63s for the
# launcher's entire startup. Paying that on every launch to find apps that
# change a few times a year is a bad trade, so the answer is cached beside the
# engine's own dotfiles and refreshed in the background.
UWP_CACHE_PATH = Path.home() / ".filefind_apps.json"
UWP_TIMEOUT = 25          # seconds; a hung PowerShell must not leak a thread

# Store entries are launched through the shell's applications folder rather than
# by path, since there is no path to open.
UWP_SHELL_PREFIX = "shell:AppsFolder\\"


class AppEntry:
    """One installed application: its display name and how to launch it."""

    def __init__(self, name: str, target: Path, source: str):
        self.name = name                  # Shown as the result title
        self.target = target              # What gets opened
        self.source = source              # Which of the four sources found it
        self.lowered = name.lower()       # Cached to avoid lowering on every keystroke


class AppPlugin(BasePlugin):
    """Searches installed applications and launches the selected one."""

    name = "apps"
    keyword = GLOBAL_WILDCARD    # Runs on every search, alongside file search
    search_delay = 0.0           # The list is built once and held in memory

    def __init__(self, refresh_store: bool = True):
        self._apps: List[AppEntry] = []
        self._refresh_store = refresh_store
        self._store_thread: Optional[threading.Thread] = None

    def init(self) -> None:
        """Build the application list once, at registration."""
        self.reload()
        if self._refresh_store:
            self.refresh_store_apps_async()

    def reload(self) -> int:
        """Rescan all sources. Returns the number of applications found.

        Store apps come from the cache here, never from PowerShell, so this
        stays fast enough to run during startup.
        """
        found: Dict[str, AppEntry] = {}

        # Later sources must not overwrite earlier ones, so the ordering here is
        # the priority order: Start Menu entries have the friendliest names.
        for entry in self._start_menu_apps():
            found.setdefault(entry.lowered, entry)
        for entry in self._registered_apps():
            found.setdefault(entry.lowered, entry)
        for entry in self._store_apps(self._read_store_cache()):
            found.setdefault(entry.lowered, entry)
        for entry in self._path_apps():
            found.setdefault(entry.lowered, entry)

        self._apps = list(found.values())
        return len(self._apps)

    @property
    def count(self) -> int:
        return len(self._apps)

    # SOURCE 1 - Start Menu shortcuts

    @staticmethod
    def _start_menu_dirs() -> List[Path]:
        """The machine-wide and per-user Start Menu folders."""
        dirs = []
        program_data = os.environ.get("ProgramData")
        app_data = os.environ.get("APPDATA")
        if program_data:
            dirs.append(Path(program_data) / "Microsoft/Windows/Start Menu/Programs")
        if app_data:
            dirs.append(Path(app_data) / "Microsoft/Windows/Start Menu/Programs")
        return [d for d in dirs if d.is_dir()]

    def _start_menu_apps(self):
        """Yield applications from Start Menu shortcut files.

        The .lnk is never resolved: os.startfile follows it, applying the
        arguments and working directory the installer recorded. No COM needed.
        """
        for directory in self._start_menu_dirs():
            try:
                for item in directory.rglob("*"):
                    if item.suffix.lower() in SHORTCUT_SUFFIXES:
                        yield AppEntry(item.stem, item, "Start Menu")
            except OSError:
                # Skip folders we cannot read rather than aborting the scan
                continue

    # SOURCE 2 - Registered applications in the Windows registry

    def _registered_apps(self):
        """Yield applications from the registry's App Paths key.

        Programs register here at install time so that commands like "winword"
        work from the Run dialog. It catches applications that never created a
        Start Menu shortcut.
        """
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            try:
                root = winreg.OpenKey(hive, APP_PATHS_KEY)
            except OSError:
                continue

            try:
                subkey_count = winreg.QueryInfoKey(root)[0]
                for i in range(subkey_count):
                    try:
                        subkey_name = winreg.EnumKey(root, i)
                        with winreg.OpenKey(root, subkey_name) as subkey:
                            # The default value of the key holds the full path
                            target = winreg.QueryValueEx(subkey, "")[0]
                    except OSError:
                        continue

                    if not target:
                        continue

                    path = Path(target.strip('"'))
                    if path.exists():
                        yield AppEntry(path.stem, path, "Registry")
            finally:
                root.Close()

    # SOURCE 3 - Executables on the PATH

    def _path_apps(self):
        """Yield executables from folders on the PATH, without recursion.

        A PATH entry can point at a whole toolchain; descending is not worth it.
        """
        seen_dirs = set()
        for directory in os.environ.get("PATH", "").split(os.pathsep):
            if not directory:
                continue
            path = Path(directory)
            key = str(path).lower()
            if key in seen_dirs:
                continue
            seen_dirs.add(key)

            # Skip a project's own tooling folder, e.g. venv\Scripts
            if any(part.lower() in SKIP_PATH_DIRS for part in path.parts):
                continue

            try:
                if not path.is_dir():
                    continue
                for item in path.iterdir():
                    if item.suffix.lower() in EXECUTABLE_SUFFIXES:
                        yield AppEntry(item.stem, item, "PATH")
            except OSError:
                continue

    # SOURCE 4 - Store applications, via the Start Menu app list

    @staticmethod
    def _store_apps(entries):
        """Turn Get-StartApps rows into entries, keeping only the Store ones.

        An AppID ending in .exe is an ordinary program that the Start Menu scan
        has already found under a friendlier name, so it is dropped here rather
        than competing with itself.
        """
        for entry in entries or ():
            if not isinstance(entry, dict):
                continue
            name = (entry.get("Name") or "").strip()
            app_id = (entry.get("AppID") or "").strip()
            if not name or not app_id or app_id.lower().endswith(".exe"):
                continue
            yield AppEntry(name, Path(app_id), "Store")

    @staticmethod
    def _read_store_cache():
        """Load the cached Get-StartApps rows. Never raises."""
        try:
            with open(UWP_CACHE_PATH, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except Exception:
            return []
        return data if isinstance(data, list) else []

    @staticmethod
    def _write_store_cache(entries) -> None:
        """Save the rows for the next launch. Never raises."""
        try:
            with open(UWP_CACHE_PATH, "w", encoding="utf-8") as handle:
                json.dump(entries, handle)
        except Exception:
            pass            # A missing cache costs a refresh, never a crash

    @staticmethod
    def query_store_apps():
        """Ask Windows for its Start Menu app list. Slow: over a second.

        Returns None if the query failed, which is different from returning an
        empty list: a failure must leave the existing cache alone rather than
        wiping it.
        """
        try:
            done = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive",
                 "-Command", UWP_QUERY],
                capture_output=True, text=True, timeout=UWP_TIMEOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError):
            return None

        try:
            data = json.loads(done.stdout or "null")
        except ValueError:
            return None

        if isinstance(data, dict):
            return [data]           # ConvertTo-Json unwraps a single row
        return data if isinstance(data, list) else None

    def refresh_store_apps_async(self) -> threading.Thread:
        """Refresh the Store list off the startup path.

        Startup reads the cache and moves on; this catches up a second or so
        later and swaps the fuller list in. A newly installed Store app is
        therefore missing until the next launch, which is the right trade
        against making every launch wait for PowerShell.
        """
        thread = threading.Thread(
            target=self._refresh_store_apps, daemon=True, name="filefind-storeapps"
        )
        self._store_thread = thread
        thread.start()
        return thread

    def _refresh_store_apps(self) -> None:
        entries = self.query_store_apps()
        if entries is None:
            return              # Query failed; keep whatever the cache had
        self._write_store_cache(entries)
        self._merge_store_apps(entries)

    def _merge_store_apps(self, entries) -> None:
        """Add newly discovered Store apps to the live list.

        Rebuilds a whole list and swaps the reference in one assignment, so a
        query iterating the old list is never touched mid-scan.
        """
        by_name = {app.lowered: app for app in self._apps}
        added = False
        for entry in self._store_apps(entries):
            if entry.lowered not in by_name:
                by_name[entry.lowered] = entry
                added = True
        if added:
            self._apps = list(by_name.values())

    # SEARCHING

    def query(self, q: Query, token: CancellationToken) -> List[Result]:
        """Score every application against the query and return the matches."""
        if q.is_empty:
            return []

        needle = q.search.lower().strip()
        results = []

        for app in self._apps:
            # The list can hold a thousand entries, so give up promptly if the
            # user has already typed another character.
            if token.cancelled:
                return []

            score = self._score(app, needle)
            if score <= 0:
                continue

            results.append(
                Result(
                    title=app.name,
                    subtitle=self._subtitle(app),
                    score=score,
                    action=self._make_launch_action(app),
                    icon="app",
                    context={"path": app.target, "app_source": app.source},
                )
            )

        return results

    @staticmethod
    def _subtitle(app: AppEntry) -> str:
        """The grey line under the name: where this application came from.

        A Store app's target is an identifier, not a path, so it has no parent
        folder to show and would otherwise render as a bare dot.
        """
        if app.source == "Store":
            return "Store app"
        return "%s  ·  %s" % (app.source, app.target.parent)

    def _score(self, app: AppEntry, needle: str) -> int:
        """Rate how well an application matches. 0 means no match.

        Quality comes from the shared matcher; only app-vs-file placement here.
        """
        quality = matcher.score(needle, app.name)
        if quality <= 0:
            return 0

        score = quality + APP_SCORE_BONUS
        if app.source == "PATH":
            score -= PATH_SCORE_PENALTY

        return score

    @staticmethod
    def _launch_target(app: AppEntry) -> str:
        """What os.startfile should be handed for this application.

        A Store app has no file to open, so its identifier is addressed through
        the shell's applications folder instead of the filesystem.
        """
        if app.source == "Store":
            return UWP_SHELL_PREFIX + str(app.target)
        return str(app.target)

    @staticmethod
    def _make_launch_action(app: AppEntry):
        """Build the Enter action that starts the application."""

        def _launch() -> bool:
            # os.startfile hands the path to the Windows shell, which resolves
            # shortcuts and applies file associations. No command string is
            # built, so nothing in the name can be interpreted as a command.
            os.startfile(AppPlugin._launch_target(app))
            return True          # Hide the launcher after starting the app

        return _launch

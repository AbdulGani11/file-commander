"""
Application Search - Finds installed programs so they can be launched

FileFind's file index deliberately skips 'programdata' and 'appdata', which is
correct for finding documents but hides every installed application, because
that is exactly where Windows keeps its Start Menu shortcuts. This plugin
indexes applications separately, from the three places Windows actually
registers them.
"""

import os
import winreg
from pathlib import Path
from typing import Dict, List

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


class AppEntry:
    """One installed application: its display name and how to launch it."""

    def __init__(self, name: str, target: Path, source: str):
        self.name = name                  # Shown as the result title
        self.target = target              # What gets opened
        self.source = source              # Which of the three sources found it
        self.lowered = name.lower()       # Cached to avoid lowering on every keystroke


class AppPlugin(BasePlugin):
    """Searches installed applications and launches the selected one."""

    name = "apps"
    keyword = GLOBAL_WILDCARD    # Runs on every search, alongside file search
    search_delay = 0.0           # The list is built once and held in memory

    def __init__(self):
        self._apps: List[AppEntry] = []

    def init(self) -> None:
        """Build the application list once, at registration."""
        self.reload()

    def reload(self) -> int:
        """Rescan all sources. Returns the number of applications found."""
        found: Dict[str, AppEntry] = {}

        # Later sources must not overwrite earlier ones, so the ordering here is
        # the priority order: Start Menu entries have the friendliest names.
        for entry in self._start_menu_apps():
            found.setdefault(entry.lowered, entry)
        for entry in self._registered_apps():
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
                    subtitle="%s  ·  %s" % (app.source, app.target.parent),
                    score=score,
                    action=self._make_launch_action(app),
                    icon="app",
                    context={"path": app.target, "app_source": app.source},
                )
            )

        return results

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
    def _make_launch_action(app: AppEntry):
        """Build the Enter action that starts the application."""

        def _launch() -> bool:
            # os.startfile hands the path to the Windows shell, which resolves
            # shortcuts and applies file associations. No command string is
            # built, so nothing in the name can be interpreted as a command.
            os.startfile(str(app.target))
            return True          # Hide the launcher after starting the app

        return _launch

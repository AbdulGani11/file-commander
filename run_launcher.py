#!/usr/bin/env python3
"""Launch the Qt overlay.

    python run_launcher.py                 normal use: cached index + live watcher
    python run_launcher.py --rebuild       discard the cache and re-crawl
    python run_launcher.py --repo          quick uncached scan of this project
    python run_launcher.py --theme Darker  pick a theme

Normal startup loads the SQLite cache, so it is fast after the first run, and
starts the filesystem watcher so the index stays current. Leave it running:
it is a background launcher, not a command to re-run for each search.

Press Ctrl+Shift+F to toggle the overlay, Esc to hide, Enter to open.
"""

import importlib.util
import signal
import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from launcher import Dispatcher
from launcher.handlers import AppPlugin, FilePlugin
from launcher.ui import THEMES, LauncherOverlay, Theme
from launcher.ui.result_view import warm_icon_provider

HOTKEY = "ctrl+shift+f"


def load_filefind():
    """Import FileFind.py, which is a top-level script rather than a package."""
    spec = importlib.util.spec_from_file_location(
        "FileFind", Path(__file__).parent / "FileFind.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["FileFind"] = module
    spec.loader.exec_module(module)
    return module


def main() -> int:
    theme_name = "Dark"
    if "--theme" in sys.argv:
        theme_name = sys.argv[sys.argv.index("--theme") + 1]
        if theme_name not in THEMES:
            print("unknown theme %r; available: %s" % (theme_name, ", ".join(THEMES)))
            return 2

    ff = load_filefind()
    commander = None

    if "--repo" in sys.argv:
        # Quick uncached scan of this project, for trying things out
        index = ff.FileSearchIndex()
        count = index.index_folder(Path(__file__).parent)
        print("Indexed %d items from this repo (omit --repo for real use)" % count)
    else:
        if "--rebuild" in sys.argv and ff.CACHE_DB_PATH.exists():
            ff.CACHE_DB_PATH.unlink()
            print("Cache cleared; rebuilding from disk.")

        # FileCommander owns the index lifecycle: it loads the SQLite cache when
        # one exists and only crawls the disk when it does not. Calling
        # index_folder directly, as this used to, meant re-crawling every file
        # on every launch and never using the cache at all.
        commander = ff.FileCommander()
        commander.load_or_build_index()
        commander._start_watcher()      # keeps the index current while running
        index = commander.search_index

    dispatcher = Dispatcher()

    # Applications first: typing "chrome" almost always means launch it.
    apps = AppPlugin()
    dispatcher.register(apps)
    print("Indexed %d applications" % apps.count)

    dispatcher.register(FilePlugin(index))

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    overlay = LauncherOverlay(dispatcher, theme_name)

    # The shell charges about 300ms for the first icon of the session, whatever
    # it is asked about. Spend it now, with the window still hidden, rather than
    # on the user's first keystroke.
    warm_icon_provider()

    # Ctrl+C in the terminal. Qt's event loop blocks inside C++, so Python
    # never gets a chance to run its signal handler. The repeating timer hands
    # control back to the interpreter often enough for the handler to fire.
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    interrupt_timer = QTimer()
    interrupt_timer.start(200)
    interrupt_timer.timeout.connect(lambda: None)

    hotkey_ready = False
    try:
        import keyboard

        # suppress=True consumes the keystroke so it does not also reach the
        # focused application. Without it, Ctrl+Shift+F still triggers Find in
        # Files in VS Code while the overlay opens behind it.
        keyboard.add_hotkey(HOTKEY, overlay.hotkey_pressed.emit, suppress=True)
        hotkey_ready = True
        print("Press %s to open the overlay. Ctrl+C here to quit." % HOTKEY.upper())
        print("Leave this running; startup is only slow the first time.")
    except Exception as exc:
        print("Global hotkey unavailable (%s)." % exc)

    if not hotkey_ready:
        # Without a hotkey there is no other way to reach the window
        overlay.show_overlay()

    try:
        return app.exec()
    finally:
        # Order matters. The overlay's query thread and the filesystem watcher
        # both outlive exec() otherwise, which is what produced the
        # "QThread: Destroyed while thread is still running" warning on exit.
        try:
            import keyboard
            keyboard.unhook_all()
        except Exception:
            pass

        overlay.shutdown()
        dispatcher.shutdown()
        if commander is not None:
            commander._stop_watcher()


if __name__ == "__main__":
    raise SystemExit(main())

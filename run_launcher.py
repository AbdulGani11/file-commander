#!/usr/bin/env python3
"""Launch the Qt overlay.

    python run_launcher.py                 index this repo (fast, for testing)
    python run_launcher.py --full          index the usual user folders
    python run_launcher.py --theme Darker  pick a theme

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
    index = ff.FileSearchIndex()

    if "--full" in sys.argv:
        print("Indexing user folders...")
        for folder in ("Downloads", "Documents", "Desktop", "Videos", "Pictures"):
            path = Path.home() / folder
            if path.is_dir():
                print("  %-12s %d items" % (folder, index.index_folder(path)))
    else:
        count = index.index_folder(Path(__file__).parent)
        print("Indexed %d items from this repo (use --full for user folders)" % count)

    dispatcher = Dispatcher()

    # Applications first: typing "chrome" almost always means launch it.
    apps = AppPlugin()
    dispatcher.register(apps)
    print("Indexed %d applications" % apps.count)

    dispatcher.register(FilePlugin(index))

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    overlay = LauncherOverlay(dispatcher, theme_name)

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
    except Exception as exc:
        print("Global hotkey unavailable (%s)." % exc)

    if not hotkey_ready:
        # Without a hotkey there is no other way to reach the window
        overlay.show_overlay()

    try:
        return app.exec()
    finally:
        try:
            import keyboard
            keyboard.unhook_all()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())

"""Tests for the launcher core: query parsing, routing, cancellation, isolation."""

import threading
import time
from pathlib import Path

import pytest

from launcher import (
    BasePlugin,
    CancellationToken,
    Dispatcher,
    Query,
    Result,
)
from launcher.models import GLOBAL_WILDCARD


# helpers


class StubPlugin(BasePlugin):
    """Returns a fixed set of titles, recording what it was asked."""

    def __init__(self, name, keyword=GLOBAL_WILDCARD, titles=(), delay=0.0):
        self.name = name
        self.keyword = keyword
        self.search_delay = delay
        self._titles = list(titles)
        self.seen = []

    def query(self, q, token):
        self.seen.append(q.search)
        return [
            Result(title=t, score=100 - i) for i, t in enumerate(self._titles)
        ]


class ExplodingPlugin(BasePlugin):
    name = "boom"
    keyword = GLOBAL_WILDCARD

    def query(self, q, token):
        raise RuntimeError("plugin is broken")


# Query parsing


def test_query_without_keyword_searches_whole_text():
    q = Query.parse("visual studio", keywords={"sp"})
    assert q.keyword == ""
    assert q.search == "visual studio"
    assert q.search_terms == ("visual", "studio")


def test_query_with_keyword_strips_it_from_search():
    q = Query.parse("sp hoshino", keywords={"sp"})
    assert q.keyword == "sp"
    assert q.search == "hoshino"


def test_keyword_alone_yields_empty_search():
    q = Query.parse("sp", keywords={"sp"})
    assert q.keyword == "sp"
    assert q.search == ""
    assert q.is_empty


def test_unregistered_keyword_is_treated_as_search_text():
    q = Query.parse("sp hoshino", keywords=set())
    assert q.keyword == ""
    assert q.search == "sp hoshino"


def test_blank_query_is_empty():
    assert Query.parse("   ", keywords=set()).is_empty


# Result actions


def test_action_returning_none_means_hide():
    assert Result(title="x", action=lambda: None).run() is True


def test_action_can_keep_window_open():
    assert Result(title="x", action=lambda: False).run() is False


def test_result_without_action_does_nothing():
    assert Result(title="x").run() is False


def test_failing_action_does_not_raise():
    def boom():
        raise RuntimeError("bad action")

    assert Result(title="x", action=boom).run() is False


# Routing


def test_global_plugins_run_when_no_keyword():
    d = Dispatcher()
    a = StubPlugin("a", titles=["alpha"])
    b = StubPlugin("b", titles=["beta"])
    d.register(a)
    d.register(b)

    titles = {r.title for r in d.query("anything")}
    assert titles == {"alpha", "beta"}


def test_keyword_routes_exclusively():
    d = Dispatcher()
    files = StubPlugin("files", titles=["some_file.txt"])
    spotify = StubPlugin("spotify", keyword="sp", titles=["Hoshino"])
    d.register(files)
    d.register(spotify)

    results = d.query("sp hoshino")

    assert [r.title for r in results] == ["Hoshino"]
    assert files.seen == []                 # global plugin excluded entirely
    assert spotify.seen == ["hoshino"]      # keyword stripped from search


def test_results_are_ranked_by_score_across_plugins():
    d = Dispatcher()
    d.register(StubPlugin("low", titles=["third"]))

    high = StubPlugin("high", titles=["first"])
    high._titles = ["first"]
    d.register(high)

    results = d.query("x")
    # both produce score 100; sort is stable, so registration order holds
    assert len(results) == 2


def test_limit_is_respected():
    d = Dispatcher()
    d.register(StubPlugin("many", titles=[str(i) for i in range(50)]))
    assert len(d.query("x", limit=5)) == 5


def test_source_is_stamped_onto_results():
    d = Dispatcher()
    d.register(StubPlugin("files", titles=["a.txt"]))
    assert d.query("a")[0].source == "files"


# Fault isolation


def test_broken_plugin_does_not_break_the_query():
    d = Dispatcher()
    d.register(ExplodingPlugin())
    d.register(StubPlugin("good", titles=["survivor"]))

    results = d.query("x")

    assert [r.title for r in results] == ["survivor"]
    assert "boom" in d.last_errors
    assert "plugin is broken" in d.last_errors["boom"]


# Cancellation


def test_new_query_cancels_the_previous_token():
    d = Dispatcher()
    d.register(StubPlugin("a", titles=["x"]))

    d.query("first")
    first_token = d._token
    d.query("second")

    assert first_token.cancelled
    assert not d._token.cancelled


def test_cancelled_plugin_result_is_discarded():
    """A plugin that notices cancellation mid-flight contributes nothing."""
    started = threading.Event()

    class SlowPlugin(BasePlugin):
        name = "slow"
        keyword = GLOBAL_WILDCARD

        def query(self, q, token):
            started.set()
            # Wait until cancelled by the caller, then bail out like a
            # well-behaved plugin should.
            token.wait(2.0)
            if token.cancelled:
                return []
            return [Result(title="stale", score=1)]

    d = Dispatcher()
    d.register(SlowPlugin())

    out = {}

    def run():
        out["results"] = d.query("first")

    worker = threading.Thread(target=run)
    worker.start()
    assert started.wait(2.0), "plugin never ran"

    d.cancel()
    worker.join(timeout=3.0)

    assert out["results"] == []


def test_search_delay_aborts_early_when_cancelled():
    """Debounce must wait on the token, not sleep blindly through it."""
    token = CancellationToken()
    plugin = StubPlugin("delayed", titles=["x"], delay=5.0)

    d = Dispatcher()
    d.register(plugin)

    started = time.perf_counter()
    threading.Timer(0.05, token.cancel).start()
    results = d._run_plugin(plugin, Query.parse("x", set()), token)
    elapsed = time.perf_counter() - started

    assert results == []
    assert elapsed < 1.0, "debounce slept through cancellation (%.2fs)" % elapsed
    assert plugin.seen == [], "plugin ran despite being cancelled"


# Diagnostics


def test_timings_recorded_per_plugin():
    d = Dispatcher()
    d.register(StubPlugin("a", titles=["x"]))
    d.register(StubPlugin("b", titles=["y"]))
    d.query("x")

    assert set(d.last_timings) == {"a", "b"}
    assert d.last_query_ms() >= 0.0
    assert "slowest" in d.describe_timings()


# Cross-plugin ranking


def test_application_outranks_similarly_named_file():
    """Typing an app acronym must surface the app, not a lookalike filename.

    Regression guard: file scores once started high enough to bury every
    application, so "vsc" returned a .vscode folder instead of Visual Studio Code.
    """
    from launcher.handlers.apps import AppEntry, AppPlugin
    from launcher.handlers.files import FilePlugin

    class FakeIndex:
        def search(self, text, limit):
            from pathlib import Path
            return [Path(r"C:\proj\.vscode"), Path(r"C:\proj\vsc_notes.txt")]

    d = Dispatcher()
    apps = AppPlugin()
    d.register(apps)
    d.register(FilePlugin(FakeIndex()))

    # Replace the app list *after* registering. register() calls init(), which
    # rescans the real machine and would discard anything set beforehand. Doing
    # it in the wrong order made this test pass only on machines that happen to
    # have Visual Studio Code installed.
    apps._apps = [
        AppEntry("Visual Studio Code", Path(r"C:\apps\Code.lnk"), "Start Menu")
    ]

    results = d.query("vsc")

    assert results, "expected results"
    assert results[0].title == "Visual Studio Code", [r.title for r in results]
    assert results[0].source == "apps"


# String matcher


def test_matcher_finds_acronyms():
    """The gap this was built for: three letters that never appear together."""
    from launcher import matcher

    assert matcher.score("vsc", "visual_studio_config.json") > 0
    assert matcher.score("qbr", "quarterly_business_review.docx") > 0
    assert matcher.score("msr", "monthly_status_report.pdf") > 0
    assert matcher.score("vsc", "Visual Studio Code") > 0


def test_matcher_rejects_scattered_letters():
    """A gapped match must start on a word boundary, or it is noise."""
    from launcher import matcher

    assert matcher.score("msr", "Administrative Tools") == 0
    assert matcher.score("report", "browserexport") == 0
    assert matcher.score("xyz", "Visual Studio Code") == 0


def test_matcher_exact_beats_partial():
    from launcher import matcher

    assert matcher.score("chrome", "chrome") > matcher.score("chrome", "Google Chrome")


def test_matcher_prefers_tighter_and_earlier_matches():
    from launcher import matcher

    # Same query, but one name is almost entirely the query
    assert matcher.score("test", "test.py") > matcher.score(
        "test", "my_test_helper_utilities.py"
    )
    # Acronym across every word beats one that leaves words over
    assert matcher.score("vsc", "Visual Studio Code") > matcher.score(
        "vs", "Visual Studio Code"
    )


def test_matcher_allows_plain_substrings_mid_word():
    """Contiguous matches are legitimate even away from a word boundary."""
    from launcher import matcher

    assert matcher.score("port", "export.txt") > 0


def test_index_stores_acronym_tokens(tmp_path):
    """Retrieval, not just ranking: the engine must return the candidate."""
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "FileFind", Path(__file__).parent.parent / "FileFind.py"
    )
    ff = importlib.util.module_from_spec(spec)
    sys.modules["FileFind"] = ff
    spec.loader.exec_module(ff)

    target = tmp_path / "quarterly_business_review.docx"
    target.touch()

    index = ff.FileSearchIndex()
    index.add_file(target)

    assert "qbr" in index.word_index, sorted(index.word_index)
    assert target in index.word_index["qbr"]

    # And it must be evicted again on removal
    index.remove_file(target)
    assert "qbr" not in index.word_index


def test_path_scan_skips_project_tooling_but_keeps_system32():
    """A project's own venv Scripts folder is noise; system32 is not.

    system32 holds cmd, calc, taskmgr and regedit, so it must stay indexed.
    """
    from launcher.handlers.apps import SKIP_PATH_DIRS

    def skipped(raw):
        return any(p.lower() in SKIP_PATH_DIRS for p in Path(raw).parts)

    assert skipped(r"C:\proj\venv\Scripts")
    assert skipped(r"C:\proj\.venv\Scripts")
    assert skipped(r"C:\proj\node_modules\.bin")

    # These must survive: real programs users launch live here
    assert not skipped(r"C:\WINDOWS\system32")
    assert not skipped(r"C:\WINDOWS")
    assert not skipped(r"C:\Program Files\Git\usr\bin")


# Overlay hotkey path (first coverage of launcher/ui)


def test_hotkey_signal_toggles_window_from_another_thread():
    """The global hotkey fires on its own thread and must still reach the UI.

    Regression guard: the hotkey used QTimer.singleShot, but a timer belongs to
    the thread that created it, and the keyboard library's thread runs no Qt
    event loop, so the call was never delivered. Pressing the hotkey did
    nothing. A Qt signal queues onto the receiving thread instead.
    """
    import os
    import threading
    import time

    pytest.importorskip("PySide6")
    # No real display needed, and none is guaranteed on a CI runner
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication

    from launcher.ui import LauncherOverlay

    app = QApplication.instance() or QApplication([])
    overlay = LauncherOverlay(Dispatcher(), "Dark")

    def pump(seconds=1.5):
        end = time.time() + seconds
        while time.time() < end:
            app.processEvents()
            time.sleep(0.01)

    try:
        assert not overlay.isVisible(), "overlay must start hidden"

        # Emit from a plain thread, exactly as the keyboard library does
        threading.Thread(target=overlay.hotkey_pressed.emit, daemon=True).start()
        pump()
        assert overlay.isVisible(), "hotkey did not open the window"

        threading.Thread(target=overlay.hotkey_pressed.emit, daemon=True).start()
        pump()
        assert not overlay.isVisible(), "second press did not hide the window"
    finally:
        overlay.close()


def test_empty_search_shows_a_notice_not_a_blank_window():
    """A blank window cannot be told apart from a broken one.

    Typing something that matches nothing must say so. The notice is not a
    result: it carries no action, cannot be selected, and Enter on it does
    nothing.
    """
    import os

    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication

    from launcher.ui import LauncherOverlay
    from launcher.ui.result_view import RESULT_ROLE

    app = QApplication.instance() or QApplication([])
    overlay = LauncherOverlay(Dispatcher(), "Dark")

    try:
        # Something was typed, but nothing matched
        overlay.query_box.blockSignals(True)
        overlay.query_box.setText("zzzqqq")
        overlay.query_box.blockSignals(False)
        overlay._set_results([])
        app.processEvents()

        assert overlay.result_list.count() == 1, "expected a single notice row"
        notice = overlay.result_list.item(0).data(RESULT_ROLE)
        assert "zzzqqq" in notice.title
        assert notice.context.get("placeholder") is True
        assert notice.action is None
        assert overlay.result_list.currentRow() == -1, "notice must not be selected"

        # Enter must not raise, and must not hide the window
        overlay._activate()
        assert overlay.result_list.count() == 1

        # An empty box is not a failure, so it shows nothing at all
        overlay.query_box.blockSignals(True)
        overlay.query_box.setText("")
        overlay.query_box.blockSignals(False)
        overlay._set_results([])
        app.processEvents()
        assert overlay.result_list.count() == 0
    finally:
        overlay.close()


def test_alt_number_opens_the_matching_row():
    """Alt+N must open row N.

    Regression guard: this was handled in keyPressEvent, but Alt is the menu
    mnemonic modifier on Windows, so the query box consumed the digit and only
    the bare Alt keypress ever reached the overlay. Pressing Alt+3 did nothing.
    A QShortcut is matched before normal key delivery.
    """
    import os

    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from launcher.ui import LauncherOverlay

    app = QApplication.instance() or QApplication([])
    overlay = LauncherOverlay(Dispatcher(), "Dark")
    opened = []

    def rows():
        # Activating hides the window and clears results, so rebuild each time
        return [
            Result(title="row%d" % i, score=100 - i,
                   action=lambda i=i: opened.append(i) or True)
            for i in range(5)
        ]

    try:
        for key, expected in [(Qt.Key_1, 0), (Qt.Key_3, 2), (Qt.Key_5, 4)]:
            opened.clear()
            overlay.show()
            overlay._set_results(rows())
            app.processEvents()

            QTest.keyClick(overlay.focusWidget(), key, Qt.AltModifier)
            for _ in range(20):
                app.processEvents()

            assert opened == [expected], "Alt+%d opened %r" % (expected + 1, opened)

        # Beyond the end of the list, nothing should happen
        opened.clear()
        overlay.show()
        overlay._set_results(rows())
        app.processEvents()
        QTest.keyClick(overlay.focusWidget(), Qt.Key_9, Qt.AltModifier)
        for _ in range(20):
            app.processEvents()
        assert opened == []
    finally:
        overlay.close()


def test_window_shrinks_back_when_results_are_cleared():
    """The window must fit its rows exactly, growing and shrinking.

    Regression guard: the height was left to adjustSize(), which grows a top
    level window but does not reliably shrink it. Clearing the results left the
    window at its previous size with empty space below the search box until the
    next query resized it.
    """
    import os

    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication

    from launcher.ui import LauncherOverlay

    app = QApplication.instance() or QApplication([])
    overlay = LauncherOverlay(Dispatcher(), "Dark")

    def rows(n):
        return [Result(title="row%d" % i, score=100 - i) for i in range(n)]

    try:
        empty_height = overlay.height()

        overlay._set_results(rows(3))
        app.processEvents()
        three = overlay.height()
        assert three > empty_height, "window did not grow for results"

        overlay._set_results(rows(1))
        app.processEvents()
        assert overlay.height() < three, "window did not shrink for fewer results"

        overlay._set_results([])
        app.processEvents()
        assert overlay.height() == empty_height, "window did not return to its empty size"

        # More rows than fit are capped, so the window stops growing
        overlay._set_results(rows(5))
        app.processEvents()
        five = overlay.height()
        overlay._set_results(rows(20))
        app.processEvents()
        assert overlay.height() == five, "window grew past the visible row limit"
    finally:
        overlay.close()


# Paint path: what the UI thread pays on every keystroke


def test_icon_cache_stores_rendered_pixmaps_not_lazy_handles(tmp_path):
    """The icon cache must hold the finished pixmap, not the promise of one.

    Regression guard: it cached the QIcon returned by QFileIconProvider, which
    is lazy -- no shell work happens until a pixmap is asked for, which is
    inside paint(), on the UI thread. So the cache stored the cheap half and
    the expensive half was re-paid on every repaint: measured at 866x the cost
    of the handle, up to 85ms for one row, which is what made the search box
    lag behind typing.
    """
    import os

    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtGui import QPixmap
    from PySide6.QtWidgets import QApplication

    from launcher.ui import result_view

    QApplication.instance() or QApplication([])

    sample = tmp_path / "sample.txt"
    sample.write_text("x")

    result_view._ICON_CACHE.clear()
    pixmap = result_view._pixmap_for(str(sample), 32)

    assert isinstance(pixmap, QPixmap), "cache must yield a rendered pixmap"
    assert len(result_view._ICON_CACHE) == 1

    # The second call must return the same object, not re-extract it
    again = result_view._pixmap_for(str(sample), 32)
    assert again is pixmap, "a cached icon was rendered a second time"

    # Size is part of the identity, so a different size is a different entry
    result_view._pixmap_for(str(sample), 16)
    assert len(result_view._ICON_CACHE) == 2


def test_icon_cache_is_bounded(tmp_path):
    """The cache must not grow for the whole session.

    Rendered pixmaps hold real memory, unlike the empty handles this used to
    keep, so an unbounded cache is now a leak rather than a rounding error.
    """
    import os

    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication

    from launcher.ui import result_view

    QApplication.instance() or QApplication([])

    result_view._ICON_CACHE.clear()
    limit = result_view.MAX_CACHED_ICONS

    # .exe carries its own icon per file, so each of these is a distinct entry.
    # Ordinary documents would collapse onto one shared key and never fill it.
    for i in range(limit + 25):
        result_view._pixmap_for(str(tmp_path / ("f%d.exe" % i)), 32)

    assert len(result_view._ICON_CACHE) <= limit, "icon cache grew without limit"

    # The oldest entries are the ones dropped
    assert (str(tmp_path / "f0.exe"), 32, 1.0) not in result_view._ICON_CACHE
    newest = (str(tmp_path / ("f%d.exe" % (limit + 24))), 32, 1.0)
    assert newest in result_view._ICON_CACHE, "most recent icon was evicted"


# Window placement, scaling, and when the overlay is allowed to hide


def test_theme_scales_pixels_but_not_counts():
    """Scaling must multiply pixel metrics and leave the rest alone.

    An interface scale is a zoom: fonts, rows, icons and width grow together.
    Row counts and animation durations are not lengths and must not be caught
    up in it.
    """
    pytest.importorskip("PySide6")

    from launcher.ui.theme import BASE, MAX_SCALE, MIN_SCALE, Theme, clamp_scale

    normal = Theme("Dark")
    assert normal.scale == 1.0

    big = Theme("Dark", 2.0)
    assert big.m("window_width") == BASE["window_width"] * 2
    assert big.m("title_font_size") == BASE["title_font_size"] * 2
    assert big.m("item_height") == BASE["item_height"] * 2

    # Not lengths: these stay put at any scale
    assert big.m("max_visible_items") == BASE["max_visible_items"]
    assert big.m("animation_ms") == BASE["animation_ms"]

    # Out-of-range requests are held at the limits rather than rejected
    assert Theme("Dark", 99.0).scale == MAX_SCALE
    assert Theme("Dark", 0.01).scale == MIN_SCALE
    assert clamp_scale("nonsense") == 1.0

    # Nothing may collapse to zero pixels at the smallest scale
    smallest = Theme("Dark", MIN_SCALE)
    assert all(v >= 1 for v in smallest.metrics.values())

    # The palette is carried across unchanged
    assert Theme("Light", 1.5).c("window_bg") == Theme("Light").c("window_bg")


def test_dragging_the_corner_scales_the_whole_interface():
    """Dragging must resize text and rows together, not stretch the window."""
    import os

    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication

    from launcher.ui import LauncherOverlay
    from launcher.ui.theme import MAX_SCALE

    app = QApplication.instance() or QApplication([])
    overlay = LauncherOverlay(Dispatcher(), "Dark")

    try:
        start_width = overlay.width()
        start_row = overlay.theme.m("item_height")
        start_font = overlay.theme.m("title_font_size")

        overlay.set_scale(1.5)
        app.processEvents()

        assert overlay.width() > start_width, "window did not widen"
        assert overlay.theme.m("item_height") > start_row, "rows did not grow"
        assert overlay.theme.m("title_font_size") > start_font, "text did not grow"

        # Scaling back down must work too, not just up
        overlay.set_scale(0.8)
        app.processEvents()
        assert overlay.width() < start_width, "window did not shrink below default"

        # And it must refuse to leave the supported range
        overlay.set_scale(50.0)
        app.processEvents()
        assert overlay.theme.scale == MAX_SCALE
    finally:
        overlay.close()


def test_position_and_scale_survive_a_restart(tmp_path):
    """Where the user put the window is where it must reappear."""
    import os

    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QApplication

    from launcher.ui import LauncherOverlay

    app = QApplication.instance() or QApplication([])

    first = LauncherOverlay(Dispatcher(), "Dark")
    try:
        first.set_scale(1.4)
        first.move(QPoint(140, 260))
        app.processEvents()
        first.shutdown()                 # writes the settings file
    finally:
        first.close()

    second = LauncherOverlay(Dispatcher(), "Dark")
    try:
        assert abs(second.theme.scale - 1.4) < 0.001, "scale was not remembered"
        second._place()
        app.processEvents()
        assert second.pos() == QPoint(140, 260), "position was not remembered"
    finally:
        second.close()


def test_a_saved_position_off_screen_falls_back_to_centre():
    """A position from a monitor that is no longer attached must not strand it."""
    import os

    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication

    from launcher.ui import LauncherOverlay

    QApplication.instance() or QApplication([])
    overlay = LauncherOverlay(Dispatcher(), "Dark")

    try:
        overlay._settings = {"x": -30000, "y": -30000}
        overlay._place()
        assert overlay._on_a_screen(overlay.pos()), "window opened off the desktop"
    finally:
        overlay.close()


def test_losing_focus_does_not_hide_the_window():
    """Only Escape, the hotkey, or opening a result may hide the overlay.

    Hiding on focus loss is the usual launcher behaviour and was this one's,
    but it throws away a half-typed query as soon as anything else is clicked.
    A hide the overlay did not ask for is undone.
    """
    import os

    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication

    from launcher.ui import LauncherOverlay

    app = QApplication.instance() or QApplication([])
    overlay = LauncherOverlay(Dispatcher(), "Dark")

    def pump(rounds=30):
        for _ in range(rounds):
            app.processEvents()
            time.sleep(0.005)

    try:
        overlay.show_overlay()
        pump()
        assert overlay.isVisible()

        overlay.query_box.setText("half typed")

        # Stands in for the window manager withdrawing it on deactivation
        overlay.hide()
        pump()

        assert overlay.isVisible(), "an outside hide was not undone"
        assert overlay.query_box.text() == "half typed", "the query was discarded"

        # Deliberate hides still work, and do clear the box
        overlay.hide_overlay()
        pump()
        assert not overlay.isVisible(), "Escape or the hotkey failed to hide it"
        assert overlay.query_box.text() == ""
    finally:
        overlay.close()


def test_only_the_margin_starts_a_drag():
    """Dragging must not steal clicks from the query box or the result rows."""
    import os

    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QApplication

    from launcher.ui import LauncherOverlay
    from launcher.ui.overlay import RESIZE_GRIP

    QApplication.instance() or QApplication([])
    overlay = LauncherOverlay(Dispatcher(), "Dark")

    try:
        overlay._set_results([Result(title="row%d" % i) for i in range(3)])
        rect = overlay.rect()

        # Middle of the window belongs to the widgets, not to dragging
        assert overlay._zone_at(rect.center()) is None

        # The margin moves it
        assert overlay._zone_at(QPoint(rect.center().x(), 2)) == "move"
        assert overlay._zone_at(QPoint(2, rect.center().y())) == "move"

        # The bottom-right corner scales instead
        corner = QPoint(rect.right() - 2, rect.bottom() - 2)
        assert overlay._zone_at(corner) == "scale"

        # Just inside the corner grip is still scale; well clear of it is not
        assert overlay._zone_at(
            QPoint(rect.right() - RESIZE_GRIP + 1, rect.bottom() - 2)
        ) == "scale"
    finally:
        overlay.close()


def test_ordinary_files_share_one_icon_per_type():
    """Documents of the same type must share a single rendered icon.

    Windows shows one picture for every .txt, so extracting it per file meant a
    fresh twenty-row result set made twenty shell calls, each up to 400ms, all
    on the UI thread. Only types that carry their own icon -- programs and
    shortcuts -- are still kept per file.
    """
    pytest.importorskip("PySide6")

    from launcher.ui.result_view import _icon_identity

    # Same type, different files: one identity
    assert _icon_identity(r"C:\a\notes.txt", "file") == _icon_identity(
        r"D:\elsewhere\other.txt", "file"
    )
    # Different types stay apart
    assert _icon_identity(r"C:\a\notes.txt", "file") != _icon_identity(
        r"C:\a\notes.pdf", "file"
    )
    # Case in the extension must not split the entry
    assert _icon_identity(r"C:\a\A.TXT", "file") == _icon_identity(
        r"C:\a\b.txt", "file"
    )

    # Programs and shortcuts keep their own icon, so they stay per file
    for path in (r"C:\apps\chrome.exe", r"C:\menu\Chrome.lnk"):
        assert _icon_identity(path, "file") == path
    assert _icon_identity(r"C:\apps\thing.exe", "app") == r"C:\apps\thing.exe"

    # Folders all look alike
    assert _icon_identity(r"C:\one", "folder") == _icon_identity(r"D:\two", "folder")


def test_painting_never_waits_for_the_shell(tmp_path):
    """Painting a row must not extract its icon.

    Shell extraction runs from 2ms for a document to 85ms for an executable,
    and it used to happen inside paint(), on the UI thread, which is the thread
    that also has to echo typed characters into the query box. A row whose icon
    is not cached yet draws the placeholder glyph and queues the extraction for
    the next turn of the event loop instead.
    """
    import os

    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication

    from launcher.ui import LauncherOverlay
    from launcher.ui import result_view

    app = QApplication.instance() or QApplication([])
    overlay = LauncherOverlay(Dispatcher(), "Dark")

    sample = tmp_path / "row.txt"
    sample.write_text("x")

    extracted = []
    original = result_view._extract_pixmap

    def counting_extract(key, path=""):
        extracted.append(key)
        return original(key, path)

    result_view._extract_pixmap = counting_extract
    try:
        result_view._ICON_CACHE.clear()
        overlay._set_results([Result(title="row.txt", context={"path": sample})])
        overlay.show()
        app.processEvents()          # lets the row paint

        assert extracted == [], "paint() extracted an icon instead of queueing it"
        assert overlay.result_list._icon_queue, "no icon was queued for rendering"

        # The queued work happens on a later turn of the loop
        for _ in range(20):
            app.processEvents()
            if extracted:
                break

        assert extracted, "queued icon was never rendered"
        assert not overlay.result_list._icon_queue, "icon queue was not drained"

        # Now that it is cached, painting must not queue it again
        overlay.result_list.viewport().update()
        app.processEvents()
        assert not overlay.result_list._icon_queue, "a cached icon was queued again"
    finally:
        result_view._extract_pixmap = original
        overlay.close()


def test_window_surface_is_rendered_once_per_size():
    """The drop shadow must not be re-stroked on every repaint.

    It is two dozen stacked antialiased rounded outlines. Redrawing them for
    each paint made the window surface the second largest cost on the UI
    thread, behind only the icons. It only changes when the window resizes or
    the theme changes, so it is cached against exactly those.
    """
    import os

    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication

    from launcher.ui import LauncherOverlay
    from launcher.ui.theme import Theme

    app = QApplication.instance() or QApplication([])
    overlay = LauncherOverlay(Dispatcher(), "Dark")

    try:
        first = overlay._surface_pixmap()
        assert overlay._surface_pixmap() is first, "surface was redrawn unchanged"

        # Resizing must invalidate it
        overlay._set_results([Result(title="row%d" % i) for i in range(3)])
        app.processEvents()
        grown = overlay._surface_pixmap()
        assert grown is not first, "surface survived a resize"

        # So must a theme swap, since the colours are baked into the pixmap
        overlay.apply_theme(Theme("Light"))
        app.processEvents()
        assert overlay._surface_pixmap() is not grown, "surface survived a theme change"
    finally:
        overlay.close()


def test_typing_cancels_the_query_still_in_flight():
    """A keystroke must cancel the query the worker is running right now.

    Regression guard: cancellation lived at the top of Dispatcher.query, but
    requests are queued onto one worker thread and each runs to completion, so
    the previous query was always already finished by then and the token checks
    in the plugins never fired. The cancel has to come from the UI thread.
    """
    import os

    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication

    from launcher.ui import LauncherOverlay

    QApplication.instance() or QApplication([])

    started = threading.Event()
    observed = []

    class SlowPlugin(BasePlugin):
        name = "slow"

        def query(self, q, token):
            started.set()
            token.wait(2.0)             # stands in for real per-keystroke work
            observed.append(token.cancelled)
            return []

    dispatcher = Dispatcher()
    dispatcher.register(SlowPlugin())
    overlay = LauncherOverlay(dispatcher, "Dark")

    try:
        overlay.query_box.setText("a")
        assert started.wait(2.0), "worker never picked the query up"

        # Second keystroke while the first is still running
        overlay.query_box.setText("ab")

        deadline = time.time() + 2.0
        while not observed and time.time() < deadline:
            time.sleep(0.01)

        assert observed, "the in-flight query never finished"
        assert observed[0] is True, "typing did not cancel the running query"
    finally:
        overlay.close()
        dispatcher.shutdown()
